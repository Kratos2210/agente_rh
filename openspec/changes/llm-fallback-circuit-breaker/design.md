# Design: llm-fallback-circuit-breaker

## Context

Toda llamada LLM del runtime pasa por `MeteredLLM` (metering por etapa + trazas) que envuelve un
`LangChainLLM` construido por `build_default_llm(model=, base_url=, api_key=)`. El par
(principal, overrides baratos) se arma en `orquestacion.providers._build_pair` — tanto para el
`.env` como para el BYOK por-tenant — y el bot lo refresca en caliente por fingerprint
(`refresh_metered_llm`). Ante un fallo del proveedor, cada call site ya tiene fallback
heurístico marcado `low_confidence` (el turno no crashea), pero NO existe segundo proveedor: un
outage degrada la calidad de todo el pipeline a la vez (R3, auditoría v4).

## Goals / Non-Goals

**Goals:**
- Failover transparente a un segundo endpoint compatible-OpenAI cuando el principal falla.
- Circuit breaker: tras N fallos consecutivos dejar de pagar el timeout del principal
  (ir directo al respaldo) y sondear su recuperación tras un cooldown.
- Atribución correcta del modelo que REALMENTE atendió (costos/trazas/usage sin mentir).
- Default apagado; con los `.env` nuevos vacíos, comportamiento actual intacto.

**Non-Goals:**
- Respaldo por-tenant (BYOK de respaldo): el respaldo es de la INSTALACIÓN (un solo `.env`),
  igual que `ops_alert_email`. Un tenant con BYOK caído cae al respaldo global.
- Reintentos nuevos: los `llm_max_retries` del cliente siguen siendo la primera línea; el
  breaker cuenta fallos DESPUÉS de agotados esos reintentos.
- Fallback del modelo barato (overrides): si el barato falla, su call site ya degrada al
  heurístico; encadenar respaldo ahí duplica latencia por poco valor. Solo se envuelve el
  principal (decisión revisable).
- Health/endpoint nuevo para el estado del breaker (los contadores `errors` por etapa y el log
  ya lo hacen visible; añadirlo a `/api/health` queda como mejora futura).

## Decisions

1. **Wrapper `FallbackLLM` (composición) en vez de lógica dentro de `MeteredLLM`** — el
   protocolo `LLM` (`complete(prompt) -> str`) ya es el punto de inyección de todo el sistema;
   un wrapper compone con MeteredLLM/fakes/BYOK sin tocar el metering. Alternativa descartada:
   rama try/except dentro de `MeteredLLM.complete` (mezcla responsabilidades y no sirve a
   call sites sin metering como el juez de calidad).
2. **Breaker por instancia, con reloj inyectable** (`now=time.monotonic`) — el bot vive en un
   proceso (advisory lock para lo demás); un breaker global multi-proceso exigiría estado en DB
   por poco valor. Estados: `closed` (N fallos consecutivos → `open`), `open` (cooldown →
   `half-open`), `half-open` (una sonda: éxito → `closed`, fallo → `open`). Umbral y cooldown
   por config (`LLM_BREAKER_FAILURES=3`, `LLM_BREAKER_COOLDOWN_SECONDS=60`).
3. **Integración en `providers._build_pair`** — es el único constructor del par
   (env y BYOK): envolver ahí cubre bot, sync-applicants, juez y hot-swap sin tocar call sites.
   `build_default_llm` queda puro (los usos efímeros — `/test` de conexión, golden — no deben
   heredar el respaldo).
4. **Atribución post-call en `MeteredLLM`** — hoy `_models[stage]` y el modelo de la traza se
   capturan ANTES de `complete()`; con failover el modelo servido puede diferir. Se re-lee
   `inner.model` DESPUÉS del éxito (para `FallbackLLM`, `model` refleja quién sirvió la última
   llamada; para LLMs planos es el mismo valor → sin cambio de comportamiento).
5. **`last_usage`/`metadata` proxied** — `FallbackLLM` expone `last_usage` del LLM que sirvió y
   propaga su dict `metadata` (tracing LangSmith) a principal y respaldo antes de invocar,
   para que `MeteredLLM._tag_meta` siga funcionando sin conocer el wrapper.
6. **Validación previa con el banco existente** — `golden_eval.py --model X --base-url URL
   --api-key-env VAR` corre las suites contra el candidato a respaldo SIN tocar el `.env`
   (patrón del modelo barato, regla ya normada en llm-operacion).

## Risks / Trade-offs

- [El respaldo responde distinto (otro modelo) y baja la calidad silenciosamente] → gating por
  banco de aceptación antes de habilitarlo + el modelo servido queda atribuido en
  `llm_usage`/trazas (visible en /observabilidad y costos por modelo).
- [Doble latencia en el peor caso (timeout del principal + llamada al respaldo)] → es solo en la
  llamada que ABRE el circuito; con el breaker abierto se va directo al respaldo. Umbral bajo
  (3) para cortar rápido.
- [PII a un segundo proveedor] → misma consideración del ADR de selección de modelo; el
  operador elige el respaldo con el mismo criterio de residencia de datos. Documentado en el ADR.
- [Breaker por proceso: réplicas webhook abren/cierran cada una por su cuenta] → aceptable
  (convergen en ~N fallos cada una); estado compartido en DB es sobre-ingeniería hoy.

## Migration Plan

Sin migraciones. Deploy = release normal; activar = setear los 3 `.env` y reiniciar (o por
overlay k8s). Rollback = vaciar `LLM_FALLBACK_MODEL`. Antes de activar en prod: correr las
suites golden contra el respaldo (comando documentado en el ADR y `.env.example`).

## Open Questions

- ¿Exponer el estado del breaker en `/api/health` (como `scheduler: simulated-fallback`)?
  Diferido: los logs + `errors` por etapa bastan para el MVP.
