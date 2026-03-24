"""Raw → Bronze Google Drive 手冊索引擷取器

讀取 storage/raw/gdrive/links.txt 中的 Google Drive URL（支援資料夾與檔案），
透過 Google Drive API v3 取得檔案名稱，存入 storage/bronze/gdrive/{file_id}.json。
"""

import argparse
import json
import logging
import re
import sys
import tomllib
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

ROOT_DIR = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT_DIR / "storage" / "raw" / "gdrive"
BRONZE_DIR = ROOT_DIR / "storage" / "bronze" / "gdrive"
URL_FILE = RAW_DIR / "links.txt"

logger = logging.getLogger(__name__)

FOLDER_ID_PATTERN = re.compile(r"/folders/([a-zA-Z0-9_-]+)")
FILE_ID_PATTERN = re.compile(r"/(?:file/)?d/([a-zA-Z0-9_-]+)")


def load_urls(path: Path) -> list[str]:
    """逐行讀取 URL 檔案，跳過空行與 # 開頭的註解行。"""
    urls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def load_config() -> dict:
    """讀取 config.toml → pipelines.gdrive 設定。"""
    config_path = ROOT_DIR / "config.toml"
    with open(config_path, "rb") as f:
        return tomllib.load(f)["pipelines"]["gdrive"]


def build_drive_service(config: dict):
    """使用 Service Account 建立 Google Drive API v3 client。"""
    creds = service_account.Credentials.from_service_account_file(
        config["service_account_file"], scopes=config["scopes"]
    )
    return build("drive", "v3", credentials=creds)


def resolve_files(service, url: str) -> list[dict]:
    """解析 URL，回傳 [{"file_id": ..., "title": ...}, ...]。

    支援資料夾 URL（列出所有檔案）與單一檔案 URL。
    """
    folder_match = FOLDER_ID_PATTERN.search(url)
    if folder_match:
        folder_id = folder_match.group(1)
        logger.debug("偵測為資料夾 — %s", folder_id)
        files = []
        page_token = None
        while True:
            resp = (
                service.files()
                .list(
                    q=f"'{folder_id}' in parents and trashed = false",
                    fields="nextPageToken, files(id, name)",
                    pageSize=1000,
                    pageToken=page_token,
                )
                .execute()
            )
            for f in resp.get("files", []):
                files.append({"file_id": f["id"], "title": f["name"]})
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return files

    file_match = FILE_ID_PATTERN.search(url)
    if file_match:
        file_id = file_match.group(1)
        logger.debug("偵測為檔案 — %s", file_id)
        f = service.files().get(fileId=file_id, fields="id, name").execute()
        return [{"file_id": f["id"], "title": f["name"]}]

    return []


def main() -> None:
    parser = argparse.ArgumentParser(description="Raw → Bronze Google Drive 手冊索引擷取器")
    parser.add_argument("--force", action="store_true", help="強制覆寫已存在的 Bronze 輸出")
    parser.add_argument("--verbose", action="store_true", help="啟用 DEBUG logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if not URL_FILE.exists():
        logger.error("URL 檔案不存在: %s", URL_FILE)
        sys.exit(1)

    urls = load_urls(URL_FILE)
    logger.info("共 %d 個 URL 待處理", len(urls))

    if not urls:
        logger.info("無 URL 需處理，結束")
        return

    BRONZE_DIR.mkdir(parents=True, exist_ok=True)

    config = load_config()
    service = build_drive_service(config)

    success = 0
    skipped = 0
    failed = 0

    for url in urls:
        try:
            files = resolve_files(service, url)
            if not files:
                logger.warning("無法解析 URL — %s", url)
                failed += 1
                continue

            for file_info in files:
                file_id = file_info["file_id"]
                title = file_info["title"]
                output_path = BRONZE_DIR / f"{file_id}.json"

                if output_path.exists() and not args.force:
                    logger.debug("已存在，跳過 — %s", file_id)
                    skipped += 1
                    continue

                canonical_url = f"https://drive.google.com/file/d/{file_id}/view"
                data = {"file_id": file_id, "title": title, "url": canonical_url}
                output_path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                logger.info("輸出 → %s (title: %s)", output_path.name, title)
                success += 1
        except Exception:
            logger.exception("處理失敗 — %s", url)
            failed += 1

    logger.info("完成：處理 %d / 跳過 %d / 失敗 %d", success, skipped, failed)


if __name__ == "__main__":
    main()
