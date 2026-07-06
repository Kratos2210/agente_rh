# documentos Specification

## Purpose
Recepción, validación y almacenamiento de los documentos del candidato (CV y CUL en PDF) tras
calificar en verde. Manual de dominio: `spec/Canales.md` y `spec/Base_datos.md`.

## Requirements

### Requirement: Validación antes de descargar
Todo documento recibido DEBE (MUST) validarse ANTES de descargarse: solo PDF, tamaño ≤ 20 MB y
nombre sanitizado contra path-traversal; el destino DEBE (MUST) quedar confinado a `uploads/`.

#### Scenario: Archivo con nombre malicioso
- **GIVEN** un PDF llamado `../../etc/passwd.pdf`
- **WHEN** el bot lo recibe
- **THEN** el nombre se sanitiza y el archivo solo puede escribirse dentro de `uploads/`

### Requirement: Verificación de tipo de contenido
Con `DOCUMENT_CONTENT_CHECK_ENABLED` activo, el sistema DEBE (SHALL) verificar que el contenido
del PDF corresponda al tipo pedido (CV vs CUL) y ante mismatch DEBE (SHALL) rechazarlo y volver
a pedirlo; ante fallo de extracción DEBE (SHALL) aceptar (fail-open).

#### Scenario: Suben el CUL cuando se pidió el CV
- **GIVEN** el agente pidió la hoja de vida
- **WHEN** llega un PDF cuyo texto corresponde a un Certificado Único Laboral
- **THEN** el bot lo rechaza explicando el motivo y vuelve a pedir el CV

### Requirement: Almacenamiento durable con umbral
El contenido DEBE (SHALL) persistirse en Postgres (base64) hasta `DOCUMENT_DB_MAX_BYTES`
(default 5 MB); por encima DEBE (SHALL) quedar solo en disco con flag `stored="disk"`. La
descarga DEBE (SHALL) servirse desde la DB con fallback a disco para documentos previos.

#### Scenario: PDF de 2 MB sobrevive un redeploy
- **GIVEN** un CV de 2 MB recibido y persistido
- **WHEN** el backend se redespliega y se pide `GET /api/candidates/{id}/documents/cv`
- **THEN** el PDF se sirve íntegro desde la base de datos

### Requirement: Secuencia con omisión permitida
Tras calificar verde, el agente DEBE (SHALL) pedir CV y CUL en orden, permitir omitir cada uno,
y cerrar la conversación al recibir u omitir ambos; el scorecard NO DEBE (MUST) esperar a los
documentos.

#### Scenario: Candidato omite el CUL
- **GIVEN** el CV ya recibido
- **WHEN** el candidato responde que omitirá el CUL
- **THEN** la conversación cierra con el agradecimiento y el CUL queda pendiente en el detalle
