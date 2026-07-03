# Despliegue en Kubernetes

Manifests declarativos del Agente de Selección. La base de datos NO se despliega aquí:
se usa un proyecto **Supabase cloud** (o un Postgres gestionado equivalente).

> 🔧 Atajo: `deploy/deploy.sh` automatiza estos pasos (`build`, `push`, `validate`,
> `k8s-apply`, `k8s-status`, `scale`).

## Aplicar

```bash
# 1) Construir y publicar las imágenes (reemplazar el registry)
docker build -f Dockerfile.backend -t REGISTRY/agente-rh-backend:v1 .
docker build -f frontend/Dockerfile -t REGISTRY/agente-rh-frontend:v1 .
docker push REGISTRY/agente-rh-backend:v1 && docker push REGISTRY/agente-rh-frontend:v1
#    …y actualizar `image:` en los deployments (o usar `kustomize edit set image`).

# 2) Secretos (NUNCA commitear secret.yaml)
cp deploy/k8s/secret.example.yaml deploy/k8s/secret.yaml   # completar valores
kubectl apply -f deploy/k8s/namespace.yaml -f deploy/k8s/secret.yaml

# 3) Todo lo demás
kubectl apply -k deploy/k8s/

# 4) Verificar
kubectl -n agente-rh get pods
kubectl -n agente-rh port-forward svc/backend 8000:8000 &
curl http://localhost:8000/api/health
```

## Decisiones que estos manifests codifican

| Decisión | Por qué |
|---|---|
| `backend: replicas: 1` + strategy `Recreate` | El bot de Telegram usa **polling** y Telegram admite un solo consumidor de `getUpdates` por token; dos pods → 409 Conflict. El scheduler interno sí tolera N réplicas (advisory lock de Postgres). Para escalar el backend: migrar el bot a **webhook** (ver `docs/despliegue.md`). |
| `frontend: replicas: 2` | El dashboard es stateless (JWT en el cliente): escala horizontal libre. |
| Volúmenes `emptyDir` | Los datos durables viven en Postgres (documentos, scorecards, checkpoints). Lo efímero es re-generable: modelos HF (se re-descargan) e índice RAG (re-sembrar con `scripts/seed_company_kb.py`). Si las re-descargas molestan, convertir `hf-cache` en PVC. |
| `startupProbe` con margen amplio | El primer arranque descarga los embeddings (~500 MB); sin startupProbe la liveness mataría el pod antes de estar listo. |
| Secret fuera del kustomization | La plantilla es `secret.example.yaml`; el real se aplica aparte o lo inyecta un secret manager (External Secrets/Vault/Doppler — `docs/gestion_secretos.md`). |
| Ingress `/api` + `/mcp` → backend, `/` → frontend | Mismo patrón del Caddyfile de compose: una sola entrada pública. |

## Validación sin cluster

```bash
kubectl apply -k deploy/k8s/ --dry-run=client
kubectl apply -f deploy/k8s/secret.example.yaml --dry-run=client
```
