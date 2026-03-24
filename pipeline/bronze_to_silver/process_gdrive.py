"""Bronze → Silver pipeline for Google Drive manual index.

Reads JSON files from storage/bronze/gdrive/,
sends each title to Gemini for metadata inference and HyDE generation,
and writes LangChain-Document-compatible JSON to storage/silver/gdrive/.
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

BRONZE_DIR = ROOT_DIR / "storage" / "bronze" / "gdrive"
SILVER_DIR = ROOT_DIR / "storage" / "silver" / "gdrive"

SYSTEM_PROMPT = """\
你是一位電子鎖產品手冊索引編輯。你會收到一份手冊的標題。

請執行以下任務：

1. 推斷品牌(brand)和型號(model)，無法確定則填 "general"
2. 撰寫一句摘要描述這份手冊的內容
3. 生成 2~3 句客戶可能用來尋找這份手冊的白話文問題
"""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "brand": {
            "type": "string",
            "description": "品牌名稱，無法確定則填 general",
        },
        "model": {
            "type": "string",
            "description": "產品型號，無法確定則填 general",
        },
        "summary": {
            "type": "string",
            "description": "一句話摘要描述手冊內容",
        },
        "questions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "2~3 句客戶可能會問的白話文問題",
        },
    },
    "required": ["brand", "model", "summary", "questions"],
}

log = logging.getLogger(__name__)


# ── helpers ──────────────────────────────────────────────────────────

def load_pipeline_config() -> dict:
    """Read [pipelines.gdrive_silver] from config.toml."""
    config_path = ROOT_DIR / "config.toml"
    with open(config_path, "rb") as f:
        config = tomllib.load(f)
    return config["pipelines"]["gdrive_silver"]


def process_one_file(llm_func: Callable, bronze_data: dict) -> list[dict]:
    """Send a manual title to the LLM and return a list of Document dicts."""
    title = bronze_data["title"]
    file_id = bronze_data["file_id"]
    url = bronze_data["url"]

    user_prompt = f"手冊標題：{title}"

    result = llm_func(user_prompt, SYSTEM_PROMPT, RESPONSE_SCHEMA)

    if not isinstance(result, dict):
        raise ValueError("LLM response is not an object")

    for field in ("brand", "model", "summary", "questions"):
        if field not in result:
            raise ValueError(f"Missing '{field}' in LLM response")

    # 組合 page_content（疑問句 + 摘要）
    questions_str = "\n".join(result["questions"])
    page_content = f"【常見問題】\n{questions_str}\n\n【知識內容】\n{result['summary']}"

    # 建立 metadata
    metadata = {
        "brand": result["brand"],
        "model": result["model"],
        "category": "manual",
        "source_type": "gdrive",
        "source": file_id,
        "chunk_index": 1,
        "raw_text": f"{result['summary']}\n連結：{url}",
        "url": url,
    }

    return [{"page_content": page_content, "metadata": metadata}]


# ── main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Process bronze gdrive JSON → silver JSON"
    )
    parser.add_argument(
        "--file",
        type=str,
        help="Process a single file (filename only, relative to bronze/gdrive/)",
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
            log.info("OK → %s (%d docs)", out_path.name, len(result))
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
