-- 0026: métricas de CALIDAD del LLM como SIGNO VITAL (paso 4 — medición continua).
--
-- Hasta ahora la calidad (fundamentación/relevancia de las respuestas del bot) se auditaba
-- OFFLINE con `scripts/groundedness_judge.py`: una foto manual. Esta tabla persiste el
-- resultado diario por tenant/métrica que produce el barrido continuo del scheduler
-- (`api/scheduler.py::_quality_sweep`), para que la evaluación pase de "foto" a "signo
-- vital" con tendencia visible en el dashboard y alerta push cuando cae bajo el umbral.
--
-- Una fila por (tenant, métrica, día): el barrido hace upsert idempotente en cada corrida.
-- `metric` ∈ {'grounded','answer_relevance'} (ver evaluation/quality.py).

create table if not exists quality_metrics (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    uuid not null references tenants(id) on delete cascade,
    metric       text not null,
    day          date not null,
    rate         double precision not null default 0,   -- proporción 0..1 de aprobadas
    sample_size  integer not null default 0,
    threshold    double precision not null default 0,   -- umbral vigente al medir (min_rate)
    created_at   timestamptz not null default now(),
    unique (tenant_id, metric, day)
);

create index if not exists idx_quality_metrics_tenant on quality_metrics (tenant_id, metric, day desc);

grant all privileges on quality_metrics to service_role;

-- RLS por tenant (patrón 0018: latente para service_role, defensa en profundidad).
alter table quality_metrics enable row level security;
drop policy if exists tenant_isolation on quality_metrics;
create policy tenant_isolation on quality_metrics for all to anon, authenticated
    using (tenant_id = app_current_tenant())
    with check (tenant_id = app_current_tenant());

-- Config por tenant (patrón 0017: default en código si no hay fila). Alerta de calidad:
-- muestrea `sample` trazas answer/día y avisa si la tasa de fundamentadas cae bajo `min_rate`.
-- Default OFF: el juez consume LLM y requiere LLM_TRACE_ENABLED para tener trazas.
insert into app_settings (tenant_id, key, value)
select id, 'quality_alerts',
       '{"enabled": false, "sample": 20, "min_rate": 0.9, "notify_email": ""}'::jsonb
from tenants
on conflict (tenant_id, key) do nothing;
