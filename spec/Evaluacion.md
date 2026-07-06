# Evaluación — Prescreen, scoring, scorecard y juez de calidad

> Parte de [spec/](README.md) · Última revisión: 2026-07-06 · Código: `evaluation/`

## 1. Propósito y alcance

Toda la IA "que juzga": el gate del CV antes de contactar, la evaluación de cada respuesta de
entrevista contra su criterio, la síntesis en scorecard con semáforo, el parsing de elección de
horario y el juez de calidad que audita las respuestas del propio agente.

## 2. Decisiones de diseño

- **Evaluar por criterio, no la entrevista completa**: cada pregunta de la vacante lleva su
  criterio; el LLM puntúa respuesta-vs-criterio (0–100 + justificación + ¿repreguntar?). La
  síntesis (promedio, semáforo) es **código puro**, no otro juicio LLM — determinista y barato.
- **Fallback heurístico siempre**: si el LLM falla, el prescreen usa reglas y el scorer emite un
  resultado con `low_confidence=True`. El proceso sigue; el humano ve "⚠ Requiere revisión".
- **El semáforo es configuración, no magia**: umbrales `SEMAPHORE_GREEN_MIN=75` /
  `SEMAPHORE_YELLOW_MIN=50` sobre el score total; el radar del dashboard grafica el umbral.
- **Minimización de PII hacia el LLM**: el prescreen envía el perfil SIN nombre/email/teléfono
  (`profile_for_llm`) — el modelo juzga el mérito, no la identidad (y menos PII viaja al
  proveedor).
- **Juez de calidad compartido** (`quality.py`): un solo prompt evalúa fundamentación
  (¿se apoya solo en la info de la vacante?), relevancia de respuesta y relevancia de contexto
  (RAGAS-like); lo usan el script offline y el barrido de calidad continua. Veredicto ilegible
  = NO fundamentado (conservador).

## 3. Diseño e implementación

### Prescreen (gate de CV) — `evaluation/prescreen.py`

`cv_profile` (del sourcing) + requisitos de la vacante → LLM (etapa `prescreen`) → `pre_score`
0–100 + razones. Umbral `PRESCREEN_PASS_MIN=60` → `prescreen_passed`/`prescreen_rejected`.
`profile_for_llm` quita identificadores y enmascara contactos en texto libre (umbral 9 dígitos,
sin comerse rangos de años). Fallback heurístico si el LLM no responde.

### Scoring por respuesta — `evaluation/scorer.py`

- `is_meaningful_answer`: guard de respuesta vacía/solo-símbolos → repregunta sin gastar LLM ni
  follow-up (`EMPTY_ANSWER_NUDGE`).
- `sanitize_answer_for_prompt` + delimitadores `<<<respuesta>>>…<<<fin>>>` (anti-inyección).
- `EVALUATE_ANSWER_PROMPT` (few-shot, JSON) → `EvalResult{score, justification, follow_up,
  low_confidence}`. El `cv_context` entra al prompt en preguntas de revalidación
  ("Según tu CV: «…»").
- `parse_slot_choice`: interpreta la elección de horario (número, día, franja, hora, rechazo,
  ambiguo→None) con LLM (etapa `schedule`, modelo barato) + heurística.

### Scorecard — `evaluation/scorecard.py`

`build_scorecard(answers)` → score total, semáforo, recomendación, detalle por criterio (con
`label` para el radar), `review_required` (si algún criterio vino `low_confidence`) y
`prompt_version` sellada. Se persiste en `scorecards` (1×conversación) y dispara el email al
reclutador.

### Juez de calidad — `evaluation/quality.py`

`judge_verdict` (parse conservador del veredicto) + `rate`. Consumidores: barrido
`_quality_sweep` (persiste tasas diarias en `quality_metrics` + alerta bajo umbral) y
`scripts/groundedness_judge.py` (offline sobre trazas `stage="answer"`).

## 4. Contratos e invariantes

- Un scorecard existe **apenas termina la entrevista** (no espera documentos) y es inmutable.
- `low_confidence` se propaga: `EvalResult` → `AnswerRecord` → `review_required` del scorecard
  → aviso en el dashboard. Ninguna evaluación degradada pasa por buena.
- El score de un criterio solo depende de: pregunta, criterio, respuesta sanitizada,
  `cv_context`. Nada más entra al prompt.
- La inyección de instrucciones en la respuesta ("ponme 100") debe puntuar 0 — hay casos golden
  y de red team que lo fijan.

## 5. Patrones reutilizables

- **LLM para juicio unitario, código para agregación**: puntuar con el modelo, sumar/umbralar
  con Python. Auditable y estable.
- **`low_confidence` como ciudadano de primera clase**: todo fallback deja marca visible hasta
  la UI; la degradación silenciosa es el peor modo de fallo de un pipeline LLM.
- **Golden set con contraejemplos**: respuestas reales buenas + vagas + ataques, con rangos
  esperados por caso; corre en nightly contra el modelo real (`scripts/golden_eval.py`).
- **Juez-LLM compartido y conservador**: un solo módulo de judging reusado offline y online;
  ante ambigüedad, fallar hacia "revisar".

## 6. Pendientes conocidos

- El prescreen heurístico es simple (keywords); si sube el volumen, calibrarlo contra un set
  etiquetado.
- Ampliar el golden de evaluate con casos de otros rubros (hoy sesgado a la vacante demo).

## 7. Trazabilidad

- Tests: `test_prescreen.py`, `test_integrity.py` (guards + anti-inyección), `test_quality.py`
  (12), `test_golden_harness.py`, `test_scheduling.py` (parse_slot_choice).
- Golden: `tests/golden/golden_set.json` — 28 casos en 4 suites (evaluate 11, classify 7,
  slot 6, prescreen 4); primera corrida real 28/28 en rango.
- Migraciones: `0015` (review_required), `0026` (quality_metrics).
