# Cloud SQL pgvector 連線指南

本文件說明如何從本地端連線至 GCP Cloud SQL PostgreSQL（已啟用 pgvector）並寫入向量資料。

---

## 1. Cloud SQL 實例資訊

| 項目 | 值 |
|------|-----|
| 連線名稱 | `cedar-scope-489604-g3:asia-east1:lock-ai` |
| 公開 IP | `35.229.228.13` |
| 連接埠 | `5432` |
| 資料庫名稱 | `lock-ai-db` |
| 使用者名稱 | `lock-ai` |
| pgvector 擴充 | 已啟用 |

---

## 2. 連線方式：Cloud SQL Auth Proxy

使用 Cloud SQL Auth Proxy 建立加密通道，不需要設定 Authorized Networks（IP 白名單）。

### 2.1 安裝 Proxy

```bash
gcloud components install cloud-sql-proxy
```

### 2.2 登入 GCP

確保已登入且帳號具有 **Cloud SQL Client** 角色：

```bash
gcloud auth login
```

### 2.3 啟動 Proxy

在獨立的終端機視窗中執行（需保持運行）：

```bash
cloud-sql-proxy cedar-scope-489604-g3:asia-east1:lock-ai --port 5433
```

看到 `Ready for new connections` 即代表連線通道已建立。Proxy 會將本地 `localhost:5433` 的流量轉發至 Cloud SQL。

### 2.4 設定 `.env`

```env
PG_VECTOR_URI=postgresql+psycopg://lock-ai:<URL_ENCODED_PASSWORD>@localhost:5433/lock-ai-db
VERTEX_PROJECT_ID=cedar-scope-489604-g3
VERTEX_LOCATION=asia-east1
```

> **注意**：密碼中若含有特殊字元（如 `@`、`;`、`+`、`*`、`[`），必須做 URL Encoding。
> 例如 `@` → `%40`、`;` → `%3B`、`+` → `%2B`、`*` → `%2A`、`[` → `%5B`

---

## 3. 替代連線方式：公開 IP 直連

若不使用 Proxy，可直接透過公開 IP 連線，但需設定 IP 白名單。

### 3.1 查詢本機公開 IP

```bash
curl -s ifconfig.me
```

### 3.2 新增 Authorized Network

GCP Console → **Cloud SQL** → 實例 `lock-ai` → **連線** → **已授權的網路** → 新增 `<YOUR_IP>/32`

### 3.3 設定 `.env`

```env
PG_VECTOR_URI=postgresql+psycopg://lock-ai:<URL_ENCODED_PASSWORD>@35.229.228.13:5432/lock-ai-db
```

> **注意**：公開 IP 連線需注意 IP 變動問題，每次 IP 改變都要重新設定白名單。建議正式環境使用 Cloud SQL Auth Proxy。

---

## 4. 寫入向量資料

Proxy 啟動後，即可使用既有的 seed 腳本寫入：

```bash
# 全部資料源一次寫入（清空重建）
python pipeline/silver_to_gold/seed_pgvector.py --all --reset --verbose

# 單一資料源
python pipeline/silver_to_gold/seed_pgvector.py --database video --reset --verbose

# 單檔驗證
python pipeline/silver_to_gold/seed_pgvector.py --database video --file "客服問診 SOP 核心.json" --verbose
```

---

## 5. 驗證資料

透過 Proxy 連線後，可用 `psql` 直接查詢 Cloud SQL：

```bash
psql "postgresql://lock-ai:<PASSWORD>@localhost:5433/lock-ai-db"
```

```sql
-- 查看 collection 列表
SELECT name, uuid FROM langchain_pg_collection;

-- 查看各 collection 的 chunk 數量
SELECT c.name, count(e.id)
FROM langchain_pg_collection c
LEFT JOIN langchain_pg_embedding e ON c.uuid = e.collection_id
GROUP BY c.name;

-- 預覽寫入內容
SELECT LEFT(document, 80) AS content_preview, cmetadata->>'source' AS source
FROM langchain_pg_embedding
WHERE collection_id = (SELECT uuid FROM langchain_pg_collection WHERE name = 'kb_video')
LIMIT 5;
```

---

## 6. 與本地 Docker pgvector 的差異

| | 本地 Docker | Cloud SQL |
|--|------------|-----------|
| 連線方式 | 直連 `localhost:5433` | Cloud SQL Auth Proxy → `localhost:5433` |
| 認證 | 帳密直連 | GCP IAM + 帳密 |
| IP 限制 | 無 | Authorized Networks 或 Proxy |
| 適用場景 | 開發測試 | 正式環境 |
| `.env` 差異 | `lock:0000@localhost:5433/lock_AI_data` | `lock-ai:<encoded_pw>@localhost:5433/lock-ai-db` |

> **切換環境**：只需修改 `.env` 中的 `PG_VECTOR_URI`，pipeline 腳本與主應用程式碼不需要任何變更。
