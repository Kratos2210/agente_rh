# entrevista Specification

## Purpose
La entrevista conversacional: consentimiento, preguntas con follow-ups, dudas del candidato,
inactividad y protecciones del turno. Manual de dominio: `spec/Agente.md` y `spec/Producto.md`.

## Requirements

### Requirement: Consentimiento previo
El sistema DEBE (SHALL) obtener consentimiento explícito (botón Acepto) antes de la primera
pregunta y sellar `consent_at` una sola vez; "No interesado" DEBE (SHALL) cerrar como `declined`.

#### Scenario: Candidato acepta
- **GIVEN** un candidato `invited` que recibió saludo + botones
- **WHEN** toca Acepto
- **THEN** se sella `consent_at`, recibe el detalle del puesto y la primera pregunta

### Requirement: Follow-ups acotados por pregunta
Ante una respuesta vaga el agente DEBE (SHALL) repreguntar hasta `INTERVIEW_MAX_FOLLOW_UPS`
veces por pregunta y luego DEBE (MUST) avanzar a la siguiente.

#### Scenario: Se agota el follow-up
- **GIVEN** `INTERVIEW_MAX_FOLLOW_UPS=1` y una respuesta vaga ya repreguntada
- **WHEN** la segunda respuesta sigue vaga
- **THEN** el agente registra la evaluación y pasa a la siguiente pregunta

### Requirement: Respuesta vacía no consume recursos
Una respuesta vacía o de solo símbolos DEBE (SHALL) producir una re-pregunta amable SIN llamar
al LLM y SIN consumir follow-up.

#### Scenario: Candidato manda "..."
- **GIVEN** una pregunta activa
- **WHEN** el candidato responde "..."
- **THEN** el agente repite la pregunta con un nudge, sin registrar evaluación ni gastar tokens

### Requirement: Dudas del candidato con tope
El agente DEBE (SHALL) responder dudas sobre el puesto fundándose solo en la información de la
vacante/base de conocimiento, con tope de 3 dudas por pregunta; alcanzado el tope DEBE (MUST)
cortar sin LLM e insistir con la pregunta pendiente.

#### Scenario: Cuarta duda consecutiva
- **GIVEN** un candidato que ya hizo 3 dudas en la misma pregunta
- **WHEN** envía una cuarta duda
- **THEN** recibe el corte de dudas (sin llamada LLM) y la pregunta pendiente

### Requirement: Revalidación del CV
Las preguntas con `cv_field` DEBEN (SHALL) reformularse citando el dato del CV ("Según tu CV:
«…»") y el `cv_context` DEBE (SHALL) entrar a la evaluación de esa respuesta.

#### Scenario: Pregunta de experiencia con CV cargado
- **GIVEN** una pregunta con `cv_field=experiencia` y un CV importado
- **WHEN** el agente formula la pregunta
- **THEN** el enunciado cita el dato del CV y el scoring recibe el contexto

### Requirement: Manejo de inactividad
El sistema DEBE (SHALL) enviar recordatorios configurables por tenant (`reminder_minutes`,
`max_reminders`) en las fases saludo/entrevista/documentos/agenda, y al agotarlos DEBE (SHALL)
cerrar: entrevista → `no_response`; documentos → `finished` sin penalizar; la coordinación de
agenda NO DEBE (MUST) auto-cerrarse (escala a RR.HH.).

#### Scenario: Candidato deja de responder en la entrevista
- **GIVEN** recordatorios agotados en fase entrevista
- **WHEN** el barrido dispara el timeout
- **THEN** la conversación cierra con mensaje de cierre y el candidato queda `no_response`

### Requirement: Entrada del candidato blindada
Todo texto del candidato que entre a un prompt DEBE (MUST) ir sanitizado (delimitadores fuera,
tope de longitud) y encerrado entre `<<<respuesta>>>…<<<fin>>>`; los patrones de eco-inyección
DEBEN (SHALL) derivarse con respuesta segura sin llamar al LLM.

#### Scenario: Instrucción de eco inyectada en una duda
- **GIVEN** una "duda" que ordena responder únicamente con una palabra dada
- **WHEN** el clasificador la rutea como duda
- **THEN** el agente responde con la deflexión segura sin invocar al modelo

### Requirement: Turno atómico y estados terminales
Los turnos de un mismo thread DEBEN (MUST) serializarse (lock por thread, también entre
réplicas); en estados `medical_*`/`hired` un mensaje del candidato DEBE (SHALL) recibir un acuse
sin reprocesar la máquina de estados.

#### Scenario: Mensaje durante el examen médico
- **GIVEN** un candidato en `medical_scheduled`
- **WHEN** escribe por Telegram
- **THEN** recibe el acuse informativo y su estado no cambia
