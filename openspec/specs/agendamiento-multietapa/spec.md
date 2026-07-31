# agendamiento-multietapa Specification

## Purpose
Coordinación conversacional de reuniones a lo largo de las tres etapas del proceso (RR.HH.,
líder, gerencia), con calendario real o simulado. Manual de dominio: `spec/Integraciones.md`,
`spec/Agente.md` y `spec/Producto.md`.

## Requirements

### Requirement: Slots dentro de la ventana laboral
Las opciones de horario propuestas DEBEN (SHALL) calcularse dentro de la ventana laboral del
tenant (días/horas/zona) descontando la agenda ocupada del entrevistador; NUNCA DEBE (MUST)
proponerse un slot fuera de ventana.

#### Scenario: Agenda con la mañana ocupada
- **GIVEN** ventana 09–18 L–V y el reclutador ocupado de 09 a 13 del lunes
- **WHEN** se generan 3 opciones
- **THEN** todas caen entre las 13 y las 18 de días hábiles

### Requirement: Una reunión por conversación y etapa
El sistema DEBE (SHALL) mantener a lo sumo UNA reunión por `(conversation_id, stage)`;
re-agendar DEBE (MUST) actualizar la existente, nunca duplicarla.

#### Scenario: Reagendar la etapa de líder
- **GIVEN** una reunión `lead` ya registrada
- **WHEN** se coordina un nuevo horario para la misma etapa
- **THEN** la fila existente se actualiza y sigue habiendo una sola reunión `lead`

### Requirement: Registro-primero ante el calendario externo
La reunión DEBE (MUST) registrarse en la base de datos ANTES de crear el evento en el proveedor
de calendario; el enlace/`event_id` se completa después. Un crash intermedio NO DEBE (MUST)
producir eventos duplicados.

#### Scenario: Crash entre registro y evento
- **GIVEN** la reunión registrada sin `meet_link`
- **WHEN** el proceso muere antes de crear el evento y la reconciliación la detecta
- **THEN** se alerta "reunión sin enlace" y el reintento completa la MISMA fila

### Requirement: Modalidad presencial sin Meet
Las reuniones `onsite` DEBEN (SHALL) crear el evento con `location` y SIN enlace Meet, y la
confirmación al candidato DEBE (SHALL) incluir dirección, contacto y recordatorio de DNI; la
reconciliación NO DEBE (MUST) alertar "sin enlace" para presenciales.

#### Scenario: Gerencia presencial
- **GIVEN** la etapa `manager` (100 % presencial)
- **WHEN** se confirma el horario
- **THEN** el evento lleva la dirección de oficina y el candidato recibe la confirmación presencial

### Requirement: Reintentos de elección acotados
El parsing de la elección de horario DEBE (SHALL) tolerar hasta `MAX_SLOT_RETRIES=3` intentos
fallidos, escalar a RR.HH. UNA sola vez y aun así aceptar una elección válida tardía.

#### Scenario: Candidato elige un horario inexistente tres veces
- **GIVEN** 3 respuestas que no matchean ningún slot
- **WHEN** llega la cuarta respuesta y es válida
- **THEN** ya se escaló a RR.HH. una vez y la reunión igualmente se agenda

### Requirement: Multi-etapa configurable por vacante
El encadenamiento de etapas DEBE (SHALL) depender de la asignación en la vacante: sin líder ni
gerencia el proceso DEBE (SHALL) cerrar en la Fase 1 (retrocompatibilidad); la decisión y el
feedback de cada etapa DEBEN (SHALL) registrarse en `stage_feedback`.

#### Scenario: Vacante sin líder asignado
- **GIVEN** una vacante solo con reclutador de RR.HH.
- **WHEN** RR.HH. aprueba la etapa `hr`
- **THEN** el proceso avanza directo a la decisión final sin fases intermedias
