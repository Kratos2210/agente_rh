# CLAUDE.md — Agente de Selección de Talento (`agente_rh`)

Contexto del proyecto para futuras sesiones. Leer antes de tocar el código.

## Qué es
Agente de selección de talento (FLUJO 1) que conduce una **entrevista conversacional por
Telegram** (luego WhatsApp), **evalúa** cada respuesta del candidato contra los criterios de la
vacante y genera un **scorecard con semáforo (🟢/🟡/🔴) + recomendación** para el reclutador.
Inspirado en "SofIA" de Sifrah (la entrevista que le hicieron a Alberto el 16/06/2026).

Flujo: el reclutador carga una vacante (requisitos, preguntas, criterios) → primer contacto con
botones **Acepto / No interesado** → entrevista pregunta-por-pregunta con follow-ups si la respuesta
es vaga → el agente responde dudas del candidato sobre el puesto (RAG) → evaluación → scorecard al
reclutador (email + dashboard) → notificación al candidato según decisión.

## Origen
**Fork de `../agente_pro`** (Next.js + FastAPI + bot Telegram + motor RAG + Docker). Se podó lo que
no aplica: quiz, voz (Piper/Whisper), OCR, upload multi-doc. Se conserva el motor RAG (Chroma + HF
embeddings) para responder dudas del candidato sobre el puesto.

## Stack
- **Python 3.12 + uv** (gestor — usar `uv`, nunca pip directo). `uv sync --extra dev`.
- **Cerebro**: LangGraph (máquina de estados de la entrevista, checkpointer durable en Postgres).
- **LLM**: compatible-OpenAI vía `src.qa_chain.build_llm` (Groq/Qwen3 o AI Gateway). `.env` OPENAI_*.
- **Persistencia de negocio**: **Supabase (Postgres)** — vacantes, candidatos, respuestas, scorecards.
- **RAG (base de conocimiento de la empresa)**: Chroma local + embeddings `intfloat/multilingual-e5-base`
  + hybrid search (BM25 + vectorial) + cross-encoder liviano. Heredado de agente_pro (`src/`).
- **Canal**: Telegram (modo polling en el lifespan de FastAPI; cero infra). WhatsApp Cloud API después.
- **API**: FastAPI (`api/main.py`). **Dashboard**: Next.js 16 (`frontend/`).
- **Reporte**: email SMTP al reclutador (patrón de `../qrs`).

## Estructura
- `api/main.py` — FastAPI: lifespan (arranca bot Telegram), CORS, health; endpoints reclutador (Fase 5).
- `api/telegram_bot.py` — bot Telegram (se reescribe en Fase 3: turno de entrevista + botones inline).
- `agent/` — **NUEVO** cerebro LangGraph: `state.py`, `graph.py`, `nodes.py`, `prompts.py`.
- `evaluation/` — **NUEVO** `scorer.py` (respuesta vs criterio) + `scorecard.py` (semáforo + recomendación).
- `channels/` — **NUEVO** `base.py` (interfaz Channel), `telegram.py`, `whatsapp.py` (stub Fase 6).
- `db/` — **NUEVO** `client.py` (Supabase) + `repositories.py` (CRUD).
- `notifications/email.py` — **NUEVO** reporte SMTP al reclutador.
- `supabase/migrations/` — **NUEVO** esquema SQL + seed de la vacante demo.
- `src/` — reutilizado de agente_pro: `config.py`, `qa_chain.py` (`build_llm`, RAG), `classifier.py`
  (`suggest_questions`), `vectorstore.py`, `reranker.py`, `embeddings.py`, `semantic_cache.py`,
  `registry.py`, `logging_config.py`, `observability.py`.
- `frontend/` — base Next.js de agente_pro (se adapta a dashboard reclutador en Fase 5).

## Config (`.env`) — ver `.env.example`
- `OPENAI_*` (LLM), `SUPABASE_URL/SERVICE_KEY`, `DATABASE_URL` (checkpointer LangGraph),
  `TELEGRAM_BOT_TOKEN`, `SMTP_*` + `RECRUITER_EMAIL`, `INTERVIEW_MAX_FOLLOW_UPS`,
  `SEMAPHORE_GREEN_MIN`/`SEMAPHORE_YELLOW_MIN`, y bloque RAG (`PERSIST_DIRECTORY`, `EMBEDDING_MODEL`,
  `CROSS_ENCODER_MODEL`, chunking). Settings en `src/config.py` (pydantic-settings).

## Gotchas (Mac Intel)
- **torch fijado a 2.2.2** y **onnxruntime <1.21** en `pyproject.toml`: las versiones nuevas dejaron
  de publicar wheels para macOS x86_64. No subir sin verificar wheel Intel.
- torch tarda ~90 s en importar (sin GPU). Cross-encoder: usar el **liviano** (ya configurado).

## Estado / bitácora
- **2026-06-17 — Fase 0 (Fork & poda)**: forkeado de agente_pro; podado quiz/voz/OCR/upload; `git init`
  propio (el workspace estaba enraizado en HOME); `pyproject.toml` con uv (núcleo + RAG, pins Intel);
  `config.py` extendido (Supabase/SMTP/entrevista); `api/main.py` reescrito a skeleton limpio;
  estructura `agent/ evaluation/ channels/ db/ notifications/ supabase/migrations/` creada.
- **2026-06-17 — Fase 1 (Datos)**: `supabase/migrations/0001_init.sql` (7 tablas) + `0002_seed_demo.sql`
  (vacante "Analista de Automatizaciones e IA" con las 6 preguntas reales). `db/client.py` + `db/repositories.py`.
  DB elegida: **Supabase local (Docker)** — la CLI aplica las migraciones de `supabase/migrations/`.
- **2026-06-17 — Fase 2 (Motor + evaluación)**: `agent/{llm,state,prompts,nodes,graph}.py` + `evaluation/{scorer,scorecard}.py`.
  Grafo LangGraph de un nodo con runner durable (Memory para tests, Postgres para prod). LLM inyectable
  (fake en tests). `scripts/demo.py` (--alberto / interactivo). **tests/test_interview.py 5/5 verde**.
- **2026-06-17 — Fase 3 (Telegram, código)**: `channels/{base,telegram,whatsapp}.py` + `agent/service.py`
  (núcleo agnóstico que proyecta el estado a Supabase y notifica al reclutador) + `api/telegram_bot.py`
  reescrito (handlers PTB + botones inline Acepto/No interesado, trabajo síncrono en hilo).
  **Pendiente verificar en vivo** (necesita Supabase corriendo + token Telegram + key LLM):
  PostgresSaver.setup(), upsert on_conflict de supabase-py, polling del bot.
- **2026-06-17 — Fase 4 (Reporte)**: `notifications/email.py` (scorecard HTML+texto al reclutador, SMTP
  patrón qrs; ya cableado como notifier en el bot) + `notifications/candidate.py` (notificación
  avanza/rechaza al candidato vía API HTTP de Telegram, para que la dispare el dashboard). Render verificado.
- **2026-06-17 — Verificación en vivo (backend)**: Supabase local (Docker, CLI binario en
  `~/.local/share/supabase`; storage+analytics+vector DESACTIVADOS en config.toml por health checks
  fallidos en Mac Intel). Migración `0003_grants.sql` (service_role necesita GRANTs aunque saltee RLS).
  Service key = SERVICE_ROLE_KEY (JWT). Flujo completo verificado contra DB real (checkpointer Postgres
  + proyección a Supabase). **Demo con Groq real (qwen3-32b): scorecard 90/100 🟢 con justificaciones
  reales y follow-up real.** Bot Telegram arrancado en polling (health ok). `build_default_llm` ya no
  importa qa_chain (arranque sin torch).
- **2026-06-17 — Fase 5 (Dashboard)**: endpoints reclutador en `api/main.py` (vacantes CRUD, candidatos
  con semáforo, detalle scorecard, decisión avanzar/rechazar→notify). Frontend Next.js 16: `lib/api.ts`,
  `components/Shell.tsx`, `app/page.tsx` (vacantes + alta), `app/vacantes/[id]` (candidatos+semáforo),
  `app/candidatos/[id]` (scorecard+transcripción+avanzar/rechazar). Podados componentes RAG/quiz/voz.
  3 rutas compilan (HTTP 200), detalle devuelve scorecard real. **MVP end-to-end completo.**
- **2026-06-17 — Detalle del puesto**: migración `0004_position_details.sql` (columna `details_message`
  + aviso real de Sifrah); el bot lo envía tras *Acepto* y el dashboard lo muestra formateado (parser sin emojis).
- **2026-06-17 — Pre-filtro automático de postulantes (sourcing + CV gate + revalidación + métricas)**:
  migraciones `0005_prescreening.sql` (candidates: source/cv_profile/prescreen/documents; vacancy_questions:
  cv_field; tabla `llm_usage`) + `0006_seed_question_cv_fields.sql`. `integrations/sourcing.py`
  (SimulatedConnector + fixture Bumeran) → `evaluation/prescreen.py` (gate del CV con fallback heurístico) →
  `agent/sourcing_service.py` (importa, pre-filtra, contacta a los aptos; Telegram solo entrega a chats que
  hicieron /start, resto = contacto simulado). Motor: fase `awaiting_docs` (pide **CUL** real por Telegram,
  `_on_document` lo descarga a `uploads/`) + revalidación (preguntas con `cv_field` se reformulan "Según tu
  CV: «…»") + `cv_context` en el scoring. Tokens: `MeteredLLM` + `complete_staged` por etapa → `llm_usage` →
  endpoints `/metrics`. Frontend: botón "Sincronizar postulantes", panel de embudo+tokens, dos puntajes por
  candidato (gate CV + entrevista), secciones Perfil CV/Pre-filtro/Documentos, métricas globales en la home.
  **Verificado contra DB real + Groq**: sync 3 importados → 1 apto (Daniela 92 pass→invited) / 2 reject;
  tokens registrados (prescreen 3871). **38/38 tests verde; tsc OK; 3 rutas HTTP 200.**
- **2026-06-17 — Contacto controlado + estados por fase**: el `sync` ya NO auto-contacta; deja aptos en
  `prescreen_passed` ("Apto · por contactar") con **idempotencia** (re-sync no retrocede ni re-contacta a
  quien ya pasó de fase). Nuevo `auto_contact_on_pass` (config, default off). `InterviewService.initiate_contact`
  + endpoint `POST /api/candidates/{id}/contact` (idempotente: solo desde `prescreen_passed`, 409 si no;
  resuelve chat real o redirige a `DEMO_TELEGRAM_CHAT_ID` liberándolo de otro candidato; envía saludo +
  botones por el bot vivo vía `run_coroutine_threadsafe`; marca `invited`). Conector ya no fabrica chats
  (el redirect demo se hace al contactar). Frontend: badge de fase + botón **Contactar** en aptos (lista) y
  **stepper de fases** + botón "Contactar por Telegram" idempotente (detalle). **41/41 tests; tsc OK;
  verificado: sync deja Daniela `prescreen_passed` (contacted=0), contactar a no-apto → 409.**
- **2026-06-17 — CUL diferido (fuera del flujo de aceptación)**: se quitó el paso del Certificado Único
  Laboral del consentimiento (queda como mejora futura "más adelante"). Al **Acepto** va directo a
  detalle del puesto + primera pregunta; la revalidación se hace con la serie de preguntas (cv_field).
  Removido: fase `awaiting_docs`, prompts CUL, handler de documentos de Telegram, plumbing de `document`
  en service/graph/channels/state; sección "Documentos" del frontend. `documents` (columna) y
  `add_candidate_document` quedan latentes para reusar luego. **40/40 tests; tsc OK.**
- **2026-06-17 — Fix transcripción + felicitaciones al cumplir perfil**: (1) BUG: las conversaciones se
  identifican por `langgraph_thread_id` (=canal:chat) único; el override de demo reasignaba el chat a otro
  candidato cuyo thread ya tenía conversación, así los mensajes se atribuían al ocupante anterior (Hola) y
  el nuevo (Daniela) quedaba sin conversación → transcripción 0. Fix: `_claim_chat` ahora **purga** la
  conversación + checkpoint del thread reasignado (`repositories.delete_thread_conversations` +
  `delete_langgraph_checkpoint`) antes de asignarlo, garantizando atribución correcta. (2) `_finalize`:
  si el scorecard es **verde** (cumple perfil), envía `QUALIFIED_NEXT_STEPS` (felicitaciones + anuncio de
  pedir hoja de vida/CUL) y luego `CLOSING_THANKS`. **Verificado por simulación service-level (LLM fake,
  sin Telegram): conversación de Daniela con 19 mensajes, revalidación + felicitaciones + cierre, verde 92.**
- **2026-06-17 — Recepción de documentos (CV + CUL) post-calificación**: al cumplir el perfil (verde),
  `_finalize` pasa a fase `awaiting_docs` y pide en orden la **hoja de vida (CV)** y el **Certificado
  Único Laboral (CUL)** (`DOC_SEQUENCE`). El candidato sube el PDF por Telegram (`_on_document` lo descarga
  a `uploads/`), el motor lo marca (`save_document`) y el servicio lo persiste en `candidates.documents`
  (`add_candidate_document`); se puede *omitir*. Al recibir/omitir ambos → `CLOSING_THANKS` + `finished`.
  El scorecard se guarda y notifica apenas existe (no espera los documentos). Frontend: sección
  "Documentos" en el detalle (CV/CUL recibido/pendiente). **42/42 tests; tsc OK; verificado por simulación:
  CV+CUL guardados en el candidato, secuencia y cierre correctos.**
- **2026-06-17 — Descarga de documentos desde el dashboard**: `_persist_save_document` ahora guarda
  `local_path`; nuevo endpoint `GET /api/candidates/{id}/documents/{tipo}` que sirve el PDF con
  `FileResponse` (streaming, baja memoria), con fallback por `filename` bajo `uploads/**` para docs
  previos y guarda anti path-traversal (debe estar dentro de `uploads/`). Frontend: el nombre del
  CV/CUL en "Documentos" es un enlace que abre el PDF en pestaña nueva (`api.documentUrl`). **Verificado:
  CV/CUL → 200 application/pdf (PDF válido), tipo inexistente → 404; tsc OK.**
- **2026-06-17 — Gráfico de radar (perfil por criterio)**: en el detalle del candidato, un radar SVG
  (sin librerías) grafica el puntaje 0-100 de cada criterio del scorecard (datos que vienen de las
  respuestas por Telegram), con polígono punteado del **umbral para avanzar** (`green_min`). Backend:
  `get_candidate_detail` devuelve `thresholds` (de `semaphore_thresholds`). Ejes numerados 1..N que
  mapean a las tarjetas "Evaluación por criterio" (ahora con badge numérico). **tsc OK; render 200.**
- **2026-06-17 — Etiquetas (palabras clave) en el radar**: migración `0007_question_labels.sql` (columna
  `label` en `vacancy_questions` + seed demo: Formación/Experiencia/Disponibilidad/Dominio técnico/Caso
  real/Salario). `label` se propaga por el motor (`_to_qspec`→QuestionSpec→AnswerRecord→`_per_criterion`)
  y `get_candidate_detail` hace **backfill** por posición para scorecards ya guardados. Radar muestra la
  palabra clave en cada vértice (anclaje izq/centro/der, viewBox más ancho) y las tarjetas de criterio
  la usan como título. **tsc OK; API devuelve labels; render 200.**
- **2026-06-17 — Auto-contacto programado + página Configuración**: migración `0008_app_settings.sql`
  (tabla `app_settings` key/value + seed `auto_contact` {enabled:false, times:[11:00,15:00],
  timezone:America/Lima}). `repositories.get_app_setting/set_app_setting`. `api/main.py`: endpoints
  `GET/PUT /api/settings/auto-contact`; `_scheduler_loop` (asyncio en lifespan, tick 30 s, lee config de
  DB cada tick, dispara una vez por slot/día, corre el batch en hilo) que contacta a los `prescreen_passed`
  de vacantes abiertas reusando `_contact_candidate` (idempotente). `_now_local` (zoneinfo con fallback
  UTC-5). Frontend: ícono ⚙ en el header → `app/configuracion/page.tsx` (toggle + horarios + zona).
  Default **desactivado**. **tsc OK; GET/PUT 200; hora Lima OK; sin dependencias nuevas.**
- **2026-06-19 — Manejo de inactividad del candidato (recordatorios + cierre "No respondió")**: migración
  `0009_inactivity.sql` (columnas `last_activity_at` + `reminders_sent` en `conversations` + seed
  `app_settings.inactivity` {enabled:true, reminder_minutes:2, max_reminders:2}). Motor: `pending_timeout` +
  `closed_reason` en el estado; `runner.send(timeout=True)` → `nodes.handle_turn`/`_handle_timeout`
  (interviewing→`closed`+`closed_reason=no_response`+`CLOSING_INACTIVITY`; awaiting_docs→`finished`+
  `CLOSING_DOCS_PENDING`, sin penalizar). Prompts `REMINDER_INTERVIEW/REMINDER_DOCS/CLOSING_*`.
  `service.finalize_inactive(thread_id)` + reseteo de `last_activity_at`/`reminders_sent` en `process()` e
  `initiate_contact()`; `_sync_business` mapea `no_response` vs `declined`. `repositories.list_conversations_by_states`.
  `api/main.py`: helper puro **`_inactivity_decision`** (wait/remind/finalize), `_inactivity_sweep` (barrido en
  el `_scheduler_loop`, tick 30 s, recuerda y cierra; reusa `_bot_send` vía `run_coroutine_threadsafe`),
  endpoints `GET/PUT /api/settings/inactivity`. Frontend: `InactivityConfig` + tarjeta "Inactividad" en
  Configuración; estado `no_response` ("No respondió", rojo, off-path) en `statusLabel`/`phaseMeta`.
  Default **activado**. **49/49 tests verde (test_inactivity.py nuevo + 2 timeouts en test_interview.py); tsc OK.**
  Pendiente: aplicar `0009` (`supabase migration up`) y verificación en vivo por Telegram (Docker estaba abajo).
- **2026-06-19 — Fase 2: Agendamiento de entrevista (RR.HH. "Continuar" → coordinar horario → reunión
  Google) + roster de reclutadores**: migración `0010_scheduling.sql` (tabla **`recruiters`** [roster:
  name/email/company/role/phone/telegram_chat_id/calendar_id/active] + seed Grace Mendieta/SIFRAH;
  `vacancies.recruiter_id` FK + `meeting_duration_minutes`; tabla `meetings` 1×conversación; seed
  `app_settings.scheduling` {enabled, provider:simulated, slot_minutes:45, work_days, work 09-18,
  America/Lima, horizon 7, options 3}; grants). **`integrations/scheduling.py`**: patrón Protocol+factory
  como sourcing — `SimulatedScheduler` (default, sin credenciales: reclutador libre, Meet falso, "Sheet"
  local en uploads/), `GoogleScheduler` (lazy: Calendar freebusy + evento `conferenceData` Meet + Sheets
  append, cuenta de servicio), `get_scheduler(settings)`, helper **puro** `compute_free_slots`. Deps:
  `google-api-python-client`+`google-auth` (wheels puros, OK Intel). Motor: fases `PHASE_SCHEDULING/SCHEDULED`,
  `proposed_slots/meeting_slot/recruiter` en estado, `graph.send(start_scheduling=, recruiter=)` →
  `nodes.start_scheduling` (saludo firmado + 2-3 opciones) y rama de elección (`parse_slot_choice` en
  `scorer.py`, LLM+heurística) → `PHASE_SCHEDULED`. Servicio: `initiate_scheduling` (freebusy→slots→propuesta),
  `_finalize_scheduling` (crea reunión + Sheets + `save_meeting` + confirma por Telegram + `notify_meeting`),
  `_sync_business` mapea scheduling/scheduled; `InterviewService(scheduler, settings, notify_meeting)` cableado
  en `telegram_bot.py`. Email: `send_meeting_email` (candidato+reclutador). Inactividad: el barrido incluye
  `scheduling` (recuerda con `SCHEDULING_REMINDER`, **no** auto-cierra). API: `decision` advance→
  `initiate_scheduling`+envío por bot vivo (si `scheduling.enabled`); `GET /api/candidates/{id}/meeting`;
  CRUD `GET/POST/PUT /api/recruiters`; `GET/PUT /api/settings/scheduling`; `get_vacancy` devuelve la cartilla
  del reclutador; `VacancyIn` con `recruiter_id`+`meeting_duration_minutes`. Frontend: tipos `Recruiter/Meeting/
  SchedulingConfig`, `CvProfile.email`; **panel principal** con roster de RR.HH. (cartillas + alta) y selector
  de reclutador en alta de vacante; detalle del candidato botón "Continuar → Agendar entrevista" + Card
  Agendamiento (fecha+enlace); estados `scheduling/scheduled` en statusLabel/phaseMeta/stepper; tarjeta
  "Agendamiento" en Configuración. Email del candidato tomado del CV (`cv_profile.email`, añadido al fixture).
  **56/56 tests verde (test_scheduling.py nuevo: compute_free_slots, parse_slot_choice, SimulatedScheduler,
  fase del motor); tsc OK.** Pendiente: aplicar `0010` y verificación en vivo (Docker abajo); para Google real:
  `scheduling_provider=google` + `google_credentials_path` + compartir Calendar/Sheet con la cuenta de servicio.
- **2026-06-19 — Levantado en vivo + datos de contacto en agendamiento + visibilidad del reclutador**:
  Supabase local arriba (Docker), migraciones `0010`/`0011` aplicadas; backend (uvicorn) + dashboard
  (Next.js) corriendo; flujo de agendamiento verificado service-level contra DB real con `SimulatedScheduler`.
  Migración `0011_meeting_contacts.sql` (meetings: `candidate_phone`, `recruiter_phone`, `recruiter_name`).
  Fixture Bumeran + `sourcing.py` ahora traen `email`+`phone` al `cv_profile`. `_finalize_scheduling` arma la
  **descripción del evento** con nombre/correo/teléfono de candidato y reclutador, persiste esos campos en
  `meetings` y los añade a la fila de Sheets; `send_meeting_email` y el aviso Telegram al reclutador incluyen
  los teléfonos. `GET /api/vacancies` enriquece cada vacante con su `recruiter`. Frontend: cartilla de RR.HH.
  con **email+teléfono** e **edición** (`api.updateRecruiter`) + input teléfono; vacante muestra el
  **responsable** (lista + cartilla en detalle); card de reunión muestra correos+teléfonos de ambos.
  Reclutador demo Grace actualizado a `datawithia.oficial@gmail.com` + tel. **56/56 tests; tsc OK; endpoints
  y reunión verificados con emails reales (candidato albertoruizasto@gmail.com).**
- **2026-06-19 — Auto-contacto del apto = flujo del producto + regla de horario laboral (9–18, L–V)**:
  `auto_contact_on_pass` ahora **`True` por defecto** (`src/config.py`): al sincronizar, el sync contacta al
  instante por Telegram a los aptos nuevos (idempotente; reversible vía `.env`). `.env.example` documenta el
  bloque sourcing/auto-contacto + agendamiento. **Regla del negocio**: solo se contacta en horario laboral.
  Helper puro `_within_working_hours(now, work_days, work_start, work_end)` + `_is_working_now(settings)` (lee
  la ventana de `app_settings.scheduling`: 9–18, L–V, America/Lima). Guardia en `_contact_candidate` (punto
  único que usan sync inmediato, scheduler y botón manual): fuera de hora devuelve `contacted:false` y deja al
  candidato en `prescreen_passed` sin marcar `invited`. Barrido nuevo en `_scheduler_loop` (gated por
  `auto_contact_on_pass`): durante horario laboral contacta a los `prescreen_passed` pendientes (recoge a los
  que un sync fuera de hora dejó diferidos). **58/58 tests (test_contact.py +2: dentro/fuera de ventana).**
  **Verificado en vivo contra DB real + Groq + bot**: sync → `{imported:3,passed:1,rejected:2,contacted:1}`,
  Daniela→`invited` con `sendMessage` 200; fuera de hora (ventana 00:00–00:01) → `/contact` devuelve "Fuera de
  horario laboral" y queda `prescreen_passed`; al restaurar 9–18 el barrido la contactó sola en ~25 s.
- **2026-06-30 — Guía técnica/funcional dentro del dashboard**: la guía end-to-end (docs/guia.html, 17
  secciones: visión funcional, arquitectura, cerebro LangGraph, un turno, evaluación, sourcing,
  agendamiento, LLM & prompts, APIs, **datos**, config, librerías) pasó de HTML suelto a **página nativa
  Next.js** en `frontend/src/app/guia/page.tsx` (ruta `/guia`, entrada "Guía" en el nav del `Shell`). Se
  porta con `scripts`-scratch: extrae el `<style>` y lo **aísla bajo `#guia-doc`** (parser de llaves, sin
  fugas al resto de la app; vars `--edge/--muted/--accent` locales), retematizado al índigo de la app, y
  embebe el body con `JSON.stringify` (server component + `dangerouslySetInnerHTML`, doc de solo lectura).
  El TOC sticky parquea a `top:57px` (debajo de la barra del Shell). **Sección "Base de datos" reescrita**:
  motor PostgreSQL/Supabase (Docker local ↔ cloud), **doble persistencia** (① negocio vía cliente Supabase
  `db/client.py`+`repositories.py`, service_role+GRANTs 0003; ② estado LangGraph vía `DATABASE_URL`+
  `PostgresSaver.setup()`, thread=`canal:chat`), esquema por 12 migraciones, tabla de las 11 tablas de
  negocio con propósito/escritor. `public/guia.html` eliminado (reemplazado por la ruta). **tsc OK; `/guia`
  → 200, `/guia.html` → 404; CSS verificado aislado.**

- **2026-06-30 — Auditoría de robustez + Fase 0 (cimientos SaaS)**: auditoría completa del proceso
  (3 exploraciones paralelas + verificación) con roadmap por fases; entregable en el archivo de plan.
  **Implementada la Fase 0** (objetivo SaaS multi-empresa): (1) **Multi-tenancy** — migración
  `0013_auth_tenancy.sql` (tablas `tenants` + `users`; `tenant_id` en `vacancies`/`recruiters` con
  backfill al tenant `default`; índice `candidates(status)`; grants). (2) **Auth + RBAC** self-contained
  (sin infra extra) — `api/auth.py`: hash **bcrypt** + **JWT** (PyJWT), roles jerárquicos
  viewer<recruiter<admin, dependencias `get_current_user`/`require_role`, `authenticate` y
  `ensure_default_admin` (crea el admin del `.env` al arrancar si no hay usuarios). Endpoints
  `POST /api/auth/login` + `GET /api/auth/me`; **los 24 endpoints del dashboard** ahora exigen token y
  se **aíslan por tenant** (`_require_vacancy_in_tenant`/`_require_candidate_in_tenant`; mutaciones de
  settings/recruiters = admin, operativas = recruiter+). (3) **Scheduler con lock distribuido** — advisory
  lock de Postgres (`pg_try_advisory_lock`, key 704127) en `_scheduler_loop`: solo un proceso/réplica
  ejecuta auto-contacto + inactividad; standby con takeover si el activo muere; sin `DATABASE_URL` cae a
  modo sin-lock. Config: `jwt_secret`/`jwt_expire_minutes`/`admin_*` en `src/config.py` + `.env.example`.
  Deps: `pyjwt`+`bcrypt`. Frontend: `lib/auth.ts` (sesión en localStorage), `api.ts` adjunta `Bearer` y
  redirige al login en 401, página `/login`, guard de sesión + logout en `Shell`. **Tests: `test_auth.py`
  (16) — 76/76 verde; tsc OK.** **Verificado en vivo** (Supabase local + instancia temporal `:8001`):
  `0013` aplicada + backfill; admin bootstrapeado; sin token→401, login→200, con token→200, credenciales
  malas→401; RBAC viewer→403 en PUT/200 en GET; aislamiento: tenant ACME no ve ni lee (404) las vacantes
  del tenant default. **PENDIENTE**: reiniciar el backend `:8000` en vivo (corre código pre-auth) para
  que la protección aplique; `JWT_SECRET` real en `.env` de prod. **Limitación conocida (Fase 0.1)**: los
  `app_settings` (auto_contact/inactivity/scheduling) siguen siendo **globales** (no por-tenant); el
  scheduler es process-wide. Convertir settings a por-tenant queda para la siguiente iteración.

- **2026-06-30 — Fase 1 (No perder candidatos · confiabilidad)**: (1) **Outbox durable** — migración
  `0014_outbox.sql` (tabla `outbox`: kind/payload/status/attempts/next_attempt_at/last_error + índices +
  grants). `notifications/outbox.py`: `deliver(kind,payload)` intenta el envío en línea y **encola en
  fallo**; `drain(settings,now)` (en el scheduler, bajo el advisory lock) reintenta lo vencido con
  **backoff exponencial** (1m→6h, `backoff_seconds`/`next_state_after_failure` puras) y marca `failed`
  (**dead-letter**) al agotar `max_attempts` (6). Handlers que **lanzan**: `email.send_email` +
  `candidate.post_telegram` (nuevas primitivas raising; `email.py` extrae `build_scorecard_email`/
  `build_meeting_email`, `candidate.py` `can_send_telegram`/`post_telegram`). Call sites cableados:
  `_build_notifier`/`_build_meeting_notifier` (telegram_bot) y `decide_candidate` (main) ahora pasan por
  el outbox → **fin del fire-and-forget silencioso** (audit #4/#6). Repos: `enqueue_outbox`/
  `list_due_outbox`/`update_outbox`/`count_outbox_by_status`. (2) **Reconciliación** (#7) —
  `_reconciliation_sweep` en el scheduler: alerta estructurada de dead-letters, reuniones sin `meet_link`
  (Calendar falló, `list_meetings_without_link`) y coordinaciones de horario estancadas sin reunión
  (`_reconcile_scheduling_stuck`, pura). (3) **Documentos** (#5, parte seguridad) — `channels/documents.py`
  (`sanitize_filename` anti-traversal, `validate_document` solo-PDF + límite 20 MB); `_on_document` valida
  antes de descargar y blinda el destino dentro de `uploads/`. **Tests: `test_outbox.py`(9)+`test_documents.py`(6)
  → 90/90 verde.** **Verificado en vivo** (Supabase real): `0014` aplicada; smoke de outbox `drain` →
  `{sent:1, dead:1}` (dead-letter attempts=6 con error, ok→sent); backend reiniciado con el drenaje del
  outbox + reconciliación corriendo cada tick del scheduler. **PENDIENTE (Fase 1 restante)**: almacenamiento
  **durable** de CVs (hoy `uploads/` local se pierde en redeploy) — requiere Supabase Storage/S3 (deshabilitado
  local en Intel); dejar como config-gated. Posible mejora de observabilidad: exponer el estado del outbox
  (pending/failed) en el dashboard (Fase 3).

- **2026-06-30 — Fase 2 (Integridad y cumplimiento)**: migración `0015_integrity_audit_consent.sql`
  (`scorecards.review_required`, tabla `audit_log`, `candidates.consent_at`, seed `app_settings.retention`).
  (1) **Integridad de la evaluación** (#10/#11): `scorer.py` — `is_meaningful_answer` (guard de respuesta
  vacía/solo-símbolos en `nodes._handle_interview`: repregunta con `EMPTY_ANSWER_NUDGE` sin gastar
  follow-up ni llamar al LLM), `sanitize_answer_for_prompt` (quita delimitadores + cap 4000 chars) y el
  prompt `EVALUATE_ANSWER_PROMPT` ahora encierra la respuesta del candidato entre `<<<respuesta>>>…<<<fin>>>`
  con marco **anti-inyección**; `EvalResult.low_confidence` (True en el fallback por fallo del LLM) →
  `AnswerRecord.low_confidence` → `build_scorecard` calcula `review_required` (+ `low_confidence` por
  criterio). Frontend: aviso "⚠ Requiere revisión humana" en el veredicto + tipos `review_required`/
  `low_confidence`. (2) **Auditoría** (#8): `repositories.add_audit_log`/`list_audit_log`; helper `_audit`
  (no rompe la acción) cableado en decide/contact/vacancy CRUD/recruiter CRUD/settings PUT/erasure;
  `GET /api/audit` (admin). (3) **Consentimiento + retención** (Ley 29733): `consent_at` se sella una vez al
  aceptar (`_sync_business`); `GET/PUT /api/settings/retention` (admin); `_retention_sweep` en el scheduler
  (anonimiza PII de descartados > N días: nombre/chat/CV/documentos + borra transcripción; `_retention_purgeable`
  puro; default OFF); **erasure** `DELETE /api/candidates/{id}` (admin, auditado, cascada + checkpoint).
  **Tests: `test_integrity.py`(9) → 99/99 verde; tsc OK.** **Verificado en vivo** (Supabase real): `0015`
  aplicada; audit write/read; `/api/audit` 200, `/api/settings/retention` 200 (default OFF), erasure recruiter
  → 403; FakeLLM de `test_interview` ajustado al nuevo formato del prompt. **PENDIENTE (menor)**: la
  retención mide antigüedad por `created_at` (proxy) — evaluar `updated_at`/fecha de decisión; UI de
  auditoría/erasure en el dashboard (hoy solo API).

- **2026-06-30 — Cierre Fase 1: almacenamiento DURABLE de documentos (CV/CUL)**: migración
  `0016_document_storage.sql` (tabla `candidate_documents`: `candidate_id` FK cascade, `type`,
  `filename`, `mime`, `size_bytes`, `content_b64`, unique(candidate_id,type)). El bot descarga el PDF a
  `uploads/` (staging, ya endurecido) y el **servicio** lo lee y guarda **el contenido en Postgres**
  (`_read_document_b64` + `repositories.save_document_content`, idempotente por candidato+tipo); metadata
  sigue en `candidates.documents` con flag `stored` (db|disk|none). El endpoint de descarga
  (`GET /api/candidates/{id}/documents/{tipo}`) sirve **desde la DB** (Response bytes, base64-decode) con
  **fallback a disco** para docs previos. Retención/erasure borran también el contenido durable
  (`delete_candidate_documents` en el barrido; FK cascade en el erasure). Ventaja: sobrevive redeploys y el
  borrado no deja objetos huérfanos (sin S3/Storage, que están off en Intel; migrar a object store queda
  como optimización de escala). **Tests: `test_storage.py`(4) → 103/103 verde.** **Verificado en vivo**
  (Supabase real): `0016` aplicada, round-trip DB íntegro (36 B), cascada al borrar candidato elimina el
  documento; backend reiniciado sano. **Gotcha**: tras aplicar DDL por psql directo, PostgREST no ve la
  tabla hasta `NOTIFY pgrst, 'reload schema'` (el CLI `supabase migration up`/`start` recarga solo).

- **2026-07-01 — Fase 3 (Observabilidad operativa en el dashboard)**: expone en la UI el estado operativo
  que hasta ahora solo vivía en la API/DB (cierra los punteros "(Fase 3)" de la auditoría; sin migraciones).
  (1) **Salud del outbox**: `repositories.count_outbox_by_status(tenant_id=None)` ahora acepta filtro por
  tenant (el caller de reconciliación sigue global), + `list_outbox(tenant_id, statuses, limit)` y
  `get_outbox(id)`. Endpoints admin `GET /api/outbox` (`{counts, items}` de pending+failed, aislado por
  tenant) y `POST /api/outbox/{id}/retry` (reencola un dead-letter/pendiente: status→pending +
  next_attempt_at=now para el próximo `drain`; 404 cross-tenant, 409 si ya `sent`; auditado `outbox.retry`).
  (2) **UI**: página nueva `/observabilidad` (admin-only, entrada de nav en `Shell` gated por `role==="admin"`):
  chips pending/failed/sent + lista de envíos detenidos con motivo/intentos y botón **Reintentar**; bitácora
  de auditoría (`GET /api/audit`, últimas 100 con quién/qué/cuándo, labels legibles). (3) **Erasure UI**:
  "Zona de peligro" admin-only en el detalle del candidato → `api.eraseCandidate` (DELETE, con `window.confirm`)
  y redirect a la vacante. Frontend: `api.ts` tipos `AuditEntry/OutboxItem/OutboxHealth` + métodos
  `getAudit/getOutbox/retryOutbox/eraseCandidate`. **Tests: `test_observability.py`(5: RBAC, aislamiento por
  tenant, retry requeue/404/409) → 108/108 verde; tsc OK.** **Verificado en vivo** (2026-07-01): Supabase
  local arriba, `/api/outbox` y `/api/audit` responden; `outbox drain` smoke previo.

- **2026-07-01 — Fase 0.1 (settings POR-TENANT)**: cierra la limitación de la Fase 0 (los `app_settings`
  auto_contact/inactivity/scheduling/retention eran **globales**; el scheduler era process-wide con config
  única). Migración `0017_app_settings_tenant.sql`: añade `tenant_id` a `app_settings`, la PK pasa de `key`
  a compuesta **(tenant_id, key)**, backfill de las filas globales previas al tenant `default` (el mismo de
  0013); un tenant sin fila cae a los `_DEFAULT_*` del código. `repositories.get_app_setting(key, default,
  tenant_id)` / `set_app_setting(key, value, tenant_id)` (upsert on_conflict=`tenant_id,key`) + `list_tenants()`.
  **Endpoints** de settings (auto-contact/inactivity/scheduling/retention GET+PUT) y `decide_candidate` ahora
  leen/escriben con `user["tenant_id"]`. **Scheduler por-tenant**: `_is_working_now(settings, tenant_id)` y el
  gate de `_contact_candidate` usan `vacancy["tenant_id"]`; el auto-contacto por horarios itera
  `repo.list_tenants()` (cada empresa con su zona/horas; slot de dedupe `{tid}|{fecha}|{hh:mm}`); el
  auto-contacto del producto delega el gate de horario a cada candidato (quita el gate global). Los barridos
  `_inactivity_sweep`/`_retention_sweep` resuelven el tenant de cada ítem con `_vacancy_tenant_map()`
  ({vacancy_id→tenant_id}) + `_tenant_cfg_resolver(key, default)` (caché una lectura por tenant/barrido);
  `_contact_prescreen_passed(settings, tenant_id=None)`. `agent/service.py` (`initiate_scheduling`) lee el
  scheduling del `vacancy.tenant_id`. **Tests: `test_tenant_settings.py`(4: endpoints leen/escriben su tenant,
  resolver aísla+cachea, vacancy_tenant_map) → 112/112 verde; tsc OK.** **Verificado en vivo** (Supabase real):
  `0017` aplicada (PK compuesta, tenant_id NOT NULL, 4 filas backfilleadas a `default`); PostgREST recargado;
  aislamiento probado con 2 tenants (ACME PUT auto-contact 08:00/activo NO tocó al `default` 11:00-15:00/apagado;
  cascade al borrar el tenant limpió su fila). Gotcha ya conocido: tras el DDL, `NOTIFY pgrst, 'reload schema'`.
  **Nota**: el scheduler sigue siendo un proceso con advisory lock; ahora aplica config por-tenant dentro del tick.

- **2026-07-01 — Auditoría de integraciones externas · F2 (mínimo privilegio / aislamiento por tenant)**:
  cierra el último hallazgo ALTA de `docs/auditoria_integraciones_externas.md` (F1/F3/F4 ya corregidos). Dos
  capas de defensa. (1) **Blindaje de la capa de app en CI** — `tests/test_tenant_guards.py` recorre TODAS
  las rutas de FastAPI e impone, sin excepciones silenciosas, que toda ruta `/api/*` fuera de la allowlist
  pública (`/api/health`, `/api/auth/login`) resuelva el usuario del token (`get_current_user`/`require_role`)
  **y** que toda ruta que carga un recurso por id de la URL (`{vacancy_id}`/`{candidate_id}`/`{recruiter_id}`/
  `{outbox_id}`) pase por un guard de tenant (`_require_*_in_tenant` o chequeo de `tenant_id`); si un endpoint
  futuro lo olvida, CI falla. La cobertura actual ya pasa. (2) **RLS latente (defensa en profundidad)** —
  migración `0018_rls_tenant_isolation.sql`: activa RLS + política `tenant_isolation` (FOR ALL, roles
  `anon`/`authenticated`) en las **16 tablas de negocio**; el tenant "actual" sale del claim `tenant_id` del
  JWT (`app_current_tenant()`, con guard de cadena vacía para requests anónimos de PostgREST — sin él,
  `''::jsonb` tumbaría toda consulta anónima); las tablas hijas suben al tenant por funciones `SECURITY
  DEFINER` (`app_vacancy_tenant`/`app_conversation_tenant`/`app_candidate_tenant`) para evitar recursión de
  RLS. **Sigue latente**: el backend usa la service_role key (BYPASSRLS) → la app no cambia; RLS protege ante
  fuga de la anon key / clientes directos futuros. Para RLS efectivo sobre el backend habría que dejar
  service_role + setear `request.jwt.claims.tenant_id` por request (cambio mayor, no hecho). **122/122 tests
  verde. Verificado en vivo** (Supabase local, `0018` aplicada): `service_role` ve todas las filas (backend
  intacto); rol no-bypass con claim tenant=A ve **solo** A (no B); claim vacío → 0 filas sin error.

- **2026-07-01 — Auditoría de integraciones externas · F5 (secretos: endurecimiento + rotación)**: cierra
  el último hallazgo (BAJA) de `docs/auditoria_integraciones_externas.md` → **F1–F5 todos cerrados**. Tres
  frentes. (1) **Rotación grácil de JWT**: nuevo `jwt_secret_previous` (CSV, `src/config.py`+`.env.example`);
  se **firma** siempre con `JWT_SECRET` y al **validar** se aceptan el actual + los retirados
  (`api.auth.accepted_jwt_secrets` + `decode_access_token` prueba en orden; la expiración es definitiva, no
  sigue probando si la firma valida pero expiró). Permite rotar el secreto de firma sin cerrar sesiones vivas
  durante ≈`JWT_EXPIRE_MINUTES`; rotación de emergencia = dejar `PREVIOUS` vacío (invalida todo). (2)
  **`assert_secure_config` ampliado**: en producción bloquea además `JWT_SECRET_PREVIOUS` default/corto y
  `ADMIN_PASSWORD` <12 chars (antes solo `JWT_SECRET`/`ADMIN_PASSWORD` default). El gate **no tenía tests**;
  ahora `tests/test_secrets.py` (12: gate dev/prod + rotación). (3) **Runbook** `docs/gestion_secretos.md`:
  inventario de secretos con radio de impacto, procedimiento de rotación por secreto (JWT grácil + emergencia,
  service key, DB, Telegram, SMTP, Google, admin), cadencia y camino a un secret manager
  (Doppler/Vault/AWS-GCP/Supabase Vault) para prod. **134/134 tests verde.** Pendiente pre-prod real (no MVP):
  migrar los secretos del `.env` plano a un gestor.

- **2026-07-01 — Proceso de selección multi-etapa (Fases 2 y 3: líder del proyecto + gerencia)**: el flujo ya
  no termina en la entrevista con RR.HH. (Fase 1); ahora encadena **Fase 2** (entrevista con el **líder del
  proyecto**, presencial o Meet a elección de RR.HH.) y **Fase 3** (entrevista final con **gerencia**, 100%
  presencial), con rechazo+notificación en cada punto y **contratación** al final. El agendamiento se
  **generalizó a un `stage`** (`hr`/`lead`/`manager`) reusando toda la máquina existente. Migración
  `0019_multi_stage_selection.sql`: `recruiters.location`; `vacancies.lead_recruiter_id`/`manager_recruiter_id`;
  `meetings` +`stage/modality/location/attendance` y **unique(conversation_id, stage)** (reemplaza el unique por
  conversación → hasta 3 reuniones); `candidates.psych_exam` (jsonb); tabla **`stage_feedback`** (feedback+decisión
  por etapa, con RLS por tenant); seed demo (Christian Benites=líder, Gerencia) asignados a la vacante. Motor:
  `state` +`scheduling_stage/modality/interviewer`; `graph.send(stage=,modality=,interviewer=)`;
  `nodes.start_scheduling` usa `SCHEDULING_SESSION_LINES[(stage,modality)]`; prompts `SCHEDULING_CONFIRMED_ONSITE`
  (ubicación+contacto+DNI) y `NOTIFY_HIRED`. Servicio: `initiate_scheduling(stage,modality)` +
  `_resolve_interviewer(vacancy,stage)`; `_finalize_scheduling` idempotente por (conv,stage), rama `onsite`
  (sin Meet, con `location`) y confirmación presencial; `_sync_business` mapea `(phase,stage)`→
  `scheduling/scheduled|lead_*|mgr_*`. Scheduler (`integrations/scheduling.py`): `create_meeting(...,modality,
  location)` — onsite = evento Calendar con `location`, sin conferenceData/Meet. Notificaciones:
  `email.build/send_psych_exam_email` (estilo Multitest) + `outbox` kind `psych_exam_email`/`deliver_psych_exam`;
  `candidate.render_message` acepta `hired`. API (rol recruiter, tenant, auditado): `POST /psych-exam`
  (RR.HH. pega link+código+clave → correo + registro), `POST /attendance` (`attended`/`no_show`+reagendar|cerrar),
  `POST /advance-stage` (feedback+decisión: aprobar `hr`→agenda `lead` con modalidad elegida; `lead`→`manager`
  onsite; `manager`→`hired`; rechazar→`rejected`+notifica), `GET /meetings`; `get_candidate_detail` devuelve
  `meetings`+`stage_feedback`+`psych_exam`; `VacancyIn`/`get_vacancy` con líder/gerencia; `RecruiterIn.location`;
  `no_show` en retención. Frontend: tipos/métodos (`Meeting` con stage/modality/location/attendance,
  `StageFeedback`, `PsychExam`, `sendPsychExam`/`markAttendance`/`advanceStage`/`listMeetings`);
  statusLabel/phaseMeta/PHASE_STEPS/stages.ts con las etapas nuevas (stepper: …→RR.HH.→Líder→Gerencia→Decisión);
  detalle del candidato con panel de **exámenes psicológicos**, tarjetas por reunión, controles de **asistencia**,
  panel **feedback+decisión** (con selector de modalidad al aprobar la etapa hr) e historial; alta de vacante con
  selectores de líder/gerencia; Equipo con dirección de oficina. **150/150 tests (test_multistage.py +16); tsc +
  build OK.** **Verificado en vivo** (Supabase local): `0019` aplicada + seed + `NOTIFY pgrst`; round-trip DB real
  (2 reuniones por conversación lead+manager onsite sin Meet, `get_meeting_by_conversation_stage`, `attendance`,
  `stage_feedback`). **Verificación end-to-end completa** (`scripts/verify_multistage.py`, contra DB real + **Groq
  qwen3-32b**): drivea el flujo entero por el MISMO `InterviewService` que usa el bot (checkpointer Postgres +
  repos Supabase + SimulatedScheduler), simulando al candidato con `InboundMessage` y a RR.HH. con las mismas ops
  de los endpoints → entrevista(scorecard 🟢)→Fase 1 RR.HH.(virtual, Meet)→Fase 2 líder(presencial, dirección, sin
  Meet)→Fase 3 gerencia(presencial)→`hired`; el LLM parseó la elección de horario en las 3 etapas; 3 reuniones +
  asistencia + 3 feedbacks correctos; candidato de prueba autolimpiado. Equivale al paseo por Telegram (solo se
  reemplaza el transporte por llamadas directas). Config-gated: sin líder/gerencia asignados, el proceso cierra en
  Fase 1 (retrocompat). **Pendiente (menor)**: actualizar la guía `/guia` con las Fases 2 y 3.

- **2026-07-01 — Inactividad también en el saludo inicial**: antes la fase `greeting` (esperando
  Acepto/No interesado) **no** tenía recordatorio ni cierre (esperaba indefinidamente). Ahora el barrido de
  inactividad la incluye, reusando la MISMA config por-tenant (`reminder_minutes`/`max_reminders`):
  `_inactivity_sweep` agrega `PHASE_GREETING` a los estados barridos; `_reminder_messages` devuelve
  `REMINDER_GREETING` (invita a tocar Acepto, sin pregunta de entrevista); `nodes._handle_timeout` maneja
  `greeting`→`PHASE_CLOSED` + `closed_reason=no_response` + `CLOSING_GREETING_NO_RESPONSE` → status
  `no_response`. Nuevos prompts `REMINDER_GREETING`/`CLOSING_GREETING_NO_RESPONSE`. **152/152 tests**
  (test_inactivity.py +2: recordatorio de saludo + timeout del motor cierra `no_response`).

- **2026-07-01 — Auditoría e2e (10 dimensiones) + cierre de 5 hallazgos**: auditoría completa en
  `docs/auditoria_e2e.md` (seguridad, arquitectura, DB, UX, observabilidad, rate limiting, pipeline
  LLM, estado/memoria, grafo/consistencia, control de bucles) con backlog priorizado — top pendiente:
  rate limiting (login + bot), sanitizar los 3 prompts sin blindar, routing multi-tenant del bot
  (deep-links), N+1+paginación, métricas de fallback/latencia LLM. **Implementados los 5 hallazgos
  nuevos**: (M1) `_retention_sweep` ahora borra el **checkpoint LangGraph** (la PII del estado
  sobrevivía a la anonimización); (G2) `_finalize_scheduling` **registra la reunión ANTES** de crear
  el evento de Calendar (registro-primero: crash a mitad ya no duplica el evento; nuevo
  `repositories.update_meeting` completa link/event_id después); (G1) la reconciliación detecta
  **divergencia motor↔negocio** (fase del checkpoint vs `conversations.state`, alerta
  `state_divergence`); (I1) tope de dudas del candidato por pregunta (`MAX_CANDIDATE_QUESTIONS=3`,
  estado `questions_asked`, corte `QUESTIONS_EXHAUSTED` sin gastar LLM — el ciclo era infinito y
  reseteaba el reloj de inactividad); (I2) tope de reintentos al elegir horario (`MAX_SLOT_RETRIES=3`,
  estado `slot_retries`, escala con `SCHEDULING_ESCALATE` una sola vez; elección válida tardía sigue
  agendando; RR.HH. lo retoma vía la alerta de scheduling estancado). **160/160 tests verde
  (test_iteration_limits.py +8).**

- **2026-07-01 — Cierre del top del backlog de la auditoría (S1 + R1/R2/R3 + O1)**: (S1)
  **anti-inyección completa** — `classify_turn`/`answer_candidate_question`/`parse_slot_choice`
  ahora sanitizan (`sanitize_answer_for_prompt`) y encierran el texto del candidato entre
  `<<<respuesta>>>…<<<fin>>>` con instrucción anti-inyección (el de dudas además prohíbe confirmar
  salario/condiciones fuera de `company_info`); FakeLLMs de tests adaptados al formato. (R1)
  **login 5/min por IP** → 429 (`api/ratelimit.py`: `SlidingWindowLimiter` puro, sin deps, por
  proceso). (R2) **gobierno de turnos del bot** — `TurnGovernor`: cooldown 2 s por chat (ignora
  ráfagas en silencio) + tope diario 120 turnos (aviso único `_CAP_NOTICE_TEXT`, luego silencio),
  ANTES de gastar LLM; config `BOT_TURN_COOLDOWN_SECONDS`/`BOT_MAX_TURNS_PER_DAY` (+.env.example).
  (R3) **psych-exam idempotente** — reenviar las mismas credenciales → 409 (nuevas sí); de paso
  se corrigió un **bug latente**: el endpoint usaba `_now_iso()` sin definir en `api/main.py` →
  500 en runtime (ahora definido). (O1) **fallbacks/latencia visibles** — `MeteredLLM` acumula
  `calls/errors/duration_ms` por etapa → migración `0020_llm_usage_latency.sql` (**aplicada en
  vivo** por psql directo + `NOTIFY pgrst`, patrón conocido; la historia CLI de 0019/0020 quedó
  fuera del registro `supabase migration up`) → `record_usage` con retry retro-compatible sin las
  columnas nuevas → `_aggregate_tokens` expone `calls/errors/avg_ms` en `/api/metrics`; toda rama
  de fallback loggea `LLM fallback en <fn>`. **Smoke en vivo**: insert+agg round-trip OK.
  `docs/auditoria_e2e.md` actualizado (top-5: quedan A1 deep-links multi-tenant y D1+U1 N+1/paginación).
  **170/170 tests verde (test_ratelimit.py +7, test_integrity +3).**

- **2026-07-01 — A1: routing multi-tenant del bot (deep-links por vacante)**: cierra el bloqueante
  SaaS de la auditoría e2e (el `/start` caía en la primera vacante abierta GLOBAL, cruzando tenants).
  `service.process()` ahora resuelve con **`_resolve_context`** en orden: ① **conversación existente**
  del thread → SU vacante/candidato (sticky; de paso corrige el **bug latente** de que la respuesta de
  un candidato contactado para la vacante B se procesara contra la default y **duplicara** candidato),
  ② **deep-link** `t.me/<bot>?start=<vacancy_id>` (nuevo `InboundMessage.start_payload`; `_on_start`
  lee `context.args`; payload validado como **UUID antes de tocar la DB** — basura/inyección se corta;
  vacante inexistente o cerrada → `VACANCY_UNAVAILABLE` **sin crear candidato**, para no enganchar a
  otro tenant), ③ fallback `get_default_open_vacancy()` (retrocompat demo mono-vacante; sin vacantes →
  `NO_OPEN_VACANCY`). Prompts nuevos en `agent/prompts.py`. Dashboard: config `TELEGRAM_BOT_USERNAME`
  (`src/config.py` + `.env.example`; en el `.env` local ya seteado a `leia_talento_bot` vía getMe) →
  `GET /api/vacancies/{id}` devuelve `telegram_deep_link` y el detalle de la vacante muestra el
  **enlace del aviso copiable** (botón Copiar). **177/177 tests verde (test_routing.py +7: payload
  gana a default, inexistente/cerrada→aviso sin candidato, no-UUID no toca DB, fallback, sticky).**
  **Verificado en vivo** (backend --reload + Supabase): `GET /api/vacancies/{id}` →
  `https://t.me/leia_talento_bot?start=<vacancy_id>`; tsc OK. Nota: el login demo usa los defaults de
  config (`admin@datawith.ai`, sin `ADMIN_*` en `.env`) y responde `access_token` (no `token`).

- **2026-07-01 — D1+U1: listados sin N+1 + paginación/búsqueda**: cierra el último ítem del top-5 de
  `docs/auditoria_e2e.md` (**top-5 completo**). (D1) `repo.list_candidate_rows(vacancy_ids, search,
  limit, offset)`: UNA consulta con **embedded selects** de PostgREST (`conversations(id,created_at,
  scorecards(semaphore,total_score))` vía FKs) + columnas livianas + `count=exact` (total en la misma
  request); `repo.count_candidates_by_status(vacancy_ids)`: 1 consulta de 2 columnas → `{vacancy:
  {status: n}}`. Con eso: `GET /api/vacancies` = 3 consultas fijas (antes 2+1/vacante), `GET
  /api/candidates` = 2 (antes 1+1/vacante+2/candidato), `GET /api/vacancies/{id}/candidates` = 1,
  `GET /api/recruiters` = 3 (la carga activa sale del conteo). `_enrich_candidate_row` (2 queries/
  candidato) reemplazado por `_candidate_row_from_embed` (puro). **Gotcha PostgREST**: embebe
  `scorecards` como **objeto** (detecta to-one por el unique de `conversation_id`), no lista — el
  builder acepta ambas formas (el 500 salió en el smoke en vivo, no en los tests con fakes). (U1)
  Ambos listados de candidatos aceptan `q` (ilike por nombre, comodines `%`/`_` escapados) +
  `limit/offset` (clamp 1–500, default 100) y responden `{items,total,limit,offset}` (**breaking**
  para consumidores del array plano; frontend actualizado). UI: input "Buscar por nombre…" con
  debounce 300 ms + controles ‹ Anteriores / Siguientes › (solo si total>100) en detalle de vacante
  y Pipeline global; stat "Importados" y contadores usan `total` del servidor. **185/185 tests verde
  (test_listing.py +8: builder con embeds lista/objeto, clamps, endpoints con guard anti-N+1 —
  `list_candidates`/`get_conversation_by_candidate` truenan si un listado recae en la ruta vieja).**
  **Verificado en vivo** (Supabase real): búsqueda "dan" → Daniela 🟢 92.8 con embed real, offset
  pagina, global con `vacancy_title`, roster con carga; `/pipeline` y `/vacantes/{id}` → 200; tsc OK.

- **2026-07-02 — Backlog de auditoría: 4 lotes (seguridad+operación · bajas rápidas · refactor A2 ·
  pipeline LLM)**: cierra 12 hallazgos de `docs/auditoria_e2e.md` (tabla actualizada). **Lote 1**:
  (S2) revocación de sesión — `get_current_user` consulta `users.active` con caché TTL 60 s
  (`api.auth._is_user_revoked`; fail-open ante DB caída: revocar = DESACTIVAR el usuario, no borrarlo);
  (S3) credenciales del examen psicológico enmascaradas para `viewer` (`_psych_exam_for_role`);
  (O2) alertas de reconciliación visibles — `_collect_ops_alerts` (fuente única, filtro por tenant) +
  `GET /api/ops/alerts` (admin) + sección "Alertas operativas" en `/observabilidad`; de paso FIX real:
  `list_meetings_without_link` ahora excluye `modality=onsite` (las presenciales no llevan Meet —
  falso positivo detectado en vivo); (A3) `threading.Lock` por `thread_id` en `InterviewService`
  (sweep de inactividad vs mensaje del candidato ya no pisan el mismo checkpoint). **Lote 2**: (S5)
  `CORS_ORIGINS` por settings; (A4) purga del set `fired` del scheduler; (U2) errores humanos en el
  frontend (`req()` usa el `detail` del backend + `errorMessage()`); (U4) 401 con sesión previa →
  `/login?expired=1` con aviso. **Lote 3 (A2)**: `api/main.py` (1 667 líneas) partido en
  `api/runtime.py` (estado/defaults) + `api/scheduler.py` (loop+barridos+contacto) + `api/deps.py`
  (guards) + `api/routes/{vacancies,candidates,recruiters,settings,observability}.py`; main (~240)
  = lifespan + login/me + ensamblaje + **re-exports de compat** (los tests parchean `main.repo`/
  `main._state` — mismos objetos). Gotcha FastAPI: `include_router` queda como `_IncludedRouter`
  anidado (no aplana APIRoute) → el introspector de `test_tenant_guards` ahora recorre
  `original_router.routes`. **Lote 4 (pipeline LLM)**: `agent/prompts.PROMPT_VERSION` sellado en
  scorecard + `llm_usage` (migración `0021`, aplicada en vivo vía psql+NOTIFY; escrituras
  retro-compatibles); RAG conectado config-gated (`INTERVIEW_RAG_ENABLED`, default off) —
  `agent/rag.py` `build_company_retriever` inyectado en el runner como el LLM (motor puro, carga
  lazy, fail-safe) enriquece las dudas del candidato con Chroma; suite **golden**
  (`tests/golden/golden_set.json` + `scripts/golden_eval.py`, respuestas reales de Alberto +
  contraejemplos) — **primera corrida 9/9 con Groq qwen3-32b**. **206/206 tests (+21: hardening 14,
  pipeline 7); tsc OK. Verificado en vivo** (backend :8000 --reload + Supabase): login/401/200,
  `/api/ops/alerts` con alertas reales (y limpio tras el fix onsite), round-trip `prompt_version`
  en `llm_usage`. Backlog restante: S4, R4, A5, D2–D5, U3, O3, G3, G4 (todos BAJA/menores).

- **2026-07-02 — Cierre TOTAL del backlog e2e (11 hallazgos: S4, R4, A5, D2–D5, U3, O3, G3, G4)**:
  la auditoría `docs/auditoria_e2e.md` queda **sin hallazgos abiertos**. Migración
  `0022_backlog_close.sql` (**aplicada en vivo** vía psql directo + `NOTIFY pgrst`, patrón conocido;
  como 0019/0020 quedó fuera del registro del CLI): FK cascade `outbox.candidate_id`, tabla
  `state_transitions` (+RLS por tenant), `candidates.updated_at` + trigger, `conversations.
  last_delivery_failed_at`, RPCs `app_replace_vacancy_questions`/`app_claim_candidate_chat`.
  (S4) erasure y `_retention_sweep` purgan PII residual — `repo.delete_outbox_by_candidate` +
  `repo.scrub_audit_for_entity`; el audit del borrado ya no incluye el nombre. (R4) `_sync_limiter`
  2/min por tenant en `sync-applicants` → 429. (A5) `get_or_create_candidate` retry-on-conflict
  (el unique existía desde 0001; el insert perdedor relee la fila del ganador). (D2)
  `document_db_max_bytes` (config, 5 MB): sobre el umbral el PDF queda `stored="disk"`. (D3)
  RPCs atómicos con fallback retro-compat en `repo.replace_vacancy_questions` y
  `repo.claim_candidate_chat` (usado por `scheduler._claim_chat`). (D4)
  `repo.purge_stale_checkpoints(days)` + `_checkpoint_purge_sweep` (a lo sumo cada 6 h; config
  `checkpoint_retention_days`, default 30, 0=off). (D5) `_retention_reference_ts` usa `updated_at`
  (trigger) con fallback `created_at`. (U3) modal "escribe el nombre para confirmar" en el erasure
  (sin nombre → `BORRAR`). (O3) `api/httpmetrics.py` (singleton por plantilla de ruta) + middleware
  en `api/main.py` + `GET /api/ops/http-metrics` (admin) + tarjeta "Rendimiento HTTP" en
  `/observabilidad`. (G3) `_mark_delivery_result` en el bot + marca en `_inactivity_sweep`
  (`repo.set_delivery_failure`); alerta `delivery_failed` en `_collect_ops_alerts` solo si el
  candidato no interactuó después del fallo. (G4) `repo.add_state_transition` en
  `service._sync_business` al cambiar de fase; `get_candidate_detail` devuelve `transitions`.
  **Tests: `test_backlog_close.py` (+16: 429 por tenant, retry A5, RPC+fallback D3, purgas S4
  erasure/retención, D5, gating D4, umbral D2, filtro G3, transición única G4, HttpMetrics+RBAC
  O3) → 222/222 verde; tsc OK.** `docs/auditoria_e2e.md` actualizado (los 11 ✅ + resumen: backlog
  vacío). **Pendiente**: verificación en vivo (reiniciar backend, smoke `/api/ops/http-metrics`,
  sync doble → 429, erasure con modal) + commit.

- **2026-07-02 — Servidor MCP (Model Context Protocol) read-only**: `api/mcp.py` expone 5
  herramientas de consulta en `/mcp` (streamable HTTP montado en la MISMA app FastAPI) para
  clientes LLM externos (Claude Code/Desktop u otro orquestador): `list_vacancies`,
  `list_candidates` (por vacante o pipeline global, con `q`/paginado), `get_candidate_detail`,
  `get_metrics`, `get_ops_alerts` (admin). **Capa de adaptación pura**: cada tool invoca la MISMA
  función del endpoint FastAPI (`api/routes/*`) pasándole el user resuelto → hereda tenancy,
  enmascarado por rol (psych-exam) y los listados sin N+1; v1 sin mutaciones (capability ≠
  autoridad). **Auth**: `MCPAuthMiddleware` (ASGI, envuelve el mount) exige el MISMO Bearer JWT
  del dashboard — firma+rotación (`decode_access_token`), revocación y tenant — y deja el user en
  un contextvar que SÍ llega a la tool (el session manager arranca el server con
  `task_group.start()` desde el task del request en modo stateless). RBAC dentro de la tool
  (`_require_user(min_role)` — el Depends de FastAPI no corre al llamar la función directo).
  **Auditoría**: cada invocación → `audit_log` action `mcp.<tool>` (detalle de candidato usa
  entity_type=candidate → la purga S4 también lo cubre). Config: `MCP_ENABLED` default **off**
  (`src/config.py` + `.env.example`); el lifespan corre `session_manager.run()` explícito
  (gotcha: FastAPI NO ejecuta el lifespan de sub-apps montadas). Dep: `mcp` **1.28.1 pineado <2**
  (la 2.0 beta renombra FastMCP→MCPServer). Gotchas: anti DNS-rebinding del SDK desactivado
  (valida Host, pensado para servers locales sin auth; aquí el JWT por header lo mitiga);
  endpoint en `/mcp/` (Route "/" dentro del mount). **Tests: `test_mcp.py` (9: gating off→404,
  401 sin/mal token/revocado, tools/list, tenancy del token, cross-tenant→error, RBAC admin,
  auditoría) → 231/231 verde.** El mount es sub-app ASGI (no APIRoute): `test_tenant_guards` no
  lo recorre; `test_mcp.py` cubre su perímetro. **Verificado en vivo** (Supabase real, uvicorn
  :8010 con MCP_ENABLED=true): sin token→401; tools/list→5; `list_vacancies`→vacante demo con 3
  candidatos; `list_candidates q=dan`→`get_candidate_detail`→Daniela 92.8; `get_ops_alerts`
  admin→0 alertas. Conexión: `claude mcp add --transport http leia http://localhost:8000/mcp/
  --header "Authorization: Bearer <token>"`. Extensión futura: tools de mutación
  (contactar/decidir) gated por rol + confirmación.

## Cómo correr (resumen)
1. DB: `export PATH=$HOME/.local/share/supabase:$PATH && supabase start` (storage/analytics off).
2. `.env` con OPENAI_API_KEY (Groq), TELEGRAM_BOT_TOKEN, y keys de `supabase status`.
3. Backend+bot: `uv run uvicorn api.main:app --port 8000 --reload`.
4. Dashboard: `cd frontend && npm install && npm run dev` → http://localhost:3000.
5. Demo sin infra: `uv run python scripts/demo.py --alberto`.

## Pendiente (por fases)
- Fase 1: migraciones SQL + seed vacante demo + `db/`; checkpointer PostgresSaver sobre Supabase.
- Fase 2: motor LangGraph + evaluación; tests con las respuestas reales de Alberto.
- Fase 3: bot Telegram (turno de entrevista + botones Acepto/No interesado).
- Fase 4: scorecard final + email al reclutador.
- Fase 5: dashboard reclutador (Next.js).
- Fase 6 (post-MVP): adapter WhatsApp Cloud API; recepción/parseo de CV.

## Convenciones del usuario
Código en inglés, chat en español, `uv` (no pip), commits convencionales (`feat:`, `fix:`...).
