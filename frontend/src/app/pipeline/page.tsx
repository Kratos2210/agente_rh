"use client";

import { useEffect, useMemo, useState } from "react";
import { Shell } from "@/components/Shell";
import { KanbanBoard } from "@/components/ui";
import { api, CandidateRow, Vacancy } from "@/lib/api";
import { ACCENT, buildColumns } from "@/lib/stages";

const MONO = "var(--font-jetbrains), monospace";
const PAGE_SIZE = 100;

export default function PipelinePage() {
  const [candidates, setCandidates] = useState<CandidateRow[]>([]);
  const [vacancies, setVacancies] = useState<Vacancy[]>([]);
  const [filter, setFilter] = useState<string>("all");
  // Búsqueda por nombre (server-side, con debounce) + paginación (U1).
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);

  useEffect(() => {
    const t = setTimeout(() => { setQ(qInput.trim()); setOffset(0); }, 300);
    return () => clearTimeout(t);
  }, [qInput]);

  useEffect(() => {
    api.listAllCandidates({ q, limit: PAGE_SIZE, offset })
      .then((page) => { setCandidates(page.items); setTotal(page.total); })
      .catch(() => {});
  }, [q, offset]);
  useEffect(() => {
    api.listVacancies().then(setVacancies).catch(() => {});
  }, []);

  const filtered = useMemo(
    () => (filter === "all" ? candidates : candidates.filter((c) => c.vacancy_id === filter)),
    [candidates, filter],
  );
  const columns = useMemo(() => buildColumns(filtered), [filtered]);

  const chips = [{ id: "all", label: "Todas las vacantes" }, ...vacancies.map((v) => ({ id: v.id, label: v.title }))];

  return (
    <Shell>
      <div style={{ fontSize: 13, fontWeight: 700, color: "var(--ac)", marginBottom: 6 }}>Proceso global</div>
      <h1 style={{ margin: 0, fontSize: 30, fontWeight: 800, letterSpacing: "-.03em", color: "var(--heading)" }}>Pipeline</h1>
      <p style={{ margin: "8px 0 20px", color: "var(--muted)", fontSize: 14 }}>Todos los candidatos en proceso, ordenados por etapa del embudo.</p>

      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", marginBottom: 20 }}>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", flex: 1 }}>
          {chips.map((c) => {
            const on = filter === c.id;
            return (
              <button key={c.id} onClick={() => setFilter(c.id)} style={{
                padding: "7px 14px", borderRadius: 20, fontSize: 12.5, fontWeight: 700, cursor: "pointer",
                background: on ? ACCENT.soft : "rgba(255,255,255,.03)", color: on ? ACCENT.c : "var(--muted)",
                border: `1px solid ${on ? ACCENT.bd : "rgba(255,255,255,.08)"}`,
              }}>{c.label}</button>
            );
          })}
        </div>
        <input
          value={qInput}
          onChange={(e) => setQInput(e.target.value)}
          placeholder="Buscar por nombre…"
          style={{ padding: "8px 13px", borderRadius: 10, fontSize: 13, background: "rgba(255,255,255,.04)", border: "1px solid var(--edge)", color: "#eef2f9", outline: "none", width: 200 }}
        />
      </div>

      <KanbanBoard columns={columns} showVacancy={filter === "all"} />
      {filtered.length === 0 && (
        <p style={{ color: "var(--muted)", marginTop: 12 }}>
          {q ? `Sin resultados para “${q}”.` : "No hay candidatos en proceso."}
        </p>
      )}

      {total > PAGE_SIZE && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 14, marginTop: 18 }}>
          <button onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))} disabled={offset === 0} style={{ fontSize: 12.5, fontWeight: 700, padding: "7px 14px", borderRadius: 9, background: "rgba(255,255,255,.04)", border: "1px solid var(--edge)", color: offset === 0 ? "var(--muted-3)" : "#eef2f9", cursor: offset === 0 ? "default" : "pointer" }}>‹ Anteriores</button>
          <span style={{ fontSize: 12.5, color: "var(--muted)", fontFamily: MONO }}>{offset + 1}–{Math.min(offset + PAGE_SIZE, total)} de {total}</span>
          <button onClick={() => setOffset(offset + PAGE_SIZE)} disabled={offset + PAGE_SIZE >= total} style={{ fontSize: 12.5, fontWeight: 700, padding: "7px 14px", borderRadius: 9, background: "rgba(255,255,255,.04)", border: "1px solid var(--edge)", color: offset + PAGE_SIZE >= total ? "var(--muted-3)" : "#eef2f9", cursor: offset + PAGE_SIZE >= total ? "default" : "pointer" }}>Siguientes ›</button>
        </div>
      )}
    </Shell>
  );
}
