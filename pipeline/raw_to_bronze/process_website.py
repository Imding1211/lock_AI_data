"""Raw → Bronze 網頁爬蟲轉換器

讀取 storage/raw/website/website.txt 中的 URL 清單，
透過 Playwright 渲染動態網頁，以 BeautifulSoup 清理雜訊，
用 markdownify 轉為 Markdown，存入 storage/bronze/website/。
"""

import argparse
import logging
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup
from markdownify import markdownify
from playwright.sync_api import sync_playwright

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

RAW_DIR = ROOT_DIR / "storage" / "raw" / "website"
BRONZE_DIR = ROOT_DIR / "storage" / "bronze" / "website"
URL_FILE = RAW_DIR / "website.txt"

logger = logging.getLogger(__name__)

NOISE_TAGS = ["script", "style", "nav", "footer", "header", "aside"]
NOISE_ID_CLASS = ["SITE_FOOTER", "SITE_HEADER"]


def url_to_filename(url: str) -> str:
    """將 URL 轉換為安全的檔名（不含副檔名）。"""
    name = re.sub(r"https?://", "", url)
    name = name.rstrip("/")
    name = re.sub(r"[/.]", "_", name)
    return name


def load_urls(path: Path) -> list[str]:
    """逐行讀取 URL 檔案，跳過空行與 # 開頭的註解行。"""
    urls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def clean_html(html: str) -> str:
    """移除雜訊標籤與元素，回傳清理後的 body HTML。"""
    soup = BeautifulSoup(html, "html.parser")

    # 移除雜訊標籤
    for tag_name in NOISE_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # 移除含特定 id/class 的元素
    for noise in NOISE_ID_CLASS:
        for el in soup.find_all(id=re.compile(noise, re.IGNORECASE)):
            el.decompose()
        for el in soup.find_all(class_=re.compile(noise, re.IGNORECASE)):
            el.decompose()

    body = soup.find("body")
    return str(body) if body else str(soup)


def fetch_and_convert(url: str, browser) -> str:
    """用 Playwright 爬取頁面並轉為 Markdown。"""
    page = browser.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        # Wix 等 SPA 會持續發送背景請求，networkidle 永遠不會觸發。
        # 改用 domcontentloaded + 等待 body 可見，確保主要內容已渲染。
        page.wait_for_selector("body", state="visible", timeout=30000)
        # 額外等待讓動態內容載入
        page.wait_for_timeout(5000)
        html = page.content()
    finally:
        page.close()

    body_html = clean_html(html)
    md = markdownify(body_html, heading_style="ATX")

    # 清理多餘空行
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return md


def main() -> None:
    parser = argparse.ArgumentParser(description="Raw → Bronze 網頁爬蟲轉換器")
    parser.add_argument("--force", action="store_true", help="強制覆寫已存在的 Bronze 輸出")
    parser.add_argument("--verbose", action="store_true", help="啟用 DEBUG logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if not URL_FILE.exists():
        logger.error(f"URL 檔案不存在: {URL_FILE}")
        sys.exit(1)

    urls = load_urls(URL_FILE)
    logger.info(f"共 {len(urls)} 個 URL 待處理")

    if not urls:
        logger.info("無 URL 需處理，結束")
        return

    BRONZE_DIR.mkdir(parents=True, exist_ok=True)

    processed = 0
    skipped = 0
    failed = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for url in urls:
            filename = url_to_filename(url)
            output_path = BRONZE_DIR / f"{filename}.md"

            if output_path.exists() and not args.force:
                logger.debug(f"已存在，跳過 — {url}")
                skipped += 1
                continue

            try:
                logger.info(f"爬取中 — {url}")
                md_content = fetch_and_convert(url, browser)
                output_path.write_text(md_content, encoding="utf-8")
                logger.info(f"輸出 → {output_path}")
                processed += 1
            except Exception:
                logger.exception(f"處理失敗 — {url}")
                failed += 1

        browser.close()

    logger.info(f"完成：處理 {processed} / 跳過 {skipped} / 失敗 {failed}")


if __name__ == "__main__":
    main()
