#!/usr/bin/env bash
# Automatización de despliegue y escalado del Agente de Selección.
#
# Uso:  deploy/deploy.sh <comando> [args]
#
#   build [tag]          Construye ambas imágenes (default tag: dev).
#   push [tag]           Publica las imágenes (requiere REGISTRY, p. ej. ghcr.io/org).
#   compose-up           Levanta el stack local (backend+frontend+proxy) → :3000.
#   compose-down         Baja el stack local.
#   validate             Valida AMBOS overlays K8s (dev+prod) con kubeconform strict.
#   k8s-apply <env>      Aplica el overlay <env> (dev|prod) + secret al namespace
#                        agente-rh-<env> (exige deploy/k8s/secret.yaml).
#   k8s-status <env>     Pods, deployments y services del namespace agente-rh-<env>.
#   scale <env> <n> [--force]
#                        Escala el FRONTEND a n réplicas en agente-rh-<env>. Para el
#                        backend exige --force: el bot de Telegram en polling solo admite
#                        1 consumidor por token (docs/despliegue.md); sin webhook rompe.
#
# Variables: REGISTRY (push), KUBECTL (default kubectl). El namespace se deriva del env.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KUBECTL="${KUBECTL:-kubectl}"
TAG="${2:-dev}"

# Resuelve y valida el entorno (dev|prod) de un argumento posicional → namespace.
resolve_env() {
  local env="${1:-}"
  case "$env" in
    dev|prod) ;;
    *) echo "Entorno inválido: '${env:-<vacío>}'. Usá 'dev' o 'prod'." >&2; exit 1 ;;
  esac
  echo "$env"
}

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
  # Valida los DOS overlays: un manifest roto en cualquier entorno falla el comando.
  for env in dev prod; do
    echo "== overlay $env =="
    "$KUBECTL" kustomize "$ROOT/deploy/k8s/overlays/$env/" \
      | docker run --rm -i ghcr.io/yannh/kubeconform:latest -strict -summary -
  done
}

cmd_k8s_apply() {
  local env; env="$(resolve_env "${2:-}")"
  local ns="agente-rh-$env"
  [ -f "$ROOT/deploy/k8s/secret.yaml" ] || {
    echo "Falta deploy/k8s/secret.yaml (copiá secret.example.yaml y completalo; NO lo commitees)"
    exit 1
  }
  # El overlay ya declara el Namespace; se aplica primero para que el secret tenga dónde ir.
  "$KUBECTL" apply -f "$ROOT/deploy/k8s/overlays/$env/namespace.yaml"
  "$KUBECTL" apply -f "$ROOT/deploy/k8s/secret.yaml" -n "$ns"
  "$KUBECTL" apply -k "$ROOT/deploy/k8s/overlays/$env/"
  echo "OK: overlay $env aplicado en el namespace $ns"
}

cmd_k8s_status() {
  local env; env="$(resolve_env "${2:-}")"
  "$KUBECTL" -n "agente-rh-$env" get deployments,pods,services,ingress
}

cmd_scale() {
  local env; env="$(resolve_env "${2:-}")"
  local ns="agente-rh-$env"
  local n="${3:?Uso: deploy.sh scale <env> <n> [--force]}"
  local force="${4:-}"
  "$KUBECTL" -n "$ns" scale deployment/frontend --replicas="$n"
  echo "OK: frontend ($env) → $n réplicas"
  if [ "$force" = "--force" ]; then
    "$KUBECTL" -n "$ns" scale deployment/backend --replicas="$n"
    echo "⚠️  backend → $n réplicas con --force: asegurate de haber migrado el bot a webhook"
  else
    echo "ℹ️  backend queda en 1 réplica: el polling de Telegram solo admite un consumidor"
    echo "   por token (docs/despliegue.md). Para forzarlo: deploy.sh scale $env $n --force"
  fi
}

case "${1:-}" in
  build)        cmd_build ;;
  push)         cmd_push ;;
  compose-up)   cmd_compose_up ;;
  compose-down) cmd_compose_down ;;
  validate)     cmd_validate ;;
  k8s-apply)    cmd_k8s_apply "$@" ;;
  k8s-status)   cmd_k8s_status "$@" ;;
  scale)        cmd_scale "$@" ;;
  *)            grep '^#' "$0" | sed 's/^# \{0,1\}//' | sed -n '2,20p'; exit 1 ;;
esac
