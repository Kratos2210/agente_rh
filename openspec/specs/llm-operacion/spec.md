# llm-operacion Specification

## Purpose
Operación de los modelos de lenguaje: medición, proveedor por-tenant, routing de costos,
versionado de prompts, presupuesto y trazabilidad. Manual de dominio: `spec/Orquestacion.md`,
`spec/Costos.md` y `spec/Observabilidad.md`.

## Requirements

### Requirement: Metering universal por etapa
Toda llamada a un LLM DEBE (MUST) pasar por el wrapper de metering con etiqueta de etapa
(prescreen/evaluate/classify/answer/schedule/judge), registrando tokens, llamadas, errores,
latencia y el modelo real que la sirvió.

#### Scenario: Turno de entrevista con routing activo
- **GIVEN** routing del modelo barato para `schedule`
- **WHEN** un turno usa evaluate y schedule
- **THEN** `llm_usage` atribuye cada etapa a su modelo con sus tokens y latencia

### Requirement: El LLM nunca tumba el flujo
Un fallo del proveedor DEBE (SHALL) resolverse con fallback marcado `low_confidence` y log de
fallback; el turno del candidato DEBE (MUST) completarse igual.

#### Scenario: Timeout del proveedor en una evaluación
- **GIVEN** el proveedor agota reintentos
- **WHEN** se evalúa la respuesta
- **THEN** el flujo continúa con la evaluación de fallback marcada para revisión

### Requirement: BYOK por-tenant seguro
La API key del proveedor por-tenant DEBE (MUST) almacenarse cifrada (Fernet con clave derivada),
mostrarse solo enmascarada, excluirse de errores, y el `base_url` DEBE (MUST) rechazar
endpoints privados salvo `ALLOW_PRIVATE_LLM_ENDPOINTS=true` (anti-SSRF). El cambio de proveedor
DEBE (SHALL) aplicarse en caliente por turno (hot-swap best-effort, fallback al `.env`).

#### Scenario: Tenant cambia de Groq a Gemini
- **GIVEN** una configuración BYOK nueva guardada
- **WHEN** llega el siguiente turno de un candidato del tenant
- **THEN** el turno corre con el proveedor nuevo sin reiniciar el backend

### Requirement: Routing barato con banco de aceptación
Un modelo barato SOLO DEBE (MUST) rutearse a una etapa tras pasar la suite golden de esa etapa;
las reversiones DEBEN (SHALL) documentarse en el ADR de selección de modelo.

#### Scenario: Candidato barato falla la suite
- **GIVEN** un modelo que no pasa `golden_eval --suite classify`
- **WHEN** se decide el routing
- **THEN** la etapa permanece en el modelo principal y el resultado queda registrado

### Requirement: Prompts versionados con gate
Los prompts DEBEN (SHALL) llevar `PROMPT_VERSION` con changelog; el CI DEBE (MUST) bloquear
cambios del archivo de prompts sin bump de versión; la versión DEBE (SHALL) sellarse en
scorecards y uso LLM.

#### Scenario: PR que edita un prompt sin subir versión
- **GIVEN** un diff sobre `agente/prompts.py` con `PROMPT_VERSION` intacta
- **WHEN** corre el CI
- **THEN** el job `prompt-version-gate` falla

### Requirement: Presupuesto mensual con alerta única
El gasto mensual por tenant DEBE (SHALL) vigilarse contra `llm_budget` y alertar por correo al
cruzar el umbral porcentual, UNA vez por tenant/mes/umbral.

#### Scenario: Tenant cruza el 80 % del presupuesto
- **GIVEN** presupuesto activo con alerta al 80 %
- **WHEN** el barrido detecta 91 % consumido
- **THEN** se envía un correo de alerta y las corridas siguientes del mes no lo repiten

### Requirement: Trazas con contenido bajo flag y purgables
Las trazas prompt/respuesta por llamada DEBEN (SHALL) capturarse solo con `LLM_TRACE_ENABLED`,
capadas en longitud, visibles solo para admin, y DEBEN (MUST) purgarse con la retención y el
erasure del candidato.

#### Scenario: Erasure de un candidato trazado
- **GIVEN** trazas LLM almacenadas de sus evaluaciones
- **WHEN** un admin lo borra
- **THEN** las trazas desaparecen con él
