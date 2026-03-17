# GEMINI.md - 電子鎖 AI 客服 RAG 資料準備專案

本專案旨在建立一個基於獎章架構 (Medallion Architecture) 的數據處理環境，為電子鎖 AI 客服系統提供高品質的 RAG (檢索增強生成) 知識庫。

## 1. 專案概覽 (Project Overview)

本專案的核心目標是將原始資料（Line 聊天紀錄、YouTube 教學、產品手冊等）轉化為結構化的向量資料庫知識。

*   **核心架構**：Medallion Architecture (Raw → Bronze → Silver → Gold)。
*   **關鍵技術**：
    *   **向量資料庫**：`pgvector` (PostgreSQL)。
    *   **Embedding 模型**：Google Vertex AI `text-embedding-004` (768 維度)。
    *   **框架**：LangChain (LangChain-Postgres)。
*   **主要業務對象**：電子鎖品牌 (如 Philips, Dormakaba, Milre, AI-99 等) 的客服與技術支援。

## 2. 目錄結構與數據流 (Directory Structure & Data Flow)

### 2.1 數據儲存 (storage/)
*   **raw/**：存放未處理的原始資料。
    *   `line_chat/`：大量的客戶諮詢紀錄 (CSV 格式)。
    *   `video/` / `youtube/` / `website/`：各類產品教學與網頁抓取資料。
*   **bronze/**：初步清理後的資料，目前主要包含已提取的文本內容。
    *   `video/`：包含多種電子鎖的故障排除、安裝評估與構造解說 (TXT)。
    *   `youtube/`：AI-99 等產品的設定教學 (MD)。
*   **silver/**：預計存放進一步結構化或主題化的資料（待實作）。

### 2.2 數據處理 (pipeline/)
*   **source_to_raw**：資料獲取階段。
*   **raw_to_bronze**：初步清理與解碼。
*   **bronze_to_silver**：結構化處理與 Metadata 標記。
*   **silver_to_gold**：Embedding 與寫入向量資料庫。

### 2.3 資料庫 (database/)
*   **pgvector/**：包含 `startup.txt`，記錄了如何透過 Docker 啟動 `pgvector` 容器及初始化 SQL。

## 3. 架構設計原則 (Architecture Guidelines)

為確保專案的高擴展性與維護性，所有 Pipeline 處理器 (Processors) 必須嚴格遵守以下架構設計模式：

### 3.1 設定檔驅動 (Config-Driven)
*   所有外部服務（LLM、Embedding、Database）的配置**嚴禁 Hardcode** 於程式碼中。
*   統一透過專案根目錄的 `config.toml` 進行配置管理。
*   處理器腳本應負責讀取 `.toml` 檔案來決定運作邏輯（例如使用哪種模型、哪個提供商）。

### 3.2 工廠模式 (Factory Pattern)
*   **LLM 整合 (`llms/` 目錄)**:
    *   支援多供應商架構（如 VertexAI, Ollama, OpenAI）。
    *   處理器不直接初始化 Client。而是呼叫 `llms.get_llm(provider, model)`，透過工廠動態返回標準化的執行函數（例如 `generate_json` 介面）。
    *   新增供應商時，只需在 `llms/` 下新增對應的 `.py` 並在 `__init__.py` 的 `LLM_REGISTRY` 註冊，無需修改現有的 Pipeline 腳本。
*   **Embedding 整合 (`embeddings/` 目錄)**:
    *   同理，遵循規格書定義的 `get_embedding(db_config)` 工廠方法，動態取得 Embedding 實例。

### 3.3 冪等性與防呆機制
*   所有的 Pipeline 階段（如 Bronze to Silver）必須具備冪等性 (Idempotency)。腳本執行前應檢查輸出檔案是否已存在，避免重複處理浪費運算資源。
*   強制系統化輸出 (Structured Output)，確保 LLM 回傳的結果是純粹且合規的 JSON。
*   客觀的 Metadata（如 `source`, `source_type`）應透過 Python 腳本強制覆寫，不依賴 LLM 的推斷。

## 4. 重要開發規範 (Key Specifications)

專案根目錄包含兩份至關重要的規範文件，開發前請務必研讀：

1.  **[外部API介接與RAG資料準備規範.md](外部API介接與RAG資料準備規範.md)**：
    *   定義了 Document 的 Metadata 結構（brand, model, category, source）。
    *   提供了 Chunking (500-800 字) 與 Embedding 切換的建議。
2.  **[正式資料庫RAG開發與整合規範.md](正式資料庫RAG開發與整合規範.md)**：
    *   詳述了與 `config.toml` 的掛載協議。
    *   說明了 Embedding 工廠機制與維度驗證邏輯。
    *   提供了 `seed_db.py` 的實作範例流程。

## 5. 關鍵指令與使用 (Usage)

### 5.1 資料庫啟動
參考 `database/pgvector/startup.txt`：
```bash
docker run --name lock_AI -e POSTGRES_USER=lock -e POSTGRES_PASSWORD=0000 -e POSTGRES_DB=lock_AI_data -p 5433:5432 -d pgvector/pgvector:pg17
```

### 5.2 RAG 資料寫入 (TODO)
根據規範，未來需實作或執行 `scripts/seed_db.py`（目前工作區中尚未包含此檔案，但規範中已有程式碼範例）來將 `bronze` 或 `silver` 層級的資料寫入向量資料庫。

## 6. 開發建議與慣例

*   **中繼資料 (Metadata)**：為每份文件加上精確的 `brand` (品牌) 與 `model` (型號)，這對 AI Agent 的檢索過濾至關重要。
*   **資料切分 (Chunking)**：使用 `RecursiveCharacterTextSplitter`，並維持 10% 的 Overlap 以確保語義連貫。
*   **環境變數**：確保 `.env` 中正確配置 `PG_VECTOR_URI`、`VERTEX_PROJECT_ID` 及 `VERTEX_LOCATION`。