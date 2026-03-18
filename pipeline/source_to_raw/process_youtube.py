"""Source → Raw YouTube 下載器

讀取 config.toml 的 [pipelines.youtube_fetch].playlists，
用 yt-dlp 下載影片至 storage/raw/youtube/。
"""

import argparse
import json
import logging
import tomllib
from pathlib import Path

import yt_dlp

BASE_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = BASE_DIR / "config.toml"
RAW_DIR = BASE_DIR / "storage" / "raw" / "youtube"

logger = logging.getLogger(__name__)


def load_playlists() -> list[str]:
    """從 config.toml 讀取 playlist URLs。"""
    with open(CONFIG_PATH, "rb") as f:
        config = tomllib.load(f)
    return config["pipelines"]["youtube_fetch"]["playlists"]


def fetch_playlist_entries(playlist_url: str, verbose: bool = False) -> list[dict]:
    """擷取播放清單中所有影片的基本資訊。"""
    ydl_opts = {
        "quiet": not verbose,
        "extract_flat": False,
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(playlist_url, download=False)

    entries = []
    for entry in info.get("entries", []):
        if entry is None:
            continue
        entries.append({
            "video_id": entry["id"],
            "url": f"https://www.youtube.com/watch?v={entry['id']}",
            "title": entry.get("title", ""),
        })
    return entries


def download_video(entry: dict, verbose: bool = False) -> bool:
    """下載單一影片，回傳是否成功。"""
    video_id = entry["video_id"]
    mp4_path = RAW_DIR / f"{video_id}.mp4"
    meta_path = RAW_DIR / f"{video_id}.json"

    if mp4_path.exists():
        logger.debug(f"已存在，跳過 — {video_id}")
        return False

    ydl_opts = {
        "format": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]",
        "outtmpl": str(RAW_DIR / "%(id)s.mp4"),
        "quiet": not verbose,
    }

    logger.info(f"下載中 — {video_id} ({entry['title']})")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([entry["url"]])

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(entry, f, ensure_ascii=False, indent=2)

    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Source → Raw YouTube 下載器")
    parser.add_argument("--file", help="單檔模式：直接下載指定 video_id")
    parser.add_argument("--verbose", action="store_true", help="啟用 DEBUG logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    RAW_DIR.mkdir(parents=True, exist_ok=True)

    if args.file:
        entries = [{
            "video_id": args.file,
            "url": f"https://www.youtube.com/watch?v={args.file}",
            "title": "",
        }]
    else:
        playlists = load_playlists()
        logger.info(f"共 {len(playlists)} 個播放清單")
        entries = []
        for url in playlists:
            logger.info(f"擷取播放清單 — {url}")
            entries.extend(fetch_playlist_entries(url, verbose=args.verbose))
        logger.info(f"共 {len(entries)} 支影片待處理")

    downloaded = 0
    skipped = 0
    failed = 0

    for entry in entries:
        try:
            if download_video(entry, verbose=args.verbose):
                downloaded += 1
            else:
                skipped += 1
        except Exception:
            logger.exception(f"下載失敗 — {entry['video_id']}")
            failed += 1

    logger.info(f"完成：下載 {downloaded} / 跳過 {skipped} / 失敗 {failed}")


if __name__ == "__main__":
    main()
