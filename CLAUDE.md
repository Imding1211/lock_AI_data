# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is the **data pipeline and knowledge base** repository for a smart lock AI customer service system. It manages raw data ingestion, transformation, and preparation of documents for RAG (Retrieval-Augmented Generation) via pgvector.

This repo is the **data pipeline** repo — it contains ETL scripts under `pipeline/` and the data under `storage/`. The main application code (agents, tools, embeddings, config.toml, main.py) lives in a separate repository.

## Architecture: Medallion Data Pipeline

Data flows through a four-layer medallion architecture under `storage/`:

```
storage/raw/       → Original source files (video .MOV, LINE chat .csv, website .txt, YouTube .mp4, gdrive links)
storage/bronze/    → First transformation (video transcripts .txt, LINE chat .csv, website .md, YouTube .md, gdrive .json)
storage/silver/    → Cleaned and structured data (LangChain Document JSON Arrays per source)
storage/gold/      → pgvector collections (kb_video, kb_line_chat, kb_website, kb_youtube, kb_gdrive)
```

Processing scripts live in `pipeline/` with a consistent naming pattern — `pipeline/<stage>/process_<source>.py`:
- `pipeline/source_to_raw/` — fetch/collect raw data (currently: youtube via yt-dlp)
- `pipeline/raw_to_bronze/` — transcribe/parse raw files (video: Whisper ASR, line: de-identification, youtube: Gemini Vision, website: Playwright + markdownify, gdrive: Drive API indexing)
- `pipeline/bronze_to_silver/` — LLM-driven semantic chunking and HyDE generation for all sources
- `pipeline/silver_to_gold/seed_pgvector.py` — universal pgvector ingestion from silver JSON

## Business Domain

主要業務對象為電子鎖品牌（Chatlock AI-99、Dormakaba、Philips、Milre 等）的客服、故障排除與技術支援。知識庫服務於主應用 repo 中的四位 AI Agent（硬體維修技師、報價客服、門市助理、APP 設定專家）。

## Data Sources

- **Training videos** (`storage/raw/video/`): .MOV files of lock installation, troubleshooting, customer service SOP
- **LINE chat logs** (`storage/raw/line_chat/`): Customer service conversation CSVs
- **YouTube tutorials** (`storage/raw/youtube/`): Chatlock AI-99 lock setup guide .mp4 files downloaded via yt-dlp
- **Website** (`storage/raw/website/`): URL list (`website.txt`) of 鎖市 Wix pages, crawled via Playwright
- **Google Drive manuals** (`storage/raw/gdrive/`): URL list (`links.txt`) of product manual folders, indexed via Drive API

## Database: pgvector

Start the local pgvector container:
```bash
docker run --name lock_AI -e POSTGRES_USER=lock -e POSTGRES_PASSWORD=0000 -e POSTGRES_DB=lock_AI_data -p 5433:5432 -d pgvector/pgvector:pg17
```

Enable the vector extension:
```bash
docker exec -it lock_AI psql -U lock -d lock_AI_data -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

Note: the container maps to **port 5433** (not the default 5432).

## Running Pipeline Scripts

All scripts share common CLI flags:
```bash
python pipeline/<stage>/process_<source>.py --verbose        # Enable DEBUG logging
python pipeline/<stage>/process_<source>.py --force          # Overwrite existing output
python pipeline/<stage>/process_<source>.py --file <name>    # Process single file only
```

Seeding pgvector (silver → gold):
```bash
python pipeline/silver_to_gold/seed_pgvector.py --database video --verbose
python pipeline/silver_to_gold/seed_pgvector.py --database video --reset --verbose   # Clear & rebuild
python pipeline/silver_to_gold/seed_pgvector.py --all --verbose                      # All databases
```

## Managing pgvector Data

`database/manage_pgvector.py` provides CRUD operations on the vector database:
```bash
python database/manage_pgvector.py list                                              # All collections summary
python database/manage_pgvector.py list --database video --verbose                   # Per-source breakdown
python database/manage_pgvector.py search --database video --query "鎖舌卡住" --top-k 3
python database/manage_pgvector.py get --database video --source "客服問診 SOP 核心.txt"
python database/manage_pgvector.py update --database video --source "file.txt" --set-category troubleshoot
python database/manage_pgvector.py delete --database video --source "file.txt" --confirm
python database/manage_pgvector.py drop --database video --confirm                   # Drop entire collection
```

## Configuration

`config.toml` is the single source of truth — no hardcoded values in scripts. It contains:
- `[pipelines.*]` — per-source LLM model, temperature, provider, and source-specific settings
- `[databases.*]` — per-source pgvector collection name, embedding config, chunk size/overlap

The `llms/` and `embeddings/` modules use a factory pattern (`get_llm()`, `get_embedding()`) to provide pluggable providers based on config. Adding a new LLM/embedding backend requires only a new `.py` file and registry entry in `llms/__init__.py` or `embeddings/__init__.py`. Currently only `vertexai` is registered; `get_llm()` returns a `generate_json(prompt, system_prompt, schema)` closure, `get_vision_llm()` returns a `generate_from_video(video_path, system_prompt)` closure.

## RAG Document Requirements

Documents destined for pgvector must use `langchain_core.documents.Document` with mandatory metadata fields:
- `brand` (str) — product brand, or `"general"`
- `model` (str) — product model
- `category` (str) — `"setup"` / `"troubleshoot"` / `"knowledge"` / `"specification"`
- `source_type` (str) — `"video"` / `"line_chat"` / `"youtube"` / `"website"` / `"gdrive"`
- `source` (str) — original source filename or ID
- `chunk_index` (int) — chunk sequence number (starting from 1)
- `raw_text` (str) — pure knowledge summary without HyDE questions (for Small-to-Big retrieval)

Chunking and retrieval patterns:
- **Semantic Pre-chunking**: LLM outputs JSON Arrays of self-contained knowledge chunks. Do not use `RecursiveCharacterTextSplitter`.
- **HyDE**: Each chunk's `page_content` includes 2-3 hypothetical user questions to maximize retrieval hit rate.
- **Small-to-Big**: Full knowledge summary stored in `metadata["raw_text"]`; agents read this after retrieval for complete context.

## Key Specs

- **Python**: 3.11+ (uses `tomllib` from stdlib)
- **LLM**: Vertex AI Gemini 2.5 Flash (all pipeline stages)
- **Embedding model**: Vertex AI `text-embedding-004` (768 dimensions)
- **Vector DB**: pgvector via `langchain-postgres` with `psycopg` async
- **Integration**: New knowledge bases are registered via `config.toml` `[[databases]]` blocks in the main app repo — no code changes needed

## Design Conventions

- **Idempotency**: All processors check if output exists before running; use `--force` to overwrite. This prevents wasting LLM calls on re-runs.
- **Config-driven**: Read all external service config from `config.toml`, never hardcode.
- **Metadata override**: Objective metadata (`source`, `source_type`) must be set by Python code, not inferred by LLM.
- **Structured output**: Always use `response_mime_type="application/json"` when calling LLMs to guarantee parseable JSON.
- **Path resolution**: All pipeline scripts resolve `ROOT_DIR` via `Path(__file__).resolve().parents[N]` and insert it into `sys.path` for imports.

## Environment Variables (in .env)

- `PG_VECTOR_URI` — PostgreSQL connection string (e.g. `postgresql+psycopg://lock:0000@localhost:5433/lock_AI_data`)
- `VERTEX_PROJECT_ID` / `VERTEX_LOCATION` — GCP credentials for embeddings

## Credentials

- `credentials.json` — Google Drive Service Account key file (in `.gitignore`, required for gdrive pipeline). Path configured in `config.toml` under `[pipelines.gdrive].service_account_file`.
