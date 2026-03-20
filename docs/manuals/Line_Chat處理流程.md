# Line Chat 處理流程

LINE 客服對話從原始 CSV 到進入 pgvector 知識庫，經過三個處理階段：

```
Raw CSV → Bronze CSV → Silver JSON → Gold (pgvector)
```

---

## 階段一：Raw → Bronze（物理性清洗與時間聚合）

### 1.1 處理策略

LINE 官方帳號匯出的 CSV 檔案包含了大量零碎、無知識價值的對話（如寒暄、自動回覆、貼圖）。為確保進入 LLM (Silver 層) 的資料具備足夠的上下文 (Context) 且沒有雜訊干擾，我們必須在 Bronze 階段進行**物理性清洗與時間聚合**。

**腳本**：`pipeline/raw_to_bronze/process_line.py`

```bash
# 全量處理
python pipeline/raw_to_bronze/process_line.py --verbose

# 單檔處理
python pipeline/raw_to_bronze/process_line.py --file "1001_20240822_20240903_yen-cheng.csv" --verbose
```

### 1.2 處理流程圖

```mermaid
---
config:
  layout: dagre
  theme: base
  themeVariables:
    primaryColor: '#4A90D9'
    primaryTextColor: '#1a1a1a'
    lineColor: '#5A6A7A'
---
graph TD
    A[Raw Line Chat CSV<br/>單句零碎對話] -->|1. Drop 檔頭| B[移除前 3 行 Meta 資訊]
    B -->|2. 雜訊過濾| C{"是否為有效訊息?"}

    C -- 否 (自動回覆/貼圖/撤回) --> X[丟棄 (Drop)]
    C -- 是 --> D[萃取 傳送者, 時間, 內容]

    D -->|2.5 關鍵字篩選| D2{"整檔是否提及「鎖」?"}
    D2 -- 否 --> X2[整檔跳過 (Drop)]

    D2 -- 是 -->|3. 時間聚合| E{"兩則訊息間隔 > 2小時?"}

    E -- 否 --> F[拼接至當前 Session (Transcript)]
    E -- 是 --> G[建立新的 Session]

    F --> H[4. 長度過濾]
    G --> H

    H --> I{"Session 總字數 >= 20字?"}
    I -- 否 --> Y[丟棄短對話 (Drop)]
    I -- 是 --> J[輸出 Bronze CSV<br/>每行代表一個有效 Session]

    style A fill:#E8F4FD,stroke:#7AB8E0,stroke-width:2px
    style J fill:#FFF3E0,stroke:#FFB74D,stroke-width:3px
    style C fill:#FFE082,stroke:#F9A825,stroke-width:2px
    style D2 fill:#FFE082,stroke:#F9A825,stroke-width:2px
    style E fill:#FFE082,stroke:#F9A825,stroke-width:2px
    style I fill:#FFE082,stroke:#F9A825,stroke-width:2px
    style X fill:#FFCDD2,stroke:#E53935,stroke-width:1px,stroke-dasharray: 5 5
    style X2 fill:#FFCDD2,stroke:#E53935,stroke-width:1px,stroke-dasharray: 5 5
    style Y fill:#FFCDD2,stroke:#E53935,stroke-width:1px,stroke-dasharray: 5 5
```

### 1.3 處理細節

#### 雜訊過濾 (Noise Filtering)
*   **系統訊息**：移除 `傳送者名稱` 為 `自動回應訊息` 或 `傳送者類型` 為 `System` 的列。
*   **多媒體與無效文字**：移除內容為 `[貼圖]`, `[照片]`, `[影片]`, `[檔案]`, `[語音訊息]` 及其「已傳送」變體的對話。
*   **撤回訊息**：移除內容包含 `已收回訊息` 的列。
*   **自動回覆樣板**：移除符合 `config.toml` 中 `[pipelines.line_chat].auto_reply_patterns` 的訊息。

#### 關鍵字篩選 (Keyword Filter)
*   整檔過濾後的所有訊息中，若**未提及「鎖」**，則判定為非電子鎖相關對話，整檔跳過。

#### 時間聚合 (Session Grouping)
*   **斷點判定**：計算相鄰兩則訊息的時間差。若大於 **2 小時 (7200 秒)**，則視為一個新的對話事件 (Session)。
*   **格式拼接**：將同一個 Session 內的所有對話，依據時間順序拼接成一段文字（Transcript）。
    *   *格式範例*：`User: 請問電子鎖沒電怎麼辦？\nAccount: 您好，可以使用 9V 電池緊急供電。`

#### 長度過濾 (Length Filter)
*   **低價值剔除**：聚合完成後的 Session 文本（Transcript），若總字數小於 **20 字**，則判定為無價值的寒暄（如：「謝謝」、「不客氣」），直接丟棄。

### 1.4 輸出規格

產出的 Bronze CSV 位於 `storage/bronze/line_chat/`，每個原始 CSV 對應一個同名 Bronze CSV。

| 欄位 | 說明 | 範例 |
|------|------|------|
| `session_id` | 原始檔名 + Session 編號 | `1001_20240822_20240903_yen-cheng_session_1` |
| `start_time` | 對話開始時間 | `2024-08-22 10:18:24` |
| `end_time` | 對話結束時間 | `2024-08-22 10:50:24` |
| `transcript` | 拼接完成的純文本對話紀錄 | `yen-cheng: 您好...\n萱Mira🎈: 林先生您好...` |

---

## 階段二：Bronze → Silver（LLM 語意過濾與知識重寫）

### 2.1 處理策略

Bronze CSV 中的 Session 仍是原始對話格式，無法直接用於 RAG 檢索。此階段透過 LLM 進行 **相關性過濾** 與 **Semantic Pre-chunking**（語意前置切塊）。若對話被判定為相關，LLM 會將對話內容拆分為多個獨立知識點，每個知識點包含 **HyDE 格式**（`【常見問題】` + `【知識內容】`）的 `page_content` 與 `raw_text` 純淨摘要。具體任務包含：**相關性過濾**、**語意切分與 HyDE 格式組裝**、**Metadata 推斷**。

**腳本**：`pipeline/bronze_to_silver/process_line.py`

```bash
# 全量處理
python pipeline/bronze_to_silver/process_line.py --verbose

# 單檔處理
python pipeline/bronze_to_silver/process_line.py --file "1001_20240822_20240903_yen-cheng.csv" --verbose
```

### 2.2 處理流程圖

```mermaid
---
config:
  layout: dagre
  theme: base
  themeVariables:
    primaryColor: '#4A90D9'
    primaryTextColor: '#1a1a1a'
    lineColor: '#5A6A7A'
---
graph TD
    A[Bronze CSV<br/>每行一個 Session] -->|逐行讀取| B[取得 session_id + transcript]
    B -->|送入 LLM| C{"1. 相關性過濾<br/>is_relevant?"}

    C -- false (非電子鎖相關) --> X[跳過 (不輸出)]
    C -- true --> D[2. 語意切分<br/>Semantic Pre-chunking]

    D --> E[3. 模擬疑問句<br/>HyDE 格式組裝]
    E --> F[4. Metadata 推斷<br/>brand / model / category]

    F --> G[組合為 JSON Array<br/>每個元素一個知識點]
    G --> H[輸出 Silver JSON Array<br/>每個 Session 一個檔案]

    style A fill:#FFF3E0,stroke:#FFB74D,stroke-width:2px
    style H fill:#E8F5E9,stroke:#66BB6A,stroke-width:3px
    style C fill:#FFE082,stroke:#F9A825,stroke-width:2px
    style X fill:#FFCDD2,stroke:#E53935,stroke-width:1px,stroke-dasharray: 5 5
```

### 2.3 處理細節

#### 相關性過濾 (Relevance Filtering)

以下情況視為**不相關**，LLM 回傳 `is_relevant: false`，該 Session 不會產出 Silver JSON：
- 刻印章、買遙控器、配鑰匙等非電子鎖業務
- 純推銷、廣告
- 純寒暄、閒聊、無實質技術內容
- 內容過短或無法理解

#### 知識重寫 (Knowledge Rewriting)

LLM 將對話內容融合重寫為**客觀敘述性知識文章**：
- **禁止** Q&A 格式、禁止保留對話形式
- 將客服經驗轉化為通用技術知識
- 保留所有技術細節（型號、步驟、規格、注意事項）
- 使用正式書面中文

*範例*：對話「客人說鎖沒電…店員教他買 9V 電池」→ 文章「當電子鎖電池耗盡時，可使用 9V 方型電池接觸外部面板的緊急供電接點進行臨時供電…」

#### Metadata 推斷 (Metadata Inference)

LLM 根據對話內容推斷以下欄位：

| 欄位 | 說明 | 可選值 |
|------|------|--------|
| `brand` | 品牌 | `Dormakaba` / `Chainlock` / `general` |
| `model` | 型號 | `AI99` / `A90` / `AI88` / `general` |
| `category` | 分類 | `setup` / `troubleshoot` / `knowledge` / `specification` |

### 2.4 LLM 設定

LLM provider 和 model 由 `config.toml` 的 `[pipelines.line_chat]` 區塊控制。LLM 回應使用 JSON Schema 約束（structured output），確保輸出格式一致。API 呼叫間隔 1 秒以避免 rate limit。

### 2.5 輸出規格

產出的 Silver JSON 位於 `storage/silver/line_chat/`，每個有效 Session 對應一個 JSON 檔案，檔名為 `{session_id}.json`。格式為 **JSON Array**（若 `is_relevant` 為 true），每個元素為一個獨立知識點。

```json
[
  {
    "page_content": "【常見問題】\n安裝電子鎖前需要提供哪些照片？\n為什麼安裝電子鎖需要門和鎖的照片？\n電子鎖的安裝條件會受到哪些因素影響？\n\n【知識內容】\n電子鎖或輔助鎖的安裝作業，受限於門扇與現有鎖具的特定條件。為評估安裝可行性與潛在限制，客戶需提供清晰的門扇、現有鎖具正面以及開門後鎖舌側面的照片，供技術人員進行初步判斷。",
    "metadata": {
      "brand": "general",
      "model": "general",
      "category": "setup",
      "source_type": "line_chat",
      "source": "1036_20240704_20240810_專專_session_1",
      "chunk_index": 1,
      "raw_text": "電子鎖或輔助鎖的安裝作業，受限於門扇與現有鎖具的特定條件。為評估安裝可行性與潛在限制，客戶需提供清晰的門扇、現有鎖具正面以及開門後鎖舌側面的照片，供技術人員進行初步判斷。"
    }
  }
]
```

| 欄位 | 說明 | 範例 |
|------|------|------|
| `page_content` | HyDE 格式：`【常見問題】` + 模擬疑問句 + `【知識內容】` + 純淨摘要 | `【常見問題】\n安裝電子鎖前需要提供哪些照片？...` |
| `metadata.brand` | LLM 推斷的品牌 | `general` |
| `metadata.model` | LLM 推斷的型號 | `general` |
| `metadata.category` | LLM 推斷的分類 | `setup` |
| `metadata.source_type` | 固定為 `line_chat` | `line_chat` |
| `metadata.source` | 對應的 session_id | `1036_20240704_20240810_專專_session_1` |
| `metadata.chunk_index` | 該知識點在原始文件中的序號 | `1` |
| `metadata.raw_text` | 純淨知識摘要（供 Agent 回答使用） | `電子鎖或輔助鎖的安裝作業...` |

---

## 階段三：Silver → Gold（向量化寫入 pgvector）

### 3.1 處理策略

Silver JSON 已是 LLM 語意前置切塊後的 Document Array，每個元素為一個獨立知識點。此階段直接將 JSON Array 轉為 LangChain Documents，經 Embedding 向量化後寫入 pgvector。

**腳本**：`pipeline/silver_to_gold/seed_pgvector.py`

```bash
# 單一資料源寫入
python pipeline/silver_to_gold/seed_pgvector.py --database line_chat --reset --verbose

# 全部資料源一次寫入
python pipeline/silver_to_gold/seed_pgvector.py --all --reset --verbose

# 單檔驗證
python pipeline/silver_to_gold/seed_pgvector.py --database line_chat --file "1036_20240704_20240810_專專_session_1.json" --verbose
```

### 3.2 處理流程圖

```mermaid
---
config:
  layout: dagre
  theme: base
  themeVariables:
    primaryColor: '#4A90D9'
    primaryTextColor: '#1a1a1a'
    lineColor: '#5A6A7A'
---
graph TD
    A[Silver JSON Array<br/>每個元素一個知識點] -->|載入| B[轉為 LangChain Documents]
    B -->|Vertex AI text-embedding-004| C[向量化 (768 維)]
    C --> D[寫入 pgvector<br/>collection: kb_line_chat]

    style A fill:#E8F5E9,stroke:#66BB6A,stroke-width:2px
    style D fill:#E1BEE7,stroke:#AB47BC,stroke-width:3px
```

### 3.3 處理細節

#### 文件載入
- 讀取 `storage/silver/line_chat/` 下所有 `.json` 檔案
- 每個 JSON 為 Array，展開為多個 `langchain_core.documents.Document`，`metadata` 原封不動保留

#### 向量化與寫入
- Embedding 模型：Vertex AI `text-embedding-004`（768 維）
- 寫入 pgvector collection：`kb_line_chat`
- `--reset` 旗標會先清空 collection 再重建

### 3.4 config.toml 設定

```toml
[databases.line_chat]
type = "pgvector"
collection_name = "kb_line_chat"
source_dir = "line_chat"
connection_uri_env = "PG_VECTOR_URI"
embedding_provider = "vertexai"
embedding_model = "text-embedding-004"
embedding_dimensions = 768
```

### 3.5 驗證

```bash
# 查看 collection 文件數量
docker exec -it lock_AI psql -U lock -d lock_AI_data \
  -c "SELECT c.name, count(e.id) FROM langchain_pg_collection c LEFT JOIN langchain_pg_embedding e ON c.uuid = e.collection_id GROUP BY c.name;"

# 預覽寫入內容
docker exec -it lock_AI psql -U lock -d lock_AI_data \
  -c "SELECT LEFT(document, 80) AS preview, cmetadata->>'source' AS source FROM langchain_pg_embedding WHERE collection_id = (SELECT uuid FROM langchain_pg_collection WHERE name = 'kb_line_chat') LIMIT 5;"
```

---

## 全流程摘要

| 階段 | 輸入 | 輸出 | 處理方式 | 腳本 |
|------|------|------|---------|------|
| Raw → Bronze | `storage/raw/line_chat/*.csv` | `storage/bronze/line_chat/*.csv` | 規則式清洗 + 時間聚合 | `pipeline/raw_to_bronze/process_line.py` |
| Bronze → Silver | `storage/bronze/line_chat/*.csv` | `storage/silver/line_chat/*.json` | LLM 過濾 + Semantic Pre-chunking + HyDE | `pipeline/bronze_to_silver/process_line.py` |
| Silver → Gold | `storage/silver/line_chat/*.json` | pgvector `kb_line_chat` | 直接轉 Documents + Embedding 寫入 | `pipeline/silver_to_gold/seed_pgvector.py` |
