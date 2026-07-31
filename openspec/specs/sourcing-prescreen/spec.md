# sourcing-prescreen Specification

## Purpose
Importar postulantes desde portales de empleo y aplicar el pre-filtro automático de CV antes de
cualquier contacto. Manual de dominio: `spec/Integraciones.md` y `spec/Evaluacion.md`.

## Requirements

### Requirement: Dedupe por identidad de plataforma
El sync DEBE (SHALL) deduplicar candidatos por `source_ref` (id estable del portal) y NO por
atributos mutables como el chat; un re-sync NO DEBE (MUST) duplicar candidatos, retroceder
estados ni re-contactar a quien ya avanzó de fase.

#### Scenario: Re-sync con candidata ya contactada
- **GIVEN** una candidata importada con `source_ref=bmn-10241` que ya está en estado `invited`
- **WHEN** se ejecuta de nuevo el sync de postulantes de la vacante
- **THEN** no se crea otra candidata, su estado sigue `invited` y `contacted` reporta 0

### Requirement: Gate de CV con umbral configurable
El sistema DEBE (SHALL) puntuar cada CV contra los requisitos de la vacante (0–100) y clasificar
al candidato en `prescreen_passed` si `pre_score >= PRESCREEN_PASS_MIN` o `prescreen_rejected`
en caso contrario, registrando las razones.

#### Scenario: CV apto pasa el gate
- **GIVEN** un CV cuyo perfil cumple los requisitos y `PRESCREEN_PASS_MIN=60`
- **WHEN** corre el pre-filtro
- **THEN** el candidato queda `prescreen_passed` ("Apto · por contactar") sin ser contactado por el sync

### Requirement: Minimización de PII hacia el LLM
El prescreen DEBE (SHALL) enviar al LLM el perfil SIN nombre, email, teléfono ni id externo
(`profile_for_llm`), enmascarando contactos incrustados en el texto libre.

#### Scenario: Perfil con teléfono en el texto libre
- **GIVEN** un CV cuyo resumen incluye un número de 9 dígitos
- **WHEN** se construye el prompt del gate
- **THEN** el número va enmascarado y los campos de identidad no aparecen en el prompt

### Requirement: Degradación ante fallo del LLM
Si el LLM falla, el gate DEBE (SHALL) resolver con el fallback heurístico y continuar el proceso;
el fallo NUNCA DEBE (MUST) dejar al candidato sin clasificar.

#### Scenario: Proveedor LLM caído durante el sync
- **GIVEN** el proveedor LLM devuelve error en todas las llamadas
- **WHEN** corre el pre-filtro de 3 candidatos importados
- **THEN** los 3 quedan clasificados por la heurística y el sync termina sin excepción

### Requirement: Límite de frecuencia del sync
El endpoint de sincronización DEBE (SHALL) limitar a 2 ejecuciones por minuto por tenant y
responder 429 con mensaje humano al excederlo.

#### Scenario: Tercer sync dentro del minuto
- **GIVEN** dos syncs ejecutados en el último minuto para el tenant
- **WHEN** se dispara un tercero
- **THEN** la API responde 429 sin importar candidatos
