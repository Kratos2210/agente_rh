# CI/CD — Pipelines, gates de calidad y entrega continua

> Parte de [spec/](README.md) · Última revisión: 2026-07-06 · Código: `.github/workflows/`

## 1. Propósito y alcance

Los dos workflows de GitHub Actions (`ci.yml`, `nightly-quality.yml`), los gates que protegen
la calidad del pipeline LLM y la postura de entrega: **Entrega Continua sí, Despliegue Continuo
deliberadamente no**. Repo: `github.com/Kratos2210/agente_rh` (público por minutos de Actions).

## 2. Decisiones de diseño

- **CI que valida lo que se va a publicar**: el job docker construye AMBAS imágenes
  (backend + frontend) en cada push/PR — así el PR valida exactamente lo que el job de
  publicación (solo-main) subirá.
- **Gate de versionado de prompts**: cambiar `agente/prompts.py` sin subir `PROMPT_VERSION`
  rompe el build (diff contra la base del PR o `event.before`; tolera primer push/force-push).
  El prompt es código: se versiona o no se mergea.
- **Nightly con LLM real, separado del CI**: el golden (28 casos) y el red team (12 ataques)
  corren contra Groq a las 02:00 Lima + dispatch manual — la variabilidad del modelo no bloquea
  cada push, pero la deriva se detecta en horas. El juez de fundamentación se auto-omite con
  aviso si no hay DB accesible (secrets gated).
- **Entrega ≠ Despliegue**: cada merge a main publica imágenes versionadas a GHCR
  (`sha-<commit12>` inmutable + `latest`), pero el apply a K8s es decisión humana — hay
  entrevistas en curso y no hay cluster productivo; el siguiente salto (Environments/ArgoCD)
  está documentado, no improvisado.

## 3. Diseño e implementación

### `ci.yml` (push/PR)

| Job | Qué hace |
|---|---|
| backend | `uv sync` + `pytest` (467 tests) |
| frontend | `npm ci` + lint + `tsc --noEmit` (+build) |
| docker | Build de `Dockerfile.backend` + imagen del frontend (sin publicar) |
| k8s-manifests | kubeconform strict sobre AMBOS overlays (dev + prod) |
| prompt-version-gate | Falla si cambia `agente/prompts.py` sin bump de `PROMPT_VERSION` |
| publish-image (solo main) | Publica a GHCR `ghcr.io/kratos2210/agente-rh-{backend,frontend}` con tags `sha-*` + `latest`; login con `GITHUB_TOKEN`; escribe en el summary el comando `kustomize edit set image` |

### `nightly-quality.yml` (cron 02:00 Lima + dispatch)
Golden multi-suite contra el LLM real (secret `OPENAI_API_KEY` + vars `OPENAI_API_BASE/MODEL`)
· red team (12 ataques, sale 1 ante breach) · juez de groundedness gated a DB accesible.

### Higiene de proceso
- `scripts/setup-branch-protection.sh`: aplica los 5 checks requeridos vía `gh api` (para
  cuando el plan del repo lo permita).
- Commits convencionales (`feat:`, `fix:`, `docs:`…); PRs para cambios auditables.
- **Bug real cazado por CI**: los gates "a lo sumo cada N min" del scheduler usaban sentinel
  `0.0` con `time.monotonic()` — en un runner recién booteado el primer barrido se saltaba;
  fix con sentinel `None`. Moraleja: CI en entorno fresco encuentra lo que la máquina de dev
  nunca verá.

## 4. Contratos e invariantes

- Main siempre es desplegable: cada merge deja artefacto inmutable versionado en GHCR.
- Ningún cambio de prompt llega a main sin bump de versión (gate automático).
- Los manifests de K8s de ambos entornos validan en cada push (kubeconform strict).
- El deploy productivo referencia tags `sha-*`, nunca `latest`.

## 5. Patrones reutilizables

- **Gate de artefactos versionados para activos no-código** (prompts, esquemas): diff del
  archivo + exigencia de bump — barato y elimina la deriva invisible.
- **Nightly para lo estocástico, CI para lo determinista**: el LLM real no entra al camino
  crítico del push.
- **Publicar en CI, aplicar por decisión**: entrega continua con control humano del último
  paso, con el paso siguiente (CD real) documentado de antemano.
- **Validar en PR exactamente lo que se publicará en main** (mismos builds, mismos manifests).

## 6. Pendientes conocidos

- Branch protection server-side (requiere plan Pro o repo público — script listo).
- Despliegue Continuo (GitHub Environments o ArgoCD) cuando exista cluster productivo.
- El nightly del juez sigue auto-omitido mientras la DB sea local.

## 7. Trazabilidad

- Workflows: `.github/workflows/ci.yml`, `.github/workflows/nightly-quality.yml`.
- Estado narrado: sección "CI/CD — dónde estamos" de `docs/despliegue.md`.
- Primer run real: 5/5 jobs verdes; nightly manual 28/28 golden contra Groq desde Actions.
