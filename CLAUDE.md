# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is the **data pipeline and knowledge base** repository for a smart lock AI customer service system. It manages raw data ingestion, transformation, and preparation of documents for RAG (Retrieval-Augmented Generation) via pgvector.

This repo is a **data-only** repo — it contains no application code (no Python scripts yet in pipeline/). The main application code (agents, tools, embeddings, config.toml, main.py) lives in a separate repository.

## Architecture: Medallion Data Pipeline

Data flows through a four-layer medallion architecture under `storage/`:

```
storage/raw/       → Original source files (video .MOV, LINE chat .csv)
storage/bronze/    → First transformation (video transcripts .txt, YouTube summaries .md)
storage/silver/    → (planned) Cleaned and structured data
storage/gold/      → (planned) Final RAG-ready Documents
```

Processing scripts for each stage go in `pipeline/`:
- `pipeline/source_to_raw/` — fetch/collect raw data
- `pipeline/raw_to_bronze/` — transcribe videos, parse chats
- `pipeline/bronze_to_silver/` — clean and normalize
- `pipeline/silver_to_gold/` — produce final Documents for pgvector ingestion

All pipeline directories are currently empty (scaffolded).

## Data Sources

- **Training videos** (`storage/raw/video/`): .MOV files of lock installation, troubleshooting, customer service SOP
- **LINE chat logs** (`storage/raw/line_chat/`): Customer service conversation CSVs
- **YouTube tutorials** (`storage/bronze/youtube/`): AI-99 lock setup guides in Markdown

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

## RAG Document Requirements

Documents destined for pgvector must use `langchain_core.documents.Document` with mandatory metadata fields:
- `brand` (str) — product brand, or `"general"`
- `model` (str) — product model
- `category` (str) — e.g. `"setup"`, `"troubleshoot"`, `"warranty"`, `"specification"`
- `source` (str) — original source filename

Chunking: use `RecursiveCharacterTextSplitter` with chunk_size 500-800, overlap 10%.

## Key Specs

- **Embedding model**: Vertex AI `text-embedding-004` (768 dimensions)
- **Vector DB**: pgvector via `langchain-postgres` with `psycopg` async
- **Integration**: New knowledge bases are registered via `config.toml` `[[databases]]` blocks in the main app repo — no code changes needed

## Environment Variables (in .env)

- `PG_VECTOR_URI` — PostgreSQL connection string
- `VERTEX_PROJECT_ID` / `VERTEX_LOCATION` — GCP credentials for embeddings
