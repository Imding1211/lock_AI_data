"""Raw → Bronze Video ASR 轉錄器

將 storage/raw/video/ 下的 .MOV / .mp4 透過 OpenAI Whisper 轉錄，
輸出帶 [MM:SS] 時間戳的逐字稿至 storage/bronze/video/。
"""

import argparse
import logging
import sys
import tomllib
from pathlib import Path

import whisper

ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT_DIR / "config.toml"
RAW_DIR = ROOT_DIR / "storage" / "raw" / "video"
BRONZE_DIR = ROOT_DIR / "storage" / "bronze" / "video"

logger = logging.getLogger(__name__)


def load_config() -> dict:
    """從 config.toml 讀取 video_asr 設定。"""
    with open(CONFIG_PATH, "rb") as f:
        config = tomllib.load(f)
    return config["pipelines"]["video_asr"]


def format_timestamp(seconds: float) -> str:
    """將秒數轉為 [MM:SS] 格式。"""
    minutes = int(seconds) // 60
    secs = int(seconds) % 60
    return f"[{minutes:02d}:{secs:02d}]"


def transcribe_one(model, filepath: Path, fp16: bool) -> str:
    """轉錄單一影片，回傳帶時間戳的逐字稿文字。"""
    result = model.transcribe(
        str(filepath),
        fp16=fp16,
        initial_prompt="這是一段關於電子鎖或客服對話的繁體中文教學影片。",
    )

    lines = []
    for segment in result["segments"]:
        ts = format_timestamp(segment["start"])
        text = segment["text"].strip()
        if text:
            lines.append(f"{ts} {text}")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Raw → Bronze Video ASR 轉錄器")
    parser.add_argument("--file", help="單檔模式：處理指定檔案（相對於 RAW_DIR）")
    parser.add_argument("--force", action="store_true", help="強制覆寫已存在的 Bronze 輸出")
    parser.add_argument("--verbose", action="store_true", help="啟用 DEBUG logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    config = load_config()
    model_type = config.get("model_type", "medium")
    fp16 = config.get("fp16", False)

    logger.info(f"載入 Whisper 模型: {model_type}")
    model = whisper.load_model(model_type)

    BRONZE_DIR.mkdir(parents=True, exist_ok=True)

    if args.file:
        candidates = [RAW_DIR / args.file]
    else:
        mov_files = list(RAW_DIR.rglob("*.[mM][oO][vV]"))
        mp4_files = list(RAW_DIR.rglob("*.[mM][pP]4"))
        candidates = sorted(set(mov_files + mp4_files))

    found = len(candidates)
    logger.info(f"共 {found} 支影片待處理")

    processed = 0
    skipped = 0
    failed = 0

    for filepath in candidates:
        if not filepath.exists():
            logger.error(f"檔案不存在: {filepath}")
            failed += 1
            continue

        output_path = BRONZE_DIR / f"{filepath.stem}.txt"

        if output_path.exists() and not args.force:
            logger.debug(f"已存在，跳過 — {filepath.name}")
            skipped += 1
            continue

        try:
            logger.info(f"轉錄中 — {filepath.name}")
            transcript = transcribe_one(model, filepath, fp16)

            with open(output_path, "w", encoding="utf-8") as f:
                f.write(transcript + "\n")

            logger.info(f"輸出 → {output_path}")
            processed += 1
        except Exception:
            logger.exception(f"處理失敗 — {filepath.name}")
            failed += 1

    logger.info(f"完成：找到 {found} / 處理 {processed} / 跳過 {skipped} / 失敗 {failed}")


if __name__ == "__main__":
    main()
