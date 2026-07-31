# Auditoría OpenSpec/SDD de `spec/` — y adopción del marco

> Fecha: 2026-07-06 · Marco auditado: [OpenSpec](https://github.com/Fission-AI/openspec)
> (Fission-AI, CLI v1.5.0) · Objeto: carpeta `spec/` (22 docs, commit `66b07eb`)
> Resultado: **adopción del CLI oficial** con arquitectura de dos capas (este informe documenta
> el veredicto punto por punto y las decisiones).

## 1. Qué define OpenSpec

OpenSpec es un sistema de **Spec-Driven Development nativo para agentes IA**. Su modelo:

- **`openspec/specs/<capacidad>/spec.md`** — la *verdad actual* del sistema en formato
  normativo: `## Purpose` + `## Requirements` con `### Requirement:` (keyword SHALL/MUST
  obligatoria — el validador la exige literal) y `#### Scenario:` (GIVEN/WHEN/THEN).
- **`openspec/changes/<nombre>/`** — *propuestas de cambio*: `proposal.md` (por qué/qué),
  `design.md` (cómo, opcional), `tasks.md` (checklist) y `specs/` con **deltas**
  (`## ADDED/MODIFIED/REMOVED/RENAMED Requirements`).
- **Archivo** — al completarse un cambio, los deltas se fusionan a los specs principales y la
  carpeta pasa al historial.
- **Ciclo**: `/opsx:explore` → `/opsx:propose` → `/opsx:apply` → `/opsx:archive` (+ `sync`).
- **Tooling**: CLI `openspec init/validate/list/status/archive/...` + slash commands y skills
  para el asistente + contexto del proyecto en `openspec/config.yaml` (v1.5; la doc histórica
  menciona `project.md`).

## 2. Matriz de auditoría: `spec/` vs los puntos de OpenSpec

| # | Punto del marco | Veredicto | Evidencia / brecha |
|---|---|---|---|
| 1 | Verdad actual documentada por capacidad | 🟡 Parcial | Los 22 docs de `spec/` documentan TODO el sistema, pero por *dominio técnico* (RAG, APIs, DB), no por *capacidad de comportamiento*; sin formato normativo |
| 2 | Requirements con SHALL/MUST | ❌ Faltaba | Lo más cercano: sección "Contratos e invariantes" de cada doc (declarativa, no normativa, sin keyword) |
| 3 | Escenarios GIVEN/WHEN/THEN | ❌ Faltaba | Ningún doc tenía escenarios verificables |
| 4 | Workflow de cambios (proposal/design/tasks/deltas) | ❌ Faltaba | `spec/` solo describía el estado final; no existía el artefacto "propuesta" — la mitad del SDD que gobierna mejoras |
| 5 | Archivo/historial de cambios | ❌ Faltaba | El historial vivía en la bitácora de CLAUDE.md (narrativa, no fusionable) |
| 6 | Contexto del proyecto para el agente | 🟡 Parcial | Repartido entre CLAUDE.md (bitácora de 100+ KB) y `spec/README.md`; nada en el formato compacto que el agente consume al proponer |
| 7 | Validación automática de specs | ❌ Faltaba | Sin validador; los specs podían derivar en formato/contenido |
| 8 | Integración con el asistente (slash commands) | ❌ Faltaba | La metodología del README era manual, sin comandos |
| 9 | Decisiones con porqués (ADR) | ✅ Cubierto de más | Sección 2 de cada spec + `docs/arquitectura.md` — **OpenSpec no modela esto** |
| 10 | Patrones reutilizables para proyectos nuevos | ✅ Cubierto de más | Sección 5 de cada spec — fuera del alcance de OpenSpec |
| 11 | Trazabilidad a tests/migraciones | ✅ Cubierto de más | Sección 7 de cada spec — OpenSpec no la exige |
| 12 | Deuda declarada | ✅ Cubierto de más | Sección 6 de cada spec (los pendientes alimentan propuestas: ver §5) |

**Síntesis**: `spec/` era una excelente *biblioteca de dominio* pero no un sistema SDD operable:
le faltaban el formato normativo verificable (puntos 2–3, 7) y — lo más importante — **el ciclo
de cambios** (4–5, 8) que es donde el spec "gobierna" el desarrollo futuro. A la inversa,
OpenSpec no cubre decisiones/patrones/trazabilidad (9–12), que es justo lo que `spec/` hace bien.

## 3. Decisión: dos capas complementarias

Se adoptó el **CLI oficial** (`@fission-ai/openspec` 1.5.0, `openspec init --tools claude`) con
esta arquitectura documental:

| Capa | Carpeta | Rol | Cuándo se toca |
|---|---|---|---|
| **Normativa (OpenSpec)** | `openspec/specs/` + `openspec/changes/` | QUÉ debe cumplir el sistema (requirements + escenarios) y CÓMO evoluciona (propuestas → deltas → archivo) | En cada mejora: `/opsx:propose` → apply → archive |
| **Dominio (manual)** | `spec/` (22 docs) | POR QUÉ es así (decisiones), CÓMO está hecho (implementación), qué patrones llevar a proyectos nuevos | Cuando cambia una decisión, un patrón o la implementación de fondo |

Reglas de convivencia:
- Cada capability spec enlaza su manual de dominio en el `## Purpose`; los docs de `spec/`
  enlazan su(s) capability specs.
- Una mejora nace como change en `openspec/changes/` (nunca editando el spec principal
  directo); al archivar, si cambió una decisión, se actualiza también el doc de dominio.
- Idioma: specs en español con verbo normativo **"DEBE (SHALL)"** — la keyword entre paréntesis
  satisface el validador (verificado: exige el literal SHALL/MUST en cada requirement).

## 4. Lo implementado en esta adopción

- `openspec/` inicializado (CLI oficial): `config.yaml` con el contexto del proyecto +
  convenciones y reglas por artefacto; comandos `/opsx:{explore,propose,apply,archive,sync}` y
  5 skills en `.claude/`.
- **13 capability specs** en `openspec/specs/` (derivados de "Contratos e invariantes" de
  `spec/` + `spec/Producto.md`): `sourcing-prescreen`, `contacto`, `entrevista`,
  `evaluacion-scorecard`, `documentos`, `agendamiento-multietapa`, `examenes-contratacion`,
  `auth-tenancy`, `privacidad-retencion`, `notificaciones-outbox`, `canal-telegram`,
  `llm-operacion`, `mcp` — **`openspec validate --all --strict` = 14/14 en verde** (13 specs +
  1 change). Nota: el dashboard no lleva spec normativo propio; sus reglas viven en las
  capacidades que consume (`spec/UX_UI.md` sigue siendo su manual).
- **Change de ejemplo vivo**: `openspec/changes/inactividad-estados-medicos/` — pendiente REAL
  del backlog (auditoría v3, Parte D) con los 4 artefactos (proposal/design/tasks/delta ADDED),
  listo para ejecutarse con `/opsx:apply` cuando se decida. Sirve de plantilla del ciclo.
- Cross-links en ambas direcciones + sección "OpenSpec (SDD operativo)" en `spec/README.md`.

## 5. Cómo usar esto (guía corta)

**Mejora sobre este proyecto**: `/opsx:propose "idea"` → revisar proposal/design/tasks/deltas →
`/opsx:apply` (implementa tareas) → suite verde → `/opsx:archive` (fusiona deltas al spec
principal). Los "Pendientes conocidos" (sección 6 de cada doc de `spec/`) son la cantera natural
de propuestas.

**Proyecto desde cero**: `openspec init --tools claude` → poblar `config.yaml` (contexto) →
primeras capacidades como specs normativos (qué DEBE hacer) ANTES de codear → cada feature es
un change → conservar de `agente_rh` la plantilla de `spec/` (README) para la biblioteca de
dominio cuando el proyecto acumule decisiones que expliquen el porqué.

## 6. Brechas residuales (post-adopción)

1. **Cobertura normativa parcial por diseño**: los 13 specs capturan los contratos críticos,
   no TODO invariante de los 22 docs (p. ej. observabilidad y despliegue quedan como manual +
   tests). Ampliar por demanda: cuando un cambio toque un área sin spec, crearla en el mismo change.
2. **`openspec validate` no corre en CI** — candidato natural a job en `ci.yml` (barato, sin red).
3. **Los escenarios no están enlazados 1:1 a tests**: la trazabilidad sigue siendo por dominio
   (sección 7 de `spec/`); un mapeo escenario→test es mejora futura.
4. La doc pública de OpenSpec menciona `project.md`; la v1.5 del CLI usa `config.yaml` — si el
   marco reintroduce `project.md`, migrar el bloque `context`.

## 7. Veredicto

`spec/` **cubría bien la mitad "documentación"** del SDD (y más que OpenSpec en decisiones,
patrones y trazabilidad) pero **no cubría la mitad "operativa"** (formato normativo verificable
+ ciclo de cambios). Con la adopción del CLI, el repo queda alineado con OpenSpec de punta a
punta: 13 specs normativos válidos, workflow de cambios instalado y ejercitado con una propuesta
real, y la biblioteca de dominio intacta como diferencial. Para próximos desarrollos, ambas
piezas se llevan juntas: OpenSpec gobierna el cambio; `spec/` conserva el conocimiento.
