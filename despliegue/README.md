# `despliegue/` — Despliegue

Componente **Despliegue** de la rúbrica: manifiestos de Kubernetes y scripts de automatización de
despliegue y escalado.

| Ruta | Rol |
|---|---|
| `deploy.sh` | Automatización: `build`/`push`/`compose-up`/`compose-down`/`validate` (kubeconform)/`k8s-apply`/`k8s-status`/`scale`. |
| `k8s/base/` | Manifiestos comunes (deployments, services, ingress `/api`+`/mcp`+`/telegram/webhook`, configmap). |
| `k8s/overlays/{dev,prod}/` | Kustomize por entorno (namespace, imagen, réplicas, recursos, dominio/CORS). |
| `k8s/secret.example.yaml` | Plantilla del Secret (copiar a `secret.yaml`, NO commitear). |
| `k8s/secret-manager/` | Scaffolding External Secrets Operator (Doppler/Vault/…) para prod. |

**Decisión clave:** backend `replicas:1` en dev (bot Telegram en **polling** = un consumidor por token);
en prod el overlay activa **webhook** y escala a `replicas:2` (el scheduler es multi-réplica por advisory
lock de Postgres). Serverless argumentado en `docs/despliegue.md`.

**Cómo ejecutar:** `despliegue/deploy.sh validate` (kubeconform 7/7) · `despliegue/deploy.sh compose-up` ·
`despliegue/deploy.sh k8s-apply <dev|prod>`. Complementan: `Dockerfile.backend`, `docker-compose.yml`,
`.github/workflows/` (CI + publicación a GHCR), `docs/despliegue.md`.
