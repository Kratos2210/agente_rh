"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Shell, BackLink } from "@/components/Shell";
import { Radar, ScoreRing, Stepper, Toast } from "@/components/ui";
import { api, errorMessage, CandidateDetail, LlmTrace, MeetingStage, PHASE_STEPS, phaseMeta } from "@/lib/api";
import { isAdmin } from "@/lib/auth";
import { ACCENT, avatarColor, initials, scoreColor, sourceIcon, stageMeta } from "@/lib/stages";

const MONO = "var(--font-jetbrains), monospace";
const panel: React.CSSProperties = { padding: "20px 22px", borderRadius: 15, background: "rgba(255,255,255,.02)", border: "1px solid var(--edge)" };
const inputStyle: React.CSSProperties = { padding: "9px 12px", borderRadius: 9, background: "rgba(255,255,255,.03)", border: "1px solid var(--edge)", color: "#eef2f9", fontSize: 13 };
const btnPrimary: React.CSSProperties = { padding: "10px 18px", borderRadius: 10, background: "#34d399", color: "#06231a", fontWeight: 700, border: "none", cursor: "pointer" };
const btnGhost: React.CSSProperties = { padding: "10px 18px", borderRadius: 10, background: "rgba(255,255,255,.05)", border: "1px solid var(--edge)", color: "#cfd8e8", fontWeight: 700, cursor: "pointer" };
const btnDanger: React.CSSProperties = { padding: "10px 18px", borderRadius: 10, background: "rgba(248,113,113,.1)", border: "1px solid rgba(248,113,113,.3)", color: "#f87171", fontWeight: 700, cursor: "pointer" };

function formatWhen(iso: string): string {
  try {
    return new Date(iso).toLocaleString("es-PE", { weekday: "long", day: "2-digit", month: "long", hour: "2-digit", minute: "2-digit", timeZone: "America/Lima" });
  } catch { return iso; }
}

function buildSteps(status: string) {
  const current = phaseMeta[status]?.step ?? 0;
  const offPath = current < 0;
  return PHASE_STEPS.map((s, i) => ({
    label: s.label,
    state: (offPath ? "todo" : i < current ? "done" : i === current ? "current" : "todo") as "done" | "current" | "todo",
  }));
}

// Etapa (hr/lead/manager) que está en curso según el status del candidato.
const STAGE_OF: Record<string, MeetingStage> = {
  scheduling: "hr", scheduled: "hr",
  lead_scheduling: "lead", lead_scheduled: "lead",
  mgr_scheduling: "manager", mgr_scheduled: "manager",
};
const STAGE_LABEL: Record<MeetingStage, string> = { hr: "RR.HH.", lead: "Líder del proyecto", manager: "Gerencia" };

export default function CandidatePage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [data, setData] = useState<CandidateDetail | null>(null);
  const [error, setError] = useState("");
  const [acting, setActing] = useState(false);
  const [toast, setToast] = useState("");
  const [contacting, setContacting] = useState(false);
  const [erasing, setErasing] = useState(false);
  const [eraseOpen, setEraseOpen] = useState(false);
  const [eraseName, setEraseName] = useState("");
  const [admin, setAdmin] = useState(false);
  const [txOpen, setTxOpen] = useState(false);
  const [tracesOpen, setTracesOpen] = useState(false);
  const [traces, setTraces] = useState<LlmTrace[] | null>(null);
  const [feedback, setFeedback] = useState("");
  const [nextModality, setNextModality] = useState<"virtual" | "onsite">("onsite");
  const [exam, setExam] = useState({ link: "", code: "", key: "" });

  const load = () => {
    if (!id) return;
    api.getCandidate(id).then(setData).catch((e) => setError(errorMessage(e)));
  };
  useEffect(load, [id]);
  useEffect(() => setAdmin(isAdmin()), []);

  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(""), 3500); };

  // Trazas LLM (O-1): carga perezosa al abrir la sección (son pesadas y solo-admin).
  const toggleTraces = () => {
    const next = !tracesOpen;
    setTracesOpen(next);
    if (next && traces === null) {
      api.getTraces(id).then((r) => setTraces(r.items)).catch((e) => { setTraces([]); flash("Error: " + errorMessage(e)); });
    }
  };

  // U3 (auditoría): el borrado definitivo exige tipear el nombre del candidato (modal
  // propio en lugar del window.confirm nativo). Si el candidato no tiene nombre
  // (anonimizado), la palabra de confirmación es BORRAR.
  const eraseTarget = data?.candidate.name?.trim() || "BORRAR";
  const eraseReady = eraseName.trim() === eraseTarget;
  const erase = async () => {
    if (!data || !eraseReady) return;
    setErasing(true);
    try {
      await api.eraseCandidate(id);
      router.replace(data.vacancy ? `/vacantes/${data.vacancy.id}` : "/");
    } catch (e) {
      flash("Error: " + errorMessage(e));
      setErasing(false);
      setEraseOpen(false);
      setEraseName("");
    }
  };

  const decide = async (decision: "advance" | "reject") => {
    setActing(true);
    try {
      const r = await api.decide(id, decision);
      flash(r.scheduling_started ? "Aprobado · coordinando entrevista por Telegram 📅" : decision === "advance" ? "Candidato aprobado ✅" : "Candidato rechazado · notificación enviada.");
      load();
    } catch (e) { flash("Error: " + errorMessage(e)); } finally { setActing(false); }
  };
  const contact = async () => {
    setContacting(true);
    try {
      const r = await api.contactCandidate(id);
      flash(r.contacted ? "Contactado por Telegram ✅" : `No se contactó: ${r.note}`);
      load();
    } catch (e) { flash("Error: " + errorMessage(e)); } finally { setContacting(false); }
  };
  const sendExam = async () => {
    if (!exam.link.trim()) { flash("Falta el link del examen"); return; }
    setActing(true);
    try {
      const r = await api.sendPsychExam(id, exam);
      flash(r.sent ? "Examen psicológico enviado por correo ✅" : "Correo encolado (revisa SMTP/email del candidato)");
      setExam({ link: "", code: "", key: "" });
      load();
    } catch (e) { flash("Error: " + errorMessage(e)); } finally { setActing(false); }
  };
  const doAttendance = async (stage: MeetingStage, attended: "attended" | "no_show", reschedule = false) => {
    setActing(true);
    try {
      await api.markAttendance(id, { stage, attended, reschedule });
      flash(attended === "attended" ? "Asistencia registrada ✅" : reschedule ? "Reagendando por Telegram 📅" : "Proceso cerrado (no asistió).");
      load();
    } catch (e) { flash("Error: " + errorMessage(e)); } finally { setActing(false); }
  };
  const doAdvance = async (stage: MeetingStage, decision: "approved" | "rejected") => {
    setActing(true);
    try {
      const r = await api.advanceStage(id, { stage, decision, feedback, modality: nextModality });
      flash(
        decision === "rejected" ? "Candidatura rechazada · candidato notificado."
          : r.status === "hired" ? "¡Contratado! 🎉 Candidato notificado."
          : "Aprobado · coordinando la siguiente entrevista por Telegram 📅",
      );
      setFeedback("");
      load();
    } catch (e) { flash("Error: " + errorMessage(e)); } finally { setActing(false); }
  };

  if (error) return <Shell width={1020}><BackLink href="/" label="Volver" /><p style={{ color: "#f87171" }}>Error: {error}</p></Shell>;
  if (!data) return <Shell width={1020}><p style={{ color: "var(--muted)" }}>Cargando…</p></Shell>;

  const { candidate, vacancy, scorecard, transcript, thresholds } = data;
  const sm = stageMeta(candidate.status);
  const cv = candidate.cv_profile || {};
  const prescreen = candidate.prescreen;
  const documents = candidate.documents ?? [];
  const docCv = documents.find((d) => d.type === "cv");
  const docCul = documents.find((d) => d.type === "cul");
  const meetings = data.meetings ?? [];
  const stageFeedback = data.stage_feedback ?? [];
  const psychExam = data.psych_exam ?? null;
  const activeStage = STAGE_OF[candidate.status];
  const activeMeeting = activeStage ? meetings.find((m) => m.stage === activeStage) : undefined;
  // El scorecard decidido ya no bloquea la barra: solo el estado `finished` la muestra.
  const decidedInitial = candidate.status !== "finished";
  // Fase 1 (RR.HH.): el examen psicológico se comparte mientras el candidato esté evaluado o coordinando/agendado con RR.HH.
  const inPhase1 = ["finished", "scheduling", "scheduled"].includes(candidate.status);
  const isHired = candidate.status === "hired";
  const canContact = candidate.status === "prescreen_passed";
  const waiting = ["invited", "consented", "interviewing"].includes(candidate.status) && !scorecard;
  const green = thresholds?.green_min ?? 75;

  return (
    <Shell width={1020}>
      <BackLink href={vacancy ? `/vacantes/${vacancy.id}` : "/"} label={vacancy?.title ?? "Volver"} />

      {/* header */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: 20, marginBottom: 24 }}>
        <div style={{ width: 60, height: 60, borderRadius: 16, background: avatarColor(candidate.name), display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 800, fontSize: 24, color: "#0a0e16", flex: "none" }}>{initials(candidate.name)}</div>
        <div style={{ flex: 1 }}>
          <h1 style={{ margin: 0, fontSize: 28, fontWeight: 800, letterSpacing: "-.03em", color: "var(--heading)" }}>{candidate.name || "Candidato"}</h1>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 7 }}>
            <span style={{ padding: "3px 10px", borderRadius: 7, fontSize: 12, fontWeight: 700, background: sm.soft, color: sm.color }}>{sm.label}</span>
            <span style={{ fontSize: 13, color: "var(--muted)" }}>{sourceIcon(candidate.source || "")} {candidate.source || candidate.channel}</span>
          </div>
        </div>
        {scorecard && <ScoreRing score={scorecard.total_score} />}
      </div>

      {/* stepper */}
      <div style={{ marginBottom: 16 }}><Stepper steps={buildSteps(candidate.status)} /></div>

      {/* Fase 1 — exámenes psicológicos (RR.HH. pega link + código + clave y se envía por correo) */}
      {inPhase1 && (
        <div style={{ ...panel, marginBottom: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 14, fontWeight: 700, color: "#eef2f9", marginBottom: 4 }}>🧠 Exámenes psicológicos</div>
          {psychExam ? (
            <div style={{ fontSize: 13, color: "#aeb8cc", marginTop: 8 }}>
              ✅ Enviado el {formatWhen(psychExam.sent_at)} {psychExam.sent_by ? `por ${psychExam.sent_by}` : ""}.
              <div style={{ marginTop: 6, fontSize: 12, color: "var(--muted)" }}>Link: {psychExam.link || "—"} · Código: {psychExam.code || "—"} · Clave: {psychExam.key || "—"}</div>
            </div>
          ) : (
            <>
              <div style={{ fontSize: 12.5, color: "var(--muted)", margin: "4px 0 12px" }}>Crea la prueba en la plataforma (p.ej. Multitest), pega aquí el link + código + clave y se envía por correo al candidato.</div>
              <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr", gap: 10 }}>
                <input value={exam.link} onChange={(e) => setExam({ ...exam, link: e.target.value })} placeholder="Link de la evaluación" style={inputStyle} />
                <input value={exam.code} onChange={(e) => setExam({ ...exam, code: e.target.value })} placeholder="Código de acceso" style={inputStyle} />
                <input value={exam.key} onChange={(e) => setExam({ ...exam, key: e.target.value })} placeholder="Clave de acceso" style={inputStyle} />
              </div>
              <button onClick={sendExam} disabled={acting} style={{ marginTop: 12, padding: "10px 18px", borderRadius: 10, background: "var(--ac)", color: "var(--ac-ink)", fontWeight: 700, border: "none", cursor: "pointer", opacity: acting ? 0.6 : 1 }}>Enviar examen por correo</button>
            </>
          )}
        </div>
      )}

      {/* Proceso de entrevistas — una tarjeta por etapa (RR.HH. → líder → gerencia) */}
      {meetings.map((m) => {
        const label = STAGE_LABEL[m.stage];
        const onsite = m.modality === "onsite";
        return (
          <div key={m.id} style={{ padding: "20px 22px", borderRadius: 15, background: "linear-gradient(135deg,var(--ac-soft),rgba(255,255,255,.015))", border: "1px solid var(--ac-bd)", marginBottom: 16 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 15, fontWeight: 700, color: "#eef2f9", marginBottom: 14 }}>
              <span style={{ color: "var(--ac)" }}>▦</span>Entrevista · {label}
              <span style={{ padding: "2px 9px", borderRadius: 7, fontSize: 11, fontWeight: 700, background: "rgba(255,255,255,.06)", color: "#cfd8e8" }}>{onsite ? "Presencial" : "Virtual (Meet)"}</span>
              {m.attendance === "attended" && <span style={{ fontSize: 11.5, color: "#34d399", fontWeight: 700 }}>✓ Asistió</span>}
              {m.attendance === "no_show" && <span style={{ fontSize: 11.5, color: "#f87171", fontWeight: 700 }}>✕ No asistió</span>}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "13px 28px", fontSize: 13.5 }}>
              <div><span style={{ color: "var(--muted)", fontWeight: 600 }}>Fecha</span><div style={{ color: "#eef2f9", marginTop: 3, fontWeight: 600 }}>{formatWhen(m.scheduled_at)}</div></div>
              <div><span style={{ color: "var(--muted)", fontWeight: 600 }}>{onsite ? "Lugar" : "Enlace"}</span><div style={{ marginTop: 3 }}>{onsite ? <span style={{ color: "#aeb8cc" }}>{m.location || "—"}</span> : m.meet_link ? <a href={m.meet_link} target="_blank" rel="noopener noreferrer" style={{ color: "var(--ac)", fontWeight: 600, textDecoration: "none" }}>{m.meet_link.replace("https://", "")}</a> : "—"}</div></div>
            </div>
          </div>
        );
      })}

      {/* Coordinando horario (aún sin reunión de la etapa activa) */}
      {activeStage && !activeMeeting && (
        <div style={{ ...panel, marginBottom: 16 }}>
          <p style={{ fontSize: 13.5, color: "#aeb8cc", margin: 0 }}>Coordinando el horario con el candidato por Telegram (entrevista con {STAGE_LABEL[activeStage]})…</p>
        </div>
      )}

      {/* Asistencia (RR.HH. la marca tras la entrevista de la etapa activa) */}
      {activeStage && activeMeeting && activeMeeting.attendance === "" && (
        <div style={{ ...panel, marginBottom: 16 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: "#eef2f9", marginBottom: 4 }}>Asistencia · {STAGE_LABEL[activeStage]}</div>
          <div style={{ fontSize: 12.5, color: "var(--muted)", marginBottom: 14 }}>¿El candidato asistió a la entrevista?</div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <button onClick={() => doAttendance(activeStage, "attended")} disabled={acting} style={btnPrimary}>Asistió</button>
            <button onClick={() => doAttendance(activeStage, "no_show", true)} disabled={acting} style={btnGhost}>No fui: Reagendar</button>
            <button onClick={() => doAttendance(activeStage, "no_show", false)} disabled={acting} style={btnDanger}>No fui: Cerrar</button>
          </div>
        </div>
      )}

      {/* Feedback + decisión (tras registrar asistencia de la etapa activa) */}
      {activeStage && activeMeeting && activeMeeting.attendance === "attended" && (
        <div style={{ ...panel, marginBottom: 16 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: "#eef2f9", marginBottom: 4 }}>Feedback del entrevistador · {STAGE_LABEL[activeStage]}</div>
          <div style={{ fontSize: 12.5, color: "var(--muted)", marginBottom: 12 }}>
            {activeStage === "manager" ? "Decisión final: contratar o rechazar." : "Registra el feedback y aprueba para agendar la siguiente entrevista, o rechaza."}
          </div>
          <textarea value={feedback} onChange={(e) => setFeedback(e.target.value)} placeholder="Comentarios del entrevistador…" rows={3} style={{ ...inputStyle, width: "100%", resize: "vertical", marginBottom: 12 }} />
          {activeStage === "hr" && (
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14, fontSize: 13, color: "#aeb8cc" }}>
              <span style={{ fontWeight: 600 }}>Modalidad de la entrevista con el líder:</span>
              <label style={{ display: "flex", alignItems: "center", gap: 5, cursor: "pointer" }}><input type="radio" checked={nextModality === "onsite"} onChange={() => setNextModality("onsite")} /> Presencial</label>
              <label style={{ display: "flex", alignItems: "center", gap: 5, cursor: "pointer" }}><input type="radio" checked={nextModality === "virtual"} onChange={() => setNextModality("virtual")} /> Meet</label>
            </div>
          )}
          <div style={{ display: "flex", gap: 10 }}>
            <button onClick={() => doAdvance(activeStage, "rejected")} disabled={acting} style={btnDanger}>✕ Rechazar</button>
            <button onClick={() => doAdvance(activeStage, "approved")} disabled={acting} style={btnPrimary}>
              {activeStage === "manager" ? "✓ Contratar" : activeStage === "lead" ? "✓ Aprobar → agendar con gerencia" : "✓ Aprobar → agendar con líder"}
            </button>
          </div>
        </div>
      )}

      {/* Historial de feedback por etapa */}
      {stageFeedback.length > 0 && (
        <div style={{ ...panel, marginBottom: 16 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: "#eef2f9", marginBottom: 10 }}>Historial de decisiones</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {stageFeedback.map((f) => (
              <div key={f.id} style={{ fontSize: 12.5, color: "#aeb8cc", paddingBottom: 8, borderBottom: "1px solid var(--edge-soft)" }}>
                <b style={{ color: "#eef2f9" }}>{STAGE_LABEL[f.stage] || f.stage}</b> · <span style={{ color: f.decision === "approved" ? "#34d399" : "#f87171", fontWeight: 700 }}>{f.decision === "approved" ? "Aprobado" : "Rechazado"}</span> {f.decided_email ? `· ${f.decided_email}` : ""}
                {f.feedback && <div style={{ marginTop: 3, color: "#9aa4b8" }}>{f.feedback}</div>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Contratado */}
      {isHired && (
        <div style={{ marginBottom: 16, padding: "16px 20px", borderRadius: 14, background: "rgba(52,211,153,.1)", border: "1px solid rgba(52,211,153,.3)", color: "#34d399", fontSize: 14, fontWeight: 700 }}>🎉 Candidato contratado — proceso finalizado.</div>
      )}

      {/* contacto manual (aptos sin contactar) */}
      {canContact && (
        <div style={{ ...panel, marginBottom: 16, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 14 }}>
          <div style={{ fontSize: 13, color: "var(--muted)" }}>Apto en CV. Inicia la conversación de Telegram (saludo + Acepto/No interesado).</div>
          <button onClick={contact} disabled={contacting} style={{ padding: "10px 18px", borderRadius: 10, background: "var(--ac)", color: "var(--ac-ink)", fontWeight: 700, border: "none", cursor: "pointer", opacity: contacting ? 0.6 : 1, flex: "none" }}>{contacting ? "Contactando…" : "Contactar por Telegram"}</button>
        </div>
      )}

      {/* CV + prefiltro */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginBottom: 16 }}>
        <div style={panel}>
          <div style={{ fontSize: 14, fontWeight: 700, color: "#eef2f9", marginBottom: 4 }}>Perfil del CV</div>
          {cv.headline && <div style={{ fontSize: 12.5, color: "var(--ac)", fontWeight: 600, marginBottom: 16 }}>{cv.headline}</div>}
          <div style={{ display: "flex", flexDirection: "column", gap: 11, fontSize: 13 }}>
            <Row k="Correo" v={cv.email} />
            <Row k="Teléfono" v={cv.phone} />
            <Row k="Formación" v={cv.education ? [cv.education.level, cv.education.career].filter(Boolean).join(" — ") : ""} />
            <Row k="Experiencia" v={cv.years_experience != null && cv.years_experience !== "" ? `${cv.years_experience} años` : ""} />
            <Row k="Ubicación" v={cv.location} />
            <Row k="Pretensión" v={cv.salary_expectation} />
          </div>
          {cv.skills && cv.skills.length > 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 16, paddingTop: 16, borderTop: "1px solid var(--edge-soft)" }}>
              {cv.skills.map((s, i) => <span key={i} style={{ padding: "4px 10px", borderRadius: 7, background: "rgba(255,255,255,.04)", border: "1px solid rgba(255,255,255,.08)", fontSize: 11.5, fontWeight: 600, color: "#cfd8e8" }}>{s}</span>)}
            </div>
          )}
        </div>

        <div style={panel}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
            <div style={{ fontSize: 14, fontWeight: 700, color: "#eef2f9" }}>Pre-filtro automático</div>
            {prescreen?.pre_score != null && <div style={{ fontFamily: MONO, fontSize: 13, fontWeight: 700, color: scoreColor(prescreen.pre_score) }}>{Math.round(prescreen.pre_score)}/100</div>}
          </div>
          {prescreen?.summary && <div style={{ fontSize: 12.5, color: "#9aa4b8", lineHeight: 1.55, marginBottom: 14 }}>{prescreen.summary}</div>}
          <div style={{ display: "flex", flexDirection: "column", gap: 11 }}>
            {(prescreen?.per_requirement ?? []).map((r, i) => (
              <div key={i} style={{ display: "flex", gap: 10, fontSize: 12.5, lineHeight: 1.45 }}>
                <span style={{ color: r.met ? "#34d399" : "#f87171", fontWeight: 700, flex: "none", marginTop: 1 }}>{r.met ? "✓" : "✗"}</span>
                <span style={{ color: "#aeb8cc" }}><b style={{ color: "#eef2f9", fontWeight: 700 }}>{r.requirement}.</b> {r.note}</span>
              </div>
            ))}
          </div>
          {(docCv || docCul || candidate.status === "finished") && (
            <div style={{ marginTop: 16, paddingTop: 14, borderTop: "1px solid var(--edge-soft)", display: "flex", flexDirection: "column", gap: 9 }}>
              <DocRow label="Hoja de vida (CV)" doc={docCv} candidateId={candidate.id} type="cv" />
              <DocRow label="Certificado Único Laboral" doc={docCul} candidateId={candidate.id} type="cul" />
            </div>
          )}
        </div>
      </div>

      {/* esperando respuesta */}
      {waiting && (
        <div style={{ padding: 24, borderRadius: 15, background: "rgba(251,191,36,.06)", border: "1px solid rgba(251,191,36,.22)", textAlign: "center", marginBottom: 16 }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: "#fbbf24", marginBottom: 6 }}>⏳ Esperando respuesta del candidato</div>
          <div style={{ fontSize: 13, color: "#aeb8cc" }}>El agente envió las preguntas por Telegram. La evaluación se generará al completar la entrevista.</div>
        </div>
      )}

      {/* evaluación IA */}
      {scorecard && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginBottom: 16 }}>
            {scorecard.per_criterion.length >= 3 && (
              <div style={panel}>
                <div style={{ fontSize: 14, fontWeight: 700, color: "#eef2f9", marginBottom: 3 }}>Perfil por criterio</div>
                <div style={{ fontSize: 11.5, color: "var(--muted)", marginBottom: 6 }}>Línea punteada = umbral para avanzar ({green})</div>
                <Radar crit={scorecard.per_criterion.map((c, i) => ({ n: c.label || String(i + 1), score: c.score ?? 0 }))} threshold={green} />
              </div>
            )}
            <div style={panel}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 14, fontWeight: 700, color: "#eef2f9", marginBottom: 12 }}><span style={{ color: "var(--ac)" }}>✦</span>Veredicto del agente</div>
              <div style={{ display: "inline-flex", alignItems: "center", gap: 7, padding: "5px 12px", borderRadius: 8, background: sm.soft, border: `1px solid ${sm.color}55`, color: sm.color, fontSize: 12.5, fontWeight: 700, marginBottom: 13 }}>● {scorecard.semaphore === "green" ? "Recomendado para avanzar" : scorecard.semaphore === "yellow" ? "Revisar con cuidado" : "No recomendado"}</div>
              {scorecard.review_required && (
                <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "9px 12px", borderRadius: 9, background: "rgba(251,191,36,.1)", border: "1px solid rgba(251,191,36,.35)", color: "#fbbf24", fontSize: 12.5, fontWeight: 600, marginBottom: 13 }}>
                  ⚠ Requiere revisión humana — alguna respuesta no se pudo evaluar con confianza.
                </div>
              )}
              <div style={{ fontSize: 13, color: "#aeb8cc", lineHeight: 1.6 }}>{scorecard.summary}</div>
              <div style={{ fontSize: 13, fontWeight: 700, color: "#eef2f9", margin: "16px 0 6px" }}>Recomendación</div>
              <div style={{ fontSize: 13, color: "#aeb8cc", lineHeight: 1.6 }}>{scorecard.recommendation}</div>
            </div>
          </div>

          <div style={{ fontSize: 15, fontWeight: 700, color: "#eef2f9", margin: "24px 0 12px" }}>Evaluación por criterio</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {scorecard.per_criterion.map((c, i) => {
              const s = c.score ?? 0;
              return (
                <div key={i} style={{ padding: "16px 18px", borderRadius: 13, background: "rgba(255,255,255,.02)", border: "1px solid var(--edge)" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 13 }}>
                    <div style={{ fontFamily: MONO, fontSize: 18, fontWeight: 700, color: scoreColor(s), width: 62, flex: "none" }}>{c.score != null ? c.score : "s/d"}<span style={{ fontSize: 11, color: "var(--muted-3)" }}>/100</span></div>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: 14, fontWeight: 700, color: "#eef2f9" }}>{c.label || c.criterion}</div>
                      <div style={{ fontSize: 12.5, color: "#8a94a8", marginTop: 3, lineHeight: 1.5 }}>{c.justification}</div>
                    </div>
                  </div>
                  <div style={{ height: 5, borderRadius: 4, background: "rgba(255,255,255,.06)", marginTop: 12, overflow: "hidden" }}><div style={{ height: "100%", width: `${s}%`, borderRadius: 4, background: scoreColor(s) }} /></div>
                </div>
              );
            })}
          </div>
        </>
      )}

      {!scorecard && !waiting && !canContact && (
        <div style={panel}><p style={{ color: "var(--muted)", margin: 0 }}>La entrevista aún no termina; no hay scorecard todavía.</p></div>
      )}

      {/* transcripción */}
      {transcript.length > 0 && (
        <>
          <div onClick={() => setTxOpen((v) => !v)} style={{ marginTop: 16, display: "flex", alignItems: "center", gap: 9, padding: "14px 18px", borderRadius: 12, background: "rgba(255,255,255,.025)", border: "1px solid var(--edge)", cursor: "pointer", fontSize: 13.5, fontWeight: 600, color: "#aeb8cc" }}>
            <span style={{ transform: txOpen ? "rotate(0deg)" : "rotate(-90deg)" }}>▾</span>Ver transcripción de la entrevista (Telegram · {transcript.length} mensajes)
          </div>
          {txOpen && (
            <div style={{ padding: "18px 20px", borderRadius: 12, background: "rgba(255,255,255,.015)", border: "1px solid var(--edge-soft)", marginTop: 9, display: "flex", flexDirection: "column", gap: 12 }}>
              {transcript.map((m, i) => {
                const user = m.role === "user";
                return (
                  <div key={i} style={{ display: "flex", flexDirection: user ? "row-reverse" : "row" }}>
                    <div style={{ maxWidth: "78%", padding: "10px 13px", borderRadius: 13, background: user ? "var(--ac-soft)" : "rgba(255,255,255,.04)", fontSize: 13, lineHeight: 1.5, color: user ? "#eef2f9" : "#cfd8e8" }}>{m.content}</div>
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}

      {/* Trazas LLM crudas (O-1): prompt/respuesta por llamada — replay/debug, solo admin */}
      {admin && (
        <>
          <div onClick={toggleTraces} style={{ marginTop: 16, display: "flex", alignItems: "center", gap: 9, padding: "14px 18px", borderRadius: 12, background: "rgba(255,255,255,.025)", border: "1px solid var(--edge)", cursor: "pointer", fontSize: 13.5, fontWeight: 600, color: "#aeb8cc" }}>
            <span style={{ transform: tracesOpen ? "rotate(0deg)" : "rotate(-90deg)" }}>▾</span>
            Trazas LLM · evaluación cruda{traces ? ` (${traces.length} llamadas)` : ""}
          </div>
          {tracesOpen && (
            <div style={{ padding: "18px 20px", borderRadius: 12, background: "rgba(255,255,255,.015)", border: "1px solid var(--edge-soft)", marginTop: 9, display: "flex", flexDirection: "column", gap: 12 }}>
              {traces === null && <div style={{ fontSize: 12.5, color: "var(--muted)" }}>Cargando…</div>}
              {traces?.length === 0 && (
                <div style={{ fontSize: 12.5, color: "var(--muted)" }}>
                  Sin trazas registradas. Activa <span style={{ fontFamily: MONO }}>LLM_TRACE_ENABLED=true</span> en el backend para guardar prompt/respuesta de cada llamada.
                </div>
              )}
              {traces?.map((t) => (
                <div key={t.id} style={{ padding: "12px 14px", borderRadius: 11, background: "rgba(255,255,255,.02)", border: `1px solid ${t.error ? "rgba(248,113,113,.3)" : "var(--edge)"}` }}>
                  <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap", marginBottom: 8, fontSize: 11.5, color: "var(--muted)" }}>
                    <span style={{ padding: "2px 8px", borderRadius: 6, background: "var(--ac-soft)", color: "#eef2f9", fontWeight: 700 }}>{t.stage}</span>
                    <span>{t.model}</span>
                    <span>{t.duration_ms} ms</span>
                    <span>{formatWhen(t.created_at)}</span>
                    {t.prompt_version && <span>prompt {t.prompt_version}</span>}
                    {t.error && <span style={{ color: "#f87171", fontWeight: 700 }}>error → fallback</span>}
                  </div>
                  <pre style={{ margin: 0, whiteSpace: "pre-wrap", fontFamily: MONO, fontSize: 11, lineHeight: 1.5, color: "#8b95a8", maxHeight: 180, overflowY: "auto" }}>{t.prompt_text}</pre>
                  {t.response_text && (
                    <pre style={{ margin: "8px 0 0", whiteSpace: "pre-wrap", fontFamily: MONO, fontSize: 11, lineHeight: 1.5, color: "#cfd8e8", maxHeight: 180, overflowY: "auto", borderTop: "1px solid var(--edge-soft)", paddingTop: 8 }}>{t.response_text}</pre>
                  )}
                  {t.error && <div style={{ marginTop: 8, fontFamily: MONO, fontSize: 11, color: "#f87171" }}>{t.error}</div>}
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {/* barra de decisión inicial (Fase 1: continuar a la entrevista con RR.HH.) */}
      {scorecard && !decidedInitial && (
        <div style={{ position: "sticky", bottom: 0, zIndex: 50, marginTop: 18, padding: "18px 0 4px", background: "linear-gradient(180deg,transparent,rgba(10,14,22,.92) 30%)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 14, padding: "16px 20px", borderRadius: 15, background: "#0f1521", border: "1px solid rgba(255,255,255,.1)", boxShadow: "0 -8px 30px rgba(0,0,0,.4)" }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: "#eef2f9" }}>Decisión del reclutador</div>
              <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 2 }}>El candidato {scorecard.semaphore === "green" ? "superó el umbral en la evaluación." : "requiere tu criterio."}</div>
            </div>
            <button onClick={() => decide("reject")} disabled={acting} style={{ display: "flex", alignItems: "center", gap: 8, padding: "12px 20px", borderRadius: 11, background: "rgba(248,113,113,.1)", border: "1px solid rgba(248,113,113,.3)", color: "#f87171", fontSize: 14, fontWeight: 700, cursor: "pointer", opacity: acting ? 0.5 : 1 }}>✕ Rechazar</button>
            <button onClick={() => decide("advance")} disabled={acting} style={{ display: "flex", alignItems: "center", gap: 8, padding: "12px 24px", borderRadius: 11, background: "#34d399", color: "#06231a", fontSize: 14, fontWeight: 800, border: "none", cursor: "pointer", boxShadow: "0 8px 22px rgba(52,211,153,.3)", opacity: acting ? 0.5 : 1 }}>✓ Continuar → Agendar entrevista con RR.HH.</button>
          </div>
        </div>
      )}

      {admin && (
        <div style={{ marginTop: 26, padding: "16px 20px", borderRadius: 14, background: "rgba(248,113,113,.05)", border: "1px solid rgba(248,113,113,.22)" }}>
          <div style={{ fontSize: 13.5, fontWeight: 700, color: "#f87171", marginBottom: 4 }}>Zona de peligro</div>
          <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
            <div style={{ flex: 1, minWidth: 240, fontSize: 12.5, color: "var(--muted)" }}>
              Borra definitivamente al candidato y todos sus datos (transcripción, CV, evaluación). Irreversible · derecho al olvido (Ley 29733).
            </div>
            <button
              onClick={() => { setEraseName(""); setEraseOpen(true); }}
              disabled={erasing}
              style={{ padding: "10px 18px", borderRadius: 10, background: "rgba(248,113,113,.12)", border: "1px solid rgba(248,113,113,.35)", color: "#f87171", fontSize: 13, fontWeight: 700, cursor: "pointer", opacity: erasing ? 0.5 : 1, whiteSpace: "nowrap" }}
            >
              🗑 Borrar candidato
            </button>
          </div>
        </div>
      )}

      {/* Modal de confirmación del borrado (U3: tipear el nombre, no confirm nativo) */}
      {eraseOpen && (
        <div style={{ position: "fixed", inset: 0, zIndex: 100, background: "rgba(5,8,14,.72)", backdropFilter: "blur(3px)", display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
          <div style={{ width: 440, maxWidth: "100%", padding: "22px 24px", borderRadius: 16, background: "#0f1521", border: "1px solid rgba(248,113,113,.35)", boxShadow: "0 20px 60px rgba(0,0,0,.55)" }}>
            <div style={{ fontSize: 15, fontWeight: 800, color: "#f87171", marginBottom: 8 }}>Borrado definitivo</div>
            <div style={{ fontSize: 13, color: "#aeb8cc", lineHeight: 1.55, marginBottom: 14 }}>
              Se eliminarán <b style={{ color: "#eef2f9" }}>todos los datos</b> de {data.candidate.name || "este candidato"}:
              transcripción, CV, evaluación y notificaciones. Irreversible · derecho al olvido (Ley 29733).
            </div>
            <div style={{ fontSize: 12.5, color: "var(--muted)", marginBottom: 8 }}>
              Escribe <b style={{ color: "#eef2f9" }}>{eraseTarget}</b> para confirmar:
            </div>
            <input
              value={eraseName}
              onChange={(e) => setEraseName(e.target.value)}
              placeholder={eraseTarget}
              autoFocus
              style={{ ...inputStyle, width: "100%", marginBottom: 16 }}
            />
            <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
              <button onClick={() => { setEraseOpen(false); setEraseName(""); }} disabled={erasing} style={btnGhost}>Cancelar</button>
              <button
                onClick={erase}
                disabled={!eraseReady || erasing}
                style={{ ...btnDanger, background: "rgba(248,113,113,.16)", opacity: !eraseReady || erasing ? 0.45 : 1, cursor: !eraseReady || erasing ? "not-allowed" : "pointer" }}
              >
                {erasing ? "Borrando…" : "Borrar definitivamente"}
              </button>
            </div>
          </div>
        </div>
      )}

      {toast && <Toast message={toast} />}
    </Shell>
  );
}

function Row({ k, v }: { k: string; v?: string | null }) {
  if (!v) return null;
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
      <span style={{ color: "var(--muted)" }}>{k}</span>
      <span style={{ color: "#dbe2ee", textAlign: "right" }}>{v}</span>
    </div>
  );
}

function DocRow({ label, doc, candidateId, type }: { label: string; doc?: { filename: string }; candidateId: string; type: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 9, fontSize: 12.5, color: "#aeb8cc" }}>
      📄 {label}{" "}
      {doc ? (
        <a href={api.documentUrl(candidateId, type)} target="_blank" rel="noopener noreferrer" style={{ color: "#34d399", fontWeight: 600, textDecoration: "none" }}>✓ {doc.filename || "ver PDF"}</a>
      ) : (
        <span style={{ color: "var(--muted)" }}>pendiente</span>
      )}
    </div>
  );
}
