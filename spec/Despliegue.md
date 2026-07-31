# Despliegue — Docker, Kubernetes, local real y decisión serverless

> Parte de [spec/](README.md) · Última revisión: 2026-07-06 · Código: `despliegue/`, `Dockerfile.backend`, `docker-compose.yml`

## 1. Propósito y alcance

Los tres caminos de despliegue (Compose, Kubernetes, local con launchd+Caddy), la estructura
base+overlays, el manejo de secretos en cluster y la decisión razonada sobre serverless.
La publicación de imágenes está en [CI_CD.md](CI_CD.md).

## 2. Decisiones de diseño

- **Kustomize base + overlays** (`despliegue/k8s/base` + `overlays/{dev,prod}`): la base no
  lleva namespace ni valores de entorno; cada overlay define su namespace
  (`agente-rh-dev`/`-prod`), `ENVIRONMENT`, tag de imagen, réplicas, recursos y dominio/CORS.
- **La topología del bot dicta las réplicas**: dev = polling → backend `replicas:1` +
  estrategia `Recreate` (un solo `getUpdates` por token); prod = webhook → `replicas:2` +
  `RollingUpdate` (seguro sin polling). La restricción está codificada en los overlays y en
  `deploy.sh scale` (backend exige `--force` con explicación).
- **Serverless: no para el núcleo, sí para el borde (futuro)**: el bot (long-lived), el
  scheduler (loop con lock) y el RAG (torch, modelo en memoria) no encajan en FaaS; la API y
  las notificaciones podrían migrar tras consolidar webhook. Decisión argumentada en
  `docs/despliegue.md` en vez de moda.
- **Perfil de producción en el overlay**: prod enciende trazas LLM, caché de dudas, routing
  barato, webhook y Phoenix in-cluster — los "signos vitales" no dependen de que alguien
  recuerde flags (riesgo #1 de la auditoría v2, cerrado).
- **Secretos**: `secret.example.yaml` sin namespace (se aplica por entorno con `-n`);
  scaffolding **External Secrets Operator** en `despliegue/k8s/secret-manager/` (SecretStore +
  ExternalSecret provider-agnostic, ejemplo Doppler) que materializa el MISMO Secret — no
  cableado a kustomize a propósito (CRDs que kubeconform no valida).

## 3. Diseño e implementación

### Imágenes
- `Dockerfile.backend`: uv + `uv pip install -r pyproject.toml` (el proyecto no declara
  build-system); **torch==2.2.2 del índice CPU ANTES del resto** (no arrastrar CUDA); sin
  OCR/voz; HEALTHCHECK con urllib. ~2.3 GB.
- Frontend: imagen Next.js standalone.
- `docker-compose.yml`: backend+frontend con Supabase del host (`host.docker.internal` +
  `extra_hosts` para Linux); volúmenes hf-cache/chroma/uploads.

### `despliegue/deploy.sh` (subcomandos)
`build` · `push` · `compose-up` (espera health) · `compose-down` · `validate` (kubeconform
strict vía docker, AMBOS overlays, 7/7 recursos) · `k8s-apply <env>` (exige secret.yaml) ·
`k8s-status <env>` · `scale <env>` (frontend libre; backend gated).

### Kubernetes
Base: deployments backend/frontend con startup/readiness/liveness probes, services, ingress
(`/api` + `/mcp` → backend; prod agrega `/telegram/webhook`). Gotcha aprendido: el ConfigMap
debe usar los nombres canónicos de `core/config.py` (`ENVIRONMENT`, `OPENAI_API_BASE`) —
pydantic ignora desconocidos y prod corría como development (bug real, corregido).

### Local "producción chica"
`despliegue/launchd/` + `Caddyfile`: backend+frontend como servicios de macOS detrás de Caddy
— el despliegue real actual mientras no hay cluster.

## 4. Contratos e invariantes

- La base kustomize **nunca** lleva valores de entorno; todo lo específico vive en overlays.
- `ENVIRONMENT=production` en prod es obligatorio: activa `assert_secure_config` (secretos
  débiles = el pod no arranca; verificado en directo).
- kubeconform strict verde en ambos overlays es gate de CI.
- Prod despliega tags `sha-*` de GHCR (inmutables), nunca `latest`.

## 5. Patrones reutilizables

- **Base+overlays con el entorno como ÚNICA diferencia** — y validación de manifests en CI
  desde el día uno (el render se revisa como código).
- **Codificar las restricciones operativas en el tooling** (replicas del bot gated con
  `--force` + explicación): la restricción sobrevive a la rotación de operadores.
- **Perfil de entorno explícito**: qué flags enciende producción es un artefacto revisable
  (y testeado: `test_prod_profile.py`), no tribal knowledge.
- **Decidir serverless por cargas** (long-lived vs request/response) y escribir el porqué; "no
  por ahora" documentado vale más que un sí a medias.

## 6. Pendientes conocidos

- Cluster productivo real (hoy launchd local + manifests validados).
- Cargar secretos a un gestor y aplicar el ExternalSecret (acción externa).
- E2E webhook Telegram→pod con HTTPS público.

## 7. Trazabilidad

- Docs: `docs/despliegue.md` (tres caminos + serverless + CI/CD), `despliegue/k8s/README.md`,
  `despliegue/k8s/secret-manager/README.md`.
- CI: job `k8s-manifests` (kubeconform dev+prod). Tests: `test_prod_profile.py`, `test_webhook.py`.
- Verificado: render correcto de namespace/ENVIRONMENT/imagen/réplicas por overlay; gate de
  producción bloqueando secretos default con `RuntimeError`.
