# `core/` — Infraestructura transversal (fuera de la rúbrica)

Carpeta **no** exigida por la rúbrica, pero legítima: aloja la infra transversal que ninguno de los siete
componentes reclama en exclusiva. Evita huérfanos y un `src/` casi vacío tras la reorg. Es el nivel más
bajo del layering (`agente → orquestacion → retrieval → ranking → core`): no importa a los demás paquetes.

| Archivo | Rol |
|---|---|
| `config.py` | Settings (pydantic-settings, `.env`), `get_settings`, `assert_secure_config` (gate de prod). ~40 importadores. |
| `logging_config.py` | Logging (formato clásico o JSON + `request_id`), `setup_logging`, `get_logger`. |
| `registry.py` | Registry local (SQLite) para documentos/hilos/caché del RAG heredado. |
