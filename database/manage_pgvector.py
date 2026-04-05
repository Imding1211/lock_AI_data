"""pgvector CRUD 管理工具

提供 list / search / get / update / delete / drop 六個子命令，
用於管理 pgvector 知識庫的文件與 collection。

用法：
    python database/manage_pgvector.py list
    python database/manage_pgvector.py search --database video --query "鎖舌卡住" --top-k 3
    python database/manage_pgvector.py get --database video --source "客服問診 SOP 核心.txt"
    python database/manage_pgvector.py update --database video --source "客服問診 SOP 核心.txt" --set-category troubleshoot
    python database/manage_pgvector.py delete --database video --source "客服問診 SOP 核心.txt"
    python database/manage_pgvector.py drop --database video --confirm
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import psycopg
import tomllib
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from embeddings import get_embedding

ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT_DIR / "config.toml"

logger = logging.getLogger(__name__)


# ── 共用基礎設施 ─────────────────────────────────────────────────────


def load_config() -> dict:
    """讀取 config.toml。"""
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def resolve_db(config: dict, db_key: str) -> tuple[dict, str]:
    """驗證 database key 存在並取得連線字串，失敗則 sys.exit(1)。"""
    all_databases = config.get("databases", {})
    if db_key not in all_databases:
        logger.error("config.toml 中找不到 [databases.%s]", db_key)
        sys.exit(1)
    db_config = all_databases[db_key]
    uri = os.getenv(db_config["connection_uri_env"])
    if not uri:
        logger.error("缺少連線字串！請在 .env 中設定 %s", db_config["connection_uri_env"])
        sys.exit(1)
    return db_config, uri


def get_sync_connection(connection_uri: str) -> psycopg.Connection:
    """從 langchain 格式的 URI 建立 psycopg 同步連線。"""
    sync_uri = connection_uri.replace("postgresql+psycopg://", "postgresql://")
    return psycopg.connect(sync_uri)


def get_vector_store(db_config: dict, connection_uri: str):
    """建立 PGVector 實例（僅 search 子命令使用）。"""
    from langchain_postgres import PGVector

    embed_fn = get_embedding(db_config)
    return PGVector(
        embeddings=embed_fn,
        collection_name=db_config["collection_name"],
        connection=connection_uri,
    )


def get_collection_uri(config: dict, db_key: str | None) -> str | None:
    """取得任意一個 database 的連線字串（用於 list 全域查詢）。"""
    all_databases = config.get("databases", {})
    target = db_key if db_key else next(iter(all_databases), None)
    if not target or target not in all_databases:
        return None
    return os.getenv(all_databases[target]["connection_uri_env"])


# ── 子命令實作 ───────────────────────────────────────────────────────


def cmd_list(args, config: dict) -> None:
    """列出 collection 統計資訊。"""
    uri = get_collection_uri(config, args.database)
    if not uri:
        logger.error("無法取得連線字串")
        sys.exit(1)

    conn = get_sync_connection(uri)
    try:
        with conn.cursor() as cur:
            if args.database:
                db_config = config["databases"][args.database]
                collection_name = db_config["collection_name"]

                cur.execute(
                    """SELECT COUNT(e.id) AS doc_count
                       FROM langchain_pg_collection c
                       JOIN langchain_pg_embedding e ON e.collection_id = c.uuid
                       WHERE c.name = %s""",
                    (collection_name,),
                )
                row = cur.fetchone()
                count = row[0] if row else 0
                print(f"\n  {collection_name}: {count} 份文件")

                if args.verbose and count > 0:
                    cur.execute(
                        """SELECT e.cmetadata->>'source' AS source,
                                  e.cmetadata->>'brand' AS brand,
                                  e.cmetadata->>'category' AS category,
                                  COUNT(*) AS cnt
                           FROM langchain_pg_embedding e
                           JOIN langchain_pg_collection c ON e.collection_id = c.uuid
                           WHERE c.name = %s
                           GROUP BY source, brand, category
                           ORDER BY source, brand, category""",
                        (collection_name,),
                    )
                    rows = cur.fetchall()
                    print(f"\n  {'source':<40} {'brand':<15} {'category':<15} {'count':>5}")
                    print(f"  {'-'*40} {'-'*15} {'-'*15} {'-'*5}")
                    for source, brand, category, cnt in rows:
                        print(f"  {(source or '-'):<40} {(brand or '-'):<15} {(category or '-'):<15} {cnt:>5}")
            else:
                cur.execute(
                    """SELECT c.name, COUNT(e.id) AS doc_count
                       FROM langchain_pg_collection c
                       LEFT JOIN langchain_pg_embedding e ON e.collection_id = c.uuid
                       GROUP BY c.name
                       ORDER BY c.name"""
                )
                rows = cur.fetchall()
                if not rows:
                    print("\n  尚無任何 collection")
                    return
                print(f"\n  {'collection':<25} {'count':>8}")
                print(f"  {'-'*25} {'-'*8}")
                total = 0
                for name, count in rows:
                    print(f"  {name:<25} {count:>8}")
                    total += count
                print(f"  {'-'*25} {'-'*8}")
                print(f"  {'合計':<25} {total:>8}")
    finally:
        conn.close()

    print()


def cmd_search(args, config: dict) -> None:
    """語意相似度搜尋。"""
    db_config, uri = resolve_db(config, args.database)
    vector_store = get_vector_store(db_config, uri)

    filter_dict = {}
    if args.filter_brand:
        filter_dict["brand"] = args.filter_brand
    if args.filter_category:
        filter_dict["category"] = args.filter_category

    results = vector_store.similarity_search_with_score(
        args.query,
        k=args.top_k,
        filter=filter_dict if filter_dict else None,
    )

    if not results:
        print("\n  找不到符合條件的文件\n")
        return

    print(f"\n  搜尋：「{args.query}」（top-{args.top_k}）\n")
    for i, (doc, score) in enumerate(results, 1):
        content_preview = doc.page_content[:200].replace("\n", " ")
        print(f"  ── 結果 {i} (distance: {score:.4f}) ──")
        print(f"  source:   {doc.metadata.get('source', '-')}")
        print(f"  brand:    {doc.metadata.get('brand', '-')}")
        print(f"  model:    {doc.metadata.get('model', '-')}")
        print(f"  category: {doc.metadata.get('category', '-')}")
        print(f"  chunk:    {doc.metadata.get('chunk_index', '-')}")
        print(f"  內容：{content_preview}...")
        if args.verbose and doc.metadata.get("raw_text"):
            raw_preview = doc.metadata["raw_text"][:300].replace("\n", " ")
            print(f"  raw_text：{raw_preview}...")
        print()


def cmd_get(args, config: dict) -> None:
    """依 source 查詢文件。"""
    db_config, uri = resolve_db(config, args.database)
    collection_name = db_config["collection_name"]

    conn = get_sync_connection(uri)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT e.id, e.document, e.cmetadata
                   FROM langchain_pg_embedding e
                   JOIN langchain_pg_collection c ON e.collection_id = c.uuid
                   WHERE c.name = %s AND e.cmetadata->>'source' = %s
                   ORDER BY (e.cmetadata->>'chunk_index')::int""",
                (collection_name, args.source),
            )
            rows = cur.fetchall()

        if not rows:
            print(f"\n  在 {collection_name} 中找不到 source = \"{args.source}\" 的文件\n")
            return

        print(f"\n  {collection_name} / source = \"{args.source}\" — 共 {len(rows)} 份文件\n")
        for doc_id, document, cmetadata in rows:
            meta = cmetadata if isinstance(cmetadata, dict) else json.loads(cmetadata)
            print(f"  ── chunk {meta.get('chunk_index', '?')} (id: {doc_id}) ──")
            print(f"  brand: {meta.get('brand', '-')}  model: {meta.get('model', '-')}  category: {meta.get('category', '-')}")
            if args.verbose:
                content_preview = (document or "")[:500]
                print(f"  page_content:\n    {content_preview}")
                if meta.get("raw_text"):
                    raw_preview = meta["raw_text"][:300]
                    print(f"  raw_text:\n    {raw_preview}")
            else:
                content_preview = (document or "")[:200].replace("\n", " ")
                print(f"  內容：{content_preview}...")
            print()
    finally:
        conn.close()


def cmd_update(args, config: dict) -> None:
    """更新文件 metadata。"""
    updates = {}
    if args.set_brand is not None:
        updates["brand"] = args.set_brand
    if args.set_model is not None:
        updates["model"] = args.set_model
    if args.set_category is not None:
        updates["category"] = args.set_category

    if not updates:
        logger.error("請至少提供一個 --set-* 參數（--set-brand / --set-model / --set-category）")
        sys.exit(1)

    db_config, uri = resolve_db(config, args.database)
    collection_name = db_config["collection_name"]

    conn = get_sync_connection(uri)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE langchain_pg_embedding
                   SET cmetadata = cmetadata || %s::jsonb
                   WHERE collection_id = (
                       SELECT uuid FROM langchain_pg_collection WHERE name = %s
                   )
                   AND cmetadata->>'source' = %s""",
                (json.dumps(updates), collection_name, args.source),
            )
            affected = cur.rowcount
        conn.commit()

        if affected == 0:
            print(f"\n  找不到符合條件的文件（collection: {collection_name}, source: {args.source}）\n")
        else:
            print(f"\n  已更新 {affected} 份文件的 metadata：{updates}\n")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cmd_delete(args, config: dict) -> None:
    """刪除文件。"""
    if not args.source and not args.id:
        logger.error("請提供 --source 或 --id")
        sys.exit(1)

    db_config, uri = resolve_db(config, args.database)
    collection_name = db_config["collection_name"]

    conn = get_sync_connection(uri)
    try:
        with conn.cursor() as cur:
            # 先計算影響筆數
            if args.source:
                cur.execute(
                    """SELECT COUNT(*) FROM langchain_pg_embedding e
                       JOIN langchain_pg_collection c ON e.collection_id = c.uuid
                       WHERE c.name = %s AND e.cmetadata->>'source' = %s""",
                    (collection_name, args.source),
                )
                count = cur.fetchone()[0]
                target_desc = f"source = \"{args.source}\""
            else:
                cur.execute(
                    """SELECT COUNT(*) FROM langchain_pg_embedding e
                       JOIN langchain_pg_collection c ON e.collection_id = c.uuid
                       WHERE c.name = %s AND e.id::text = %s""",
                    (collection_name, args.id),
                )
                count = cur.fetchone()[0]
                target_desc = f"id = {args.id}"

            if count == 0:
                print(f"\n  找不到符合條件的文件（{target_desc}）\n")
                return

            # 確認刪除
            if not args.confirm:
                answer = input(f"  即將從 {collection_name} 刪除 {count} 份文件（{target_desc}），確認？(y/N): ")
                if answer.strip().lower() != "y":
                    print("  已取消")
                    return

            # 執行刪除
            if args.source:
                cur.execute(
                    """DELETE FROM langchain_pg_embedding
                       WHERE collection_id = (
                           SELECT uuid FROM langchain_pg_collection WHERE name = %s
                       )
                       AND cmetadata->>'source' = %s""",
                    (collection_name, args.source),
                )
            else:
                cur.execute(
                    """DELETE FROM langchain_pg_embedding
                       WHERE collection_id = (
                           SELECT uuid FROM langchain_pg_collection WHERE name = %s
                       )
                       AND id::text = %s""",
                    (collection_name, args.id),
                )
            deleted = cur.rowcount
        conn.commit()
        print(f"\n  已從 {collection_name} 刪除 {deleted} 份文件\n")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cmd_drop(args, config: dict) -> None:
    """刪除整個 collection。"""
    db_config, uri = resolve_db(config, args.database)
    collection_name = db_config["collection_name"]

    conn = get_sync_connection(uri)
    try:
        with conn.cursor() as cur:
            # 查看目前文件數
            cur.execute(
                """SELECT COUNT(e.id) FROM langchain_pg_embedding e
                   JOIN langchain_pg_collection c ON e.collection_id = c.uuid
                   WHERE c.name = %s""",
                (collection_name,),
            )
            count = cur.fetchone()[0]

            # 確認刪除
            if not args.confirm:
                answer = input(
                    f"  即將刪除整個 collection「{collection_name}」（{count} 份文件），確認？(y/N): "
                )
                if answer.strip().lower() != "y":
                    print("  已取消")
                    return

            # 先刪 embeddings，再刪 collection
            cur.execute(
                """DELETE FROM langchain_pg_embedding
                   WHERE collection_id = (
                       SELECT uuid FROM langchain_pg_collection WHERE name = %s
                   )""",
                (collection_name,),
            )
            cur.execute(
                "DELETE FROM langchain_pg_collection WHERE name = %s",
                (collection_name,),
            )
        conn.commit()
        print(f"\n  已刪除 collection「{collection_name}」（{count} 份文件）\n")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── CLI 主程式 ───────────────────────────────────────────────────────


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="pgvector CRUD 管理工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = subparsers.add_parser("list", help="列出 collection 統計")
    p_list.add_argument("--database", type=str, default=None,
                        help="config.toml 中的 database key")
    p_list.add_argument("--verbose", action="store_true",
                        help="顯示按 source / brand / category 的細分統計")

    # search
    p_search = subparsers.add_parser("search", help="語意相似度搜尋")
    p_search.add_argument("--database", type=str, required=True)
    p_search.add_argument("--query", type=str, required=True)
    p_search.add_argument("--top-k", type=int, default=3)
    p_search.add_argument("--filter-brand", type=str, default=None,
                          help="依 brand 過濾")
    p_search.add_argument("--filter-category", type=str, default=None,
                          help="依 category 過濾")
    p_search.add_argument("--verbose", action="store_true",
                          help="顯示 raw_text")

    # get
    p_get = subparsers.add_parser("get", help="依 source 查詢文件")
    p_get.add_argument("--database", type=str, required=True)
    p_get.add_argument("--source", type=str, required=True)
    p_get.add_argument("--verbose", action="store_true",
                       help="顯示完整 page_content 與 raw_text")

    # update
    p_update = subparsers.add_parser("update", help="更新文件 metadata")
    p_update.add_argument("--database", type=str, required=True)
    p_update.add_argument("--source", type=str, required=True,
                          help="目標文件的 source 值")
    p_update.add_argument("--set-brand", type=str, default=None)
    p_update.add_argument("--set-model", type=str, default=None)
    p_update.add_argument("--set-category", type=str, default=None)
    p_update.add_argument("--verbose", action="store_true")

    # delete
    p_delete = subparsers.add_parser("delete", help="刪除文件")
    p_delete.add_argument("--database", type=str, required=True)
    p_delete.add_argument("--source", type=str, default=None,
                          help="依 source 刪除所有相關文件")
    p_delete.add_argument("--id", type=str, default=None,
                          help="依 UUID 刪除單一文件")
    p_delete.add_argument("--confirm", action="store_true",
                          help="跳過確認提示")
    p_delete.add_argument("--verbose", action="store_true")

    # drop
    p_drop = subparsers.add_parser("drop", help="刪除整個 collection")
    p_drop.add_argument("--database", type=str, required=True)
    p_drop.add_argument("--confirm", action="store_true",
                        help="跳過確認提示")
    p_drop.add_argument("--verbose", action="store_true")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    config = load_config()

    handlers = {
        "list": cmd_list,
        "search": cmd_search,
        "get": cmd_get,
        "update": cmd_update,
        "delete": cmd_delete,
        "drop": cmd_drop,
    }
    handlers[args.command](args, config)


if __name__ == "__main__":
    main()
