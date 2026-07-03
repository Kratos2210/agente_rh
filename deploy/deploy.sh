#!/usr/bin/env bash
# Automatización de despliegue y escalado del Agente de Selección.
#
# Uso:  deploy/deploy.sh <comando> [args]
#
#   build [tag]          Construye ambas imágenes (default tag: dev).
#   push [tag]           Publica las imágenes (requiere REGISTRY, p. ej. ghcr.io/org).
#   compose-up           Levanta el stack local (backend+frontend+proxy) → :3000.
#   compose-down         Baja el stack local.
#   validate             Valida los manifests K8s (kubeconform strict, vía Docker).
#   k8s-apply            Aplica namespace+secret+kustomization (exige deploy/k8s/secret.yaml).
#   k8s-status           Pods, deployments y services del namespace.
#   scale <n> [--force]  Escala el FRONTEND a n réplicas. Para el backend exige --force:
#                        el bot de Telegram en polling solo admite 1 consumidor por token
#                        (ver docs/despliegue.md); escalarlo sin migrar a webhook rompe el bot.
#
# Variables: REGISTRY (push), NAMESPACE (default agente-rh), KUBECTL (default kubectl).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-agente-rh}"
KUBECTL="${KUBECTL:-kubectl}"
TAG="${2:-dev}"

cmd_build() {
  docker build -f "$ROOT/Dockerfile.backend" -t "agente-rh-backend:$TAG" "$ROOT"
  docker build -f "$ROOT/frontend/Dockerfile" -t "agente-rh-frontend:$TAG" "$ROOT"
  echo "OK: agente-rh-backend:$TAG y agente-rh-frontend:$TAG"
}

cmd_push() {
  : "${REGISTRY:?Definí REGISTRY (p. ej. REGISTRY=ghcr.io/mi-org)}"
  for img in agente-rh-backend agente-rh-frontend; do
    docker tag "$img:$TAG" "$REGISTRY/$img:$TAG"
    docker push "$REGISTRY/$img:$TAG"
  done
  echo "OK: publicadas en $REGISTRY con tag $TAG"
}

cmd_compose_up() {
  [ -f "$ROOT/.env" ] || { echo "Falta $ROOT/.env (copiá .env.example)"; exit 1; }
  docker compose -f "$ROOT/docker-compose.yml" up --build -d
  echo "Esperando salud del backend…"
  for _ in $(seq 1 60); do
    if curl -sf http://localhost:3000/api/health >/dev/null 2>&1; then
      echo "OK: http://localhost:3000 (health 200)"; return 0
    fi
    sleep 5
  done
  echo "TIMEOUT: el backend no reportó salud; ver docker compose logs backend" >&2
  exit 1
}

cmd_compose_down() {
  docker compose -f "$ROOT/docker-compose.yml" down
}

cmd_validate() {
  "$KUBECTL" kustomize "$ROOT/deploy/k8s/" \
    | docker run --rm -i ghcr.io/yannh/kubeconform:latest -strict -summary -
}

cmd_k8s_apply() {
  [ -f "$ROOT/deploy/k8s/secret.yaml" ] || {
    echo "Falta deploy/k8s/secret.yaml (copiá secret.example.yaml y completalo; NO lo commitees)"
    exit 1
  }
  "$KUBECTL" apply -f "$ROOT/deploy/k8s/namespace.yaml"
  "$KUBECTL" apply -f "$ROOT/deploy/k8s/secret.yaml"
  "$KUBECTL" apply -k "$ROOT/deploy/k8s/"
  echo "OK: aplicado en el namespace $NAMESPACE"
}

cmd_k8s_status() {
  "$KUBECTL" -n "$NAMESPACE" get deployments,pods,services,ingress
}

cmd_scale() {
  local n="${2:?Uso: deploy.sh scale <n> [--force]}"
  local force="${3:-}"
  "$KUBECTL" -n "$NAMESPACE" scale deployment/frontend --replicas="$n"
  echo "OK: frontend → $n réplicas"
  if [ "$force" = "--force" ]; then
    "$KUBECTL" -n "$NAMESPACE" scale deployment/backend --replicas="$n"
    echo "⚠️  backend → $n réplicas con --force: asegurate de haber migrado el bot a webhook"
  else
    echo "ℹ️  backend queda en 1 réplica: el polling de Telegram solo admite un consumidor"
    echo "   por token (docs/despliegue.md). Para forzarlo: deploy.sh scale $n --force"
  fi
}

case "${1:-}" in
  build)        cmd_build ;;
  push)         cmd_push ;;
  compose-up)   cmd_compose_up ;;
  compose-down) cmd_compose_down ;;
  validate)     cmd_validate ;;
  k8s-apply)    cmd_k8s_apply ;;
  k8s-status)   cmd_k8s_status ;;
  scale)        cmd_scale "$@" ;;
  *)            grep '^#' "$0" | sed 's/^# \{0,1\}//' | sed -n '2,20p'; exit 1 ;;
esac
