# Despliegue en Kubernetes

Manifests declarativos del Agente de Selección, organizados como **base + overlays**
(kustomize). La base de datos NO se despliega aquí: se usa un proyecto **Supabase cloud**
(o un Postgres gestionado equivalente).

```
deploy/k8s/
  base/                 # recursos comunes (SIN namespace ni valores de entorno)
  overlays/
    dev/                # namespace agente-rh-dev, ENVIRONMENT=development, tag :dev
    prod/               # namespace agente-rh-prod, ENVIRONMENT=production, tag versionado
  secret.example.yaml   # plantilla del Secret (aplicar aparte, por entorno)
```

> 🔧 Atajo: `deploy/deploy.sh` automatiza todo (`validate`, `k8s-apply <env>`,
> `k8s-status <env>`, `scale <env> <n>`).

## Diferencias entre entornos

| | dev (`agente-rh-dev`) | prod (`agente-rh-prod`) |
|---|---|---|
| `ENVIRONMENT` | `development` — `assert_secure_config` solo **avisa** | `production` — **bloquea** el arranque con secretos débiles |
| Imagen | `agente-rh-*:dev` | `REEMPLAZAR-registry/agente-rh-*:v1` (tag inmutable) |
| Frontend réplicas | 1 | 2 |
| Recursos backend | reducidos (250m / 1Gi) | plenos (500m / 1.5Gi) |
| Dominio / CORS | `dev.…` | `app.…` |
| Trazas LLM | ON (datos de prueba) | OFF (PII real, Ley 29733) |

El **backend queda en `replicas: 1` en ambos** (el bot de Telegram en polling admite un
solo `getUpdates` por token; el scheduler sí tolera N réplicas por el advisory lock).

## Aplicar

```bash
# 1) Imágenes (reemplazar el registry). En prod, tag versionado — no `latest`.
docker build -f Dockerfile.backend  -t REGISTRY/agente-rh-backend:v1 .
docker build -f frontend/Dockerfile -t REGISTRY/agente-rh-frontend:v1 .
docker push REGISTRY/agente-rh-backend:v1 && docker push REGISTRY/agente-rh-frontend:v1
#    …y ajustar `images:` en overlays/<env>/kustomization.yaml (o `kustomize edit set image`).

# 2) Secreto (NUNCA commitear secret.yaml). Se aplica al namespace del entorno:
cp deploy/k8s/secret.example.yaml deploy/k8s/secret.yaml   # completar valores
kubectl apply -f deploy/k8s/overlays/prod/namespace.yaml
kubectl apply -f deploy/k8s/secret.yaml -n agente-rh-prod

# 3) El overlay completo
kubectl apply -k deploy/k8s/overlays/prod/

# 4) Verificar
kubectl -n agente-rh-prod get pods
kubectl -n agente-rh-prod port-forward svc/backend 8000:8000 &
curl http://localhost:8000/api/health
```

O, equivalente y más corto: `deploy/deploy.sh k8s-apply prod`.

## Decisiones que estos manifests codifican

| Decisión | Por qué |
|---|---|
| Base sin namespace ni valores de entorno | El overlay fija namespace (`namespace:`), `ENVIRONMENT`, dominio, réplicas y tag. Un solo lugar por diferencia. |
| Namespace declarado en cada overlay | El transformer `namespace:` reubica los recursos pero NO renombra un objeto `Namespace`; por eso cada overlay trae el suyo (`agente-rh-dev`/`-prod`). |
| `ENVIRONMENT=production` en el overlay prod | Activa `api.auth.assert_secure_config`: el backend se NIEGA a arrancar con `JWT_SECRET`/`ADMIN_PASSWORD` débiles o default. |
| Env vars por nombre EXACTO de campo | pydantic ignora nombres que no matchean (`OPENAI_BASE_URL`/`APP_ENV` eran ignorados → LLM en localhost y gate inactivo). Los correctos son `OPENAI_API_BASE`/`ENVIRONMENT`. |
| `backend: replicas: 1` + `Recreate` | Polling de Telegram (un consumidor por token). Escalar el backend ⇒ migrar a **webhook** (`docs/despliegue.md`). |
| Volúmenes `emptyDir` | Los datos durables viven en Postgres. Lo efímero es re-generable: modelos HF y el índice RAG (`scripts/seed_company_kb.py`). Para evitar re-descargas, `hf-cache` → PVC. |
| Secret fuera del kustomization y sin namespace fijo | Se aplica por entorno con `-n`; el real lo inyecta un secret manager (External Secrets/Vault/Doppler — `docs/gestion_secretos.md`). |

## Validación sin cluster

```bash
deploy/deploy.sh validate                       # ambos overlays, kubeconform strict
# o manual, por entorno:
kubectl kustomize deploy/k8s/overlays/dev/  | kubeconform -strict -summary -
kubectl kustomize deploy/k8s/overlays/prod/ | kubeconform -strict -summary -
```
