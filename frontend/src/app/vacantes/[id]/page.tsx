"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Shell, BackLink } from "@/components/Shell";
import { KanbanBoard } from "@/components/ui";
import { api, CandidateRow, Metrics, Vacancy } from "@/lib/api";
import { ACCENT, avatarColor, buildColumns, cvChip, initials, stageMeta } from "@/lib/stages";

const MONO = "var(--font-jetbrains), monospace";
const PAGE_SIZE = 100;
const EMOJI_PREFIX = /^[\p{Extended_Pictographic}\u{1F1E6}-\u{1F1FF}️‍]+\s*/u;

// Renderiza el detalle del puesto (texto plano con emojis para Telegram) como secciones.
function PositionDetails({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let bullets: string[] = [];
  let key = 0;
  const flush = () => {
    if (bullets.length) {
      blocks.push(<ul key={`u${key++}`} style={{ display: "grid", gap: 7, paddingLeft: 20, color: "#aeb8cc", fontSize: 14, lineHeight: 1.55, listStyle: "disc" }}>{bullets.map((b, i) => <li key={i}>{b}</li>)}</ul>);
      bullets = [];
    }
  };
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) { flush(); continue; }
    if (line.startsWith("-")) { bullets.push(line.replace(/^-\s*/, "")); continue; }
    flush();
    const hadEmoji = EMOJI_PREFIX.test(line);
    const clean = line.replace(EMOJI_PREFIX, "").trim();
    if (hadEmoji && clean.split(/\s+/).length <= 3) blocks.push(<div key={`h${key++}`} style={{ fontWeight: 700, color: "#eef2f9", fontSize: 13, letterSpacing: ".04em", textTransform: "uppercase", marginTop: 6 }}>{clean}</div>);
    else blocks.push(<p key={`p${key++}`} style={{ fontSize: 14, color: "#aeb8cc", lineHeight: 1.65, margin: 0 }}>{clean}</p>);
  }
  flush();
  return <div style={{ display: "grid", gap: 12 }}>{blocks}</div>;
}

export default function VacancyPage() {
  const { id } = useParams<{ id: string }>();
  const [vacancy, setVacancy] = useState<Vacancy | null>(null);
  const [candidates, setCandidates] = useState<CandidateRow[]>([]);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [error, setError] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [msg, setMsg] = useState("");
  const [contactingId, setContactingId] = useState("");
  const [view, setView] = useState<"kanban" | "lista">("kanban");
  const [puestoOpen, setPuestoOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  const copyDeepLink = async () => {
    if (!vacancy?.telegram_deep_link) return;
    try {
      await navigator.clipboard.writeText(vacancy.telegram_deep_link);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard no disponible (http): el enlace queda visible para copiar a mano */ }
  };

  // Búsqueda por nombre (server-side, con debounce) + paginación (U1).
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  useEffect(() => {
    const t = setTimeout(() => { setQ(qInput.trim()); setOffset(0); }, 300);
    return () => clearTimeout(t);
  }, [qInput]);

  const load = useCallback(() => {
    if (!id) return;
    Promise.all([
      api.getVacancy(id),
      api.listCandidates(id, { q, limit: PAGE_SIZE, offset }),
      api.getVacancyMetrics(id),
    ])
      .then(([v, page, m]) => { setVacancy(v); setCandidates(page.items); setTotal(page.total); setMetrics(m); })
      .catch((e) => setError(String(e)));
  }, [id, q, offset]);
  useEffect(() => { load(); }, [load]);

  const handleContact = async (e: React.MouseEvent, candId: string) => {
    e.preventDefault(); e.stopPropagation();
    setContactingId(candId); setMsg("");
    try {
      const r = await api.contactCandidate(candId);
      setMsg(r.contacted ? `Contactado ✅ ${r.note}` : `No se contactó: ${r.note}`);
      load();
    } catch (err) { setMsg(`Error: ${String(err)}`); } finally { setContactingId(""); }
  };
  const handleSync = async () => {
    if (!id) return;
    setSyncing(true); setMsg("");
    try {
      const r = await api.syncApplicants(id);
      setMsg(`Importados ${r.imported} · aptos ${r.passed} · descartados ${r.rejected} · contactados ${r.contacted}`);
      load();
    } catch (e) { setMsg(`Error: ${String(e)}`); } finally { setSyncing(false); }
  };

  if (error) return <Shell><BackLink href="/" label="Vacantes" /><p style={{ color: "#f87171" }}>Error: {error}</p></Shell>;
  if (!vacancy) return <Shell><p style={{ color: "var(--muted)" }}>Cargando…</p></Shell>;

  const cnt = (pred: (c: CandidateRow) => boolean) => candidates.filter(pred).length;
  const contactedSet = ["invited", "consented", "interviewing", "finished", "scheduling", "scheduled", "advanced"];
  const stats = [
    { value: total, label: "Importados", color: "#e8edf6" },
    { value: cnt((c) => c.prescreen_verdict === "pass"), label: "Aptos (CV)", color: "#34d399" },
    { value: cnt((c) => c.status === "prescreen_rejected"), label: "Descartados", color: "#f87171" },
    { value: cnt((c) => contactedSet.includes(c.status)), label: "Contactados", color: "#fbbf24" },
    { value: cnt((c) => c.total_score != null), label: "Evaluados", color: "#a78bfa" },
    { value: cnt((c) => c.status === "scheduled"), label: "Agendados", color: ACCENT.c },
  ];
  const columns = buildColumns(candidates);
  const r = vacancy.recruiter;
  const sub = [vacancy.area, vacancy.location, vacancy.modality].filter(Boolean).join(" · ");

  return (
    <Shell>
      <BackLink href="/" label="Vacantes" />
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 20, flexWrap: "wrap", marginBottom: 22 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 30, fontWeight: 800, letterSpacing: "-.03em", color: "var(--heading)" }}>{vacancy.title}</h1>
          <div style={{ marginTop: 8, fontSize: 13.5, color: "var(--muted)" }}>{(vacancy.questions?.length ?? 0)} preguntas · {total} candidatos{sub ? ` · ${sub}` : ""}</div>
        </div>
        <button onClick={handleSync} disabled={syncing} style={{
          display: "flex", alignItems: "center", gap: 11, padding: "12px 18px", borderRadius: 11,
          background: "linear-gradient(135deg,var(--ac),var(--ac-btn))", color: "#fff", fontWeight: 700, fontSize: 13.5,
          border: "none", cursor: syncing ? "default" : "pointer", opacity: syncing ? 0.6 : 1, boxShadow: "0 8px 22px var(--ac-soft)",
        }}>
          <span className={syncing ? "hspin" : ""}>↻</span> {syncing ? "Sincronizando…" : "Sincronizar postulantes"}
        </button>
      </div>

      {r && (
        <div style={{ display: "flex", alignItems: "center", gap: 14, padding: "16px 18px", borderRadius: 15, background: "rgba(255,255,255,.025)", border: "1px solid var(--edge)", marginBottom: 14 }}>
          <div style={{ width: 46, height: 46, borderRadius: 12, background: avatarColor(r.name), display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 800, color: "#0a0e16", fontSize: 17 }}>{initials(r.name)}</div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 11, color: "var(--muted-2)", fontWeight: 700, letterSpacing: ".05em", textTransform: "uppercase", marginBottom: 3 }}>Responsable del proceso</div>
            <div style={{ fontSize: 14.5, color: "#eef2f9" }}><b style={{ fontWeight: 700 }}>{r.name}</b>{r.role ? ` · ${r.role}` : ""}{r.company ? ` · ${r.company}` : ""}</div>
            <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 3 }}>✉ {r.email || "—"} · ☎ {r.phone || "—"}</div>
          </div>
        </div>
      )}

      {vacancy.telegram_deep_link && (
        <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "13px 18px", borderRadius: 15, background: "rgba(255,255,255,.025)", border: "1px solid var(--edge)", marginBottom: 14, flexWrap: "wrap" }}>
          <div style={{ fontSize: 11, color: "var(--muted-2)", fontWeight: 700, letterSpacing: ".05em", textTransform: "uppercase" }}>Enlace del aviso (Telegram)</div>
          <span style={{ flex: 1, minWidth: 0, fontFamily: MONO, fontSize: 12.5, color: "#9aa4b8", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{vacancy.telegram_deep_link}</span>
          <button onClick={copyDeepLink} style={{ fontSize: 12, borderRadius: 9, padding: "7px 13px", fontWeight: 700, background: copied ? "rgba(52,211,153,.15)" : "var(--ac)", color: copied ? "#34d399" : "var(--ac-ink)", border: "none", cursor: "pointer" }}>{copied ? "Copiado ✓" : "Copiar"}</button>
        </div>
      )}

      {/* stats */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(6,1fr)", gap: 1, background: "var(--edge)", border: "1px solid var(--edge)", borderRadius: 15, overflow: "hidden", marginBottom: 8 }}>
        {stats.map((s) => (
          <div key={s.label} style={{ padding: "18px 16px", background: "#0d1220" }}>
            <div style={{ fontFamily: MONO, fontSize: 26, fontWeight: 700, color: s.color, lineHeight: 1 }}>{s.value}</div>
            <div style={{ fontSize: 11.5, color: "var(--muted)", fontWeight: 600, marginTop: 7 }}>{s.label}</div>
          </div>
        ))}
      </div>
      <div style={{ fontSize: 12, color: "var(--muted-2)", marginBottom: 24, paddingLeft: 2 }}>Tokens consumidos: <b style={{ fontFamily: MONO, color: "#9aa4b8", fontWeight: 600 }}>{(metrics?.tokens?.total ?? 0).toLocaleString()}</b></div>

      {msg && <p style={{ fontSize: 13, color: "var(--ac)", marginBottom: 14 }}>{msg}</p>}

      {/* pipeline */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 14, marginBottom: 14 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ fontSize: 18, fontWeight: 800, letterSpacing: "-.02em", color: "var(--heading)" }}>Pipeline de candidatos</div>
          <div style={{ fontSize: 12, color: "var(--muted-2)", fontWeight: 600 }}>{total} en proceso</div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <input
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            placeholder="Buscar por nombre…"
            style={{ padding: "8px 13px", borderRadius: 10, fontSize: 13, background: "rgba(255,255,255,.04)", border: "1px solid var(--edge)", color: "#eef2f9", outline: "none", width: 200 }}
          />
          <div style={{ display: "flex", alignItems: "center", gap: 2, padding: 3, borderRadius: 10, background: "rgba(255,255,255,.04)", border: "1px solid var(--edge)" }}>
            {(["kanban", "lista"] as const).map((v) => (
              <div key={v} onClick={() => setView(v)} style={{ padding: "6px 13px", borderRadius: 7, fontSize: 12.5, fontWeight: 700, cursor: "pointer", background: view === v ? ACCENT.soft : "transparent", color: view === v ? ACCENT.c : "var(--muted)" }}>{v === "kanban" ? "▦ Kanban" : "☰ Lista"}</div>
            ))}
          </div>
        </div>
      </div>

      {view === "kanban" ? (
        <KanbanBoard columns={columns} />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
          {candidates.map((c) => {
            const sm = stageMeta(c.status);
            const cv = cvChip(c.prescreen_score);
            const canContact = c.status === "prescreen_passed";
            return (
              <Link key={c.id} href={`/candidatos/${c.id}`} style={{ display: "flex", alignItems: "center", gap: 14, padding: "15px 18px", borderRadius: 13, background: "rgba(255,255,255,.025)", border: "1px solid var(--edge)", textDecoration: "none" }}>
                <span style={{ width: 11, height: 11, borderRadius: "50%", background: sm.color, boxShadow: `0 0 0 4px ${sm.soft}`, flex: "none" }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 15, fontWeight: 700, color: "#eef2f9" }}>{c.name}</div>
                  <div style={{ display: "flex", alignItems: "center", gap: 9, marginTop: 4 }}>
                    <span style={{ padding: "2px 9px", borderRadius: 6, fontSize: 11, fontWeight: 700, background: sm.soft, color: sm.color }}>{sm.label}</span>
                    <span style={{ fontSize: 11.5, color: "var(--muted)" }}>{c.source}</span>
                  </div>
                </div>
                {canContact && (
                  <button onClick={(e) => handleContact(e, c.id)} disabled={contactingId === c.id} style={{ fontSize: 12, borderRadius: 9, padding: "7px 13px", fontWeight: 700, background: "var(--ac)", color: "var(--ac-ink)", border: "none", cursor: "pointer", opacity: contactingId === c.id ? 0.6 : 1 }}>{contactingId === c.id ? "Contactando…" : "Contactar"}</button>
                )}
                {c.prescreen_score != null && <span style={{ fontFamily: MONO, fontSize: 11.5, fontWeight: 700, padding: "3px 9px", borderRadius: 7, background: cv.bg, color: cv.color }}>CV {Math.round(c.prescreen_score)} · {cv.verdict}</span>}
                {c.total_score != null && <span style={{ fontFamily: MONO, fontSize: 16, fontWeight: 700, color: "var(--ac)" }}>{Math.round(c.total_score)}</span>}
                <span style={{ color: "var(--muted-3)", fontSize: 17 }}>›</span>
              </Link>
            );
          })}
          {candidates.length === 0 && (
            <p style={{ color: "var(--muted)" }}>
              {q ? `Sin resultados para “${q}”.` : "Aún no hay candidatos. Usa “Sincronizar postulantes”."}
            </p>
          )}
        </div>
      )}

      {total > PAGE_SIZE && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 14, marginTop: 18 }}>
          <button onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))} disabled={offset === 0} style={{ fontSize: 12.5, fontWeight: 700, padding: "7px 14px", borderRadius: 9, background: "rgba(255,255,255,.04)", border: "1px solid var(--edge)", color: offset === 0 ? "var(--muted-3)" : "#eef2f9", cursor: offset === 0 ? "default" : "pointer" }}>‹ Anteriores</button>
          <span style={{ fontSize: 12.5, color: "var(--muted)", fontFamily: MONO }}>{offset + 1}–{Math.min(offset + PAGE_SIZE, total)} de {total}</span>
          <button onClick={() => setOffset(offset + PAGE_SIZE)} disabled={offset + PAGE_SIZE >= total} style={{ fontSize: 12.5, fontWeight: 700, padding: "7px 14px", borderRadius: 9, background: "rgba(255,255,255,.04)", border: "1px solid var(--edge)", color: offset + PAGE_SIZE >= total ? "var(--muted-3)" : "#eef2f9", cursor: offset + PAGE_SIZE >= total ? "default" : "pointer" }}>Siguientes ›</button>
        </div>
      )}

      {/* detalle del puesto */}
      <div style={{ marginTop: 26, borderRadius: 15, background: "rgba(255,255,255,.02)", border: "1px solid var(--edge)", overflow: "hidden" }}>
        <div onClick={() => setPuestoOpen((v) => !v)} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "16px 20px", cursor: "pointer" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 15, fontWeight: 700, color: "#eef2f9" }}><span style={{ color: "var(--ac)" }}>▤</span>Detalle del puesto</div>
          <span style={{ color: "var(--muted)", fontSize: 13, transform: puestoOpen ? "rotate(0deg)" : "rotate(-90deg)" }}>▾</span>
        </div>
        {puestoOpen && (
          <div style={{ padding: "4px 24px 24px", borderTop: "1px solid var(--edge-soft)" }}>
            <div style={{ paddingTop: 18 }}>
              {vacancy.details_message ? <PositionDetails text={vacancy.details_message} /> : (
                <div style={{ display: "grid", gap: 14 }}>
                  {vacancy.description && <p style={{ margin: 0, fontSize: 14, color: "#aeb8cc", lineHeight: 1.65, whiteSpace: "pre-wrap" }}>{vacancy.description}</p>}
                  {vacancy.requirements && (<div><div style={{ fontWeight: 700, color: "#eef2f9", fontSize: 13, letterSpacing: ".04em", textTransform: "uppercase", margin: "6px 0 8px" }}>Requisitos</div><p style={{ margin: 0, fontSize: 14, color: "#aeb8cc", lineHeight: 1.65, whiteSpace: "pre-wrap" }}>{vacancy.requirements}</p></div>)}
                  {vacancy.benefits && vacancy.benefits.length > 0 && (<div><div style={{ fontWeight: 700, color: "#eef2f9", fontSize: 13, letterSpacing: ".04em", textTransform: "uppercase", margin: "6px 0 8px" }}>Beneficios</div><ul style={{ margin: 0, paddingLeft: 20, display: "grid", gap: 7, color: "#aeb8cc", fontSize: 14, lineHeight: 1.55 }}>{vacancy.benefits.map((b, i) => <li key={i}>{b}</li>)}</ul></div>)}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </Shell>
  );
}
