# Stack — Tecnologías, versiones y gotchas

> Parte de [spec/](README.md) · Última revisión: 2026-07-06 · Fuente: `pyproject.toml`, `frontend/package.json`

## 1. Propósito y alcance

Inventario razonado del stack: qué tecnología cumple qué rol, qué versiones están fijadas y por
qué, y las trampas de plataforma aprendidas (Mac Intel, wheels, tiempos de import).

## 2. Decisiones de diseño

- **Python 3.12 + `uv`** (nunca pip directo): lockfile reproducible (`uv.lock`), instalación
  rápida en CI y Docker (`uv sync --extra dev`).
- **Supabase (Postgres)** para el negocio: Postgres real con API PostgREST + CLI de migraciones
  + RLS, corriendo local en Docker sin costo. El mismo Postgres sirve de checkpointer LangGraph.
- **Next.js 16 (App Router) + Tailwind** para el dashboard: server components, tipado
  compartido en `frontend/src/lib/api.ts`.
- **LangGraph** solo por lo que aporta (checkpointing durable, inyección); **LangChain** para el
  cliente LLM (`ChatOpenAI`) y el pipeline RAG.
- **Chroma local + embeddings HuggingFace** (`intfloat/multilingual-e5-base`): RAG sin servicios
  externos; los PDF/PII nunca salen de la máquina.

## 3. Diseño e implementación

### Backend (deps principales de `pyproject.toml`)

| Dependencia | Rol |
|---|---|
| `fastapi` + `uvicorn` | API, webhook Telegram, montaje MCP |
| `langgraph` + `langgraph-checkpoint-postgres` + `psycopg` | Motor conversacional durable |
| `langchain-openai` / `langchain-core` / `langchain(+community/chroma/huggingface/text-splitters)` | Cliente LLM + RAG |
| `supabase` | Cliente PostgREST del negocio |
| `python-telegram-bot` | Bot (polling y webhook) |
| `pyjwt` + `bcrypt` + `cryptography` | Auth JWT, hash de contraseñas, cifrado Fernet (BYOK) |
| `pydantic-settings` | Configuración tipada desde `.env` |
| `chromadb` + `sentence-transformers` + `transformers` + `torch==2.2.2` + `rank-bm25` | Vector store, embeddings, re-ranker, BM25 |
| `pypdf` + `docx2txt` | Extracción de texto de documentos |
| `google-api-python-client` + `google-auth(-oauthlib)` | Calendar/Meet/Sheets (wheels puros) |
| `mcp` (**pineado <2**) | Servidor Model Context Protocol |
| `sentry-sdk`, `arize-phoenix-otel`, `openinference-instrumentation-langchain` | Observabilidad opcional |
| `httpx` | Cliente HTTP (Telegram API directa, tests) |
| dev: `pytest` | Suite de tests |

### Frontend

Next.js 16 App Router · TypeScript · Tailwind CSS · sin librerías de gráficos (radar SVG a mano).
Comandos: `npm run dev` / `npx tsc --noEmit` / `npm run lint` / `npm run build`.

### Tooling

- `uv run pytest` · `uv run uvicorn api.main:app --port 8000 --reload`.
- Supabase CLI (binario en `~/.local/share/supabase`): `supabase start` / `supabase migration up`.
- Docker + docker-compose; kubeconform para validar manifests.

## 4. Contratos e invariantes

- **`torch==2.2.2` y `onnxruntime<1.21` NO se suben** sin verificar que exista wheel para
  macOS x86_64 (las versiones nuevas dejaron de publicarlo).
- **`mcp<2`**: la 2.0 beta renombra `FastMCP`→`MCPServer` (breaking).
- El cross-encoder configurado es el **liviano** (`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`);
  el default pesado (`BAAI/bge-reranker-v2-m3`) tarda ~5 min/consulta sin GPU.
- En Docker, torch se instala **desde el índice CPU antes que el resto** para no arrastrar CUDA
  (ver `Dockerfile.backend`).

## 5. Patrones reutilizables

- **Fijar pins por plataforma con el porqué en un comentario** (y en este spec): el pin sin
  contexto se "arregla" en el próximo upgrade y rompe.
- **Carga lazy de deps pesadas**: torch tarda ~90 s en importar en CPU → embeddings/re-ranker se
  cargan en el primer uso, nunca en el arranque ni en el request path (`agente/rag.py`,
  encolado de `kb_reindex` sin intento en línea).
- **Preferir wheels puros** para integraciones (google-api-python-client) — cero fricción
  multi-plataforma.
- **Un solo gestor de paquetes** (uv) en dev, CI y Docker: mismo lockfile en los tres.

## 6. Pendientes conocidos

- Revisar los pins Intel si se migra a Apple Silicon o Linux (podrían soltarse).
- El proyecto no declara `build-system` en `pyproject.toml` (solo deps): Docker instala con
  `uv pip install -r pyproject.toml`.

## 7. Trazabilidad

- `pyproject.toml`, `uv.lock`, `Dockerfile.backend`, `frontend/package.json`.
- Gotchas documentados también en `CLAUDE.md` (sección "Gotchas (Mac Intel)").
