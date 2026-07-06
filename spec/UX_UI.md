# UX/UI — Dashboard del reclutador (Next.js)

> Parte de [spec/](README.md) · Última revisión: 2026-07-06 · Código: `frontend/src/`

## 1. Propósito y alcance

La interfaz del reclutador/admin: pipeline de candidatos, detalle con scorecard, configuración
y observabilidad. Next.js 16 (App Router) + TypeScript + Tailwind, sin librerías de UI ni de
gráficos. La audiencia es RR.HH. — lenguaje de negocio en español, cero jerga técnica.

## 2. Decisiones de diseño

- **Cliente API único tipado** (`lib/api.ts`): todos los fetch pasan por `req()` — adjunta el
  `Bearer`, redirige a `/login?expired=1` en 401, y muestra el `detail` del backend como
  mensaje humano (`errorMessage()`). Los tipos TS espejan las respuestas del backend.
- **Espejo de estados** (`lib/stages.ts`): `ALL_KNOWN`/`KANBAN_COLUMNS`/`PHASE_STEPS` duplican
  `core/estados.py` — con un test de paridad backend↔frontend que impide que diverjan.
- **Sesión en localStorage + guard en `Shell`**: sin backend de sesión; el `Shell` (layout con
  nav) verifica el token, gatea entradas por rol (`/observabilidad` solo admin) y ofrece logout.
- **SVG a mano en vez de librería de charts**: el radar de criterios es un SVG inline
  (polígono del candidato + polígono punteado del umbral, labels por vértice) — cero deps,
  tema propio.
- **Confirmaciones proporcionales al riesgo**: acciones destructivas (erasure) exigen escribir
  el nombre del candidato en un modal; acciones normales son un click.

## 3. Diseño e implementación

### Páginas (12, `frontend/src/app/`)

| Ruta | Contenido |
|---|---|
| `/` | Vacantes + alta, roster RR.HH., embudo global, métricas de tokens/costo/latencia |
| `/login` | Login (aviso de sesión expirada con `?expired=1`) |
| `/vacantes/nueva` | Alta de vacante (preguntas, criterios, reclutador/líder/gerencia) |
| `/vacantes/[id]` | Candidatos de la vacante: tiles por fase, Kanban, búsqueda con debounce 300 ms, paginación, deep-link copiable, kit de onboarding, métricas |
| `/candidatos/[id]` | Detalle: stepper de fases, scorecard + radar, transcripción, documentos (links a PDF), reuniones por etapa, asistencia, feedback+decisión, exámenes psicológico/médico, onboarding, trazas LLM (admin), zona de peligro (erasure) |
| `/pipeline` | Pipeline global cross-vacante con búsqueda (`?q=` desde el header) y paginación |
| `/equipo` | Roster de reclutadores (cartillas con email/teléfono/dirección, alta y edición) |
| `/configuracion` | Tarjetas por-tenant: auto-contacto, inactividad, agendamiento, retención, examen médico, costos/presupuesto LLM, proveedor LLM (BYOK con "Probar conexión"), SLAs, calidad |
| `/observabilidad` | Admin: alertas operativas, salud del outbox (+retry), calidad IA del día, rendimiento HTTP (p95/p99), bitácora de auditoría |
| `/onboarding` | Contratados: fecha de ingreso, kit enviado/pendiente |
| `/costos` | Reporte de costos paginado |
| `/guia` | Guía técnica/funcional embebida (17 secciones, CSS aislado bajo `#guia-doc`) |

### Patrones de interfaz
- **Semáforo** 🟢/🟡/🔴 consistente en listas, detalle y Kanban; badge "⚠ Requiere revisión
  humana" cuando `review_required`.
- **Stepper de fases** (Contactado → Entrevista → RR.HH. → Líder → Gerencia → Examen médico →
  Decisión) alimentado por `phaseMeta`/`PHASE_STEPS`.
- **Estados de carga y error explícitos**: guards "Cargando…" (sin flash de estado vacío) y
  banners de error con el mensaje del backend (los `.catch(() => {})` se erradicaron).
- **Gating por rol en el cliente** (nav y secciones admin-only) — la autoridad real siempre es
  el backend; la UI solo esconde.
- **Enmascarado**: credenciales de examen psicológico ocultas para viewer; API key BYOK siempre
  como `gsk_...XXXX` con input password "vacío = mantener".

## 4. Contratos e invariantes

- Ninguna página llama `fetch` directo: todo por `lib/api.ts`.
- La UI nunca inventa estados: labels y columnas salen de `stages.ts` (paridad testeada).
- `tsc --noEmit` y `npm run lint` limpios son gate de CI; los cambios de API breaking se
  acompañan del frontend en el mismo commit.

## 5. Patrones reutilizables

- **Un `req()` central** con auth+errores+redirect: el 90 % de la robustez de UI en un solo lugar.
- **Espejo tipado de catálogos del backend + test de paridad** en vez de strings sueltos.
- **SVG inline para visualizaciones simples** (radar, barras): menos deps, tema exacto,
  render server-friendly.
- **Confirmación proporcional al riesgo** (click → confirm → escribir-el-nombre).

## 6. Pendientes conocidos

- UI de gestión de usuarios (hoy API + CLI `scripts/create_user.py`).
- i18n (hoy es-PE fijo); accesibilidad auditada informalmente.

## 7. Trazabilidad

- Verificación: `npx tsc --noEmit`, `npm run lint`, `npm run build` (CI); smokes Playwright
  headless en verificaciones en vivo; `test_estados.py` (paridad con `stages.ts`).
- Guía de usuario: `/guia` (fuente `frontend/src/app/guia/page.tsx`).
