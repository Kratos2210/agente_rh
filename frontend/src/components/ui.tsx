"use client";

import Link from "next/link";
import type { CandidateRow } from "@/lib/api";
import { ACCENT, avatarColor, cvChip, initials, scoreColor, sourceIcon, stageMeta } from "@/lib/stages";

const MONO = "var(--font-jetbrains), monospace";

// ── Barra segmentada (mini progreso por etapa en la lista de vacantes) ──────────
export function MiniBar({ stats }: { stats: { v: number; c: string }[] }) {
  const total = stats.reduce((a, s) => a + s.v, 0) || 1;
  return (
    <div style={{ display: "flex", gap: 3, height: 7, borderRadius: 5, overflow: "hidden", background: "rgba(255,255,255,.05)" }}>
      {stats.map((s, i) => (
        <div key={i} style={{ flex: s.v / total, background: s.c, opacity: 0.85 }} />
      ))}
    </div>
  );
}

// ── Embudo de selección (dashboard) ────────────────────────────────────────────
export function Funnel({ rows }: { rows: [string, number, string][] }) {
  const max = Math.max(1, ...rows.map((r) => r[1]));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
      {rows.map((r, i) => (
        <div key={i} style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{ width: 96, fontSize: 12.5, color: "#9aa4b8", fontWeight: 600, textAlign: "right", flex: "none" }}>{r[0]}</div>
          <div style={{ flex: 1, height: 26, borderRadius: 7, background: "rgba(255,255,255,.04)", overflow: "hidden" }}>
            <div style={{ height: "100%", width: `${Math.round((r[1] / max) * 100)}%`, borderRadius: 7, background: r[2], opacity: 0.85, minWidth: 8, transition: "width .5s" }} />
          </div>
          <div style={{ width: 38, fontFamily: MONO, fontSize: 13, fontWeight: 700, color: "#e8edf6", flex: "none" }}>{r[1]}</div>
        </div>
      ))}
    </div>
  );
}

// ── Radar de perfil por criterio (SVG sin librerías) ────────────────────────────
export function Radar({ crit, threshold = 75 }: { crit: { n: string; score: number }[]; threshold?: number }) {
  const N = crit.length;
  if (!N) return null;
  const cx = 185, cy = 160, R = 108;
  const pt = (v: number, i: number): [number, number] => {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / N;
    const r = (R * v) / 100;
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  };
  const poly = (v: number) => crit.map((_, i) => pt(v, i).join(",")).join(" ");
  const dataStr = crit.map((c, i) => pt(c.score, i).join(",")).join(" ");
  return (
    <svg viewBox="0 0 370 300" style={{ width: "100%", display: "block" }}>
      {[25, 50, 75, 100].map((g) => (
        <polygon key={g} points={poly(g)} fill="none" stroke="rgba(255,255,255,.06)" strokeWidth={1} />
      ))}
      {crit.map((_, i) => { const [x, y] = pt(100, i); return <line key={i} x1={cx} y1={cy} x2={x} y2={y} stroke="rgba(255,255,255,.05)" strokeWidth={1} />; })}
      <polygon points={poly(threshold)} fill="none" stroke={ACCENT.c} strokeWidth={1.5} strokeDasharray="4 4" opacity={0.5} />
      <polygon points={dataStr} fill={ACCENT.soft} stroke={ACCENT.c} strokeWidth={2} strokeLinejoin="round" />
      {crit.map((c, i) => { const [x, y] = pt(c.score, i); return <circle key={i} cx={x} cy={y} r={3.5} fill={ACCENT.c} />; })}
      {crit.map((c, i) => {
        const [x, y] = pt(118, i);
        const anchor = Math.abs(x - cx) < 14 ? "middle" : x > cx ? "start" : "end";
        return <text key={i} x={x} y={y + 4} fill="#9aa4b8" fontSize={11} fontWeight={600} fontFamily="Manrope,sans-serif" textAnchor={anchor}>{c.n}</text>;
      })}
    </svg>
  );
}

// ── Anillo de puntaje (detalle de candidato) ────────────────────────────────────
export function ScoreRing({ score }: { score: number }) {
  const angle = (score / 100) * 360;
  return (
    <div style={{ width: 84, height: 84, borderRadius: "50%", background: `conic-gradient(${ACCENT.c} ${angle}deg, rgba(255,255,255,.08) 0)`, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div style={{ width: 66, height: 66, borderRadius: "50%", background: "var(--background)", display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column" }}>
        <div style={{ fontFamily: MONO, fontSize: 20, fontWeight: 700, color: "var(--ac)", lineHeight: 1 }}>{Math.round(score)}</div>
        <div style={{ fontSize: 9, color: "var(--muted-2)", fontWeight: 600 }}>/100</div>
      </div>
    </div>
  );
}

// ── Stepper de fases del proceso ────────────────────────────────────────────────
export function Stepper({ steps }: { steps: { label: string; state: "done" | "current" | "todo" }[] }) {
  return (
    <div style={{ display: "flex", alignItems: "center", padding: "16px 20px", borderRadius: 14, background: "rgba(255,255,255,.025)", border: "1px solid var(--edge)", overflowX: "auto" }}>
      {steps.map((st, i) => {
        const done = st.state === "done", cur = st.state === "current";
        return (
          <div key={i} style={{ display: "flex", alignItems: "center", flex: 1, minWidth: 0 }}>
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 7, flex: "none" }}>
              <div style={{
                width: 26, height: 26, borderRadius: "50%", background: done ? "#34d399" : "transparent",
                border: `2px solid ${cur ? "var(--ac)" : done ? "#34d399" : "rgba(255,255,255,.15)"}`,
                display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11,
                color: done ? "#06231a" : "var(--muted-3)", fontWeight: 700,
              }}>{done ? "✓" : i + 1}</div>
              <div style={{ fontSize: 10.5, fontWeight: 700, color: cur ? "var(--heading)" : done ? "#9aa4b8" : "var(--muted-3)", whiteSpace: "nowrap" }}>{st.label}</div>
            </div>
            {i < steps.length - 1 && (
              <div style={{ height: 2, flex: 1, minWidth: 20, background: st.state === "done" ? "#34d399" : "rgba(255,255,255,.1)", margin: "0 6px 18px" }} />
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Chip de etapa ───────────────────────────────────────────────────────────────
export function StageChip({ status }: { status: string }) {
  const m = stageMeta(status);
  return (
    <span style={{ padding: "2px 9px", borderRadius: 6, fontSize: 11, fontWeight: 700, background: m.soft, color: m.color }}>{m.label}</span>
  );
}

// ── Tarjeta de candidato (Kanban) ───────────────────────────────────────────────
export function CandidateCard({ c, showVacancy }: { c: CandidateRow; showVacancy?: boolean }) {
  const cv = cvChip(c.prescreen_score);
  return (
    <Link href={`/candidatos/${c.id}`} style={{
      display: "block", padding: 12, borderRadius: 11, background: "#10151f", border: "1px solid rgba(255,255,255,.07)",
      textDecoration: "none",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 7 }}>
        <div style={{ width: 30, height: 30, borderRadius: 9, background: avatarColor(c.name), display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 700, fontSize: 12, color: "#0a0e16", flex: "none" }}>{initials(c.name)}</div>
        <div style={{ fontSize: 13, fontWeight: 700, color: "#eef2f9", flex: 1, minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{c.name}</div>
      </div>
      {showVacancy && c.vacancy_title && (
        <div style={{ fontSize: 10.5, color: "var(--muted)", fontWeight: 600, marginBottom: 7 }}>{c.vacancy_title}</div>
      )}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 10.5, color: "var(--muted)", fontWeight: 600 }}>{sourceIcon(c.source)} {c.source}</span>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          {c.total_score != null && <span style={{ fontFamily: MONO, fontSize: 11, fontWeight: 700, color: "var(--ac)" }}>{Math.round(c.total_score)}</span>}
          <span style={{ fontFamily: MONO, fontSize: 10.5, fontWeight: 700, padding: "2px 7px", borderRadius: 6, background: cv.bg, color: cv.color }}>CV {c.prescreen_score != null ? Math.round(c.prescreen_score) : "—"}</span>
        </div>
      </div>
    </Link>
  );
}

// ── Tablero Kanban ──────────────────────────────────────────────────────────────
export function KanbanBoard({ columns, showVacancy }: { columns: { key: string; label: string; color: string; items: CandidateRow[] }[]; showVacancy?: boolean }) {
  return (
    <div style={{ display: "flex", gap: 12, overflowX: "auto", paddingBottom: 14 }}>
      {columns.map((col) => (
        <div key={col.key} style={{ flex: "0 0 232px", minWidth: 232 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "0 4px 11px" }}>
            <span style={{ width: 8, height: 8, borderRadius: "50%", background: col.color }} />
            <span style={{ fontSize: 12.5, fontWeight: 700, color: "#cfd8e8" }}>{col.label}</span>
            <span style={{ fontFamily: MONO, fontSize: 11.5, color: "var(--muted-2)", fontWeight: 600 }}>{col.items.length}</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 9, minHeight: 60, padding: 9, borderRadius: 13, background: "rgba(255,255,255,.018)", border: "1px solid var(--edge-soft)" }}>
            {col.items.map((c) => <CandidateCard key={c.id} c={c} showVacancy={showVacancy} />)}
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Toast ───────────────────────────────────────────────────────────────────────
export function Toast({ message }: { message: string }) {
  return (
    <div className="hpop" style={{
      position: "fixed", bottom: 24, left: "50%", transform: "translateX(-50%)", zIndex: 90,
      display: "flex", alignItems: "center", gap: 11, padding: "14px 22px", borderRadius: 13,
      background: "#0f1521", border: "1px solid rgba(52,211,153,.3)", boxShadow: "0 12px 40px rgba(0,0,0,.5)",
    }}>
      <div style={{ width: 24, height: 24, borderRadius: "50%", background: "#34d399", display: "flex", alignItems: "center", justifyContent: "center", color: "#06231a", fontWeight: 800, fontSize: 13 }}>✓</div>
      <span style={{ fontSize: 13.5, fontWeight: 700, color: "#eef2f9" }}>{message}</span>
    </div>
  );
}

// Helper compartido: construye las columnas Kanban desde una lista de candidatos.
export { scoreColor };
