"""Bronze → Silver pipeline for video transcripts.

Reads raw speech-recognition transcripts from storage/bronze/video/,
sends each to Gemini for correction / structuring, and writes
LangChain-Document-compatible JSON to storage/silver/video/.
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
BRONZE_DIR = ROOT_DIR / "storage" / "bronze" / "video"
SILVER_DIR = ROOT_DIR / "storage" / "silver" / "video"

SYSTEM_PROMPT = """\
你是一位資深電子鎖技術編輯。你會收到一段由語音辨識自動產生的影片逐字稿，內容關於電子鎖的安裝、維修、客服或產品知識。

請執行以下四項任務：

## 1. 語音辨識糾錯
常見錯誤對照表（請一併修正其他明顯的語音辨識錯誤）：
| 錯誤 | 正確 |
|------|------|
| 鞋舌 / 鞋匠 | 鎖舌 |
| 掌機賣 / 掌進麥 | 掌靜脈 |
| 收口 | 受口 |
| 連提鎖 | 連體鎖 |
| 密碼版 | 密碼面板 |
| 鎖匠 | 鎖箱 |
| 屍體 | 實體 |
| 卡順 | 卡榫 |
| 坑客人 | 坑（此處應刪除整句不相關口語） |

## 2. 去噪
- 移除口頭禪：然後、就是、對、好、那、嗯、齁、OK 等
- 移除重複句、假啟動、語句中斷後重說的片段
- 移除非內容段落（如「要錄喔？」「暫停」「等一下」等拍攝指令）
- 移除時間戳 [MM:SS]

## 3. 結構化重寫
根據內容性質選擇合適格式：
- **教學類** → 步驟列表（步驟 1、步驟 2…）
- **知識解說類** → 段落 + 小標題
- **故障排除類** → 問題描述 → 可能原因 → 解決方法

保留所有技術細節，不要遺漏任何重要資訊。使用正式書面中文。

## 4. Metadata 推斷
根據檔名和內容推斷以下欄位：
- brand：品牌名稱（Dormakaba / Chainlock / general）
- model：型號（如 AI99、A90，無法確定則填 general）
- category：分類，從以下選擇一個：setup / troubleshoot / knowledge / specification
"""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "page_content": {
            "type": "string",
            "description": "清洗、糾錯、結構化後的完整文字內容",
        },
        "metadata": {
            "type": "object",
            "properties": {
                "brand": {"type": "string"},
                "model": {"type": "string"},
                "category": {"type": "string"},
                "source_type": {"type": "string"},
                "source": {"type": "string"},
            },
            "required": ["brand", "model", "category", "source_type", "source"],
        },
    },
    "required": ["page_content", "metadata"],
}

log = logging.getLogger(__name__)


# ── helpers ──────────────────────────────────────────────────────────

def load_pipeline_config() -> dict:
    """Read [pipelines.video] from config.toml."""
    config_path = ROOT_DIR / "config.toml"
    with open(config_path, "rb") as f:
        config = tomllib.load(f)
    return config["pipelines"]["video"]


def process_one_file(llm_func: Callable, filepath: Path) -> dict:
    """Send a single transcript to the LLM and return parsed JSON."""
    transcript = filepath.read_text(encoding="utf-8")
    user_prompt = f"檔名：{filepath.name}\n\n逐字稿內容：\n{transcript}"

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
    result["metadata"]["source_type"] = "video"
    result["metadata"]["source"] = filepath.name

    return result


# ── main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Process bronze video transcripts → silver JSON"
    )
    parser.add_argument(
        "--file",
        type=str,
        help="Process a single file (filename only, relative to bronze/video/)",
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
        files = sorted(BRONZE_DIR.glob("*.txt"))
        if not files:
            sys.exit(f"No .txt files found in {BRONZE_DIR}")

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
