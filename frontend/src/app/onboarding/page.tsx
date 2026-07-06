"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Shell } from "@/components/Shell";
import { OnboardingKitEditor } from "@/components/OnboardingKitEditor";
import { api, ClosingCandidate, errorMessage, OnboardingSummary, Vacancy } from "@/lib/api";
import { ACCENT, avatarColor, buildClosingColumns, closingColumnKey, initials, stageMeta } from "@/lib/stages";

const MONO = "var(--font-jetbrains), monospace";
const PAGE_SIZE = 100;

const fmtDate = (s?: string | null) => (s ? String(s).slice(0, 10) : "");

// Tarjeta del cierre con acciones inline según la sub-etapa (fijar fecha / enviar kit). Estado
// local (input de fecha, ocupado, mensaje) para no re-renderizar todo el tablero al escribir.
function ClosingCard({ c, showVacancy, onChanged }: { c: ClosingCandidate; showVacancy?: boolean; onChanged: () => void }) {
  const col = closingColumnKey(c);
  const m = stageMeta(c.status);
  const [dateInput, setDateInput] = useState(fmtDate(c.start_date));
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const saveDate = async () => {
    if (!dateInput) return;
    setBusy(true); setMsg("");
    try { await api.setStartDate(c.id, dateInput); onChanged(); }
    catch (e) { setMsg(errorMessage(e)); } finally { setBusy(false); }
  };
  const sendKit = async () => {
    setBusy(true); setMsg("");
    try { await api.sendOnboarding(c.id); onChanged(); }
    catch (e) { setMsg(errorMessage(e)); } finally { setBusy(false); }
  };

  const inp = { padding: "7px 9px", borderRadius: 8, background: "rgba(255,255,255,.04)", border: "1px solid var(--edge)", color: "#eef2f9", fontSize: 12, width: "100%" } as const;
  const btn = (on: boolean) => ({ padding: "7px 10px", borderRadius: 8, fontSize: 11.5, fontWeight: 700, border: "none", cursor: on ? "pointer" : "default", background: on ? "var(--ac)" : "rgba(255,255,255,.05)", color: on ? "var(--ac-ink)" : "var(--muted-2)", opacity: busy ? 0.6 : 1 } as const);

  return (
    <div style={{ padding: 12, borderRadius: 11, background: "#10151f", border: "1px solid rgba(255,255,255,.07)" }}>
      <Link href={`/candidatos/${c.id}`} style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 8, textDecoration: "none" }}>
        <div style={{ width: 30, height: 30, borderRadius: 9, background: avatarColor(c.name), display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 700, fontSize: 12, color: "#0a0e16", flex: "none" }}>{initials(c.name)}</div>
        <div style={{ fontSize: 13, fontWeight: 700, color: "#eef2f9", flex: 1, minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{c.name}</div>
      </Link>
      {showVacancy && c.vacancy_title && (
        <div style={{ fontSize: 10.5, color: "var(--muted)", fontWeight: 600, marginBottom: 8 }}>{c.vacancy_title}</div>
      )}
      <span style={{ display: "inline-block", padding: "2px 9px", borderRadius: 6, fontSize: 10.5, fontWeight: 700, background: m.soft, color: m.color, marginBottom: 9 }}>{m.label}</span>

      {col === "medical" && (
        <div style={{ fontSize: 11.5, color: "var(--muted)" }}>
          {c.medical_exam?.scheduled_at
            ? <>Cita: <b style={{ color: "#cfd8e8" }}>{c.medical_exam.scheduled_at}</b>{c.medical_exam.clinic ? ` · ${c.medical_exam.clinic}` : ""}</>
            : "Examen por programar"}
          <Link href={`/candidatos/${c.id}`} style={{ display: "block", marginTop: 6, fontSize: 11.5, fontWeight: 700, color: "var(--ac)", textDecoration: "none" }}>Gestionar en el detalle →</Link>
        </div>
      )}

      {col === "no_date" && (
        <div style={{ display: "grid", gap: 7 }}>
          <div style={{ fontSize: 11, color: "var(--muted)" }}>Fija la fecha de ingreso para habilitar el kit.</div>
          <input type="date" value={dateInput} onChange={(e) => setDateInput(e.target.value)} style={inp} />
          <button onClick={saveDate} disabled={busy || !dateInput} style={btn(!busy && !!dateInput)}>Guardar fecha de ingreso</button>
        </div>
      )}

      {col === "kit_pending" && (
        <div style={{ display: "grid", gap: 7 }}>
          <div style={{ fontSize: 11.5, color: "var(--muted)" }}>Ingreso: <b style={{ color: "#cfd8e8" }}>{fmtDate(c.start_date)}</b></div>
          <button onClick={sendKit} disabled={busy || !c.kit_configured} title={c.kit_configured ? "" : "La vacante no tiene kit configurado"} style={btn(!busy && c.kit_configured)}>
            {c.kit_configured ? "Enviar kit ahora" : "Sin kit configurado"}
          </button>
        </div>
      )}

      {col === "done" && (
        <div style={{ fontSize: 11.5, color: "var(--muted)" }}>
          ✅ Kit enviado {c.onboarding?.sent_at ? `el ${fmtDate(c.onboarding.sent_at)}` : ""}
          {c.start_date ? <div style={{ marginTop: 3 }}>Ingreso: <b style={{ color: "#cfd8e8" }}>{fmtDate(c.start_date)}</b></div> : null}
        </div>
      )}

      {msg && <div style={{ marginTop: 7, fontSize: 11, color: "#f87171" }}>{msg}</div>}
    </div>
  );
}

export default function OnboardingPage() {
  const [items, setItems] = useState<ClosingCandidate[]>([]);
  const [summary, setSummary] = useState<OnboardingSummary | null>(null);
  const [noKit, setNoKit] = useState<{ id: string; title: string }[]>([]);
  const [vacancies, setVacancies] = useState<Vacancy[]>([]);
  const [filter, setFilter] = useState("all");
  const [error, setError] = useState("");
  const [kitsOpen, setKitsOpen] = useState(false);

  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const t = setTimeout(() => { setQ(qInput.trim()); setOffset(0); }, 300);
    return () => clearTimeout(t);
  }, [qInput]);

  const load = useCallback(() => {
    api.listClosing({ q, limit: PAGE_SIZE, offset })
      .then((page) => { setItems(page.items); setTotal(page.total); setSummary(page.summary); setNoKit(page.vacancies_without_kit); })
      .catch((e) => setError(errorMessage(e)))
      .finally(() => setLoading(false));
  }, [q, offset]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { api.listVacancies().then(setVacancies).catch((e) => setError(errorMessage(e))); }, []);

  const filtered = useMemo(
    () => (filter === "all" ? items : items.filter((c) => c.vacancy_id === filter)),
    [items, filter],
  );
  const columns = useMemo(() => buildClosingColumns(filtered), [filtered]);

  const kpis = summary
    ? [
        { value: summary.en_medico, label: "En examen médico", color: "#2dd4bf" },
        { value: summary.sin_fecha, label: "Sin fecha de ingreso", color: "#fbbf24" },
        { value: summary.kit_pendiente, label: "Kit pendiente", color: "#a78bfa" },
        { value: summary.proximos_ingresos, label: "Ingresan en ≤7 días", color: "#34d399" },
      ]
    : [];
  const chips = [{ id: "all", label: "Todas las vacantes" }, ...vacancies.map((v) => ({ id: v.id, label: v.title }))];

  return (
    <Shell>
      <div style={{ fontSize: 13, fontWeight: 700, color: "var(--ac)", marginBottom: 6 }}>Cierre del proceso</div>
      <h1 style={{ margin: 0, fontSize: 30, fontWeight: 800, letterSpacing: "-.03em", color: "var(--heading)" }}>Onboarding</h1>
      <p style={{ margin: "8px 0 20px", color: "var(--muted)", fontSize: 14 }}>Examen médico, contratados y envío del kit de incorporación — todo el tramo final en un lugar.</p>

      {error && <p style={{ color: "#f87171" }}>Error: {error}</p>}

      {/* KPIs */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 1, background: "var(--edge)", border: "1px solid var(--edge)", borderRadius: 15, overflow: "hidden", marginBottom: noKit.length ? 12 : 24 }}>
        {kpis.map((s) => (
          <div key={s.label} style={{ padding: "18px 16px", background: "#0d1220" }}>
            <div style={{ fontFamily: MONO, fontSize: 26, fontWeight: 700, color: s.color, lineHeight: 1 }}>{s.value}</div>
            <div style={{ fontSize: 11.5, color: "var(--muted)", fontWeight: 600, marginTop: 7 }}>{s.label}</div>
          </div>
        ))}
      </div>

      {/* Alerta: vacantes con contratados pero sin kit configurado (el barrido no envía nada) */}
      {noKit.length > 0 && (
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", padding: "12px 16px", borderRadius: 13, background: "rgba(251,191,36,.09)", border: "1px solid rgba(251,191,36,.28)", marginBottom: 24 }}>
          <span style={{ fontSize: 13, fontWeight: 700, color: "#fbbf24" }}>⚠ {noKit.length} vacante(s) con contratados pero sin kit</span>
          <span style={{ fontSize: 12.5, color: "var(--muted)" }}>configúralo abajo para poder enviar el onboarding:</span>
          {noKit.map((v) => (
            <Link key={v.id} href={`/vacantes/${v.id}`} style={{ fontSize: 12, fontWeight: 700, color: "#fbbf24", textDecoration: "none", borderBottom: "1px dotted rgba(251,191,36,.5)" }}>{v.title}</Link>
          ))}
        </div>
      )}

      {/* Filtros + búsqueda */}
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
        <input value={qInput} onChange={(e) => setQInput(e.target.value)} placeholder="Buscar por nombre…"
          style={{ padding: "8px 13px", borderRadius: 10, fontSize: 13, background: "rgba(255,255,255,.04)", border: "1px solid var(--edge)", color: "#eef2f9", outline: "none", width: 200 }} />
      </div>

      {/* Tablero del cierre */}
      <div style={{ display: "flex", gap: 12, overflowX: "auto", paddingBottom: 14 }}>
        {columns.map((col) => (
          <div key={col.key} style={{ flex: "0 0 248px", minWidth: 248 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "0 4px 11px" }}>
              <span style={{ width: 8, height: 8, borderRadius: "50%", background: col.color }} />
              <span style={{ fontSize: 12.5, fontWeight: 700, color: "#cfd8e8" }}>{col.label}</span>
              <span style={{ fontFamily: MONO, fontSize: 11.5, color: "var(--muted-2)", fontWeight: 600 }}>{col.items.length}</span>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 9, minHeight: 60, padding: 9, borderRadius: 13, background: "rgba(255,255,255,.018)", border: "1px solid var(--edge-soft)" }}>
              {col.items.map((c) => <ClosingCard key={c.id} c={c} showVacancy={filter === "all"} onChanged={load} />)}
            </div>
          </div>
        ))}
      </div>
      {loading && items.length === 0 && <p style={{ color: "var(--muted)", marginTop: 12 }}>Cargando…</p>}
      {!loading && filtered.length === 0 && !error && (
        <p style={{ color: "var(--muted)", marginTop: 12 }}>{q ? `Sin resultados para “${q}”.` : "No hay candidatos en el cierre del proceso."}</p>
      )}

      {total > PAGE_SIZE && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 14, marginTop: 18 }}>
          <button onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))} disabled={offset === 0} style={{ fontSize: 12.5, fontWeight: 700, padding: "7px 14px", borderRadius: 9, background: "rgba(255,255,255,.04)", border: "1px solid var(--edge)", color: offset === 0 ? "var(--muted-3)" : "#eef2f9", cursor: offset === 0 ? "default" : "pointer" }}>‹ Anteriores</button>
          <span style={{ fontSize: 12.5, color: "var(--muted)", fontFamily: MONO }}>{offset + 1}–{Math.min(offset + PAGE_SIZE, total)} de {total}</span>
          <button onClick={() => setOffset(offset + PAGE_SIZE)} disabled={offset + PAGE_SIZE >= total} style={{ fontSize: 12.5, fontWeight: 700, padding: "7px 14px", borderRadius: 9, background: "rgba(255,255,255,.04)", border: "1px solid var(--edge)", color: offset + PAGE_SIZE >= total ? "var(--muted-3)" : "#eef2f9", cursor: offset + PAGE_SIZE >= total ? "default" : "pointer" }}>Siguientes ›</button>
        </div>
      )}

      {/* Gestión de kits por vacante */}
      <div style={{ marginTop: 30 }}>
        <div onClick={() => setKitsOpen((v) => !v)} style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer", marginBottom: kitsOpen ? 12 : 0 }}>
          <h2 style={{ margin: 0, fontSize: 17, fontWeight: 800, color: "var(--heading)" }}>Kits por vacante</h2>
          <span style={{ fontFamily: MONO, fontSize: 12, color: "var(--muted-2)" }}>{vacancies.length}</span>
          <span style={{ color: "var(--muted)", fontSize: 13, transform: kitsOpen ? "rotate(0deg)" : "rotate(-90deg)" }}>▾</span>
        </div>
        {kitsOpen && (
          <div style={{ display: "grid", gap: 10 }}>
            {vacancies.map((v) => (
              <OnboardingKitEditor key={v.id} vacancy={v} headerLabel={v.title} onSaved={load} />
            ))}
            {vacancies.length === 0 && <p style={{ color: "var(--muted)" }}>No hay vacantes.</p>}
          </div>
        )}
      </div>
    </Shell>
  );
}
