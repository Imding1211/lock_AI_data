"""Bronze → Silver pipeline for YouTube transcripts.

Reads vision-generated transcripts from storage/bronze/youtube/,
sends each to Gemini for rewriting / structuring, and writes
LangChain-Document-compatible JSON to storage/silver/youtube/.
"""

import argparse
import json
import logging
import sys
import time
import tomllib
from pathlib import Path
from typing import Callable

# ── paths ────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from llms import get_llm

BRONZE_DIR = ROOT_DIR / "storage" / "bronze" / "youtube"
SILVER_DIR = ROOT_DIR / "storage" / "silver" / "youtube"

SYSTEM_PROMPT = """\
你是一位資深電子鎖技術編輯。請閱讀以下 YouTube 影片的逐字教學稿（包含 [MM:SS] 時間戳記）。
請保留所有時間戳記（如 [00:00]、[01:15]）原封不動，
將文字整理得更為通順、專業，使其成為一篇完美的「操作設定指南」。
同時請根據內容推斷 brand, model, category。
"""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "page_content": {
            "type": "string",
            "description": "整理後的知識文章，保留 [MM:SS] 時間戳記但不含任何連結",
        },
        "metadata": {
            "type": "object",
            "properties": {
                "brand": {"type": "string"},
                "model": {"type": "string"},
                "category": {"type": "string"},
            },
            "required": ["brand", "model", "category"],
        },
    },
    "required": ["page_content", "metadata"],
}

log = logging.getLogger(__name__)


# ── helpers ──────────────────────────────────────────────────────────

def load_pipeline_config() -> dict:
    """Read [pipelines.youtube_silver] from config.toml."""
    config_path = ROOT_DIR / "config.toml"
    with open(config_path, "rb") as f:
        config = tomllib.load(f)
    return config["pipelines"]["youtube_silver"]


def process_one_file(llm_func: Callable, bronze_data: dict) -> dict:
    """Process a single bronze YouTube JSON through LLM rewriting."""
    video_id = bronze_data["video_id"]
    url = bronze_data["url"]
    title = bronze_data["title"]
    transcript = bronze_data["transcript"]

    user_prompt = f"影片標題：{title}\n\n逐字稿內容：\n{transcript}"

    result = llm_func(user_prompt, SYSTEM_PROMPT, RESPONSE_SCHEMA)

    # Validate required fields
    if "page_content" not in result or not result["page_content"]:
        raise ValueError("Missing or empty page_content in response")
    if "metadata" not in result:
        raise ValueError("Missing metadata in response")
    for field in ("brand", "model", "category"):
        if field not in result["metadata"]:
            raise ValueError(f"Missing metadata.{field} in response")

    # Force-overwrite source fields to ensure correctness
    result["metadata"]["source_type"] = "youtube"
    result["metadata"]["source"] = video_id
    result["metadata"]["url"] = url

    return result


# ── main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Process bronze YouTube transcripts → silver JSON"
    )
    parser.add_argument(
        "--file",
        type=str,
        help="Process a single file (filename only, relative to bronze/youtube/)",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing silver files")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    SILVER_DIR.mkdir(parents=True, exist_ok=True)

    pipeline_cfg = load_pipeline_config()
    llm_func = get_llm(
        provider=pipeline_cfg["llm_provider"],
        model=pipeline_cfg["llm_model"],
        temperature=pipeline_cfg.get("temperature", 0.3),
    )

    # Determine which files to process
    if args.file:
        files = [BRONZE_DIR / args.file]
        if not files[0].exists():
            sys.exit(f"File not found: {files[0]}")
    else:
        files = sorted(BRONZE_DIR.glob("*.json"))
        if not files:
            sys.exit(f"No .json files found in {BRONZE_DIR}")

    log.info("Found %d file(s) to process", len(files))

    success = 0
    skipped = 0
    failed = 0

    for filepath in files:
        out_path = SILVER_DIR / filepath.name

        # Idempotency check
        if out_path.exists() and not args.force:
            log.info("SKIP (already exists): %s", filepath.name)
            skipped += 1
            continue

        log.info("Processing: %s", filepath.name)
        try:
            bronze_data = json.loads(filepath.read_text(encoding="utf-8"))
            result = process_one_file(llm_func, bronze_data)
            out_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log.info("OK → %s", out_path.name)
            success += 1
        except Exception:
            log.exception("FAILED: %s", filepath.name)
            failed += 1

        # Rate-limit: 1 second between API calls
        if filepath != files[-1]:
            time.sleep(1)

    log.info("Done. success=%d  skipped=%d  failed=%d", success, skipped, failed)


if __name__ == "__main__":
    main()
