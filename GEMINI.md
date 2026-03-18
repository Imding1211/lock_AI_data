# GEMINI.md - 電子鎖 AI 客服 RAG 資料準備專案

本專案旨在建立一個基於獎章架構 (Medallion Architecture) 的數據處理環境，為電子鎖 AI 客服系統提供高品質的 RAG (檢索增強生成) 知識庫。

## 1. 專案概覽 (Project Overview)

本專案的核心目標是將四種異質原始資料（Line 聊天紀錄、影片轉錄、網站抓取、YouTube）轉化為結構化、可高效檢索的 pgvector 向量資料庫知識。

*   **核心架構**：Medallion Architecture (Raw → Bronze → Silver → Gold)。
*   **關鍵技術**：
    *   **向量資料庫**：`pgvector` (PostgreSQL) + `langchain-postgres`。
    *   **資料處理**：Python, LangChain。
    *   **LLM 引擎**：支援多模型切換 (預設 Google Vertex AI `gemini-2.5-flash`)，用於資料清洗與結構化。
    *   **Embedding 模型**：支援多模型切換 (預設 Google Vertex AI `text-embedding-004`, 768 維度)。
*   **主要業務對象**：電子鎖品牌 (如 Philips, Dormakaba, Milre, AI-99 等) 的客服、故障排除與技術支援。

## 2. 目錄結構與數據流 (Directory Structure & Data Flow)

專案嚴格遵守獎章架構，每個階段有明確的職責：

### 2.1 數據儲存 (storage/)
*   **raw/**：存放未處理的原始異質資料 (CSV, HTML 等)。
    *   `line_chat/`, `video/`, `youtube/`, `website/`。
*   **bronze/**：初步清理後的純文本資料 (TXT, MD)。
    *   移除特殊編碼，統一轉為 UTF-8 文本，但內容仍為非結構化或包含雜訊（如 ASR 的口語贅字）。
*   **silver/**：**專案的資料中樞 (Hub)**。
    *   所有子目錄的產出皆為**標準化 JSON**。
    *   該 JSON 對應 LangChain 的 `Document` 物件，必須包含 `page_content` 與標準化的 `metadata` 欄位 (`brand`, `model`, `category`, `source`, `source_type`)。

### 2.2 數據處理引擎 (pipeline/)
*   **bronze_to_silver/**：資料清洗與結構化中台。
    *   針對不同資料源撰寫專屬 Processor (如 `process_video.py`)。
    *   **核心策略**：利用 LLM 進行語音糾錯、去噪與重組，並強制輸出 JSON 格式。
*   **silver_to_gold/**：通用向量入庫引擎。
    *   `seed_pgvector.py`：單一泛用腳本。遞迴讀取 `storage/silver/` 下所有 JSON，進行切塊 (Chunking)、Embedding 並寫入 pgvector。

### 2.3 服務模組
*   **llms/**：LLM 提供商工廠，供 Pipeline 呼叫以進行資料清洗。
*   **embeddings/**：Embedding 提供商工廠，供 Gold 層寫入與 Agent 檢索使用。
*   **database/pgvector/**：包含 `startup.txt`，記錄 PostgreSQL 的 Docker 啟動指令。

## 3. 架構設計原則 (Architecture Guidelines)

為確保專案的高擴展性與維護性，所有 Pipeline 與服務必須嚴格遵守以下設計模式：

### 3.1 設定檔驅動 (Config-Driven)
*   **嚴禁 Hardcode**：所有外部服務（LLM 選擇、Embedding 模型、Database 連線）的配置禁止寫死於 Python 腳本中。
*   統一透過專案根目錄的 `config.toml` 進行配置管理。Pipeline 腳本負責讀取該檔來決定運作邏輯。

### 3.2 工廠模式 (Factory Pattern)
系統利用動態工廠模式實現對外部服務的依賴反轉，達到隨插即用 (Plug-and-Play)：
*   **LLM 整合 (`llms/__init__.py`)**：
    *   處理器呼叫 `get_llm(provider, model)` 取得一個封裝好的閉包函數 (`generate_json`)。
    *   新增供應商（如 Ollama）只需新增 `.py` 實作並註冊至 `LLM_REGISTRY`，呼叫端無需修改。
*   **Embedding 整合 (`embeddings/__init__.py`)**：
    *   遵循 `get_embedding(db_config)` 介面，依據 config 返回對應的 LangChain Embeddings 實例。

### 3.3 冪等性與防呆機制 (Idempotency & Robustness)
*   **冪等性**：所有的 Pipeline 處理器 (特別是呼叫 LLM 的 Bronze to Silver 階段) 執行前必須檢查輸出檔案是否已存在 (`out_path.exists()`)，避免中斷後重跑浪費運算資源。
*   **結構化輸出保證**：呼叫 LLM 時，必須透過 API 設定 (`response_mime_type="application/json"` 或 `response_format`) 強制 LLM 回傳純 JSON，防止 Markdown 解析錯誤。
*   **Metadata 強制覆寫**：客觀事實的中繼資料（如 `source` 檔名, `source_type` 來源類別）應透過 Python 腳本強制覆寫，不依賴 LLM 推斷，以確保資料血緣的絕對正確。

## 4. 關鍵指令與營運 (Usage & Operations)

### 4.1 資料庫啟動
參考 `database/pgvector/startup.txt`：
```bash
docker run --name lock_AI -e POSTGRES_USER=lock -e POSTGRES_PASSWORD=0000 -e POSTGRES_DB=lock_AI_data -p 5433:5432 -d pgvector/pgvector:pg17
```

### 4.2 Pipeline 執行指令
*   **清洗 Video 資料 (Bronze → Silver)**
    ```bash
    python pipeline/bronze_to_silver/process_video.py --verbose
    # 若需覆寫已存在的檔案請加 --force
    ```
*   **寫入向量資料庫 (Silver → Gold)**
    ```bash
    # 寫入單一資料源 (例如 video)
    python pipeline/silver_to_gold/seed_pgvector.py --database video --verbose
    
    # 若需清空並重建 Table 請加 --reset
    python pipeline/silver_to_gold/seed_pgvector.py --database video --reset --verbose
    
    # 一次性將所有設定好的資料庫寫入 (包含 video, line_chat, website, youtube)
    python pipeline/silver_to_gold/seed_pgvector.py --all --verbose
    ```

## 5. 開發建議與慣例

*   **中繼資料 (Metadata)**：為每份文件精確標註 `brand` (品牌) 與 `model` (型號)，這是 AI Agent 進行 Metadata Filtering 檢索的關鍵。
*   **資料切分 (Chunking)**：目前全域採用 `RecursiveCharacterTextSplitter`，預設 `chunk_size=600`, `chunk_overlap=60`。
*   **環境變數**：開發前請確保 `.env` 已正確配置 `PG_VECTOR_URI`、`VERTEX_PROJECT_ID` 及 `VERTEX_LOCATION`。
