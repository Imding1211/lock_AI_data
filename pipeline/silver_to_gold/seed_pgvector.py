"""
Silver → Gold：將 storage/silver/ 下的標準化 JSON 直接寫入 pgvector。

用法：
    # 單一資料源
    python pipeline/silver_to_gold/seed_pgvector.py --database video --reset --verbose

    # 單檔驗證
    python pipeline/silver_to_gold/seed_pgvector.py --database video --file "客服問診 SOP 核心.json" --verbose

    # 全部資料源一次寫入
    python pipeline/silver_to_gold/seed_pgvector.py --all --reset --verbose
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import tomllib
from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_postgres import PGVector

# 讓 import embeddings 能正確解析
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from embeddings import get_embedding

SILVER_DIR = Path(__file__).resolve().parents[2] / "storage" / "silver"
CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.toml"

logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="Silver → Gold: seed pgvector")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--database", type=str, default=None,
                       help="config.toml 中的 database key（如 video、line_chat）")
    group.add_argument("--all", action="store_true",
                       help="處理 config.toml 中所有 databases")
    parser.add_argument("--file", type=str, default=None,
                        help="處理單一 JSON 檔（相對於該 database 的 source_dir）")
    parser.add_argument("--reset", action="store_true",
                        help="清空 collection 後重建")
    parser.add_argument("--verbose", action="store_true",
                        help="啟用 debug logging")
    return parser.parse_args()


def load_silver_files(source_dir: Path, file_arg: str | None) -> list[Path]:
    if file_arg:
        target = source_dir / file_arg
        if not target.exists():
            logger.error(f"找不到檔案: {target}")
            sys.exit(1)
        return [target]
    if not source_dir.exists():
        logger.warning(f"目錄不存在: {source_dir}")
        return []
    files = sorted(source_dir.rglob("*.json"))
    if not files:
        logger.warning(f"{source_dir} 下找不到任何 .json 檔案")
    return files


def json_to_documents(path: Path) -> list[Document]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return [
            Document(page_content=item["page_content"], metadata=item["metadata"])
            for item in data
        ]
    return [Document(page_content=data["page_content"], metadata=data["metadata"])]


def seed_one_database(db_key: str, db_config: dict, connection_uri: str,
                      file_arg: str | None, reset: bool):
    collection_name = db_config["collection_name"]
    source_dir = SILVER_DIR / db_config["source_dir"]

    logger.info(f"=== [{db_key}] collection: {collection_name}, 來源: {source_dir} ===")

    # 初始化 embedding
    embed_fn = get_embedding(db_config)

    # 載入 Silver JSON
    files = load_silver_files(source_dir, file_arg)
    logger.info(f"找到 {len(files)} 份 Silver JSON")

    documents = []
    for path in files:
        try:
            docs = json_to_documents(path)
            documents.extend(docs)
            logger.debug(f"  載入: {path.name}")
        except Exception as e:
            logger.error(f"  載入失敗: {path} — {e}")

    if not documents:
        logger.warning(f"[{db_key}] 沒有成功載入任何文件，跳過")
        return

    # 寫入 pgvector
    logger.info(f"正在寫入 pgvector collection: {collection_name} (reset={reset})")
    vector_store = PGVector(
        embeddings=embed_fn,
        collection_name=collection_name,
        connection=connection_uri,
        pre_delete_collection=reset,
    )
    vector_store.add_documents(documents)
    logger.info(f"[完成] 成功寫入 {len(documents)} 份 Documents 至 {collection_name}")


def main():
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # 讀取 config
    with open(CONFIG_PATH, "rb") as f:
        config = tomllib.load(f)

    all_databases = config.get("databases", {})

    # 載入環境變數
    load_dotenv()

    # 決定要處理哪些 database
    if args.all:
        if args.file:
            logger.error("--all 不能與 --file 同時使用")
            sys.exit(1)
        db_keys = list(all_databases.keys())
    else:
        if args.database not in all_databases:
            logger.error(f"config.toml 中找不到 [databases.{args.database}]")
            sys.exit(1)
        db_keys = [args.database]

    for db_key in db_keys:
        db_config = all_databases[db_key]
        connection_uri = os.getenv(db_config["connection_uri_env"])
        if not connection_uri:
            logger.error(f"缺少連線字串！請在 .env 中設定 {db_config['connection_uri_env']}")
            sys.exit(1)
        seed_one_database(db_key, db_config, connection_uri, args.file, args.reset)


if __name__ == "__main__":
    main()
