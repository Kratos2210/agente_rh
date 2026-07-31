# Delta: examenes-contratacion

## ADDED Requirements

### Requirement: Recordatorios en fase médica
El barrido de inactividad DEBE (SHALL) incluir los estados `medical_pending` y
`medical_scheduled`, enviando recordatorios al candidato con la configuración por-tenant de
inactividad (`reminder_minutes`, `max_reminders`); al agotarlos NO DEBE (MUST) auto-cerrar el
proceso, sino emitir la alerta operativa `medical_unresponsive` para decisión de RR.HH.

#### Scenario: Candidato no confirma la cita médica
- **GIVEN** un candidato en `medical_scheduled` sin actividad por más de `reminder_minutes`
- **WHEN** corre el barrido de inactividad
- **THEN** recibe un recordatorio de la cita por Telegram

#### Scenario: Recordatorios agotados
- **GIVEN** un candidato en fase médica con `max_reminders` enviados y sin respuesta
- **WHEN** corre el siguiente barrido
- **THEN** su estado no cambia y aparece la alerta `medical_unresponsive` en observabilidad
