"use client";

import { useEffect, useState } from "react";
import { Shell, Card, BackLink } from "@/components/Shell";
import { api, AuditEntry, OutboxHealth, OutboxItem } from "@/lib/api";
import { isAdmin } from "@/lib/auth";

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
  const [audit, setAudit] = useState<AuditEntry[] | null>(null);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [retrying, setRetrying] = useState<string | null>(null);
  const [allowed, setAllowed] = useState<boolean | null>(null);

  const load = () => {
    api.getOutbox().then(setOutbox).catch((e) => setError(String(e)));
    api.getAudit().then(setAudit).catch((e) => setError(String(e)));
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
      setMsg("Error: " + String(e));
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
