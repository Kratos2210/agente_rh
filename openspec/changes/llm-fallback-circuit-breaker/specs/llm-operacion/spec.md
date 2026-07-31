# Delta: llm-operacion

## ADDED Requirements

### Requirement: Fallback de proveedor con circuit breaker
Un fallo del proveedor principal DEBE (SHALL) resolverse sirviendo la llamada con el proveedor
de respaldo (`LLM_FALLBACK_*`) de forma transparente para el candidato; tras el umbral de
fallos consecutivos el circuito DEBE (SHALL) abrirse (las llamadas van directo al respaldo sin
pagar el timeout del principal) y DEBE (SHALL) sondear la recuperación tras el cooldown. El
modelo que realmente atendió cada llamada DEBE (MUST) quedar atribuido en el metering y las
trazas. Sin configuración de respaldo el comportamiento DEBE (MUST) ser el actual (fallback
heurístico `low_confidence`). Un proveedor de respaldo SOLO DEBE (MUST) habilitarse tras pasar
el banco de aceptación golden (mismo gating que el routing barato).

#### Scenario: Outage del principal con respaldo configurado
- **GIVEN** el proveedor principal devuelve errores y el respaldo está configurado
- **WHEN** se evalúa la respuesta de un candidato
- **THEN** la evaluación la sirve el modelo de respaldo, sin `low_confidence`, y `llm_usage`
  atribuye la llamada al modelo de respaldo

#### Scenario: Circuito abierto no paga el timeout
- **GIVEN** el umbral de fallos consecutivos alcanzado (circuito abierto)
- **WHEN** llega la siguiente llamada LLM antes del cooldown
- **THEN** se sirve directo con el respaldo, sin intentar el principal

#### Scenario: Recuperación del principal
- **GIVEN** el circuito abierto y el cooldown vencido
- **WHEN** llega la siguiente llamada
- **THEN** una sonda intenta el principal; si responde, el circuito cierra y vuelve a ser el
  proveedor activo

#### Scenario: Sin respaldo configurado (default)
- **GIVEN** `LLM_FALLBACK_MODEL` vacío
- **WHEN** el proveedor principal falla
- **THEN** aplica el comportamiento actual (fallback heurístico marcado `low_confidence`)
