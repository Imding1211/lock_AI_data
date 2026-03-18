"""Raw → Bronze YouTube 視覺解析器

將 storage/raw/youtube/ 下的 .mp4 透過視覺模型解析，
結合 metadata 輸出 Bronze JSON 至 storage/bronze/youtube/。
"""

import argparse
import json
import logging
import sys
import time
import tomllib
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from llms import get_vision_llm

CONFIG_PATH = ROOT_DIR / "config.toml"
RAW_DIR = ROOT_DIR / "storage" / "raw" / "youtube"
BRONZE_DIR = ROOT_DIR / "storage" / "bronze" / "youtube"

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
你是一位專業的影片內容分析助手。請仔細觀看這支智慧門鎖教學影片，並產出完整的逐步操作紀錄。

要求：
1. 每個段落必須標註 [MM:SS] 時間戳記
2. 擷取畫面上出現的所有文字（包含 APP 介面、按鈕文字、提示訊息）
3. 詳細描述操作步驟與畫面變化
4. 使用繁體中文
5. 輸出格式為結構化 Markdown

範例格式：
[00:00] 影片開頭，畫面顯示「AI-99 安裝教學」標題
[00:15] 操作者打開 APP，點擊「新增裝置」按鈕
[00:30] 畫面切換至藍牙配對頁面，顯示「搜尋中...」
"""


def load_config() -> dict:
    """從 config.toml 讀取 youtube_vision 設定。"""
    with open(CONFIG_PATH, "rb") as f:
        config = tomllib.load(f)
    return config["pipelines"]["youtube_vision"]


def process_video(
    video_id: str,
    generate_from_video: callable,
) -> dict | None:
    """處理單一影片，回傳 Bronze JSON dict 或 None。"""
    mp4_path = RAW_DIR / f"{video_id}.mp4"
    meta_path = RAW_DIR / f"{video_id}.json"

    if not mp4_path.exists():
        logger.error(f"影片不存在: {mp4_path}")
        return None

    # 讀取 metadata
    if meta_path.exists():
        with open(meta_path, "r", encoding="utf-8") as f:
            metadata = json.load(f)
    else:
        logger.warning(f"Metadata 不存在，使用預設值 — {video_id}")
        metadata = {
            "video_id": video_id,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "title": "",
        }

    logger.info(f"解析中 — {video_id} ({metadata.get('title', '')})")
    transcript = generate_from_video(str(mp4_path), SYSTEM_PROMPT)

    return {
        "video_id": metadata["video_id"],
        "url": metadata["url"],
        "title": metadata["title"],
        "transcript": transcript,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Raw → Bronze YouTube 視覺解析器")
    parser.add_argument("--file", help="單檔模式：處理指定 video_id")
    parser.add_argument("--force", action="store_true", help="強制覆寫已存在的 Bronze 輸出")
    parser.add_argument("--verbose", action="store_true", help="啟用 DEBUG logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    config = load_config()
    generate_from_video = get_vision_llm(
        provider=config["llm_provider"],
        model=config["llm_model"],
        temperature=config.get("temperature", 0.1),
    )

    BRONZE_DIR.mkdir(parents=True, exist_ok=True)

    if args.file:
        video_ids = [args.file]
    else:
        video_ids = sorted(
            p.stem for p in RAW_DIR.glob("*.mp4")
        )

    logger.info(f"共 {len(video_ids)} 支影片待處理")

    processed = 0
    skipped = 0
    failed = 0

    for video_id in video_ids:
        output_path = BRONZE_DIR / f"{video_id}.json"

        if output_path.exists() and not args.force:
            logger.debug(f"已存在，跳過 — {video_id}")
            skipped += 1
            continue

        try:
            result = process_video(video_id, generate_from_video)
            if result is None:
                failed += 1
                continue

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

            logger.info(f"輸出 → {output_path}")
            processed += 1
        except Exception:
            logger.exception(f"處理失敗 — {video_id}")
            failed += 1

        time.sleep(1)

    logger.info(f"完成：處理 {processed} / 跳過 {skipped} / 失敗 {failed}")


if __name__ == "__main__":
    main()
