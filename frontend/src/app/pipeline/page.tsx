"use client";

import { useEffect, useMemo, useState } from "react";
import { Shell } from "@/components/Shell";
import { KanbanBoard } from "@/components/ui";
import { api, CandidateRow, Vacancy } from "@/lib/api";
import { ACCENT, buildColumns } from "@/lib/stages";

export default function PipelinePage() {
  const [candidates, setCandidates] = useState<CandidateRow[]>([]);
  const [vacancies, setVacancies] = useState<Vacancy[]>([]);
  const [filter, setFilter] = useState<string>("all");

  useEffect(() => {
    api.listAllCandidates().then(setCandidates).catch(() => {});
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

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 20 }}>
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

      <KanbanBoard columns={columns} showVacancy={filter === "all"} />
      {filtered.length === 0 && <p style={{ color: "var(--muted)", marginTop: 12 }}>No hay candidatos en proceso.</p>}
    </Shell>
  );
}
