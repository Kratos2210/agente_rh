# Plantilla de post-mortem (5 líneas)

> Roadmap v2 · paso 4 — operación a prueba de ausencias. Un post-mortem corto y sin
> culpables después de cada incidente que afecte a un candidato o al reclutador. El
> objetivo no es el reporte: es que la causa quede escrita y la prevención entre al
> backlog. Copiá el bloque de abajo a `docs/postmortems/AAAA-MM-DD-titulo.md` y llenalo.

---

## AAAA-MM-DD — <título corto del incidente>

- **Impacto:** <a quién y cuánto: p. ej. "3 candidatos no recibieron el correo de agendamiento durante 2 h", "el bot dejó de responder de 14:00 a 14:20">.
- **Causa raíz:** <qué falló de fondo, no el síntoma: p. ej. "el token de Telegram rotó y el Secret del pod quedó desactualizado">.
- **Detección:** <cómo nos enteramos y en cuánto: alerta de `/observabilidad`, correo de ops, o un humano lo notó — y la latencia hasta detectarlo>.
- **Mitigación:** <qué se hizo para volver a la normalidad: reintento del outbox, reinicio del pod, rollback, contacto manual al candidato>.
- **Prevención:** <el/los cambios concretos para que no vuelva a pasar, con dueño y ticket: p. ej. "mover el token a un secret manager (#42)", "alerta si el outbox supera N pendientes">.

---

## Cómo usarla

1. **Cuándo:** ante cualquier incidente con impacto en un candidato, en el reclutador, o
   en la disponibilidad del bot/dashboard. Si dudás si amerita post-mortem, escribilo: son 5 líneas.
2. **Cuándo NO:** un bug atrapado por CI o por un test antes de llegar a producción no necesita
   post-mortem (ya lo cubrió el proceso).
3. **Insumos de datos** (todo ya existe en el sistema, no hay que reconstruir de memoria):
   - `/observabilidad` — salud del outbox (envíos detenidos + motivo), alertas operativas,
     rendimiento HTTP y calidad de las respuestas.
   - Bitácora de auditoría (`GET /api/audit`) — quién hizo qué y cuándo.
   - Trazas LLM (`GET /api/candidates/{id}/traces`, admin) si el incidente fue de evaluación.
   - Logs del backend (JSON con `request_id` si `LOG_JSON=true`; Sentry si hay `SENTRY_DSN`).
4. **Sin culpables:** el post-mortem describe el sistema y el proceso, no a la persona. La
   pregunta es "¿qué del sistema permitió esto?", no "¿quién se equivocó?".
5. **Cierre:** cada línea de **Prevención** debe terminar como un ticket con dueño. Un
   post-mortem sin acción de prevención registrada no está cerrado.
