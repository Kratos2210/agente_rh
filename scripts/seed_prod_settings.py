"""Enciende los settings por-tenant del PERFIL DE PRODUCCIÓN (roadmap v2, paso 1).

Las capacidades de calidad viven como filas en `app_settings` (por-tenant), no como env
vars: por eso NO se pueden fijar en el ConfigMap de k8s. Este script las enciende en la
base de datos, tenant por tenant. Es la contraparte del overlay `prod` (que enciende
trazas/modelo-barato/caché por env).

Se entrega como SCRIPT y no como migración a propósito: prod y dev son bases de datos
distintas, y una migración encendería la calidad también en dev (que quema LLM en el
barrido diario). Correrlo SOLO contra la base de PRODUCCIÓN, después de desplegar:

    uv run python scripts/seed_prod_settings.py            # enciende quality_alerts
    uv run python scripts/seed_prod_settings.py --sla      # + alertas SLA (ops)
    uv run python scripts/seed_prod_settings.py --dry-run  # muestra sin escribir

Idempotente: hace merge sobre la fila existente (respeta sample/min_rate/notify_email),
solo flipea `enabled`. Requiere el bloque Supabase del .env de prod.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.logging_config import get_logger  # noqa: E402

logger = get_logger("scripts.seed_prod_settings")


def seed(enable_sla: bool = False, dry_run: bool = False) -> int:
    from api.runtime import _DEFAULT_QUALITY_ALERTS, _DEFAULT_SLA_ALERTS
    from db import repositories as repo

    tenants = repo.list_tenants()
    if not tenants:
        logger.error("No hay tenants (¿Supabase de prod corriendo? ¿migraciones aplicadas?)")
        return 1

    # (key, default, overrides) — el override se fusiona sobre lo que haya en la DB.
    targets: list[tuple[str, dict, dict]] = [
        ("quality_alerts", _DEFAULT_QUALITY_ALERTS, {"enabled": True}),
    ]
    if enable_sla:
        targets.append(("sla_alerts", _DEFAULT_SLA_ALERTS, {"enabled": True, "ops_alerts": True}))

    changed = 0
    for tenant in tenants:
        tid = tenant["id"]
        name = tenant.get("name") or tid
        for key, default, override in targets:
            current = repo.get_app_setting(key, dict(default), tid) or dict(default)
            merged = {**default, **current, **override}
            if merged == current:
                logger.info("[%s] %s ya estaba: %s", name, key, {k: merged[k] for k in override})
                continue
            if dry_run:
                logger.info("[%s] %s → %s (dry-run, no escribe)", name, key, override)
            else:
                repo.set_app_setting(key, merged, tid)
                logger.info("[%s] %s encendido: %s", name, key, override)
            changed += 1

    print(
        f"OK: {len(tenants)} tenant(s) procesados, {changed} cambio(s) "
        f"{'(dry-run)' if dry_run else 'aplicados'}."
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sla", action="store_true", help="Encender también sla_alerts (ops)")
    parser.add_argument("--dry-run", action="store_true", help="Mostrar sin escribir")
    args = parser.parse_args()
    sys.exit(seed(enable_sla=args.sla, dry_run=args.dry_run))
