-- 0025: snapshots periódicos de las métricas HTTP (observabilidad O-6).
--
-- `HttpMetrics` vive en memoria del proceso: un reinicio/redeploy borra el historial
-- de rendimiento. El scheduler vuelca un snapshot por ruta cada N minutos
-- (`HTTP_SNAPSHOT_MINUTES`, 0 = off) y poda los viejos (`HTTP_SNAPSHOT_RETENTION_DAYS`).
-- Los contadores son ACUMULADOS desde el arranque del proceso (el consumidor puede
-- derivar deltas entre snapshots consecutivos del mismo proceso).

create table if not exists http_metrics_snapshots (
    id             uuid primary key default gen_random_uuid(),
    taken_at       timestamptz not null default now(),
    route          text not null,
    count          integer not null default 0,
    errors         integer not null default 0,
    client_errors  integer not null default 0,
    avg_ms         integer not null default 0,
    p95_ms         integer not null default 0,
    p99_ms         integer not null default 0,
    max_ms         integer not null default 0
);

create index if not exists idx_http_snapshots_taken on http_metrics_snapshots (taken_at desc);

grant all privileges on http_metrics_snapshots to service_role;

-- Datos operativos del proceso (sin tenant): RLS activado SIN políticas para
-- anon/authenticated = denegado por defecto; solo el backend (service_role) accede.
alter table http_metrics_snapshots enable row level security;
