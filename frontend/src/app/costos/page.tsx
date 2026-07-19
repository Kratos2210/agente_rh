"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Shell, Card, BackLink } from "@/components/Shell";
import { api, errorMessage, CostsReport, CostsVacancy } from "@/lib/api";
import { isAdmin } from "@/lib/auth";
import { fmtCost } from "@/lib/stages";

const MONO = "var(--font-jetbrains), monospace";
const RANGES = [7, 30, 90];

function when(iso: string): string {
  try {
    return new Date(iso).toLocaleString("es-PE", {
      day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "America/Lima",
    });
  } catch {
    return iso;
  }
}

function dayLabel(day: string): string {
  // "2026-07-05" → "05 jul" (sin pasar por Date para evitar el corrimiento UTC).
  const [, m, d] = day.split("-");
  const months = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
  return `${d} ${months[Number(m) - 1] || m}`;
}

function fmtTokens(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}K` : String(n);
}

function Kpi({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{ padding: "12px 18px", borderRadius: 13, background: "rgba(255,255,255,.03)", border: "1px solid var(--edge)", minWidth: 140 }}>
      <div style={{ fontSize: 24, fontWeight: 800, color, lineHeight: 1.1, fontFamily: MONO, letterSpacing: "-.02em" }}>{value}</div>
      <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 5, fontWeight: 600 }}>{label}</div>
    </div>
  );
}

// Barras diarias de costo (sin librerías, patrón Funnel/MiniBar): tooltip nativo con el detalle.
function DailyBars({ report }: { report: CostsReport }) {
  const max = Math.max(...report.daily.map((d) => d.cost), 0);
  if (max === 0)
    return <p className="text-sm" style={{ color: "var(--muted)" }}>Sin consumo en el período.</p>;
  return (
    <div>
      <div style={{ display: "flex", alignItems: "flex-end", gap: report.days > 45 ? 1 : 3, height: 120 }}>
        {report.daily.map((d) => (
          <div
            key={d.day}
            title={`${dayLabel(d.day)} · $${fmtCost(d.cost)} · ${d.tokens.toLocaleString()} tokens`}
            style={{
              flex: 1,
              height: `${d.cost > 0 ? Math.max(4, (d.cost / max) * 100) : 0}%`,
              minHeight: d.cost > 0 ? 4 : 0,
              borderRadius: "3px 3px 0 0",
              background: "linear-gradient(180deg, var(--accent), rgba(139,140,250,.35))",
              alignSelf: "flex-end",
            }}
          />
        ))}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 6, fontSize: 11, color: "var(--muted)" }}>
        <span>{dayLabel(report.daily[0]?.day || "")}</span>
        <span>{dayLabel(report.daily[report.daily.length - 1]?.day || "")}</span>
      </div>
    </div>
  );
}

function VacancyRow({ vac, totalCost, expanded, onToggle }: {
  vac: CostsVacancy; totalCost: number; expanded: boolean; onToggle: () => void;
}) {
  const pct = totalCost > 0 ? Math.round((vac.cost / totalCost) * 100) : 0;
  return (
    <>
      <tr onClick={onToggle} style={{ borderTop: "1px solid var(--edge)", cursor: "pointer" }}>
        <td style={{ padding: "9px 10px", color: "#dbe2ee", fontWeight: 600, fontSize: 13 }}>
          <span style={{ color: "var(--muted)", marginRight: 8, fontSize: 11 }}>{expanded ? "▼" : "▶"}</span>
          {vac.title || "(sin título)"}
          {vac.status !== "open" && (
            <span style={{ marginLeft: 8, padding: "2px 8px", borderRadius: 999, background: "rgba(148,163,184,.12)", color: "#94a3b8", fontSize: 10.5, fontWeight: 700 }}>
              {vac.status === "closed" ? "Cerrada" : vac.status}
            </span>
          )}
        </td>
        <td style={{ padding: "9px 10px", textAlign: "right", color: "#dbe2ee", fontFamily: MONO }}>{vac.tokens.total.toLocaleString()}</td>
        <td style={{ padding: "9px 10px", textAlign: "right", color: "#34d399", fontFamily: MONO, fontWeight: 700 }}>${fmtCost(vac.cost)}</td>
        <td style={{ padding: "9px 10px", textAlign: "right", color: "var(--muted)", fontSize: 12 }}>{pct}%</td>
      </tr>
      {expanded && vac.candidates.map((c, i) => (
        <tr key={c.candidate_id ?? `general-${i}`} style={{ borderTop: "1px solid rgba(255,255,255,.04)", background: "rgba(255,255,255,.015)" }}>
          <td style={{ padding: "7px 10px 7px 34px", fontSize: 12.5 }}>
            {c.candidate_id ? (
              <Link href={`/candidatos/${c.candidate_id}`} style={{ color: "var(--accent)", fontWeight: 600 }}>
                {c.name || "Candidato anonimizado"}
              </Link>
            ) : (
              <span style={{ color: "var(--muted)" }}>Proceso general (sourcing / pre-filtro)</span>
            )}
            <span style={{ marginLeft: 10, fontSize: 11, color: "var(--muted)" }}>últ. actividad {when(c.last_at)}</span>
          </td>
          <td style={{ padding: "7px 10px", textAlign: "right", color: "#aeb8cc", fontFamily: MONO, fontSize: 12.5 }}>{c.tokens.toLocaleString()}</td>
          <td style={{ padding: "7px 10px", textAlign: "right", color: "#aeb8cc", fontFamily: MONO, fontSize: 12.5 }}>${fmtCost(c.cost)}</td>
          <td />
        </tr>
      ))}
    </>
  );
}

export default function CostosPage() {
  const [report, setReport] = useState<CostsReport | null>(null);
  const [days, setDays] = useState(30);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [allowed, setAllowed] = useState<boolean | null>(null);

  useEffect(() => {
    setAllowed(isAdmin());
    if (!isAdmin()) return;
    setReport(null);
    api.getCosts(days).then(setReport).catch((e) => setError(errorMessage(e)));
  }, [days]);

  const toggle = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  if (allowed === false)
    return (
      <Shell>
        <BackLink href="/" label="Vacantes" />
        <p style={{ color: "var(--muted)" }}>Esta sección es solo para administradores.</p>
      </Shell>
    );

  const t = report?.totals;
  const avgDaily = report && report.days > 0 ? report.totals.cost / report.days : 0;

  return (
    <Shell>
      <BackLink href="/" label="Vacantes" />
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
        <h1 className="text-2xl font-bold mb-1" style={{ flex: 1 }}>Costos LLM</h1>
        <div style={{ display: "flex", gap: 6 }}>
          {RANGES.map((r) => (
            <button
              key={r}
              onClick={() => setDays(r)}
              style={{
                padding: "7px 14px", borderRadius: 9, fontSize: 12.5, fontWeight: 700, cursor: "pointer",
                border: "1px solid var(--edge)",
                background: days === r ? "var(--accent)" : "transparent",
                color: days === r ? "var(--accent-ink)" : "var(--muted)",
              }}
            >
              {r} días
            </button>
          ))}
        </div>
      </div>
      <p className="text-sm mb-6" style={{ color: "var(--muted)" }}>
        Consumo de tokens y costo estimado por vacante, día y candidato
        {report ? ` · desde ${dayLabel(report.since)} (zona ${report.timezone})` : ""}.
      </p>

      {error && <p style={{ color: "#f87171", marginBottom: 14 }}>Error: {error}</p>}

      {/* ── KPIs del período ─────────────────────────────────────────── */}
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 18 }}>
        <Kpi label="Costo del período" value={t ? `$${fmtCost(t.cost)}` : "…"} color="#34d399" />
        <Kpi label="Tokens" value={t ? fmtTokens(t.tokens.total) : "…"} color="#a78bfa" />
        <Kpi label="Llamadas LLM" value={t ? String(t.calls) : "…"} color="#22d3ee" />
        <Kpi label="Promedio diario" value={report ? `$${fmtCost(avgDaily)}` : "…"} color="#fbbf24" />
      </div>

      {/* ── Serie diaria ─────────────────────────────────────────────── */}
      <Card style={{ marginBottom: 18 }}>
        <h2 className="font-semibold mb-1">Consumo por día</h2>
        <p className="text-sm mb-4" style={{ color: "var(--muted)" }}>
          Costo estimado por día ({report?.timezone || "America/Lima"}). Pasa el cursor sobre una barra para ver el detalle.
        </p>
        {!report ? (
          <p className="text-sm" style={{ color: "var(--muted)" }}>Cargando…</p>
        ) : (
          <DailyBars report={report} />
        )}
      </Card>

      {/* ── Por vacante, con drill-down por candidato ────────────────── */}
      <Card style={{ marginBottom: 18 }}>
        <h2 className="font-semibold mb-1">Por vacante</h2>
        <p className="text-sm mb-4" style={{ color: "var(--muted)" }}>
          Haz clic en una vacante para ver qué candidatos generaron el consumo.
        </p>
        {!report ? (
          <p className="text-sm" style={{ color: "var(--muted)" }}>Cargando…</p>
        ) : report.vacancies.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--muted)" }}>Sin consumo registrado en el período.</p>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
              <thead>
                <tr style={{ color: "var(--muted)", textAlign: "left" }}>
                  <th style={{ padding: "6px 10px", fontWeight: 600 }}>Vacante / candidato</th>
                  <th style={{ padding: "6px 10px", fontWeight: 600, textAlign: "right" }}>Tokens</th>
                  <th style={{ padding: "6px 10px", fontWeight: 600, textAlign: "right" }}>Costo</th>
                  <th style={{ padding: "6px 10px", fontWeight: 600, textAlign: "right" }}>% del total</th>
                </tr>
              </thead>
              <tbody>
                {report.vacancies.map((v) => (
                  <VacancyRow
                    key={v.vacancy_id}
                    vac={v}
                    totalCost={report.totals.cost}
                    expanded={expanded.has(v.vacancy_id)}
                    onToggle={() => toggle(v.vacancy_id)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* ── Desglose por modelo ──────────────────────────────────────── */}
      {report && Object.keys(report.totals.cost_by_model).length > 0 && (
        <Card>
          <h2 className="font-semibold mb-1">Por modelo</h2>
          <p className="text-sm mb-4" style={{ color: "var(--muted)" }}>
            Costo del período según el modelo que atendió cada etapa (routing de costos).
          </p>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
            {Object.entries(report.totals.cost_by_model).map(([model, cost]) => (
              <div key={model} style={{ padding: "10px 16px", borderRadius: 11, background: "rgba(255,255,255,.02)", border: "1px solid var(--edge)" }}>
                <div style={{ fontSize: 12, color: "var(--muted)", fontFamily: MONO }}>{model || "(sin modelo)"}</div>
                <div style={{ fontSize: 18, fontWeight: 800, color: "#34d399", fontFamily: MONO, marginTop: 3 }}>${fmtCost(cost)}</div>
              </div>
            ))}
          </div>
        </Card>
      )}
    </Shell>
  );
}
