# ADR — Selección de modelo LLM y estrategia de costos

**Fecha:** 2026-07-03 · **Estado:** aceptado · **Contexto:** roadmap paso 5 (optimización de
costos) de `audit/auditoria_final.md`. Cierra la recomendación 2.1.2 ("la elección inicial del
modelo no está justificada por escrito") y 2.3.3 ("ninguna palanca activa de reducción de costo").

## Decisión

1. **Modelo principal: `qwen/qwen3-32b` servido por Groq** (compatible-OpenAI), con
   `reasoning_effort: "none"` (sin `<think>`) y `temperature: 0.2`.
2. **Arquitectura agnóstica de proveedor**: el LLM es inyectable (`agent/llm.build_default_llm`);
   cambiar de proveedor = cambiar `OPENAI_API_BASE` / `OPENAI_API_KEY` / `OPENAI_MODEL`.
3. **Routing de costos por etapa** (paso 5): las etapas simples y frecuentes (`classify`,
   `schedule`) van a un modelo más barato (`LLM_CHEAP_MODEL` + `LLM_CHEAP_STAGES`) sin tocar el
   motor. **Modelo barato elegido y validado: `llama-3.1-8b-instant`** (Groq, ~$0.05/$0.08 por 1M
   in/out — un orden de magnitud bajo el principal), aprobado contra el banco de aceptación golden:
   `classify` 7/7 y `slot` 6/6 (ver 2.1.2 y "Cómo elegir un modelo barato"). El costo real por
   modelo/etapa queda medido (`llm_usage.model`, O-2).
4. **Caché semántica de dudas** (paso 5): las respuestas a preguntas del candidato sobre el puesto
   se cachean por vacante (`INTERVIEW_ANSWER_CACHE_ENABLED`); una duda repetida no gasta tokens.

## Por qué Qwen3-32B en Groq

| Criterio | Valoración | Nota |
|---|---|---|
| **Latencia** | ★★★★★ | Groq (LPU) da baja latencia por token; el turno del candidato se mide p50/p95/p99 (O-3). El chat conversacional tolera bien la latencia de Groq. |
| **Costo** | ★★★★☆ | ~$0.29/$0.59 por 1M tokens in/out (precios sembrados en `llm_pricing`). Un orden de magnitud bajo GPT-4-class para la calidad requerida (clasificar/puntuar/redactar breve). |
| **Calidad** | ★★★★☆ | Suficiente para las tareas: clasificación binaria, puntuación con justificación, redacción breve en español. Validado por el golden (28/28 en rango) y el juez de fundamentación (paso 4). |
| **Español** | ★★★★☆ | Qwen3 rinde bien en español (dominio del proyecto: Perú). |
| **Privacidad / residencia** | ★★☆☆☆ | ⚠️ **Groq es un proveedor de EE.UU.**: los prompts (con respuestas del candidato = PII, Ley 29733) salen del país. Mitigado por: trazas propias (no SaaS obligatorio), Sentry sin PII, y la posibilidad de migrar a un proveedor con residencia local o self-hosted sin tocar el motor. **Pendiente de producción real con datos de clientes**: evaluar un proveedor con acuerdo de tratamiento de datos / residencia. |

**Supresión del razonamiento** (`reasoning_effort: "none"`): consciente. Las tareas son
clasificación/puntuación/redacción breve con salida JSON; el CoT encarece y complica el parseo sin
mejorar el resultado (los campos `justification`/`ack` son racionalización para el reclutador, no
razonamiento intermedio). Ver 2.2.1 de la auditoría.

## Alternativas consideradas

- **GPT-4o / Claude (modelos frontier)**: mejor calidad marginal, ~10× costo y PII igualmente fuera
  del país. No justificado para tareas de esta complejidad. Se pueden adoptar puntualmente vía el
  routing por etapa si alguna etapa lo requiriera.
- **Modelo local (LM Studio / Ollama, on-prem)**: resuelve la residencia de datos, pero exige
  infraestructura de GPU y sube la latencia. Viable como camino de privacidad si un cliente lo pide;
  la arquitectura ya lo permite (solo cambia `OPENAI_API_BASE`).
- **Un solo modelo para todo (status quo pre-paso-5)**: simple, pero paga el modelo grande en
  etapas triviales (`classify` corre en CADA turno). El routing lo corrige sin complejidad de motor.

## Cómo cambiar de modelo (procedimiento)

1. Ajustar `OPENAI_MODEL` (y `OPENAI_API_BASE`/`OPENAI_API_KEY` si cambia el proveedor).
2. Correr el **golden** (`scripts/golden_eval.py`, 4 suites) como banco de aceptación.
3. Correr el **juez de fundamentación** (`scripts/groundedness_judge.py`) sobre trazas reales.
4. Comparar costo/latencia con el desglose `by_model` del dashboard (O-2/O-3).
5. Actualizar los precios en `llm_pricing` del tenant.

## Cómo elegir un modelo barato (banco de aceptación, 2.1.2)

El golden acepta `--model` para benchmarkear un candidato SIN tocar el `.env`. Se validan las
suites de las etapas ruteadas (`classify` para `classify`, `slot` para `schedule`):

```
uv run python scripts/golden_eval.py --model <candidato> --suite classify
uv run python scripts/golden_eval.py --model <candidato> --suite slot
```

Un candidato se acepta si pasa ambas (sale con código 0). Candidatos medidos (Groq, 2026-07-03):

| Candidato | classify | slot | Veredicto |
|---|---|---|---|
| `llama-3.1-8b-instant` | 7/7 | 6/6 | ✅ **elegido** (el más barato) |
| `openai/gpt-oss-20b` | 7/7 | 6/6 | ✅ apto (alternativa) |

## Palancas de costo activas (paso 5)

| Palanca | Config | Efecto |
|---|---|---|
| Routing por etapa | `LLM_CHEAP_MODEL=llama-3.1-8b-instant`, `LLM_CHEAP_STAGES` (`classify,schedule`) | Etapas simples → modelo barato validado (golden 13/13); medible en `llm_usage.model`. |
| Caché de dudas | `INTERVIEW_ANSWER_CACHE_ENABLED` | Duda repetida por vacante → 0 tokens (hit semántico). |
| Guardrails de consumo | `TurnGovernor`, topes de iteración, cortes sin-LLM | Ya evitan gasto inútil por diseño (auditoría 2.2.1/2.2.2). |
| Presupuesto + alerta | `llm_budget` por tenant (O-2) | Alerta al 80% del presupuesto mensual. |

**Revisión mensual sugerida** (auditoría 2.3.3): 10 min con el desglose `cost_by_model` del
dashboard para decidir si mover más etapas al modelo barato o ajustar el principal.
