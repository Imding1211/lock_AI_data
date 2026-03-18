# seed_pgvector 使用指南

本文件說明如何使用 `pipeline/silver_to_gold/seed_pgvector.py` 將 Silver 層的標準化 JSON 切塊後寫入 pgvector 向量資料庫。

---

## 1. 前置準備

### 1.1 啟動 pgvector 容器

```bash
docker run --name lock_AI \
  -e POSTGRES_USER=lock \
  -e POSTGRES_PASSWORD=0000 \
  -e POSTGRES_DB=lock_AI_data \
  -p 5433:5432 \
  -d pgvector/pgvector:pg17
```

啟用 vector 擴充：

```bash
docker exec -it lock_AI psql -U lock -d lock_AI_data -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

### 1.2 設定環境變數

在專案根目錄的 `.env` 檔案中設定以下變數：

```env
PG_VECTOR_URI=postgresql+psycopg://lock:0000@localhost:5433/lock_AI_data
VERTEX_PROJECT_ID=your-gcp-project-id
VERTEX_LOCATION=your-gcp-region
```

### 1.3 安裝依賴

```bash
pip install -r requirements.txt
```

---

## 2. config.toml 參數說明

每個資料來源在 `config.toml` 中對應一個 `[databases.<key>]` 區塊。腳本根據 `--database` 指定的 key 讀取對應設定。

```toml
[databases.video]
type = "pgvector"
collection_name = "kb_video"
source_dir = "video"
connection_uri_env = "PG_VECTOR_URI"
embedding_provider = "vertexai"
embedding_model = "text-embedding-004"
embedding_dimensions = 768
```

| 參數 | 必填 | 說明 |
|------|------|------|
| `type` | 是 | 固定為 `"pgvector"` |
| `collection_name` | 是 | pgvector 中的 collection 名稱，每個資料來源獨立 |
| `source_dir` | 是 | 對應 `storage/silver/` 下的子目錄名稱 |
| `connection_uri_env` | 是 | `.env` 中 PostgreSQL 連線字串的變數名稱 |
| `embedding_provider` | 是 | Embedding 供應商（目前支援 `"vertexai"`） |
| `embedding_model` | 是 | Embedding 模型名稱 |
| `embedding_dimensions` | 是 | 向量維度，必須與模型輸出匹配 |

### 目前已設定的資料來源

| database key | collection_name | source_dir | 來源說明 |
|-------------|----------------|------------|---------|
| `video` | `kb_video` | `video/` | 訓練影片逐字稿 |
| `line_chat` | `kb_line_chat` | `line_chat/` | LINE 客服對話紀錄 |
| `website` | `kb_website` | `website/` | 官網產品資訊 |
| `youtube` | `kb_youtube` | `youtube/` | YouTube 教學影片 |

---

## 3. Silver JSON 格式

腳本讀取的 JSON 檔案必須包含 `page_content` 和 `metadata` 兩個欄位：

```json
{
  "page_content": "文件內容文字...",
  "metadata": {
    "brand": "general",
    "model": "general",
    "category": "troubleshoot",
    "source_type": "video",
    "source": "原始檔名.txt"
  }
}
```

`metadata` 的欄位結構由各 Bronze → Silver 處理腳本決定，`seed_pgvector.py` 會原封不動地寫入。

---

## 4. CLI 使用方式

### 4.1 參數一覽

| 參數 | 說明 |
|------|------|
| `--database <key>` | 處理單一資料來源（與 `--all` 互斥，二擇一必填） |
| `--all` | 處理 config.toml 中所有 databases（與 `--database` 互斥） |
| `--file <filename>` | 只處理指定的單一 JSON 檔（相對於 `source_dir` 的路徑） |
| `--reset` | 清空該 collection 後重建（首次建立或需要重建時使用） |
| `--verbose` | 啟用 debug 級別日誌 |

### 4.2 常見用法

**單檔驗證**——開發時測試單一檔案，避免消耗大量 Embedding token：

```bash
python pipeline/silver_to_gold/seed_pgvector.py \
  --database video \
  --file "客服問診 SOP 核心.json" \
  --reset --verbose
```

**單一資料源全量寫入**——處理某個來源的所有 Silver JSON：

```bash
python pipeline/silver_to_gold/seed_pgvector.py \
  --database video \
  --reset --verbose
```

**全部資料源一次寫入**——處理所有已設定的 databases：

```bash
python pipeline/silver_to_gold/seed_pgvector.py \
  --all --reset --verbose
```

> **注意**：`--all` 不能與 `--file` 同時使用。

---

## 5. 驗證資料

### 5.1 查看 collection 列表

```bash
docker exec -it lock_AI psql -U lock -d lock_AI_data \
  -c "SELECT name, uuid FROM langchain_pg_collection;"
```

### 5.2 查看各 collection 的 chunk 數量

```bash
docker exec -it lock_AI psql -U lock -d lock_AI_data \
  -c "SELECT c.name, count(e.id) FROM langchain_pg_collection c LEFT JOIN langchain_pg_embedding e ON c.uuid = e.collection_id GROUP BY c.name;"
```

### 5.3 預覽寫入的內容

```bash
docker exec -it lock_AI psql -U lock -d lock_AI_data \
  -c "SELECT LEFT(document, 80) AS content_preview, cmetadata->>'source' AS source FROM langchain_pg_embedding WHERE collection_id = (SELECT uuid FROM langchain_pg_collection WHERE name = 'kb_video') LIMIT 5;"
```

---

## 6. 掛載至主應用（lock_AI_Agent）

資料寫入 pgvector 後，需要在主應用 repo 的 `config.toml` 註冊，系統才能檢索。

### 6.1 新增 `[[databases]]` 區塊

#### db_video — 訓練影片知識庫

```toml
[[databases]]
name               = "db_video"
type               = "pgvector"
description        = "訓練影片知識庫：安裝、故障排除、客服 SOP"
collection_name    = "kb_video"
connection_uri_env = "PG_VECTOR_URI"
top_k              = 3

embedding_provider       = "vertexai"
embedding_model          = "text-embedding-004"
embedding_project_id_env = "VERTEX_PROJECT_ID"
embedding_location_env   = "VERTEX_LOCATION"
embedding_dimensions     = 768
```

#### db_line_chat — LINE 客服對話知識庫

```toml
[[databases]]
name               = "db_line_chat"
type               = "pgvector"
description        = "LINE 客服對話知識庫：真實客戶問答紀錄"
collection_name    = "kb_line_chat"
connection_uri_env = "PG_VECTOR_URI"
top_k              = 3

embedding_provider       = "vertexai"
embedding_model          = "text-embedding-004"
embedding_project_id_env = "VERTEX_PROJECT_ID"
embedding_location_env   = "VERTEX_LOCATION"
embedding_dimensions     = 768
```

#### db_website — 官網產品資訊知識庫

```toml
[[databases]]
name               = "db_website"
type               = "pgvector"
description        = "官網產品資訊知識庫：產品規格、價格、保固條款"
collection_name    = "kb_website"
connection_uri_env = "PG_VECTOR_URI"
top_k              = 3

embedding_provider       = "vertexai"
embedding_model          = "text-embedding-004"
embedding_project_id_env = "VERTEX_PROJECT_ID"
embedding_location_env   = "VERTEX_LOCATION"
embedding_dimensions     = 768
```

#### db_youtube — YouTube 教學影片知識庫

```toml
[[databases]]
name               = "db_youtube"
type               = "pgvector"
description        = "YouTube 教學影片知識庫：AI-99 電子鎖安裝教學"
collection_name    = "kb_youtube"
connection_uri_env = "PG_VECTOR_URI"
top_k              = 3

embedding_provider       = "vertexai"
embedding_model          = "text-embedding-004"
embedding_project_id_env = "VERTEX_PROJECT_ID"
embedding_location_env   = "VERTEX_LOCATION"
embedding_dimensions     = 768
```

四個資料庫的對應關係：

| `name`（Agent 工具名稱） | `collection_name`（pgvector） | 說明 |
|--------------------------|------------------------------|------|
| `db_video` | `kb_video` | 訓練影片知識庫 |
| `db_line_chat` | `kb_line_chat` | LINE 客服對話知識庫 |
| `db_website` | `kb_website` | 官網產品資訊知識庫 |
| `db_youtube` | `kb_youtube` | YouTube 教學影片知識庫 |

### 6.2 `[[databases]]` 參數說明

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

### 6.3 綁定至 Agent

在 `[[agents]]` 的 `tools` 陣列加入對應的 `name`：

```toml
[[agents]]
name        = "product_expert"
label       = "產品規格專家"
description = "負責回答產品規格、設定操作、保固相關問題"
tools       = ["db_video", "db_website", "db_youtube", "db_line_chat", "transfer_to_human"]
prompt_file = "agents/prompts/product_expert.md"
```

### 6.4 驗證

1. 啟動系統：`python main.py`
2. 確認啟動日誌出現以下四個工具的註冊訊息：
   - `[*] 已註冊工具: db_video`
   - `[*] 已註冊工具: db_line_chat`
   - `[*] 已註冊工具: db_website`
   - `[*] 已註冊工具: db_youtube`
3. 確認各工具皆顯示 `[*] 維度驗證通過`
4. 實際對話測試 Agent 能否檢索到對應知識庫的內容
