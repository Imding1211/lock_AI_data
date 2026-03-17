# 正式資料庫 RAG 開發與整合規範

本文件說明如何建立 pgvector 知識庫並掛載至系統。只要滿足兩個條件——使用 `langchain-postgres` 的 `PGVector` 寫入資料、在 `config.toml` 正確設定——系統即可直接使用，其餘實作細節由開發者自行決定。

---

## 1. 使用 LangChain 建立資料庫

### 1.1 唯一硬性要求

系統透過 `langchain-postgres` 的 `PGVector` 類別存取向量資料庫。因此寫入資料時**必須使用同一類別**，確保 Table schema 與索引格式相容。

```python
from langchain_core.documents import Document
from langchain_postgres import PGVector
```

### 1.2 Embedding 維度一致性

`config.toml` 中的 `embedding_dimensions` 必須與寫入時使用的 Embedding 模型輸出維度**完全匹配**。系統啟動時 `PGVectorRetriever`（`tools/pgvector_store.py`）會自動校驗，不匹配會拋出 `ValueError`。

### 1.3 建立範例

以下為最小可運作的 seed 腳本，完整範例可參考 `scripts/seed_db.py`：

```python
import os
from langchain_core.documents import Document
from langchain_postgres import PGVector
from embeddings import get_embedding

# 準備文件——page_content 與 metadata 的內容結構由開發者自行設計
docs = [
    Document(
        page_content="你的知識內容...",
        metadata={"source": "my_doc.txt"},  # metadata 欄位自由定義
    ),
]

# Embedding 配置需與 config.toml 中對應的 [[databases]] 區塊一致
db_config = {
    "embedding_provider": "vertexai",
    "embedding_model": "text-embedding-004",
    "embedding_project_id_env": "VERTEX_PROJECT_ID",
    "embedding_location_env": "VERTEX_LOCATION",
    "embedding_dimensions": 768,
}

embed_fn = get_embedding(db_config)
connection_uri = os.environ["PG_VECTOR_URI"]

vector_store = PGVector(
    embeddings=embed_fn,
    collection_name="my_collection",  # 對應 config.toml 的 collection_name
    connection=connection_uri,
    pre_delete_collection=True,       # 首次建立或需要重建時使用
)

vector_store.add_documents(docs)
```

> **提示**：`get_embedding()` 是專案內的工廠函數（`embeddings/__init__.py`），根據 `embedding_provider` 自動建立對應的 Embedding 實例。目前支援 `"vertexai"` 與 `"ollama"`，如需新增供應商，在 `embeddings/` 目錄實作並註冊至 `REGISTRY` 即可。

---

## 2. config.toml 掛載協議

### 2.1 運作原理

系統啟動時，`tools/__init__.py` 的 `build_tools()` 會掃描所有 `[[databases]]` 區塊，根據 `type` 欄位建立 Retriever 實例並註冊為 LangChain Tool。Agent 透過 `tools` 陣列中的名稱引用這些工具。

**只需設定，無需改程式碼。**

### 2.2 新增 `[[databases]]` 區塊

```toml
[[databases]]
name               = "db_my_knowledge"           # 唯一名稱，Agent 的 tools 透過此名稱引用
type               = "pgvector"                  # 固定值
description        = "這個知識庫的用途描述"         # Agent 依此判斷何時使用該工具
collection_name    = "my_collection"             # 與 seed 腳本中的 collection_name 一致
connection_uri_env = "PG_VECTOR_URI"             # .env 中 PostgreSQL 連線字串的變數名稱
top_k              = 3                           # 每次檢索回傳筆數（預設 2）

# Embedding 配置——必須與 seed 腳本使用的模型完全一致
embedding_provider       = "vertexai"
embedding_model          = "text-embedding-004"
embedding_project_id_env = "VERTEX_PROJECT_ID"
embedding_location_env   = "VERTEX_LOCATION"
embedding_dimensions     = 768
```

| 參數 | 必填 | 說明 |
|------|------|------|
| `name` | 是 | 唯一識別名稱，供 Agent `tools` 陣列引用 |
| `type` | 是 | 固定為 `"pgvector"` |
| `description` | 是 | 工具描述，Agent 以此判斷是否呼叫 |
| `collection_name` | 是 | pgvector 中的 collection 名稱，需與 seed 時一致 |
| `connection_uri_env` | 是 | `.env` 中 PostgreSQL 連線字串的變數名稱 |
| `top_k` | 否 | 檢索回傳筆數，預設 2 |
| `embedding_provider` | 是 | Embedding 供應商（`"vertexai"` / `"ollama"`） |
| `embedding_model` | 是 | Embedding 模型名稱 |
| `embedding_project_id_env` | 視 provider | Vertex AI 專用 |
| `embedding_location_env` | 視 provider | Vertex AI 專用 |
| `embedding_dimensions` | 是 | 向量維度，必須與模型輸出匹配 |

### 2.3 綁定至 Agent

在 `[[agents]]` 的 `tools` 陣列加入新資料庫的 `name`：

```toml
[[agents]]
name        = "product_expert"
label       = "產品規格專家"
description = "負責回答產品規格、設定操作、保固相關問題"
tools       = ["db_smartlock_manual", "db_my_knowledge", "transfer_to_human"]
prompt_file = "agents/prompts/product_expert.md"
```

### 2.4 驗證

1. 執行 seed 腳本寫入資料
2. 啟動系統：`python main.py`
3. 確認啟動日誌出現 `[*] 已註冊工具: db_my_knowledge` 與 `[*] 維度驗證通過`
4. 實際對話測試 Agent 能否檢索到新知識庫的內容
