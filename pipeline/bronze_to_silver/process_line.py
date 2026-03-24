"""Bronze → Silver pipeline for LINE chat sessions.

Reads LINE chat CSV files from storage/bronze/line_chat/,
sends each session to Gemini for relevance filtering and knowledge rewriting,
and writes LangChain-Document-compatible JSON to storage/silver/line_chat/.
"""

import argparse
import json
import logging
import sys
import time
import tomllib
from pathlib import Path
from typing import Callable

import pandas as pd

# ── paths ────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from llms import get_llm

BRONZE_DIR = ROOT_DIR / "storage" / "bronze" / "line_chat"
SILVER_DIR = ROOT_DIR / "storage" / "silver" / "line_chat"

SYSTEM_PROMPT = """\
你是一位資深電子鎖技術編輯。你會收到一段 LINE 客服對話紀錄，請執行以下任務：

## 1. 相關性過濾
判斷這段對話是否與電子鎖的安裝、維修、故障排除、產品知識相關。
以下情況視為**不相關**，請回傳 `is_relevant: false`，並將 `chunks` 設為空陣列：
- 刻印章、買遙控器、配鑰匙等非電子鎖業務
- 純推銷、廣告
- 純寒暄、閒聊、無實質技術內容
- 內容過短或無法理解

## 2. 語意切分與重寫
若對話相關，將對話內容切分為多個獨立知識點，每個知識點必須：
- 自帶完整主語（例如：「Dormakaba 電子鎖電池耗盡時，應...」，不能只寫「電池耗盡時應...」）
- 將客服經驗轉化為客觀敘述性技術知識
- **禁止** Q&A 格式、禁止保留對話形式
- 保留所有技術細節（型號、步驟、規格、注意事項）
- 使用正式書面中文

範例：
- 對話「客人說鎖沒電…店員教他買 9V 電池」→ 知識點「當 Dormakaba 電子鎖電池耗盡時，可使用 9V 方型電池接觸外部面板的緊急供電接點進行臨時供電…」

## 3. 模擬疑問句
為每個知識點生成 2~3 句使用者可能會問的白話文問題。
例如：「電子鎖沒電了怎麼辦？」、「9V 電池要怎麼緊急供電？」

## 4. Metadata 推斷
根據對話內容推斷以下欄位：
- brand：品牌名稱（Dormakaba / Chainlock / general，無法確定則填 general）
- model：型號（如 AI99、A90，無法確定則填 general）
- category：分類，從以下選擇一個：setup / troubleshoot / knowledge / specification
"""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "is_relevant": {
            "type": "boolean",
            "description": "此對話是否與電子鎖技術知識相關",
        },
        "chunks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "結構化重寫後的獨立知識摘要（必須包含品牌與型號等主語）",
                    },
                    "questions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "2~3 句使用者可能會問的白話文疑問句",
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
            "description": "獨立知識點陣列（若不相關則為空陣列）",
        },
    },
    "required": ["is_relevant", "chunks"],
}

log = logging.getLogger(__name__)


# ── helpers ──────────────────────────────────────────────────────────

def load_pipeline_config() -> dict:
    """Read [pipelines.line_chat] from config.toml."""
    config_path = ROOT_DIR / "config.toml"
    with open(config_path, "rb") as f:
        config = tomllib.load(f)
    return config["pipelines"]["line_chat"]


def process_one_session(
    llm_func: Callable, session_id: str, transcript: str
) -> list[dict] | None:
    """Send a single chat session to the LLM and return a list of Document dicts, or None if irrelevant."""
    user_prompt = f"Session ID：{session_id}\n\n對話紀錄：\n{transcript}"

    result = llm_func(user_prompt, SYSTEM_PROMPT, RESPONSE_SCHEMA)

    # Validate required fields
    if "is_relevant" not in result:
        raise ValueError("回應缺少 is_relevant 欄位")

    if not result["is_relevant"] or not result.get("chunks"):
        return None

    chunks = result["chunks"]
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
        meta["source_type"] = "line_chat"
        meta["source"] = session_id
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
        description="Process bronze LINE chat sessions → silver JSON"
    )
    parser.add_argument(
        "--file",
        type=str,
        help="Process a single CSV (filename only, relative to bronze/line_chat/)",
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
            sys.exit(f"找不到檔案：{files[0]}")
    else:
        files = sorted(BRONZE_DIR.glob("*.csv"))
        if not files:
            sys.exit(f"在 {BRONZE_DIR} 中找不到 .csv 檔案")

    log.info("找到 %d 個 CSV 檔案待處理", len(files))

    success = 0
    skipped = 0
    irrelevant = 0
    failed = 0

    for csv_path in files:
        log.info("讀取 CSV：%s", csv_path.name)
        try:
            df = pd.read_csv(csv_path)
        except Exception:
            log.exception("讀取 CSV 失敗：%s", csv_path.name)
            failed += 1
            continue

        for _, row in df.iterrows():
            session_id = str(row["session_id"])
            transcript = str(row["transcript"])
            out_path = SILVER_DIR / f"{session_id}.json"

            # Idempotency check
            if out_path.exists() and not args.force:
                log.info("跳過（已存在）：%s", session_id)
                skipped += 1
                continue

            log.info("處理中：%s", session_id)
            try:
                result = process_one_session(llm_func, session_id, transcript)

                if result is None:
                    out_path.write_text("[]", encoding="utf-8")
                    log.info("[不相關 → 寫入空 JSON]：%s", session_id)
                    irrelevant += 1
                    continue

                out_path.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                log.info("完成 → %s", out_path.name)
                success += 1

            except Exception:
                log.exception("處理失敗：%s", session_id)
                failed += 1

            # Rate-limit: 1 second between API calls
            time.sleep(1)

    log.info(
        "完成。成功=%d  跳過=%d  不相關=%d  失敗=%d",
        success, skipped, irrelevant, failed,
    )


if __name__ == "__main__":
    main()
