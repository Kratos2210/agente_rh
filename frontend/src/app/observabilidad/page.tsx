"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Shell, Card, BackLink } from "@/components/Shell";
import { api, errorMessage, AuditEntry, HttpRouteMetric, OpsAlert, OpsTimeseries, OutboxHealth, OutboxItem, QualityMetric } from "@/lib/api";
import { isAdmin } from "@/lib/auth";

// Etiquetas legibles para los tipos de alerta operativa (reconciliación).
const ALERT_LABEL: Record<string, string> = {
  dead_letter: "Envíos detenidos",
  meeting_no_link: "Reunión sin enlace",
  scheduling_stuck: "Coordinación estancada",
  state_divergence: "Estado divergente",
  delivery_failed: "Entrega Telegram fallida",
};

// Etiquetas legibles para los tipos de envío del outbox.
const KIND_LABEL: Record<string, string> = {
  scorecard_email: "Scorecard → reclutador (email)",
  meeting_email: "Reunión → candidato/reclutador (email)",
  meeting_recruiter_telegram: "Reunión → reclutador (Telegram)",
  candidate_notify: "Aviso → candidato (Telegram)",
};

const ACTION_LABEL: Record<string, string> = {
  "candidate.decide": "Decisión de candidato",
  "candidate.contact": "Contacto de candidato",
  "candidate.delete": "Borrado de candidato",
  "outbox.retry": "Reintento de envío",
  "settings.update": "Cambio de configuración",
  "recruiter.create": "Alta de reclutador",
  "recruiter.update": "Edición de reclutador",
  "vacancy.create": "Alta de vacante",
  "vacancy.update": "Edición de vacante",
};

const STATUS_META: Record<string, { label: string; color: string; bg: string }> = {
  pending: { label: "Pendiente", color: "#d97706", bg: "rgba(217,119,6,.12)" },
  failed: { label: "Fallido (dead-letter)", color: "#f87171", bg: "rgba(248,113,113,.12)" },
  sent: { label: "Enviado", color: "#34d399", bg: "rgba(52,211,153,.12)" },
};

function when(iso: string): string {
  try {
    return new Date(iso).toLocaleString("es-PE", {
      day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "America/Lima",
    });
  } catch {
    return iso;
  }
}

// Rango 0..1 → color semáforo según el umbral (bajo el umbral = rojo).
function rateColor(rate: number, threshold: number): string {
  if (rate < threshold) return "#f87171";
  if (rate < threshold + 0.05) return "#d97706";
  return "#34d399";
}

function CountChip({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div style={{ padding: "12px 18px", borderRadius: 13, background: "rgba(255,255,255,.03)", border: "1px solid var(--edge)", minWidth: 120 }}>
      <div style={{ fontSize: 26, fontWeight: 800, color, lineHeight: 1 }}>{value}</div>
      <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 5, fontWeight: 600 }}>{label}</div>
    </div>
  );
}

// ── Formateadores y gráfico SVG (sin librerías) ──────────────────────────────
const fmtInt = (n: number) => Math.round(n).toLocaleString("es-PE");
const fmtUsd = (n: number) => "$" + (n < 1 ? n.toFixed(4) : n.toFixed(2));
const fmtMsN = (n: number) => Math.round(n).toLocaleString("es-PE") + " ms";
const fmtPct = (n: number) => Math.round(n * 100) + "%";
const r1 = (n: number) => Math.round(n * 10) / 10; // precisión SVG reducida

function dayShort(iso: string): string {
  const [, m, d] = iso.split("-");
  const mon = ["", "ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"][Number(m)] || m;
  return `${Number(d)} ${mon}`;
}

type ChartPoint = { day: string; value: number | null };

// Gráfico de una serie diaria: barras o línea+área, con umbral opcional (calidad).
// `value: null` = día sin dato (calidad esporádica): sin barra / rompe la línea.
function MiniChart({
  title, points, kind, color, fmt, yMax, threshold, thresholdLabel,
}: {
  title: string;
  points: ChartPoint[];
  kind: "bar" | "line";
  color: string;
  fmt: (n: number) => string;
  yMax?: number;
  threshold?: number;
  thresholdLabel?: string;
}) {
  const W = 300, H = 82, padL = 6, padR = 6, padT = 10, padB = 14;
  const n = points.length;
  const vals = points.map((p) => p.value).filter((v): v is number => v != null);
  const maxV = yMax ?? Math.max(1, ...vals, threshold ?? 0);
  const hasData = vals.some((v) => v > 0);
  const innerW = W - padL - padR;
  const x = (i: number) => padL + (n <= 1 ? innerW / 2 : (i * innerW) / (n - 1));
  const y = (v: number) => padT + (1 - v / maxV) * (H - padT - padB);
  const last = [...points].reverse().find((p) => p.value != null)?.value ?? 0;

  const bw = innerW / Math.max(1, n);
  const linePts = points.map((p, i) => (p.value == null ? null : `${r1(x(i))},${r1(y(p.value))}`));
  // Segmentos continuos (rompe en null) para la polilínea.
  const segments: string[][] = [];
  let cur: string[] = [];
  for (const lp of linePts) {
    if (lp == null) { if (cur.length) { segments.push(cur); cur = []; } }
    else cur.push(lp);
  }
  if (cur.length) segments.push(cur);
  const areaPath = segments.length === 1 && segments[0].length > 1
    ? `M${segments[0][0]} L${segments[0].slice(1).join(" ")} L${r1(x(n - 1))},${r1(y(0))} L${r1(x(0))},${r1(y(0))} Z`
    : "";

  return (
    <div style={{ padding: "12px 14px", borderRadius: 12, background: "rgba(255,255,255,.02)", border: "1px solid var(--edge)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 6 }}>
        <span style={{ fontSize: 12, color: "var(--muted)", fontWeight: 600 }}>{title}</span>
        <span style={{ fontSize: 14, fontWeight: 800, color }}>{fmt(last)}</span>
      </div>
      {!hasData ? (
        <div style={{ height: H, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--muted)", fontSize: 12 }}>sin datos</div>
      ) : (
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={title} style={{ display: "block" }}>
          {threshold != null && (
            <g>
              <line x1={padL} x2={W - padR} y1={r1(y(threshold))} y2={r1(y(threshold))} stroke="#f87171" strokeWidth="1" strokeDasharray="4 3" opacity="0.7" />
              {thresholdLabel && <text x={W - padR} y={r1(y(threshold)) - 3} textAnchor="end" fill="#f87171" fontSize="9" opacity="0.85">{thresholdLabel}</text>}
            </g>
          )}
          {kind === "bar"
            ? points.map((p, i) =>
                p.value == null || p.value === 0 ? null : (
                  <rect key={i} x={r1(x(i) - (bw * 0.36))} y={r1(y(p.value))} width={r1(bw * 0.72)} height={r1(H - padB - y(p.value))} rx="1.5" fill={color}>
                    <title>{`${dayShort(p.day)}: ${fmt(p.value)}`}</title>
                  </rect>
                )
              )
            : (
              <g>
                {areaPath && <path d={areaPath} fill={color} opacity="0.12" />}
                {segments.map((seg, si) => (
                  <polyline key={si} points={seg.join(" ")} fill="none" stroke={color} strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" />
                ))}
                {points.map((p, i) =>
                  p.value == null ? null : (
                    <circle key={i} cx={r1(x(i))} cy={r1(y(p.value))} r={n > 20 ? 1.3 : 2.1} fill={color}>
                      <title>{`${dayShort(p.day)}: ${fmt(p.value)}`}</title>
                    </circle>
                  )
                )}
              </g>
            )}
          <text x={padL} y={H - 3} fill="var(--muted)" fontSize="8.5">{dayShort(points[0].day)}</text>
          <text x={W - padR} y={H - 3} textAnchor="end" fill="var(--muted)" fontSize="8.5">{dayShort(points[n - 1].day)}</text>
        </svg>
      )}
    </div>
  );
}

const QUALITY_LABEL: Record<string, string> = {
  grounded: "Fundamentación",
  answer_relevance: "Relevancia de respuesta",
  context_relevance: "Relevancia de contexto",
};

const RANGE_OPTIONS = [7, 14, 30];

export default function ObservabilidadPage() {
  const [outbox, setOutbox] = useState<OutboxHealth | null>(null);
  const [alerts, setAlerts] = useState<OpsAlert[] | null>(null);
  const [audit, setAudit] = useState<AuditEntry[] | null>(null);
  const [http, setHttp] = useState<HttpRouteMetric[] | null>(null);
  const [quality, setQuality] = useState<QualityMetric[] | null>(null);
  const [ts, setTs] = useState<OpsTimeseries | null>(null);
  const [tsDays, setTsDays] = useState(14);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [retrying, setRetrying] = useState<string | null>(null);
  const [allowed, setAllowed] = useState<boolean | null>(null);

  const load = () => {
    api.getOutbox().then(setOutbox).catch((e) => setError(errorMessage(e)));
    api.getOpsAlerts().then((r) => setAlerts(r.alerts)).catch((e) => setError(errorMessage(e)));
    api.getAudit().then(setAudit).catch((e) => setError(errorMessage(e)));
    api.getHttpMetrics().then((r) => setHttp(r.routes)).catch((e) => setError(errorMessage(e)));
    // Panel opcional (paso 4): si el backend aún no expone el endpoint (skew de versión
    // durante un deploy), degradá a "sin datos" en vez de disparar el banner global.
    api.getQuality().then((r) => setQuality(r.metrics)).catch(() => setQuality([]));
  };

  const loadTs = (days: number) => {
    api.getTimeseries(days).then(setTs).catch(() => setTs(null));
  };

  useEffect(() => {
    setAllowed(isAdmin());
    if (isAdmin()) {
      load();
      loadTs(14);
    }
  }, []);

  const changeRange = (days: number) => {
    setTsDays(days);
    setTs(null);
    loadTs(days);
  };

  const retry = async (item: OutboxItem) => {
    setRetrying(item.id);
    setMsg("");
    try {
      await api.retryOutbox(item.id);
      setMsg("Envío reencolado ✅ — se reintentará en el próximo ciclo.");
      load();
    } catch (e) {
      setMsg("Error: " + errorMessage(e));
    } finally {
      setRetrying(null);
    }
  };

  if (allowed === false)
    return (
      <Shell>
        <BackLink href="/" label="Vacantes" />
        <p style={{ color: "var(--muted)" }}>Esta sección es solo para administradores.</p>
      </Shell>
    );

  const counts = outbox?.counts || {};

  return (
    <Shell>
      <BackLink href="/" label="Vacantes" />
      <h1 className="text-2xl font-bold mb-1">Observabilidad</h1>
      <p className="text-sm mb-6" style={{ color: "var(--muted)" }}>
        Salud de las notificaciones salientes (email/Telegram) y bitácora de acciones del dashboard.
      </p>

      {error && <p style={{ color: "#f87171", marginBottom: 14 }}>Error: {error}</p>}
      {msg && <p className="text-sm mb-4" style={{ color: "var(--accent)" }}>{msg}</p>}

      {/* ── Alertas operativas (reconciliación) ─────────────────────── */}
      <Card style={{ marginBottom: 18 }}>
        <h2 className="font-semibold mb-1">Alertas operativas</h2>
        <p className="text-sm mb-4" style={{ color: "var(--muted)" }}>
          Estados colgados que el sistema detecta solo (reuniones sin enlace, coordinaciones
          estancadas, divergencias) y requieren acción de una persona.
        </p>
        {!alerts ? (
          <p className="text-sm" style={{ color: "var(--muted)" }}>Cargando…</p>
        ) : alerts.length === 0 ? (
          <p className="text-sm" style={{ color: "#34d399" }}>✓ Sin alertas: todo el proceso está sano.</p>
        ) : (
          <div style={{ display: "grid", gap: 10 }}>
            {alerts.map((a, i) => (
              <div key={i} style={{ padding: "13px 16px", borderRadius: 12, background: "rgba(248,113,113,.05)", border: "1px solid rgba(248,113,113,.25)", display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
                <span style={{ padding: "3px 10px", borderRadius: 999, background: "rgba(248,113,113,.12)", color: "#f87171", fontSize: 11.5, fontWeight: 700, whiteSpace: "nowrap" }}>
                  {ALERT_LABEL[a.type] || a.type}
                </span>
                <div style={{ flex: 1, minWidth: 200, fontSize: 13, color: "#dbe2ee" }}>{a.detail}</div>
                {a.candidate_id && (
                  <Link href={`/candidatos/${a.candidate_id}`} style={{ fontSize: 12.5, fontWeight: 700, color: "var(--accent)", whiteSpace: "nowrap" }}>
                    Ver candidato →
                  </Link>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* ── Series de tiempo (dimensión B) ──────────────────────────── */}
      <Card style={{ marginBottom: 18 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10, marginBottom: 4 }}>
          <h2 className="font-semibold">Series de tiempo</h2>
          <div style={{ display: "flex", gap: 6 }}>
            {RANGE_OPTIONS.map((d) => (
              <button
                key={d}
                onClick={() => changeRange(d)}
                style={{
                  padding: "5px 12px", borderRadius: 8, fontSize: 12, fontWeight: 700, cursor: "pointer",
                  border: "1px solid var(--edge)",
                  background: tsDays === d ? "var(--accent)" : "transparent",
                  color: tsDays === d ? "var(--accent-ink)" : "var(--muted)",
                }}
              >
                {d}d
              </button>
            ))}
          </div>
        </div>
        <p className="text-sm mb-4" style={{ color: "var(--muted)" }}>
          Tendencia diaria de la operación (zona {ts?.timezone || "America/Lima"}). Los datos ya se
          registran; aquí se grafican en el tiempo. Pasá el cursor sobre un punto para ver el valor.
        </p>

        {!ts ? (
          <p className="text-sm" style={{ color: "var(--muted)" }}>Cargando…</p>
        ) : (
          <>
            <h3 style={{ fontSize: 13, fontWeight: 700, color: "#dbe2ee", margin: "6px 0 8px" }}>Operación LLM (esta empresa)</h3>
            <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))" }}>
              <MiniChart title="Llamadas / día" kind="bar" color="var(--accent)" fmt={fmtInt}
                points={ts.llm.map((p) => ({ day: p.day, value: p.calls }))} />
              <MiniChart title="Errores (fallback) / día" kind="bar" color="#f87171" fmt={fmtInt}
                points={ts.llm.map((p) => ({ day: p.day, value: p.errors }))} />
              <MiniChart title="Costo estimado / día" kind="line" color="#34d399" fmt={fmtUsd}
                points={ts.llm.map((p) => ({ day: p.day, value: p.cost }))} />
              <MiniChart title="Latencia media / día" kind="line" color="#d97706" fmt={fmtMsN}
                points={ts.llm.map((p) => ({ day: p.day, value: p.avg_ms }))} />
            </div>

            <h3 style={{ fontSize: 13, fontWeight: 700, color: "#dbe2ee", margin: "18px 0 8px" }}>Calidad de las respuestas (esta empresa)</h3>
            {Object.keys(ts.quality).length === 0 ? (
              <p className="text-sm" style={{ color: "var(--muted)" }}>
                Sin mediciones en el período. Activá <em>Alertas de calidad</em> y el <em>tracing</em> del bot.
              </p>
            ) : (
              <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))" }}>
                {Object.entries(ts.quality).map(([metric, pts]) => {
                  const byDay = new Map(pts.map((p) => [p.day, p]));
                  const thr = pts.length ? pts[pts.length - 1].threshold : undefined;
                  return (
                    <MiniChart
                      key={metric}
                      title={QUALITY_LABEL[metric] || metric}
                      kind="line"
                      color="#8b8cfa"
                      fmt={fmtPct}
                      yMax={1}
                      threshold={thr}
                      thresholdLabel={thr != null ? `umbral ${Math.round(thr * 100)}%` : undefined}
                      points={ts.day_keys.map((d) => ({ day: d, value: byDay.has(d) ? byDay.get(d)!.rate : null }))}
                    />
                  );
                })}
              </div>
            )}

            <h3 style={{ fontSize: 13, fontWeight: 700, color: "#dbe2ee", margin: "18px 0 8px" }}>Rendimiento HTTP (proceso · infraestructura)</h3>
            <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))" }}>
              <MiniChart title="Requests / día" kind="bar" color="var(--accent)" fmt={fmtInt}
                points={ts.http.map((p) => ({ day: p.day, value: p.requests }))} />
              <MiniChart title="Errores 5xx / día" kind="bar" color="#f87171" fmt={fmtInt}
                points={ts.http.map((p) => ({ day: p.day, value: p.errors }))} />
              <MiniChart title="p95 pico / día" kind="line" color="#d97706" fmt={fmtMsN}
                points={ts.http.map((p) => ({ day: p.day, value: p.peak_p95_ms }))} />
            </div>
            <p className="text-xs mt-3" style={{ color: "var(--muted)" }}>
              El volumen HTTP se deriva como diferencia entre snapshots consecutivos (los contadores
              son acumulados desde el arranque; un reinicio del proceso se detecta y no cuenta negativo).
            </p>
          </>
        )}
      </Card>

      {/* ── Calidad de las respuestas (signo vital · paso 4) ─────────── */}
      <Card style={{ marginBottom: 18 }}>
        <h2 className="font-semibold mb-1">Calidad de las respuestas (IA)</h2>
        <p className="text-sm mb-4" style={{ color: "var(--muted)" }}>
          Tendencia diaria de <strong>fundamentación</strong> (¿la respuesta se apoya solo en la
          info de la vacante?) y <strong>relevancia</strong> (¿atiende la pregunta?), medida por un
          juez LLM sobre las trazas reales. Se activa en Configuración → Calidad (requiere trazas).
        </p>
        {!quality ? (
          <p className="text-sm" style={{ color: "var(--muted)" }}>Cargando…</p>
        ) : quality.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--muted)" }}>
            Sin mediciones aún. Activá <em>Alertas de calidad</em> y el <em>tracing</em> del bot para
            empezar a registrar el signo vital.
          </p>
        ) : (
          <div style={{ display: "grid", gap: 10 }}>
            {(() => {
              const latest = quality[0]?.day;
              return quality
                .filter((m) => m.day === latest)
                .map((m) => (
                  <div key={m.metric} style={{ padding: "13px 16px", borderRadius: 12, background: "rgba(255,255,255,.02)", border: "1px solid var(--edge)", display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
                    <span style={{ fontSize: 22, fontWeight: 800, color: rateColor(m.rate, m.threshold), lineHeight: 1, minWidth: 62 }}>
                      {Math.round(m.rate * 100)}%
                    </span>
                    <div style={{ flex: 1, minWidth: 200 }}>
                      <div style={{ fontSize: 13.5, fontWeight: 600, color: "#dbe2ee" }}>
                        {m.metric === "grounded" ? "Fundamentación" : m.metric === "answer_relevance" ? "Relevancia de respuesta" : m.metric === "context_relevance" ? "Relevancia de contexto" : m.metric}
                      </div>
                      <div style={{ fontSize: 11.5, color: "var(--muted)", marginTop: 3 }}>
                        {when(m.day)} · n={m.sample_size} · umbral {Math.round(m.threshold * 100)}%
                      </div>
                    </div>
                    {m.rate < m.threshold && (
                      <span style={{ padding: "3px 10px", borderRadius: 999, background: "rgba(248,113,113,.12)", color: "#f87171", fontSize: 11.5, fontWeight: 700, whiteSpace: "nowrap" }}>
                        Bajo umbral
                      </span>
                    )}
                  </div>
                ));
            })()}
          </div>
        )}
      </Card>

      {/* ── Salud del outbox ─────────────────────────────────────────── */}
      <Card style={{ marginBottom: 18 }}>
        <h2 className="font-semibold mb-1">Cola de envíos (outbox)</h2>
        <p className="text-sm mb-4" style={{ color: "var(--muted)" }}>
          Cada notificación se intenta en línea; si falla queda encolada y se reintenta con backoff.
          Los <strong>fallidos</strong> agotaron sus reintentos (dead-letter) y requieren atención.
        </p>

        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 18 }}>
          <CountChip label="Pendientes" value={counts.pending || 0} color="#d97706" />
          <CountChip label="Fallidos" value={counts.failed || 0} color="#f87171" />
          <CountChip label="Enviados" value={counts.sent || 0} color="#34d399" />
        </div>

        {!outbox ? (
          <p className="text-sm" style={{ color: "var(--muted)" }}>Cargando…</p>
        ) : outbox.items.length === 0 ? (
          <p className="text-sm" style={{ color: "#34d399" }}>✓ Sin envíos pendientes ni fallidos.</p>
        ) : (
          <div style={{ display: "grid", gap: 10 }}>
            {outbox.items.map((it) => {
              const sm = STATUS_META[it.status] || STATUS_META.pending;
              return (
                <div key={it.id} style={{ padding: "13px 16px", borderRadius: 12, background: "rgba(255,255,255,.02)", border: "1px solid var(--edge)", display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
                  <span style={{ padding: "3px 10px", borderRadius: 999, background: sm.bg, color: sm.color, fontSize: 11.5, fontWeight: 700, whiteSpace: "nowrap" }}>{sm.label}</span>
                  <div style={{ flex: 1, minWidth: 200 }}>
                    <div style={{ fontSize: 13.5, fontWeight: 600, color: "#dbe2ee" }}>{KIND_LABEL[it.kind] || it.kind}</div>
                    <div style={{ fontSize: 11.5, color: "var(--muted)", marginTop: 3 }}>
                      Intento {it.attempts}/{it.max_attempts} · creado {when(it.created_at)}
                      {it.last_error ? ` · ${it.last_error.slice(0, 120)}` : ""}
                    </div>
                  </div>
                  <button
                    onClick={() => retry(it)}
                    disabled={retrying === it.id}
                    style={{ padding: "8px 14px", borderRadius: 9, background: "var(--accent)", color: "var(--accent-ink)", border: "none", fontSize: 12.5, fontWeight: 700, cursor: "pointer", opacity: retrying === it.id ? 0.6 : 1, whiteSpace: "nowrap" }}
                  >
                    {retrying === it.id ? "Reencolando…" : "Reintentar"}
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </Card>

      {/* ── Rendimiento HTTP (O3) ────────────────────────────────────── */}
      <Card style={{ marginBottom: 18 }}>
        <h2 className="font-semibold mb-1">Rendimiento HTTP</h2>
        <p className="text-sm mb-4" style={{ color: "var(--muted)" }}>
          Conteo, errores y latencia por ruta de la API desde el último arranque del backend.
        </p>
        {!http ? (
          <p className="text-sm" style={{ color: "var(--muted)" }}>Cargando…</p>
        ) : http.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--muted)" }}>Aún no hay requests registrados.</p>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
              <thead>
                <tr style={{ color: "var(--muted)", textAlign: "left" }}>
                  <th style={{ padding: "6px 10px", fontWeight: 600 }}>Ruta</th>
                  <th style={{ padding: "6px 10px", fontWeight: 600, textAlign: "right" }}>Requests</th>
                  <th style={{ padding: "6px 10px", fontWeight: 600, textAlign: "right" }}>4xx</th>
                  <th style={{ padding: "6px 10px", fontWeight: 600, textAlign: "right" }}>5xx</th>
                  <th style={{ padding: "6px 10px", fontWeight: 600, textAlign: "right" }}>Prom. (ms)</th>
                  <th style={{ padding: "6px 10px", fontWeight: 600, textAlign: "right" }}>p95 (ms)</th>
                  <th style={{ padding: "6px 10px", fontWeight: 600, textAlign: "right" }}>p99 (ms)</th>
                  <th style={{ padding: "6px 10px", fontWeight: 600, textAlign: "right" }}>Máx. (ms)</th>
                </tr>
              </thead>
              <tbody>
                {http.slice(0, 20).map((r) => (
                  <tr key={r.route} style={{ borderTop: "1px solid var(--edge)" }}>
                    <td style={{ padding: "7px 10px", color: "#dbe2ee", fontFamily: "var(--font-jetbrains), monospace", fontSize: 11.5 }}>{r.route}</td>
                    <td style={{ padding: "7px 10px", textAlign: "right", color: "#dbe2ee", fontWeight: 600 }}>{r.count}</td>
                    <td style={{ padding: "7px 10px", textAlign: "right", color: r.client_errors ? "#d97706" : "var(--muted)" }}>{r.client_errors}</td>
                    <td style={{ padding: "7px 10px", textAlign: "right", color: r.errors ? "#f87171" : "var(--muted)", fontWeight: r.errors ? 700 : 400 }}>{r.errors}</td>
                    <td style={{ padding: "7px 10px", textAlign: "right", color: "#dbe2ee" }}>{r.avg_ms}</td>
                    <td style={{ padding: "7px 10px", textAlign: "right", color: "#dbe2ee" }}>{r.p95_ms ?? "—"}</td>
                    <td style={{ padding: "7px 10px", textAlign: "right", color: "#dbe2ee" }}>{r.p99_ms ?? "—"}</td>
                    <td style={{ padding: "7px 10px", textAlign: "right", color: "var(--muted)" }}>{r.max_ms}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* ── Bitácora de auditoría ────────────────────────────────────── */}
      <Card>
        <h2 className="font-semibold mb-1">Bitácora de auditoría</h2>
        <p className="text-sm mb-4" style={{ color: "var(--muted)" }}>
          Últimas 100 acciones del dashboard (quién, qué y cuándo).
        </p>

        {!audit ? (
          <p className="text-sm" style={{ color: "var(--muted)" }}>Cargando…</p>
        ) : audit.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--muted)" }}>Aún no hay acciones registradas.</p>
        ) : (
          <div style={{ display: "grid", gap: 8 }}>
            {audit.map((a) => (
              <div key={a.id} style={{ display: "flex", alignItems: "baseline", gap: 12, padding: "9px 12px", borderRadius: 10, background: "rgba(255,255,255,.02)", border: "1px solid var(--edge)", flexWrap: "wrap" }}>
                <span style={{ fontSize: 11.5, color: "var(--muted)", minWidth: 110, whiteSpace: "nowrap" }}>{when(a.created_at)}</span>
                <span style={{ fontSize: 13, fontWeight: 600, color: "#dbe2ee" }}>{ACTION_LABEL[a.action] || a.action}</span>
                {a.summary && <span style={{ fontSize: 12.5, color: "var(--muted)" }}>· {a.summary}</span>}
                <span style={{ flex: 1 }} />
                <span style={{ fontSize: 11.5, color: "var(--muted)" }}>{a.actor_email}</span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </Shell>
  );
}
