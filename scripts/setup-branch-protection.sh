#!/usr/bin/env bash
# Activa branch protection SERVER-SIDE en `main` (roadmap v2 · paso 4).
#
# Hoy el "merge con CI verde" es disciplina + el hook pre-push local: GitHub no lo
# obliga porque el repo es privado en plan Free (las required checks server-side piden
# plan Pro o repo público). Este script deja la regla lista para el momento en que se
# cumpla esa condición — corrélo entonces y el servidor RECHAZA cualquier merge a `main`
# con checks en rojo o sin revisión.
#
# Requisitos: gh CLI autenticado con permisos de admin del repo; el repo en plan Pro o
# público. Uso:
#   scripts/setup-branch-protection.sh                    # repo por defecto (origin)
#   scripts/setup-branch-protection.sh Kratos2210/agente_rh
#
# Los 5 checks requeridos son los jobs de ci.yml que corren en cada PR (publish-image
# es solo-main y NO se exige como check de PR).
set -euo pipefail

REPO="${1:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
BRANCH="main"

echo "Aplicando branch protection a ${REPO}@${BRANCH}…"

gh api -X PUT "repos/${REPO}/branches/${BRANCH}/protection" \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "backend",
      "frontend",
      "docker",
      "k8s-manifests",
      "prompt-version-gate"
    ]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true
  },
  "restrictions": null,
  "required_linear_history": false,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON

echo "✅ Listo. Verificá en: https://github.com/${REPO}/settings/branches"
echo "   'strict: true' exige que la rama esté al día con main antes de mergear."
echo "   Nota: enforce_admins=false (el owner puede saltarse en una emergencia)."
