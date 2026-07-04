// Meta visual de las etapas del candidato y helpers de color (port de support.js del prototipo).
// Mapea los `status` reales del backend a etiqueta/color para chips, kanban y stepper.

export const ACCENT = { c: "#8b8cfa", btn: "#6366f1", soft: "rgba(139,140,250,.15)", bd: "rgba(139,140,250,.34)" };

// Costo LLM acumulado (USD): con `.toFixed(2)` un consumo real chico (p.ej. $0.0017 de unas
// pocas llamadas) se veía como "$0.00". Mostramos más decimales cuando el acumulado es sub-centavo
// para que el costo realmente usado sea visible; a partir de un centavo, 2 decimales.
export function fmtCost(c: number): string {
  return c > 0 && c < 0.01 ? c.toFixed(4) : c.toFixed(2);
}

export interface StageMeta { label: string; color: string; soft: string }

// Estados del backend → presentación. Los off-path (descartados) usan rojo.
export const STAGE: Record<string, StageMeta> = {
  pending: { label: "Pendiente", color: "#94a3b8", soft: "rgba(148,163,184,.14)" },
  sourced: { label: "Importado", color: "#94a3b8", soft: "rgba(148,163,184,.14)" },
  prescreen_passed: { label: "Apto · CV", color: "#34d399", soft: "rgba(52,211,153,.13)" },
  prescreen_rejected: { label: "Descartado en CV", color: "#f87171", soft: "rgba(248,113,113,.12)" },
  invited: { label: "Contactado", color: "#fbbf24", soft: "rgba(251,191,36,.13)" },
  consented: { label: "Aceptó", color: "#fbbf24", soft: "rgba(251,191,36,.13)" },
  interviewing: { label: "En entrevista", color: ACCENT.c, soft: ACCENT.soft },
  finished: { label: "Evaluado", color: "#a78bfa", soft: "rgba(167,139,250,.14)" },
  scheduling: { label: "Coordinando RR.HH.", color: "#a78bfa", soft: "rgba(167,139,250,.14)" },
  scheduled: { label: "Entrevista RR.HH.", color: "#34d399", soft: "rgba(52,211,153,.16)" },
  lead_scheduling: { label: "Coordinando líder", color: "#22d3ee", soft: "rgba(34,211,238,.13)" },
  lead_scheduled: { label: "Entrevista líder", color: "#22d3ee", soft: "rgba(34,211,238,.16)" },
  mgr_scheduling: { label: "Coordinando gerencia", color: "#818cf8", soft: "rgba(129,140,248,.14)" },
  mgr_scheduled: { label: "Entrevista gerencia", color: "#818cf8", soft: "rgba(129,140,248,.16)" },
  hired: { label: "Contratado", color: "#34d399", soft: "rgba(52,211,153,.2)" },
  advanced: { label: "Avanzado", color: "#34d399", soft: "rgba(52,211,153,.16)" },
  rejected: { label: "Rechazado", color: "#f87171", soft: "rgba(248,113,113,.12)" },
  no_show: { label: "No asistió", color: "#f87171", soft: "rgba(248,113,113,.12)" },
  declined: { label: "Declinó", color: "#f87171", soft: "rgba(248,113,113,.12)" },
  no_response: { label: "No respondió", color: "#f87171", soft: "rgba(248,113,113,.12)" },
};

export function stageMeta(status: string): StageMeta {
  return STAGE[status] || { label: status || "—", color: "#94a3b8", soft: "rgba(148,163,184,.14)" };
}

// Columnas del Kanban (en orden del embudo). Reúne estados afines en una columna.
export const KANBAN_COLUMNS: { key: string; label: string; color: string; statuses: string[] }[] = [
  { key: "sourced", label: "Importado", color: "#94a3b8", statuses: ["pending", "sourced"] },
  { key: "prescreen_passed", label: "Apto CV", color: "#34d399", statuses: ["prescreen_passed"] },
  { key: "invited", label: "Contactado", color: "#fbbf24", statuses: ["invited", "consented"] },
  { key: "interviewing", label: "Entrevista", color: ACCENT.c, statuses: ["interviewing"] },
  { key: "finished", label: "Evaluado", color: "#a78bfa", statuses: ["finished", "scheduling"] },
  { key: "scheduled", label: "Entrevistas", color: "#34d399", statuses: ["scheduled", "advanced", "lead_scheduling", "lead_scheduled", "mgr_scheduling", "mgr_scheduled"] },
  { key: "hired", label: "Contratado", color: "#34d399", statuses: ["hired"] },
  { key: "rejected", label: "Descartado", color: "#f87171", statuses: ["prescreen_rejected", "rejected", "no_show", "declined", "no_response"] },
];

// Agrupa candidatos en las columnas del Kanban según su `status`.
export function buildColumns<T extends { status: string }>(items: T[]) {
  return KANBAN_COLUMNS.map((col) => ({
    key: col.key,
    label: col.label,
    color: col.color,
    items: items.filter((c) => col.statuses.includes(c.status)),
  }));
}

// Chip del puntaje de CV (pre-filtro): verde ≥70, ámbar ≥50, rojo el resto.
export function cvChip(n: number | null | undefined): { bg: string; color: string; verdict: string } {
  const v = n ?? 0;
  if (n == null) return { bg: "rgba(148,163,184,.13)", color: "#94a3b8", verdict: "—" };
  if (v >= 70) return { bg: "rgba(52,211,153,.13)", color: "#34d399", verdict: "Apto" };
  if (v >= 50) return { bg: "rgba(251,191,36,.13)", color: "#fbbf24", verdict: "Revisar" };
  return { bg: "rgba(248,113,113,.13)", color: "#f87171", verdict: "Descartado" };
}

const AVATAR_COLORS = ["#5b9dff", "#34d399", "#fbbf24", "#a78bfa", "#f472b6", "#22d3ee", "#fb923c"];

export function avatarColor(name: string): string {
  return AVATAR_COLORS[(name.charCodeAt(0) || 0) % AVATAR_COLORS.length];
}

export function initials(name: string): string {
  return (name || "?").split(" ").map((w) => w[0]).filter(Boolean).slice(0, 2).join("").toUpperCase();
}

export function sourceIcon(source: string): string {
  const s = (source || "").toLowerCase();
  if (s.includes("linkedin")) return "in";
  if (s.includes("bumeran")) return "◆";
  if (s.includes("computrabajo")) return "C";
  if (s.includes("telegram")) return "✈";
  return "•";
}

// Color por puntaje 0–100 (criterios / scores).
export function scoreColor(n: number): string {
  if (n >= 90) return "#34d399";
  if (n >= 75) return ACCENT.c;
  return "#fbbf24";
}
