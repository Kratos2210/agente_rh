# Agente de Selección de Talento (`agente_rh`)

Agente que conduce una **entrevista conversacional por Telegram** (luego WhatsApp), **evalúa** cada
respuesta del candidato contra los criterios de la vacante y genera un **scorecard con semáforo
(🟢/🟡/🔴) + recomendación** para el reclutador.

- **Cerebro**: LangGraph (estado durable por candidato) + LLM compatible-OpenAI (Groq/Qwen3 o AI Gateway).
- **Datos**: Supabase (Postgres) — local vía Docker o en la nube.
- **RAG**: Chroma local + embeddings HuggingFace (responde dudas del candidato sobre el puesto).
- **Canal**: Telegram (polling, cero infra). WhatsApp Cloud API en fase posterior.
- **Reclutador**: reporte por email + dashboard Next.js (en construcción).

## Requisitos
- Python 3.12 + [uv](https://docs.astral.sh/uv/)
- Docker (para Supabase local) + [Supabase CLI](https://supabase.com/docs/guides/cli)
- Un bot de Telegram (token de [@BotFather](https://t.me/BotFather))
- API key de un LLM compatible-OpenAI (p. ej. Groq)

## Puesta en marcha

```bash
# 1) Dependencias
uv sync --extra dev

# 2) Base de datos local (Supabase) — aplica solo las migraciones de supabase/migrations/
supabase init          # primera vez (genera supabase/config.toml)
supabase start         # levanta Postgres + Studio + API; imprime las keys y URLs
supabase db reset      # aplica 0001_init.sql + 0002_seed_demo.sql (vacante demo)

# 3) Configuración
cp .env.example .env
#   Pega: SUPABASE_URL / SUPABASE_SERVICE_KEY / DATABASE_URL (de `supabase status`),
#         OPENAI_API_KEY (+ base/model), TELEGRAM_BOT_TOKEN, y (opcional) SMTP_*.

# 4) Probar el cerebro sin canal ni DB (consola)
uv run python scripts/demo.py --alberto   # reproduce la entrevista real de Alberto
uv run python scripts/demo.py             # entrevista interactiva

# 5) Backend + bot de Telegram (polling)
uv run uvicorn api.main:app --port 8000
#   Si TELEGRAM_BOT_TOKEN está seteado, el bot arranca solo. Escribe a tu bot en Telegram.

# 6) Tests
uv run pytest tests/test_interview.py -q
```

## Flujo
Primer contacto (botones Acepto / No interesado) → entrevista pregunta por pregunta con follow-ups si
la respuesta es vaga → el agente responde dudas del candidato sobre el puesto → evaluación por criterio
→ scorecard al reclutador (email + dashboard) → notificación al candidato según la decisión.

## Estructura
Ver `CLAUDE.md` (bitácora y mapa del proyecto).
