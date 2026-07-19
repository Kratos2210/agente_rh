from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Configuración central del proyecto.

    Usa pydantic-settings: lee automáticamente el archivo .env, valida los tipos
    y da errores claros si algún valor está mal formado. Los nombres de atributo
    se mantienen iguales a la versión anterior para no romper a los consumidores
    (qa_chain, vectorstore, app, embeddings).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # LLM (compatible con OpenAI)
    openai_api_base: str = "http://127.0.0.1:1234/v1"
    openai_api_key: str = "lm-studio"
    openai_model: str = "qwen2.5-14b-instruct"

    # 60 s: si el proveedor está caído, una request no se cuelga 3 minutos. El gate
    # de relevancia y el condense ya fallan en abierto, así que un corte rápido del
    # LLM degrada con gracia. Configurable por .env si un modelo lento lo necesita.
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 2

    # Optimización de costos (paso 5): rutear las etapas SIMPLES a un modelo más barato.
    # `schedule` (elegir un número de opción) no necesita el modelo grande. Vacío = todo
    # con `openai_model` (comportamiento actual). El modelo barato usa la MISMA
    # base_url/api_key (mismo proveedor compatible-OpenAI).
    # NOTA: `classify` SE QUITÓ de las etapas baratas (2026-07-04): al pasar a 3 vías
    # (answer|question|offtopic) el modelo chico (llama-3.1-8b) sobre-deflectaba dudas
    # legítimas del puesto (sueldo/horario→offtopic) — no pasa el banco golden. La
    # decisión de alcance necesita el modelo principal (qwen: 10/10). Ver ADR.
    llm_cheap_model: str = ""
    # Etapas ruteadas al modelo barato (CSV). Por defecto la más simple y frecuente.
    llm_cheap_stages: str = "schedule"

    # Caché semántica de las dudas del candidato (paso 5): si una pregunta MUY parecida ya
    # fue respondida para la MISMA vacante (coseno >= semantic_cache_threshold), se devuelve
    # la respuesta cacheada sin llamar al LLM (0 tokens). Las respuestas por vacante son
    # estables (mismo company_info), así que el hit entre candidatos es seguro. Requiere
    # embeddings (como el RAG). Default OFF (convención config-gated).
    interview_answer_cache_enabled: bool = False
    # Almacén SQLite de la caché de dudas (por vacante). Ligero, sin relación con el
    # registry de agente_pro; se crea al primer uso si la caché está activa.
    interview_answer_cache_db: str = "./answer_cache.db"

    # Robustez de la cola de indexación (1 worker):
    #   - index_max_retries: reintentos ante un fallo transitorio antes de marcar error.
    #   - index_timeout_seconds: corte duro de una indexación colgada (PDF gigante/corrupto).
    #   - index_queue_max: tope de tareas pendientes (devuelve 429 si se satura).
    index_max_retries: int = 1
    index_timeout_seconds: int = 1800
    index_queue_max: int = 20

    # LangSmith
    langsmith_tracing: str = "false"
    langsmith_api_key: str = ""
    langsmith_project: str = "agente-rh"
    # Privacidad (Ley 29733): LangSmith es un SaaS en la nube; los prompts llevan PII del
    # candidato (CV, respuestas). Con estos flags en true, a LangSmith solo le llega la
    # ESTRUCTURA de la traza + latencia/tokens, NO el texto de entrada/salida. Default true
    # (seguro por defecto). Poné false SOLO para inspeccionar contenido con data demo/ficticia.
    langsmith_hide_inputs: bool = True
    langsmith_hide_outputs: bool = True

    # Trazas LLM propias (observabilidad O-1): persistir prompt/respuesta POR LLAMADA
    # en `llm_traces` para replay/debug de evaluaciones. Off por default: los prompts
    # llevan respuestas del candidato (PII) — retención/erasure las purgan igual.
    llm_trace_enabled: bool = False
    llm_trace_max_chars: int = 8000

    # Sentry (observabilidad O-6): error tracking config-gated — sin DSN es un no-op.
    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.0  # 0 = solo errores (sin performance tracing)

    # Arize Phoenix (observabilidad LLM, OpenInference/OTel) config-gated — off por
    # defecto. Apunta a un Phoenix SELF-HOSTED (los prompts llevan PII, Ley 29733):
    # `docker run -p 6006:6006 arizephoenix/phoenix` y las llamadas LangChain emiten
    # spans (prompt/respuesta/latencia/tokens) visibles en http://localhost:6006.
    phoenix_enabled: bool = False
    phoenix_endpoint: str = "http://localhost:6006/v1/traces"
    phoenix_project: str = "agente-rh"

    # Snapshot periódico de las métricas HTTP en memoria a la DB (O-6): sobrevive
    # reinicios/redeploys. 0 = apagado. La retención poda snapshots viejos.
    http_snapshot_minutes: int = 60
    http_snapshot_retention_days: int = 14

    # Documentos. El env var se llama PDF_PATHS (alias) pero el atributo es pdf_paths_raw.
    pdf_paths_raw: str = Field(default="", validation_alias="PDF_PATHS")
    pdf_dir: str = "./data"
    persist_directory: str = "./chroma_db"
    embedding_model: str = "intfloat/multilingual-e5-base"

    # Recuperación / chunking
    retrieve_k: int = 8
    final_k: int = 5
    chunk_size: int = 1200        # caracteres por chunk (subir para documentos grandes)
    chunk_overlap: int = 150      # solapamiento entre chunks consecutivos
    semantic_threshold: float = 0.72
    max_chunk_words: int = 220
    overlap_sentences: int = 1
    collection_name: str = Field(default="pdf_rag", validation_alias="CHROMA_COLLECTION")

    # Reranker: "cross" (cross-encoder) o "heuristic" (señales pedagógicas)
    reranker: str = "cross"
    cross_encoder_model: str = "BAAI/bge-reranker-v2-m3"

    # OCR para PDFs escaneados (imagen): si la mayoría de páginas vienen casi sin
    # texto, se corre OCR (ocrmypdf, preserva la metadata de página → citas [p. N]).
    # Requiere binarios de sistema (tesseract + ghostscript); si no están, se omite
    # con un warning y el PDF se indexa tal cual (degrada con gracia).
    ocr_enabled: bool = True
    ocr_language: str = "spa"
    ocr_min_chars_per_page: int = 20

    # Filtro de relevancia previo (ahorro de tokens): antes de armar el prompt RAG
    # completo, una llamada LLM mínima (sin chunks) clasifica si la pregunta PODRÍA
    # responderse con el documento. Si es claramente off-topic, se responde con un
    # rechazo breve predefinido y NO se envía el contexto grande al LLM.
    relevance_gate: bool = True

    # Caché semántico de respuestas (ahorro de tokens): si una pregunta MUY parecida
    # (coseno >= umbral) ya fue respondida para el mismo documento, se devuelve la
    # respuesta cacheada sin llamar al LLM. Umbral alto a propósito (preferimos
    # regenerar antes que servir la respuesta de una pregunta solo parecida).
    semantic_cache: bool = True
    semantic_cache_threshold: float = 0.95

    # Dominio del asistente (parametrizable sin tocar código → "kit RAG para cualquier documento").
    assistant_domain: str = "Gobierno de Datos (DAMA-DMBOK)"
    assistant_terms: str = "metadatos, linaje de datos, calidad de datos, data stewardship, MDM"

    # Bot de Telegram (opcional). Si telegram_bot_token está vacío, el bot no arranca.
    telegram_bot_token: str = ""
    telegram_default_doc_id: str = ""
    # Lista de chat_ids permitidos separados por coma. Vacío = cualquiera puede usar el bot.
    telegram_allowed_users: str = ""
    # Username público del bot (sin @). Con él, el dashboard muestra el deep-link
    # t.me/<bot>?start=<vacancy_id> de cada vacante (routing multi-tenant del bot).
    telegram_bot_username: str = ""
    # chat_id que recibe notificaciones cuando termina la indexación de un documento.
    telegram_notify_chat_id: str = ""
    # Modo webhook (roadmap paso 3). Si telegram_webhook_url está VACÍO el bot corre en
    # POLLING (default; dev local sin infra, un solo consumidor por token). Con la URL
    # pública base del backend (p.ej. https://api.tuempresa.com) el bot registra un webhook
    # y recibe los updates en POST {url}/telegram/webhook — esto desbloquea replicas>1
    # (cada réplica procesa cualquier update) y el rolling/canary del ingress.
    telegram_webhook_url: str = ""
    # Secreto que valida que el POST viene de Telegram (header X-Telegram-Bot-Api-Secret-Token).
    # Si se deja vacío en modo webhook, se deriva determinísticamente del token del bot para
    # que nunca quede sin protección. Telegram exige [A-Za-z0-9_-], 1–256 chars.
    telegram_webhook_secret: str = ""

    # --- Agente de Selección de Talento (agente_rh) ---
    # Supabase: persistencia de negocio (vacantes, candidatos, scorecards).
    supabase_url: str = ""
    supabase_service_key: str = ""
    # Connection string Postgres de Supabase (checkpointer durable de LangGraph).
    # Supabase → Project Settings → Database → Connection string (URI).
    database_url: str = ""

    # --- Auth del dashboard (Fase 0: cimientos SaaS) ---
    # Entorno de despliegue: "development" (default) o "production"/"prod". En producción el
    # arranque exige secretos fuertes (ver api.auth.assert_secure_config).
    environment: str = "development"
    # Secreto para firmar los JWT. DEBE cambiarse en producción (.env JWT_SECRET=...).
    # El valor por defecto es solo para desarrollo local.
    jwt_secret: str = "dev-insecure-change-me-please-set-a-32B+-secret"
    # Rotación grácil del secreto JWT (F5): lista separada por comas de secretos RETIRADOS
    # que todavía se aceptan al validar (nunca al firmar). Al rotar `JWT_SECRET`, mueve el
    # valor anterior aquí durante una ventana ≈ jwt_expire_minutes para no cerrar las
    # sesiones vivas; pasada la ventana, vacíalo. Ver docs/gestion_secretos.md.
    jwt_secret_previous: str = ""
    jwt_expire_minutes: int = 720            # duración del token (12 h)
    # Admin inicial: el backend lo crea al arrancar si la tabla users está vacía.
    admin_email: str = "admin@datawith.ai"
    admin_password: str = "admin1234"        # cambiar tras el primer login
    admin_name: str = "Administrador"

    @property
    def is_production(self) -> bool:
        """True si el despliegue es de producción (exige secretos fuertes al arrancar)."""
        return str(self.environment).strip().lower() in {"production", "prod"}

    # Orígenes permitidos por CORS para el dashboard (CSV). En producción, reemplazar
    # por el dominio real del frontend (audit S5); el default cubre el dev local.
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Servidor MCP (Model Context Protocol): expone herramientas read-only del agente
    # en /mcp (streamable HTTP, mismo proceso) para clientes LLM externos. Requiere el
    # MISMO JWT del dashboard (Authorization: Bearer) y respeta tenant + rol.
    mcp_enabled: bool = False

    # SMTP para enviar el scorecard al reclutador (patrón de qrs).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    recruiter_email: str = ""
    # Destino de equipo para alertas operativas/SLA/presupuesto (paso 3). Fallback usado por
    # los barridos (_budget_sweep/_sla_sweep) cuando el tenant no define su propio
    # `notify_email` — así, en producción multi-réplica, las alertas nunca caen en un buzón
    # personal por olvidar configurarlas por tenant. Vacío = sin fallback (solo per-tenant).
    ops_alert_email: str = ""

    # Pre-filtro de postulantes (sourcing + CV gate).
    # Conector de sourcing: "simulated" (fixture) o, a futuro, "bumeran"/"linkedin".
    sourcing_provider: str = "simulated"
    # Verdict del CV se considera apto si pre_score >= prescreen_pass_min (verdict != reject).
    prescreen_pass_min: int = 60
    # chat_id real de Telegram para probar el flujo en vivo con un postulante importado
    # (Telegram no permite iniciar chat con quien no hizo /start; al contactar un apto sin
    # chat real, se redirige a este chat para que puedas probar el flujo en tu Telegram).
    demo_telegram_chat_id: str = ""
    # Si True (flujo del producto), el sync contacta automáticamente a los aptos nuevos al
    # instante por Telegram (mismo camino idempotente: re-sync no re-contacta a quien ya avanzó).
    # Reversible vía .env (AUTO_CONTACT_ON_PASS=false) para volver al contacto manual con el botón.
    auto_contact_on_pass: bool = True

    # Agendamiento de entrevista (fase 2). Proveedor: "simulated" (default, sin credenciales)
    # o "google" (Calendar free/busy + evento Meet + Sheets con cuenta de servicio).
    scheduling_provider: str = "simulated"
    # Ruta al JSON de la cuenta de servicio de Google (Workspace + Domain-Wide Delegation).
    google_credentials_path: str = ""
    # OAuth de usuario (Gmail personal): client_secret.json (one-time) + token.json (runtime,
    # con refresh token). Lo genera `scripts/google_oauth.py`. Tiene prioridad sobre la cuenta
    # de servicio cuando scheduling_provider="google".
    google_oauth_client_path: str = ""
    google_oauth_token_path: str = ""
    # Google Sheet donde se registran las reuniones (id del documento) + pestaña.
    meeting_sheet_id: str = ""
    meeting_sheet_tab: str = "Reuniones"
    # Estimación de costo: precio por cada 1000 tokens totales (0 = solo conteo, sin costo).
    token_price_per_1k: float = 0.0

    # Gobierno de turnos del bot (auditoría R2): cada mensaje del candidato cuesta llamadas
    # LLM; sin freno, cualquier usuario de Telegram puede quemar el presupuesto.
    #   - cooldown: segundos mínimos entre mensajes procesados del mismo chat (0 = off).
    #   - tope diario: turnos procesados por chat y día (0 = sin tope).
    bot_turn_cooldown_seconds: float = 2.0
    bot_max_turns_per_day: int = 120
    #   - dedupe: un MISMO texto reenviado en estos segundos (doble-tap/reintento de red) se
    #     ignora para no procesarlo como un turno extra contra la pregunta ya avanzada (0 = off).
    bot_turn_dedup_seconds: float = 6.0

    # Documentos del candidato (CV/CUL): tamaño máximo cuyo CONTENIDO se replica en
    # Postgres (audit D2: un PDF de 20 MB ≈ 27 MB de JSON por request de PostgREST).
    # Sobre el umbral el archivo queda solo en disco (uploads/, stored="disk");
    # al migrar a S3/Storage este umbral define qué va a la DB y qué al object store.
    document_db_max_bytes: int = 5 * 1024 * 1024
    # Validación de CONTENIDO del documento recibido (CV/CUL): extrae el texto del PDF y
    # verifica que corresponda al tipo pedido (heurística por palabras clave + desambiguación
    # LLM cuando duda). Ante mismatch el motor rechaza y vuelve a pedirlo. Fail-open: si la
    # extracción falla o está desactivado, se acepta sin validar (comportamiento previo).
    document_content_check_enabled: bool = True
    # Purga de checkpoints LangGraph de conversaciones terminales con más de N días
    # sin actividad (audit D4: crecían sin límite). 0 = desactivada.
    checkpoint_retention_days: int = 30

    # Entrevista: máximo de follow-ups por pregunta ante respuestas vagas.
    interview_max_follow_ups: int = 1
    # RAG en las dudas del candidato (pipeline LLM · auditoría): si True, las preguntas
    # del candidato sobre el puesto se responden recuperando fragmentos de la base de
    # conocimiento (Chroma en persist_directory) además del company_info de la vacante.
    # Carga lazy (torch tarda ~90 s en Intel); sin corpus indexado degrada con gracia
    # a company_info plano. Sembrar el corpus: `uv run python scripts/seed_company_kb.py`.
    interview_rag_enabled: bool = True
    # Colección Chroma de la base de conocimiento de la empresa (la escribe el seed,
    # la lee el retriever de las dudas — separada de la colección clásica de PDFs).
    company_kb_collection: str = "company_kb"
    # Umbrales del semáforo sobre el score total 0-100:
    #   score >= green_min  → 🟢 verde   (avanza)
    #   score >= yellow_min → 🟡 amarillo (revisar)
    #   resto               → 🔴 rojo     (no avanza)
    semaphore_green_min: int = 75
    semaphore_yellow_min: int = 50

    # Offset de página para citar el número IMPRESO en el libro, no el del PDF.
    # PyPDF numera por posición física (la 1ª hoja = pág 1 del lector). El número impreso
    # del libro suele diferir (portada, índice, prólogos). Fórmula:
    #   página_del_libro = página_del_lector_de_PDF + PAGE_OFFSET
    # Calibralo abriendo el PDF en cualquier página del cuerpo y restando el número impreso.
    # Para el DAMA-DMBOK2R actual, el desfase medido es -4 (pág. PDF 41 = pág. libro 37).
    page_offset: int = 0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def parse_pdf_paths(settings: Settings) -> List[Path]:
    candidates: List[Path] = []

    if settings.pdf_paths_raw.strip():
        raw_paths = [p.strip() for p in settings.pdf_paths_raw.split(";") if p.strip()]
        candidates.extend(Path(p) for p in raw_paths)
    else:
        data_dir = Path(settings.pdf_dir)
        if data_dir.exists():
            candidates.extend(sorted(data_dir.glob("*.pdf")))

    return [p for p in candidates if p.exists() and p.suffix.lower() == ".pdf"]
