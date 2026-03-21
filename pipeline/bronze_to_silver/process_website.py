"""Bronze → Silver pipeline for website pages.

Reads Markdown files from storage/bronze/website/,
sends each to Gemini for semantic chunking and HyDE transformation,
and writes LangChain-Document-compatible JSON to storage/silver/website/.
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
BRONZE_DIR = ROOT_DIR / "storage" / "bronze" / "website"
SILVER_DIR = ROOT_DIR / "storage" / "silver" / "website"

SYSTEM_PROMPT = """\
你是一位資深電子鎖知識編輯。你會收到一頁由爬蟲擷取的網頁 Markdown，內容可能包含電子鎖品牌「鎖市」的營業資訊、產品介紹、安裝流程、常見問題等。

請執行以下任務：

## 1. 資訊萃取與過濾
- 提取有價值的資訊：營業資訊（地址、營業時間、電話、服務項目）、技術知識（電子鎖 QA、規格、安裝流程、故障排除）
- 主動忽略以下雜訊：導覽列殘留文字、圖片連結 / alt text、行銷口號、社群媒體連結、頁尾版權聲明、空白或無意義的格式殘留

## 2. 語意切分與重寫
將內容切分為多個獨立的知識點，每個知識點必須：
- 自帶完整主語（例如：「鎖市的營業時間為...」、「Chatlock AI-99 的安裝步驟...」，不能只寫「營業時間為...」）
- 使用客觀敘述，保留所有具體細節（數字、型號、步驟）
- 使用正式書面中文

## 3. 模擬疑問句（HyDE）
為每個知識點生成 2~3 句客戶可能會問的白話文問題。
例如：「鎖市在哪裡？」、「你們幾點開門？」、「AI-99 怎麼安裝？」

## 4. Metadata 推斷
根據檔名和內容推斷以下欄位：
- brand：品牌名稱（如 Chatlock、Dormakaba，無法確定則填 general）
- model：型號（如 AI-99、A90，無法確定則填 general）
- category：分類，從以下選擇一個：setup / troubleshoot / knowledge / specification

## 5. 空頁處理
若網頁內容經過濾後無任何有價值的資訊（例如純導覽列或純圖片頁），請回傳空的 chunks 陣列。
"""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "chunks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "客觀重寫後的知識摘要（必須自帶主語「鎖市」或產品名稱）",
                    },
                    "questions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "2~3句客戶可能會問的白話文問題",
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
                "required": ["summary", "questions", "metadata"],
            },
            "description": "獨立知識點陣列。若網頁無有價值資訊，則回傳空陣列。",
        }
    },
    "required": ["chunks"],
}

log = logging.getLogger(__name__)


# ── helpers ──────────────────────────────────────────────────────────

def load_pipeline_config() -> dict:
    """Read [pipelines.website] from config.toml."""
    config_path = ROOT_DIR / "config.toml"
    with open(config_path, "rb") as f:
        config = tomllib.load(f)
    return config["pipelines"]["website"]


def process_one_file(llm_func: Callable, filepath: Path) -> list[dict]:
    """Send a single webpage Markdown to the LLM and return a list of Document dicts."""
    content = filepath.read_text(encoding="utf-8")
    user_prompt = f"檔名：{filepath.name}\n\n網頁內容：\n{content}"

    result = llm_func(user_prompt, SYSTEM_PROMPT, RESPONSE_SCHEMA)

    if not isinstance(result, dict) or "chunks" not in result:
        raise ValueError("LLM response is not an object with 'chunks'")

    chunks = result["chunks"]
    if not isinstance(chunks, list):
        raise ValueError("'chunks' is not an array")

    if len(chunks) == 0:
        return []

    final_documents = []
    for i, item in enumerate(chunks):
        for field in ("summary", "questions", "metadata"):
            if field not in item:
                raise ValueError(f"Chunk {i}: missing '{field}'")

        # 組合 page_content（疑問句 + 摘要）
        questions_str = "\n".join(item["questions"])
        page_content = f"【常見問題】\n{questions_str}\n\n【知識內容】\n{item['summary']}"

        # 建立 metadata
        meta = item["metadata"]
        meta["source_type"] = "website"
        meta["source"] = filepath.name
        meta["chunk_index"] = i + 1
        meta["raw_text"] = item["summary"]

        final_documents.append({
            "page_content": page_content,
            "metadata": meta,
        })

    return final_documents


# ── main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Process bronze website pages → silver JSON"
    )
    parser.add_argument(
        "--file",
        type=str,
        help="Process a single file (filename only, relative to bronze/website/)",
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
        files = sorted(BRONZE_DIR.glob("*.md"))
        if not files:
            sys.exit(f"No .md files found in {BRONZE_DIR}")

    log.info("Found %d file(s) to process", len(files))

    success = 0
    skipped = 0
    failed = 0

    for filepath in files:
        out_path = SILVER_DIR / f"{filepath.stem}.json"

        # Idempotency check
        if out_path.exists() and not args.force:
            log.info("SKIP (already exists): %s", filepath.name)
            skipped += 1
            continue

        log.info("Processing: %s", filepath.name)
        try:
            result = process_one_file(llm_func, filepath)
            out_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log.info("OK → %s (%d chunks)", out_path.name, len(result))
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
