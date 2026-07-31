# Tasks

## 1. Motor y prompts
- [ ] 1.1 Agregar `REMINDER_MEDICAL` a `agente/prompts.py` + bump de `PROMPT_VERSION` con changelog
- [ ] 1.2 Extender `_inactivity_sweep` (`api/scheduler.py`) para incluir `medical_pending`/`medical_scheduled` (recordar, sin auto-cierre)

## 2. Alerta operativa
- [ ] 2.1 Emitir `medical_unresponsive` desde `_collect_ops_alerts` al agotar recordatorios
- [ ] 2.2 Verificar que `_sla_sweep` la agrupe y empuje como el resto de ops alerts

## 3. Verificación
- [ ] 3.1 Tests en `tests/test_inactivity.py` (recordatorio médico + sin auto-cierre + alerta) — suite completa verde con `uv run pytest`
- [ ] 3.2 Smoke en vivo: candidato demo en `medical_scheduled` sin responder → recordatorio y alerta visibles en `/observabilidad`
- [ ] 3.3 Actualizar `spec/Producto.md` §6 (quitar el pendiente) y archivar este change (`/opsx:archive`)
