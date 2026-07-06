# spec/ — Especificaciones del sistema (spec-driven development)

> Proyecto **agente_rh** — Agente de selección de talento con entrevista conversacional,
> evaluación automática y proceso multi-etapa. Última revisión: 2026-07-06.

Esta carpeta contiene la **especificación por dominios** del sistema completo: qué hace,
cómo está diseñado, qué decisiones se tomaron (y por qué), qué invariantes se pueden asumir
y qué patrones son reutilizables. Es el resultado de una revisión end-to-end del código,
las migraciones, los tests, la infraestructura y las auditorías del proyecto.

## Para qué sirve

1. **Referencia de este proyecto**: fuente única por dominio, atemporal (a diferencia de la
   bitácora cronológica de `CLAUDE.md` o las auditorías `audit/*.md`, que son fotos en el tiempo).
2. **Base para proyectos nuevos**: cada spec destila los patrones probados aquí (sección
   "Patrones reutilizables") para arrancar un proyecto desde cero con metodología spec-driven.

## Metodología spec-driven (cómo usar esta carpeta)

- **El spec es el contrato**: antes de implementar o cambiar un dominio, se actualiza su spec
  (secciones 1–4); la implementación debe converger al spec, no al revés.
- **Trazabilidad en tres capas**: spec → código (punteros `ruta/archivo.py`) → tests
  (sección 7 de cada spec). Un cambio sin las tres capas está incompleto.
- **Decisiones con porqués**: la sección 2 registra alternativas descartadas. Si una decisión
  se revierte, se documenta el motivo (estilo ADR), no se borra la historia.
- **Deuda declarada**: la sección 6 lista pendientes honestos. Deuda no escrita = deuda invisible.
- **Para un proyecto nuevo**: copiar la estructura de archivos, conservar las secciones 2 y 5
  como semilla (decisiones y patrones ya validados), vaciar las secciones 3, 6 y 7 e ir
  llenándolas a medida que se implementa.

## Plantilla común

Todos los specs siguen la misma estructura:

1. **Propósito y alcance** · 2. **Decisiones de diseño** · 3. **Diseño e implementación** ·
4. **Contratos e invariantes** · 5. **Patrones reutilizables** · 6. **Pendientes conocidos** ·
7. **Trazabilidad** (tests, migraciones y docs relacionados).

## Índice

| Spec | Dominio |
|---|---|
| [Producto.md](Producto.md) | Visión funcional, flujo end-to-end, 22 estados del candidato, reglas de negocio |
| [Arquitectura.md](Arquitectura.md) | Componentes, límites, doble persistencia, patrones transversales |
| [Stack.md](Stack.md) | Tecnologías, versiones, pins y gotchas de plataforma |
| [Configuracion.md](Configuracion.md) | ~98 settings, convención "apagado por defecto", settings por-tenant |
| [Agente.md](Agente.md) | Cerebro conversacional LangGraph: estado, nodos, checkpointer, límites |
| [Orquestacion.md](Orquestacion.md) | Capa LLM: metering, BYOK por-tenant, routing por etapa, prompts |
| [Evaluacion.md](Evaluacion.md) | Prescreen de CV, scoring por criterio, scorecard con semáforo, juez de calidad |
| [RAG.md](RAG.md) | Base de conocimiento: Chroma, hybrid search, re-ranker, caché de respuestas |
| [APIS.md](APIS.md) | Los 67 endpoints FastAPI, convenciones de API, webhook Telegram |
| [Base_datos.md](Base_datos.md) | Supabase/Postgres: 21 tablas, 27 migraciones, RLS, RPCs |
| [Seguridad.md](Seguridad.md) | Auth/RBAC, multi-tenancy, secretos, anti-inyección, PII (Ley 29733) |
| [Canales.md](Canales.md) | Telegram dual-mode (polling/webhook), deep-links, gobierno de turnos |
| [Integraciones.md](Integraciones.md) | Sourcing de postulantes, agendamiento Google Calendar/Meet/Sheets |
| [Notificaciones.md](Notificaciones.md) | Outbox durable con reintentos, correos, avisos al candidato |
| [MCP.md](MCP.md) | Servidor Model Context Protocol: 7 tools, confirmación en dos pasos |
| [UX_UI.md](UX_UI.md) | Dashboard Next.js: 12 páginas, patrones de interfaz, sesión |
| [Observabilidad.md](Observabilidad.md) | Trazas LLM, latencias p95/p99, SLAs push, calidad continua, logs |
| [Costos.md](Costos.md) | Metering de tokens, pricing por tenant, presupuesto, optimización |
| [Test.md](Test.md) | 52 suites (~467 tests), fakes, invariantes estructurales, golden, red team |
| [CI_CD.md](CI_CD.md) | GitHub Actions, gates de calidad, nightly con LLM real, entrega continua |
| [Despliegue.md](Despliegue.md) | Docker, Kubernetes base+overlays, launchd local, decisión serverless |

## Relación con el resto de la documentación

- `CLAUDE.md` — bitácora cronológica de desarrollo (el "cómo llegamos aquí").
- `docs/` — runbooks y ADRs operativos (`gestion_secretos.md`, `despliegue.md`,
  `adr-seleccion-modelo.md`, `arquitectura.md`).
- `audit/` — auditorías de madurez con fecha (v1 72/100 → v4 ≈85/100); insumo de las secciones 6.
- `frontend/src/app/guia/` — guía navegable dentro del dashboard (audiencia: usuario del producto).
- `spec/` (esta carpeta) — estado final del sistema por dominio (audiencia: desarrollador).

## Convenciones

- Idioma: español (código e identificadores en inglés, como el resto del repo).
- Todo puntero a código/tabla/endpoint fue verificado contra el repo en la fecha de revisión.
- Nunca se incluyen valores reales de secretos: solo nombres de variables.
