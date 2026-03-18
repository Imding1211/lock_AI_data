"""Raw → Bronze LINE Chat 處理器

將 storage/raw/line_chat/ 下的 LINE 官方帳號匯出 CSV，
清洗雜訊並以 2 小時時間斷點聚合為 Session，
輸出至 storage/bronze/line_chat/。
"""

import argparse
import logging
import tomllib
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[2]
RAW_DIR = BASE_DIR / "storage" / "raw" / "line_chat"
BRONZE_DIR = BASE_DIR / "storage" / "bronze" / "line_chat"

SESSION_GAP_SECONDS = 7200  # 2 hours
MIN_TRANSCRIPT_LENGTH = 20

NOISE_CONTENT_PATTERNS = [
    "[貼圖]", "[照片]", "[影片]", "[檔案]", "[語音訊息]",
    "照片已傳送", "貼圖已傳送", "影片已傳送", "語音訊息已傳送", "檔案已傳送",
]

CONFIG_PATH = BASE_DIR / "config.toml"

logger = logging.getLogger(__name__)


def load_auto_reply_patterns() -> list[str]:
    """從 config.toml 讀取 auto_reply_patterns。"""
    with open(CONFIG_PATH, "rb") as f:
        config = tomllib.load(f)
    return config["pipelines"]["line_chat"].get("auto_reply_patterns", [])


def process_file(csv_path: Path, output_path: Path, auto_reply_patterns: list[str] | None = None) -> None:
    """處理單一 LINE chat CSV，輸出 Bronze CSV。"""

    df = pd.read_csv(csv_path, skiprows=3, encoding="utf-8-sig")

    raw_count = len(df)
    logger.debug(f"讀取 {raw_count} 筆 — {csv_path.name}")

    # --- 雜訊過濾 ---
    df = df[df["傳送者名稱"] != "自動回應訊息"]
    df = df[df["傳送者類型"] != "System"]

    content_col = "內容"
    df = df[df[content_col].notna()]

    for pattern in NOISE_CONTENT_PATTERNS:
        df = df[df[content_col] != pattern]

    if auto_reply_patterns:
        for pattern in auto_reply_patterns:
            df = df[df[content_col] != pattern]

    df = df[~df[content_col].str.contains("已收回訊息", na=False)]

    filtered_count = len(df)
    logger.debug(f"過濾後剩 {filtered_count} 筆 — {csv_path.name}")

    if filtered_count == 0:
        logger.info(f"過濾後無資料，跳過 — {csv_path.name}")
        return

    # --- 關鍵字篩選：整檔未提及「鎖」則跳過 ---
    all_content = df[content_col].str.cat(sep=" ")
    if "鎖" not in all_content:
        logger.info(f"整檔未提及「鎖」，跳過 — {csv_path.name}")
        return

    # --- 時間處理 ---
    df = df.copy()
    df["datetime"] = pd.to_datetime(df["傳送日期"] + " " + df["傳送時間"])
    df = df.sort_values("datetime").reset_index(drop=True)

    # --- Session 聚合 ---
    df["time_diff"] = df["datetime"].diff().dt.total_seconds().fillna(0)
    df["session_group"] = (df["time_diff"] > SESSION_GAP_SECONDS).cumsum()

    stem = csv_path.stem
    sessions = []

    for group_id, group_df in df.groupby("session_group"):
        lines = []
        for _, row in group_df.iterrows():
            lines.append(f"{row['傳送者名稱']}: {row[content_col]}")
        transcript = "\n".join(lines)

        if len(transcript) < MIN_TRANSCRIPT_LENGTH:
            continue

        sessions.append(
            {
                "session_id": f"{stem}_session_{len(sessions) + 1}",
                "start_time": group_df["datetime"].iloc[0],
                "end_time": group_df["datetime"].iloc[-1],
                "transcript": transcript,
            }
        )

    logger.info(
        f"{csv_path.name}: 讀取 {raw_count} 筆，過濾後剩 {filtered_count} 筆，"
        f"聚合成 {len(sessions)} 個 Session"
    )

    if not sessions:
        logger.info(f"無有效 Session，跳過 — {csv_path.name}")
        return

    out_df = pd.DataFrame(sessions)
    out_df.to_csv(output_path, index=False)
    logger.debug(f"輸出 → {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Raw → Bronze LINE Chat 處理器")
    parser.add_argument("--file", help="處理單一 CSV（相對於 storage/raw/line_chat/ 的檔名）")
    parser.add_argument("--force", action="store_true", help="強制覆寫已存在的 Bronze 輸出")
    parser.add_argument("--verbose", action="store_true", help="啟用 DEBUG logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    auto_reply_patterns = load_auto_reply_patterns()
    logger.debug(f"載入 {len(auto_reply_patterns)} 個 auto_reply_patterns")

    BRONZE_DIR.mkdir(parents=True, exist_ok=True)

    if args.file:
        files = [RAW_DIR / args.file]
    else:
        files = sorted(RAW_DIR.glob("*.csv"))

    logger.info(f"共 {len(files)} 個檔案待處理")

    for csv_path in files:
        if not csv_path.exists():
            logger.error(f"檔案不存在: {csv_path}")
            continue

        output_path = BRONZE_DIR / csv_path.name

        if output_path.exists() and not args.force:
            logger.debug(f"已存在，跳過 — {csv_path.name}")
            continue

        try:
            process_file(csv_path, output_path, auto_reply_patterns)
        except Exception:
            logger.exception(f"處理失敗 — {csv_path.name}")


if __name__ == "__main__":
    main()
