# privacidad-retencion Specification

## Purpose
Protección de datos personales del candidato (Ley 29733 Perú): consentimiento, minimización,
retención, borrado y confinamiento de la PII. Manual de dominio: `spec/Seguridad.md`.

## Requirements

### Requirement: Consentimiento sellado una vez
El consentimiento DEBE (SHALL) registrarse (`consent_at`) en el momento de aceptar la
entrevista y NO DEBE (MUST) sobrescribirse en interacciones posteriores.

#### Scenario: Candidato acepta dos veces (reintento)
- **GIVEN** un candidato que ya aceptó
- **WHEN** vuelve a llegar el evento de aceptación
- **THEN** `consent_at` conserva el primer timestamp

### Requirement: Minimización hacia terceros
La PII DEBE (MUST) minimizarse hacia todo servicio externo: perfil sin identificadores hacia el
LLM, LangSmith con inputs/outputs ocultos por defecto, Sentry sin datos personales
(`send_default_pii=False`), Phoenix solo self-hosted.

#### Scenario: Tracing SaaS activado
- **GIVEN** LangSmith habilitado con la configuración por defecto
- **WHEN** se traza una llamada de evaluación
- **THEN** al SaaS llega estructura/latencia/tokens pero no el texto del candidato

### Requirement: Retención con anonimización completa
El barrido de retención (configurable por tenant, apagado por defecto) DEBE (SHALL) anonimizar
candidatos descartados con más de N días cubriendo TODA copia de PII: nombre, chat, CV,
documentos, transcripción, checkpoint LangGraph, exámenes psicológico/médico, onboarding,
trazas LLM, outbox y scrub del audit log.

#### Scenario: Descartado supera la ventana de retención
- **GIVEN** un candidato `rejected` con 90 días y retención de 60 activa
- **WHEN** corre el barrido
- **THEN** su PII queda anonimizada en todas las tablas y su checkpoint eliminado

### Requirement: Derecho de supresión (erasure)
DEBE (SHALL) existir borrado total por admin (`DELETE /api/candidates/{id}`) con confirmación
explícita, auditado, en cascada completa (documentos, trazas, outbox, checkpoint) y sin dejar
el nombre en el registro de auditoría.

#### Scenario: Erasure desde el dashboard
- **GIVEN** un admin en el detalle del candidato
- **WHEN** confirma escribiendo el nombre en el modal
- **THEN** el candidato y todos sus datos desaparecen y queda un evento de auditoría sin PII

### Requirement: Secretos y credenciales nunca en claro
Las API keys BYOK DEBEN (MUST) almacenarse cifradas, mostrarse enmascaradas y excluirse de los
mensajes de error; las credenciales de exámenes DEBEN (SHALL) enmascararse para el rol viewer;
el token del bot NO DEBE (MUST) aparecer en logs.

#### Scenario: GET de la configuración del proveedor LLM
- **GIVEN** una API key configurada
- **WHEN** un admin consulta `GET /api/settings/llm-provider`
- **THEN** la key aparece como `gsk_...XXXX` y nunca completa
