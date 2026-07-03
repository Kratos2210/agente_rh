"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Shell, Card, BackLink } from "@/components/Shell";
import { api, errorMessage, AuditEntry, HttpRouteMetric, OpsAlert, OutboxHealth, OutboxItem, QualityMetric } from "@/lib/api";
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

export default function ObservabilidadPage() {
  const [outbox, setOutbox] = useState<OutboxHealth | null>(null);
  const [alerts, setAlerts] = useState<OpsAlert[] | null>(null);
  const [audit, setAudit] = useState<AuditEntry[] | null>(null);
  const [http, setHttp] = useState<HttpRouteMetric[] | null>(null);
  const [quality, setQuality] = useState<QualityMetric[] | null>(null);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [retrying, setRetrying] = useState<string | null>(null);
  const [allowed, setAllowed] = useState<boolean | null>(null);

  const load = () => {
    api.getOutbox().then(setOutbox).catch((e) => setError(errorMessage(e)));
    api.getOpsAlerts().then((r) => setAlerts(r.alerts)).catch((e) => setError(errorMessage(e)));
    api.getAudit().then(setAudit).catch((e) => setError(errorMessage(e)));
    api.getHttpMetrics().then((r) => setHttp(r.routes)).catch((e) => setError(errorMessage(e)));
    api.getQuality().then((r) => setQuality(r.metrics)).catch((e) => setError(errorMessage(e)));
  };

  useEffect(() => {
    setAllowed(isAdmin());
    if (isAdmin()) load();
  }, []);

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
                        {m.metric === "grounded" ? "Fundamentación" : m.metric === "answer_relevance" ? "Relevancia" : m.metric}
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
