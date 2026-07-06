# Seguridad — Auth, tenancy, secretos, anti-inyección y PII

> Parte de [spec/](README.md) · Última revisión: 2026-07-06 · Código: `api/auth.py`, `api/deps.py`, `orquestacion/providers.py`

## 1. Propósito y alcance

Modelo de seguridad completo: identidad y roles, aislamiento multi-tenant, gestión de secretos,
defensa del pipeline LLM (inyección/red teaming), protección de datos personales (Ley 29733
Perú) y endurecimiento de superficie (rate limits, archivos, SSRF).

## 2. Decisiones de diseño

- **Auth self-contained** (bcrypt + PyJWT, tabla `users` propia) en vez de un IdP externo:
  cero infraestructura extra para el MVP SaaS; los roles son jerárquicos
  `viewer < recruiter < admin` y el admin inicial se bootstrapea del `.env` si no hay usuarios.
- **Tenancy en dos capas**: la efectiva en la capa de aplicación (guards por recurso +
  **test estructural** que recorre todas las rutas) y una **RLS latente** en Postgres (16
  tablas, política por claim `tenant_id`) como defensa en profundidad — el backend usa
  service_role (BYPASSRLS), así que la RLS protege ante fuga de la anon key o clientes
  directos futuros, no ante bugs del backend (eso lo cubre la capa 1 + CI).
- **Claves derivadas por propósito** desde `JWT_SECRET`: `|mcp-confirm` (HMAC de confirmación
  MCP) y `|llm-provider` (Fernet de las API keys BYOK). Un token de un propósito jamás valida
  en otro; un solo secreto raíz que rotar.
- **Prompt-only no basta**: el red teaming demostró que un modelo chico obedece instrucciones
  inyectadas pese al marco anti-inyección → defensa en profundidad con detectores en el INPUT
  y salidas seguras sin LLM.
- **La PII se minimiza, se sella y se puede borrar**: consentimiento explícito, retención
  configurable, erasure total, y ningún SaaS de observabilidad recibe contenido por defecto.

## 3. Diseño e implementación

### Identidad y sesión
- `POST /api/auth/login` (5/min/IP) → JWT firmado con `JWT_SECRET`; expiración
  `JWT_EXPIRE_MINUTES`. **Rotación grácil**: se firma con el secreto actual y se valida contra
  actual + `JWT_SECRET_PREVIOUS` (CSV) — rotar sin cerrar sesiones; emergencia = vaciar previous.
- **Revocación**: desactivar el usuario (no borrarlo); `get_current_user` consulta `users.active`
  con caché TTL 60 s (fail-open ante DB caída). Gestión de usuarios: `api/routes/users.py`
  (admin, anti auto-bloqueo).
- `assert_secure_config`: en producción bloquea el arranque con `JWT_SECRET`/`ADMIN_PASSWORD`/
  `JWT_SECRET_PREVIOUS` débiles o default.

### Tenancy
- Guards `_require_*_in_tenant` en todo recurso por id; cross-tenant responde 404.
- `tests/test_tenant_guards.py`: invariante estructural — toda ruta `/api/*` fuera de la
  allowlist resuelve usuario Y toda ruta con `{id}` pasa por guard; si un endpoint futuro lo
  olvida, CI falla.
- RLS: migración `0018` (política `tenant_isolation` por claim JWT, funciones SECURITY DEFINER
  para tablas hijas, guard de claim vacío) + `0026`/`0019` para tablas nuevas.

### Secretos
- Runbook `docs/gestion_secretos.md`: inventario con radio de impacto + procedimiento de
  rotación por secreto. Scaffolding External Secrets en `despliegue/k8s/secret-manager/`.
- API keys BYOK cifradas con Fernet en `app_settings`; enmascaradas en GET (`gsk_...XXXX`);
  scrubbed de errores; rotar `JWT_SECRET` → descifrado falla con warning y fallback al `.env`.
- El token de Telegram no se loggea (hallazgo F1 cerrado; `test_token_redaction.py`).

### Pipeline LLM (anti-inyección y red teaming)
- Capa 1 — sanitización: `sanitize_answer_for_prompt` (quita delimitadores, cap 4000) en LOS
  CUATRO puntos donde el candidato inyecta texto (evaluate, classify, answer, slot).
- Capa 2 — marco: delimitadores `<<<respuesta>>>…<<<fin>>>` + instrucción anti-inyección;
  el prompt de dudas prohíbe confirmar salario/condiciones fuera del contexto.
- Capa 3 — detectores sin LLM: `is_echo_injection()` (patrón de eco/parroteo) → deriva con
  `SAFE_DEFLECTION` sin llamar al modelo (no depende de que el modelo resista).
- Proceso repetible: `tests/redteam/redteam_set.json` (12 ataques con guardias puras) +
  `scripts/redteam_eval.py` + job nightly `red-team`. Estado: 12/12 contenidos.

### PII (Ley 29733)
- **Consentimiento**: `consent_at` sellado al aceptar. **Minimización**: `profile_for_llm`
  (sin nombre/contactos hacia el LLM); LangSmith con `HIDE_INPUTS/OUTPUTS=true`; Sentry
  `send_default_pii=False`; Phoenix self-hosted.
- **Retención**: barrido configurable que anonimiza descartados > N días (nombre, chat, CV,
  documentos, transcripción, checkpoint LangGraph, `psych_exam`/`medical_exam`/`onboarding`,
  trazas LLM, outbox, scrub del audit).
- **Erasure**: `DELETE /api/candidates/{id}` (admin, modal de confirmación con nombre,
  auditado, cascada completa + checkpoint).

### Superficie
- Rate limits: login 5/min/IP; sync 2/min/tenant; bot con cooldown 2 s + 120 turnos/día + dedupe.
- Archivos: `sanitize_filename` (anti path-traversal), solo PDF ≤ 20 MB, validación de
  contenido (¿es un CV?) fail-open, descarga blindada dentro de `uploads/`.
- **Anti-SSRF (BYOK)**: base_url privada/loopback rechazada salvo
  `ALLOW_PRIVATE_LLM_ENDPOINTS=true`; anti-exfiltración: la key solo viaja al base_url validado.
- CORS por settings; webhook Telegram validado por secret header en tiempo constante
  (`hmac.compare_digest`).

## 4. Contratos e invariantes

- Nada público que no esté en la allowlist enumerada (`/api/health`, `/api/auth/login`,
  `/telegram/webhook` con secret).
- Ningún secreto en claro en DB, logs, respuestas de API ni mensajes de error.
- Todo texto de origen no confiable (candidato, CV) pasa por sanitización antes de un prompt.
- La anonimización debe cubrir TODO lugar donde se copió PII (estado, trazas, audit, exámenes) —
  cada feature nueva que copie PII debe sumarse al barrido de retención.

## 5. Patrones reutilizables

- **Invariantes de seguridad como tests estructurales**, no como checklist de review.
- **Derivación de claves por propósito** (`secreto|proposito`): aísla dominios criptográficos
  con un solo secreto raíz.
- **Rotación grácil**: firmar con el nuevo, validar contra nuevo+viejos, TTL acotado.
- **Red teaming como suite versionada** (set + guardias puras + nightly), no como ejercicio
  puntual: los ataques contenidos quedan fijados contra regresión.
- **Salida segura sin LLM** para inputs detectablemente maliciosos: la defensa no puede
  depender de que el modelo se porte bien.

## 6. Pendientes conocidos

- Secret manager real (hoy `.env`/Secret plano; scaffolding listo — requiere cuenta externa).
- RLS efectiva sobre el backend (setear claim por request en vez de service_role) — cambio mayor.
- Residencia de datos del proveedor LLM para prod real (PII cruda viaja a Groq; ver ADR).

## 7. Trazabilidad

- Tests: `test_auth.py`, `test_secrets.py`, `test_tenant_guards.py`, `test_integrity.py`,
  `test_redteam_harness.py`, `test_ratelimit.py`, `test_documents.py`, `test_llm_provider.py`,
  `test_langsmith_privacy.py`, `test_token_redaction.py`, `test_users.py`.
- Docs: `docs/gestion_secretos.md`, `docs/auditoria_integraciones_externas.md` (F1–F5 cerrados),
  `docs/auditoria_e2e.md`. Migraciones: `0013`, `0015`, `0018`.
