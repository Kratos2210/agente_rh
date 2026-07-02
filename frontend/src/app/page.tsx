"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Shell } from "@/components/Shell";
import { Funnel, MiniBar } from "@/components/ui";
import { api, errorMessage, Metrics, Recruiter, Vacancy } from "@/lib/api";
import { ACCENT, avatarColor, initials } from "@/lib/stages";

const MONO = "var(--font-jetbrains), monospace";

const VAC_STATUS: Record<string, { label: string; color: string; bg: string; bd: string }> = {
  open: { label: "Abierta", color: "#34d399", bg: "rgba(52,211,153,.12)", bd: "rgba(52,211,153,.3)" },
  paused: { label: "En pausa", color: "#fbbf24", bg: "rgba(251,191,36,.12)", bd: "rgba(251,191,36,.3)" },
  closed: { label: "Cerrada", color: "#94a3b8", bg: "rgba(148,163,184,.12)", bd: "rgba(148,163,184,.25)" },
};

function panel(style?: React.CSSProperties): React.CSSProperties {
  return { padding: "22px 24px", borderRadius: 16, background: "var(--card)", border: "1px solid var(--edge)", ...style };
}

export default function Home() {
  const [vacancies, setVacancies] = useState<Vacancy[]>([]);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [recruiters, setRecruiters] = useState<Recruiter[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api.listVacancies().then(setVacancies).catch((e) => setError(errorMessage(e)));
    api.getMetrics().then(setMetrics).catch(() => {});
    api.listRecruiters().then(setRecruiters).catch(() => {});
  }, []);

  const f = metrics?.funnel || {};
  const imported = f.imported ?? 0;
  const passRate = imported > 0 ? Math.round(((f.prescreen_passed ?? 0) / imported) * 100) : 0;
  const tokens = metrics?.tokens?.total ?? 0;
  const kpis = [
    { icon: "⚇", label: "Postulantes", value: String(imported) },
    { icon: "▤", label: "Pasan filtro CV", value: `${passRate}%` },
    { icon: "▲", label: "Avanzados", value: String(f.advanced ?? 0) },
    { icon: "◇", label: "Tokens IA", value: tokens >= 1000 ? `${(tokens / 1000).toFixed(1)}K` : String(tokens) },
  ];
  const funnelRows: [string, number, string][] = [
    ["Importados", f.imported ?? 0, "#94a3b8"],
    ["Aptos · CV", f.prescreen_passed ?? 0, "#34d399"],
    ["Contactados", f.invited ?? 0, "#fbbf24"],
    ["En entrevista", f.interviewing ?? 0, ACCENT.c],
    ["Evaluados", f.finished ?? 0, "#a78bfa"],
    ["Avanzados", f.advanced ?? 0, "#34d399"],
  ];

  return (
    <Shell>
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 20, flexWrap: "wrap", marginBottom: 26 }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, color: "var(--ac)", marginBottom: 6 }}>Panel de selección</div>
          <h1 style={{ margin: 0, fontSize: 34, fontWeight: 800, letterSpacing: "-.03em", color: "var(--heading)" }}>Vacantes</h1>
          <p style={{ margin: "8px 0 0", color: "var(--muted)", fontSize: 14.5, maxWidth: 520 }}>
            Configura vacantes y deja que el agente importe, filtre, entreviste y agende a los candidatos de punta a punta.
          </p>
        </div>
        <Link href="/vacantes/nueva" style={{
          display: "flex", alignItems: "center", gap: 11, padding: "13px 20px", borderRadius: 12,
          background: "linear-gradient(135deg,var(--ac),var(--ac-btn))", color: "#fff", fontWeight: 700, fontSize: 14.5,
          textDecoration: "none", boxShadow: "0 8px 22px var(--ac-soft)",
        }}>
          <span style={{ fontSize: 18, fontWeight: 600, marginTop: -1 }}>+</span> Nueva vacante
        </Link>
      </div>

      {error && <p style={{ color: "#f87171", marginBottom: 16 }}>Error: {error}. ¿El backend está en :8000?</p>}

      {/* KPIs */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 14, marginBottom: 16 }}>
        {kpis.map((k) => (
          <div key={k.label} style={{ padding: 20, borderRadius: 16, background: "linear-gradient(180deg,rgba(255,255,255,.035),rgba(255,255,255,.012))", border: "1px solid var(--edge)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--muted)", fontSize: 12.5, fontWeight: 600, marginBottom: 14 }}>
              <span style={{ color: "var(--ac)" }}>{k.icon}</span>{k.label}
            </div>
            <div style={{ fontFamily: MONO, fontSize: 30, fontWeight: 700, letterSpacing: "-.02em", color: "var(--heading)", lineHeight: 1 }}>{k.value}</div>
          </div>
        ))}
      </div>

      {/* Embudo + Equipo */}
      <div style={{ display: "grid", gridTemplateColumns: "1.55fr 1fr", gap: 14, marginBottom: 16 }}>
        <div style={panel()}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
            <div style={{ fontSize: 15, fontWeight: 700, color: "#eef2f9" }}>Embudo de selección</div>
            <div style={{ fontSize: 12, color: "var(--muted-2)", fontWeight: 600 }}>Todas las vacantes</div>
          </div>
          <div style={{ fontSize: 12.5, color: "var(--muted)", marginBottom: 18 }}>Conversión automática del agente en cada etapa.</div>
          <Funnel rows={funnelRows} />
        </div>
        <div style={panel()}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 9 }}><span style={{ color: "var(--ac)", fontSize: 15 }}>⚇</span><span style={{ fontSize: 15, fontWeight: 700, color: "#eef2f9" }}>Equipo de RR.HH.</span></div>
            <Link href="/equipo" style={{ fontSize: 13, color: "var(--ac)", fontWeight: 700, textDecoration: "none" }}>Ver todo</Link>
          </div>
          {recruiters.map((m) => (
            <div key={m.id} style={{ display: "flex", alignItems: "center", gap: 12, padding: 12, borderRadius: 13, marginBottom: 9, background: "rgba(255,255,255,.025)", border: "1px solid var(--edge-soft)" }}>
              <div style={{ width: 40, height: 40, borderRadius: 11, background: avatarColor(m.name), display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 700, color: "#0a0e16", fontSize: 15 }}>{initials(m.name)}</div>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ fontSize: 14, fontWeight: 700, color: "#eef2f9" }}>{m.name}</div>
                <div style={{ fontSize: 11.5, color: "var(--muted)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{m.role}{m.company ? ` · ${m.company}` : ""}</div>
              </div>
              <div style={{ fontFamily: MONO, fontSize: 12, color: "#9aa4b8", fontWeight: 600 }}>{m.open_vacancies ?? 0}</div>
            </div>
          ))}
          {recruiters.length === 0 && <p style={{ fontSize: 13, color: "var(--muted)" }}>Sin reclutadores aún.</p>}
        </div>
      </div>

      {/* Lista de vacantes */}
      <div style={{ fontSize: 13, fontWeight: 700, color: "var(--muted)", letterSpacing: ".06em", textTransform: "uppercase", margin: "26px 0 12px" }}>Vacantes activas</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 11 }}>
        {vacancies.map((v) => {
          const sc = VAC_STATUS[v.status] || VAC_STATUS.closed;
          const sct = v.stage_counts || {};
          const ok = (sct.prescreen_passed || 0) + (sct.finished || 0) + (sct.scheduled || 0) + (sct.advanced || 0);
          const warn = (sct.invited || 0) + (sct.consented || 0) + (sct.interviewing || 0) + (sct.scheduling || 0);
          const bad = (sct.prescreen_rejected || 0) + (sct.rejected || 0) + (sct.declined || 0) + (sct.no_response || 0);
          const rest = (v.candidate_count || 0) - ok - warn - bad;
          const bars = [
            { v: ok, c: "#34d399" }, { v: warn, c: "#fbbf24" }, { v: bad, c: "#f87171" },
            { v: Math.max(0, rest), c: "rgba(255,255,255,.08)" },
          ].filter((b) => b.v > 0);
          const meta = [v.recruiter?.company, v.area, v.recruiter?.name].filter(Boolean).join(" · ");
          return (
            <Link key={v.id} href={`/vacantes/${v.id}`} style={{
              display: "flex", alignItems: "center", gap: 18, padding: "18px 20px", borderRadius: 15,
              background: "linear-gradient(180deg,rgba(255,255,255,.035),rgba(255,255,255,.012))", border: "1px solid var(--edge)",
              textDecoration: "none",
            }}>
              <div style={{ width: 46, height: 46, borderRadius: 12, background: "var(--ac-soft)", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--ac)", fontSize: 19 }}>▣</div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 5 }}>
                  <div style={{ fontSize: 16, fontWeight: 700, color: "var(--heading)" }}>{v.title}</div>
                  <div style={{ padding: "2px 9px", borderRadius: 20, fontSize: 11, fontWeight: 700, background: sc.bg, color: sc.color, border: `1px solid ${sc.bd}` }}>{sc.label}</div>
                </div>
                <div style={{ fontSize: 12.5, color: "var(--muted)" }}>{meta || "Sin responsable"}</div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
                <div style={{ textAlign: "right" }}>
                  <div style={{ fontFamily: MONO, fontSize: 17, fontWeight: 700, color: "#eef2f9" }}>{v.candidate_count ?? 0}</div>
                  <div style={{ fontSize: 10.5, color: "var(--muted-2)", fontWeight: 600 }}>candidatos</div>
                </div>
                <div style={{ width: 140 }}>{bars.length > 0 ? <MiniBar stats={bars} /> : <MiniBar stats={[{ v: 1, c: "rgba(255,255,255,.08)" }]} />}</div>
                <span style={{ color: "var(--muted-3)", fontSize: 18 }}>›</span>
              </div>
            </Link>
          );
        })}
        {vacancies.length === 0 && !error && <p style={{ color: "var(--muted)" }}>Aún no hay vacantes. Crea la primera.</p>}
      </div>
    </Shell>
  );
}
