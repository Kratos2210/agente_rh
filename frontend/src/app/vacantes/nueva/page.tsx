"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Shell, BackLink } from "@/components/Shell";
import { api, errorMessage, Recruiter } from "@/lib/api";
import { ACCENT, avatarColor, initials } from "@/lib/stages";

const MONO = "var(--font-jetbrains), monospace";

interface Criterion { n: string; w: number; d: string }
interface NV {
  title: string; company: string; area: string; modality: string; location: string;
  salMin: string; salMax: string; desc: string;
  reqs: string[]; benefits: string[]; criteria: Criterion[]; threshold: number;
  questions: string[]; recruiterId: string; leadRecruiterId: string; managerRecruiterId: string; portals: Record<string, boolean>; autoAgent: boolean;
}

const DEFAULT_NV: NV = {
  title: "", company: "Nuevo Mundo", area: "", modality: "presencial", location: "", salMin: "", salMax: "", desc: "",
  reqs: ["Bachiller en Ingeniería de Sistemas, Software o Computación.", "Mínimo 2 años en el rol.", "Dominio de las herramientas clave del puesto.", "Disponibilidad para la modalidad indicada."],
  benefits: ["Planilla completa desde el primer día.", "EPS al 50%.", "Utilidades."],
  criteria: [
    { n: "Formación", w: 15, d: "Formación académica afín al puesto." },
    { n: "Experiencia", w: 20, d: "Años de experiencia específica verificable." },
    { n: "Disponibilidad", w: 10, d: "Disponibilidad para la modalidad y ubicación." },
    { n: "Dominio técnico", w: 25, d: "Amplitud y profundidad técnica con herramientas concretas." },
    { n: "Caso real", w: 20, d: "Explica un caso end-to-end con impacto medible." },
    { n: "Salario", w: 10, d: "Pretensión salarial clara y dentro de rango." },
  ],
  threshold: 75,
  questions: [
    "Cuéntame tu experiencia en el rol y las herramientas que dominas.",
    "¿Tienes formación afín al puesto?",
    "¿Tienes disponibilidad para la modalidad y ubicación indicadas?",
    "Describe un caso end-to-end que hayas implementado, con su impacto medible.",
    "¿Qué herramientas específicas usas?",
    "¿Cuál es tu pretensión salarial (monto, moneda, bruto/neto)?",
  ],
  recruiterId: "", leadRecruiterId: "", managerRecruiterId: "", portals: { bumeran: true, linkedin: true, computrabajo: false }, autoAgent: true,
};

const STEP_META = [["Puesto", "Datos básicos"], ["Requisitos", "Filtro de CV"], ["Criterios", "Ponderación"], ["Preguntas", "Entrevista IA"], ["Publicar", "Portales"]];
const MODS = [["presencial", "Presencial"], ["hibrido", "Híbrido"], ["remoto", "Remoto"]];
const PORTALS: [string, string, string][] = [["bumeran", "Bumeran", "#00b8a9"], ["linkedin", "LinkedIn", "#0a66c2"], ["computrabajo", "Computrabajo", "#f59e0b"]];

const field: React.CSSProperties = {
  width: "100%", padding: "12px 14px", borderRadius: 11, background: "var(--field)",
  border: "1px solid rgba(255,255,255,.1)", color: "#e8edf6", fontSize: 14, outline: "none",
};
const lbl: React.CSSProperties = { fontSize: 12.5, fontWeight: 700, color: "#aeb8cc", marginBottom: 7, display: "block" };
const panel: React.CSSProperties = { padding: 26, borderRadius: 18, background: "rgba(255,255,255,.02)", border: "1px solid var(--edge)" };

export default function NuevaVacante() {
  const router = useRouter();
  const [step, setStep] = useState(1);
  const [nv, setNv] = useState<NV>(DEFAULT_NV);
  const [recruiters, setRecruiters] = useState<Recruiter[]>([]);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => { api.listRecruiters().then(setRecruiters).catch(() => {}); }, []);
  const set = (patch: Partial<NV>) => setNv((s) => ({ ...s, ...patch }));
  const arr = <K extends keyof NV>(field: K, val: NV[K]) => set({ [field]: val } as Partial<NV>);
  const goStep = (n: number) => { setStep(n); try { window.scrollTo(0, 0); } catch {} };

  const totalW = nv.criteria.reduce((a, c) => a + (+c.w || 0), 0);
  const totalWColor = totalW >= 95 && totalW <= 105 ? "#34d399" : "#fbbf24";

  const create = async () => {
    if (!nv.title.trim()) { setErr("El título es obligatorio."); setStep(1); return; }
    setSaving(true); setErr("");
    // Cada fila de vacancy_questions combina la pregunta (texto) con el criterio (nombre/peso/label).
    const n = Math.max(nv.criteria.length, nv.questions.length);
    const questions = Array.from({ length: n }, (_, i) => {
      const c = nv.criteria[i];
      return {
        position: i + 1,
        text: nv.questions[i] || (c ? `Cuéntame sobre: ${c.n}` : ""),
        criterion: c?.d || c?.n || "",
        label: c?.n || "",
        weight: c?.w ?? 1,
        max_follow_ups: 1,
      };
    }).filter((q) => q.text.trim());
    try {
      await api.createVacancy({
        title: nv.title,
        description: nv.desc,
        requirements: nv.reqs.filter(Boolean).join("\n"),
        company_info: nv.company,
        area: nv.area,
        modality: nv.modality,
        location: nv.location,
        salary_min: nv.salMin ? Number(nv.salMin) : null,
        salary_max: nv.salMax ? Number(nv.salMax) : null,
        benefits: nv.benefits.filter(Boolean),
        portals: Object.entries(nv.portals).filter(([, on]) => on).map(([k]) => k),
        auto_agent: nv.autoAgent,
        recruiter_id: nv.recruiterId || null,
        lead_recruiter_id: nv.leadRecruiterId || null,
        manager_recruiter_id: nv.managerRecruiterId || null,
        semaphore_thresholds: { green_min: nv.threshold, yellow_min: 50 },
        questions,
      });
      router.push("/");
    } catch (e) { setErr(errorMessage(e)); setSaving(false); }
  };

  return (
    <Shell width={1080}>
      <BackLink href="/" label="Cancelar" />
      <h1 style={{ margin: "0 0 4px", fontSize: 30, fontWeight: 800, letterSpacing: "-.03em", color: "var(--heading)" }}>Nueva vacante</h1>
      <p style={{ margin: "0 0 24px", color: "var(--muted)", fontSize: 14 }}>Define el puesto y el agente importará, filtrará y entrevistará a los candidatos automáticamente.</p>

      <div style={{ display: "grid", gridTemplateColumns: "210px 1fr", gap: 24, alignItems: "start" }}>
        {/* rail */}
        <div style={{ position: "sticky", top: 84, display: "flex", flexDirection: "column", gap: 4 }}>
          {STEP_META.map((m, i) => {
            const nstep = i + 1, active = step === nstep, done = step > nstep;
            return (
              <div key={i} onClick={() => goStep(nstep)} style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 14px", borderRadius: 12, cursor: "pointer", background: active ? "rgba(255,255,255,.05)" : "transparent" }}>
                <div style={{ width: 28, height: 28, borderRadius: "50%", flex: "none", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12, fontWeight: 700, background: done ? "#34d399" : active ? ACCENT.c : "transparent", border: `2px solid ${done ? "#34d399" : active ? ACCENT.c : "rgba(255,255,255,.15)"}`, color: done ? "#06231a" : active ? "#fff" : "var(--muted-2)" }}>{done ? "✓" : nstep}</div>
                <div>
                  <div style={{ fontSize: 13.5, fontWeight: 700, color: active ? "var(--heading)" : done ? "#aeb8cc" : "var(--muted)" }}>{m[0]}</div>
                  <div style={{ fontSize: 11, color: "var(--muted-2)" }}>{m[1]}</div>
                </div>
              </div>
            );
          })}
        </div>

        {/* panels */}
        <div style={{ minWidth: 0 }}>
          {step === 1 && (
            <div style={panel}>
              <div style={{ fontSize: 18, fontWeight: 800, color: "var(--heading)", marginBottom: 2 }}>Datos del puesto</div>
              <div style={{ fontSize: 13, color: "var(--muted)", marginBottom: 22 }}>Información que verán los candidatos en los portales.</div>
              <label style={lbl}>Título del puesto</label>
              <input value={nv.title} onChange={(e) => set({ title: e.target.value })} placeholder="Ej. Analista de Automatizaciones e IA" style={{ ...field, marginBottom: 18 }} />
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 18 }}>
                <div><label style={lbl}>Empresa</label><input value={nv.company} onChange={(e) => set({ company: e.target.value })} style={field} /></div>
                <div><label style={lbl}>Área</label><input value={nv.area} onChange={(e) => set({ area: e.target.value })} placeholder="Tecnología / Retail" style={field} /></div>
              </div>
              <label style={lbl}>Modalidad</label>
              <div style={{ display: "flex", gap: 8, marginBottom: 18 }}>
                {MODS.map(([k, label]) => {
                  const on = nv.modality === k;
                  return <div key={k} onClick={() => set({ modality: k })} style={{ flex: 1, textAlign: "center", padding: 11, borderRadius: 11, fontSize: 13, fontWeight: 700, cursor: "pointer", background: on ? ACCENT.soft : "rgba(255,255,255,.03)", color: on ? ACCENT.c : "#aeb8cc", border: `1px solid ${on ? ACCENT.bd : "rgba(255,255,255,.08)"}` }}>{label}</div>;
                })}
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr 1fr", gap: 16, marginBottom: 18 }}>
                <div><label style={lbl}>Ubicación</label><input value={nv.location} onChange={(e) => set({ location: e.target.value })} placeholder="Santiago de Surco, Lima" style={field} /></div>
                <div><label style={lbl}>Salario desde (S/)</label><input value={nv.salMin} onChange={(e) => set({ salMin: e.target.value })} placeholder="5000" style={{ ...field, fontFamily: MONO }} /></div>
                <div><label style={lbl}>Hasta (S/)</label><input value={nv.salMax} onChange={(e) => set({ salMax: e.target.value })} placeholder="7000" style={{ ...field, fontFamily: MONO }} /></div>
              </div>
              <label style={lbl}>Descripción del puesto</label>
              <textarea value={nv.desc} onChange={(e) => set({ desc: e.target.value })} placeholder="Responsabilidades, objetivos y contexto del rol…" style={{ ...field, minHeight: 120, resize: "vertical", lineHeight: 1.6 }} />
            </div>
          )}

          {step === 2 && (
            <div style={panel}>
              <div style={{ fontSize: 18, fontWeight: 800, color: "var(--heading)", marginBottom: 2 }}>Requisitos y beneficios</div>
              <div style={{ fontSize: 13, color: "var(--muted)", marginBottom: 22 }}>El agente usa los requisitos como base del filtro de CV.</div>
              <EditableList items={nv.reqs} onChange={(v) => arr("reqs", v)} bullet="✓" bulletColor={ACCENT.c} addLabel="+ Agregar requisito" />
              <div style={{ height: 26 }} />
              <EditableList items={nv.benefits} onChange={(v) => arr("benefits", v)} bullet="◆" bulletColor="#34d399" addLabel="+ Agregar beneficio" title="Beneficios" />
            </div>
          )}

          {step === 3 && (
            <div style={panel}>
              <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16 }}>
                <div style={{ fontSize: 18, fontWeight: 800, color: "var(--heading)" }}>Criterios de evaluación</div>
                <div style={{ textAlign: "right" }}><div style={{ fontFamily: MONO, fontSize: 17, fontWeight: 700, color: totalWColor }}>{totalW}%</div><div style={{ fontSize: 10.5, color: "var(--muted)", fontWeight: 600 }}>peso total</div></div>
              </div>
              <div style={{ fontSize: 13, color: "var(--muted)", marginBottom: 22 }}>El agente puntúa cada criterio (0–100) en la entrevista y los pondera según su peso.</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                {nv.criteria.map((c, i) => (
                  <div key={i} style={{ padding: "16px 18px", borderRadius: 13, background: "var(--field)", border: "1px solid rgba(255,255,255,.08)" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
                      <input value={c.n} onChange={(e) => arr("criteria", nv.criteria.map((x, j) => j === i ? { ...x, n: e.target.value } : x))} style={{ flex: 1, padding: "9px 12px", borderRadius: 9, background: "rgba(255,255,255,.03)", border: "1px solid rgba(255,255,255,.08)", color: "#eef2f9", fontSize: 14, fontWeight: 700, outline: "none" }} />
                      <div style={{ fontFamily: MONO, fontSize: 14, fontWeight: 700, color: ACCENT.c, width: 46, textAlign: "right" }}>{c.w}%</div>
                      <div onClick={() => arr("criteria", nv.criteria.filter((_, j) => j !== i))} style={{ width: 32, height: 32, borderRadius: 8, background: "rgba(255,255,255,.04)", border: "1px solid rgba(255,255,255,.08)", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--muted)", cursor: "pointer", flex: "none" }}>✕</div>
                    </div>
                    <input type="range" min={0} max={40} value={c.w} onChange={(e) => arr("criteria", nv.criteria.map((x, j) => j === i ? { ...x, w: +e.target.value } : x))} style={{ width: "100%", accentColor: ACCENT.c, marginBottom: 10 }} />
                    <input value={c.d} onChange={(e) => arr("criteria", nv.criteria.map((x, j) => j === i ? { ...x, d: e.target.value } : x))} placeholder="Descripción del criterio…" style={{ width: "100%", padding: "9px 12px", borderRadius: 9, background: "rgba(255,255,255,.03)", border: "1px solid rgba(255,255,255,.07)", color: "#aeb8cc", fontSize: 12.5, outline: "none" }} />
                  </div>
                ))}
              </div>
              <div onClick={() => arr("criteria", [...nv.criteria, { n: "Nuevo criterio", w: 10, d: "" }])} style={{ display: "inline-flex", alignItems: "center", gap: 7, fontSize: 13, fontWeight: 700, color: ACCENT.c, cursor: "pointer", margin: "14px 0 24px" }}>+ Agregar criterio</div>
              <div style={{ padding: "16px 18px", borderRadius: 13, background: "var(--ac-soft)", border: "1px solid var(--ac-bd)" }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 9 }}><span style={{ fontSize: 13.5, fontWeight: 700, color: "#eef2f9" }}>Umbral para avanzar</span><span style={{ fontFamily: MONO, fontSize: 16, fontWeight: 700, color: ACCENT.c }}>{nv.threshold}/100</span></div>
                <input type="range" min={50} max={95} value={nv.threshold} onChange={(e) => set({ threshold: +e.target.value })} style={{ width: "100%", accentColor: ACCENT.c }} />
                <div style={{ fontSize: 12, color: "#9aa4b8", marginTop: 6 }}>Candidatos por debajo del umbral se descartan automáticamente.</div>
              </div>
            </div>
          )}

          {step === 4 && (
            <div style={panel}>
              <div style={{ fontSize: 18, fontWeight: 800, color: "var(--heading)", marginBottom: 2 }}>Preguntas de la entrevista</div>
              <div style={{ fontSize: 13, color: "var(--muted)", marginBottom: 22 }}>El agente las envía por Telegram y evalúa las respuestas contra tus criterios.</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 11 }}>
                {nv.questions.map((q, i) => (
                  <div key={i} style={{ display: "flex", alignItems: "flex-start", gap: 12, padding: "15px 16px", borderRadius: 13, background: "var(--field)", border: "1px solid rgba(255,255,255,.08)" }}>
                    <div style={{ width: 26, height: 26, borderRadius: 8, background: "var(--ac-soft)", color: ACCENT.c, display: "flex", alignItems: "center", justifyContent: "center", fontFamily: MONO, fontSize: 12, fontWeight: 700, flex: "none", marginTop: 2 }}>{i + 1}</div>
                    <textarea value={q} onChange={(e) => arr("questions", nv.questions.map((x, j) => j === i ? e.target.value : x))} style={{ flex: 1, minHeight: 46, padding: "8px 10px", borderRadius: 9, background: "rgba(255,255,255,.03)", border: "1px solid rgba(255,255,255,.07)", color: "#e8edf6", fontSize: 13.5, outline: "none", resize: "vertical", lineHeight: 1.5 }} />
                    <div onClick={() => arr("questions", nv.questions.filter((_, j) => j !== i))} style={{ width: 32, height: 32, borderRadius: 8, background: "rgba(255,255,255,.04)", border: "1px solid rgba(255,255,255,.08)", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--muted)", cursor: "pointer", flex: "none", marginTop: 2 }}>✕</div>
                  </div>
                ))}
              </div>
              <div onClick={() => arr("questions", [...nv.questions, ""])} style={{ display: "inline-flex", alignItems: "center", gap: 7, fontSize: 13, fontWeight: 700, color: ACCENT.c, cursor: "pointer", marginTop: 14 }}>+ Agregar pregunta</div>
            </div>
          )}

          {step === 5 && (
            <div style={panel}>
              <div style={{ fontSize: 18, fontWeight: 800, color: "var(--heading)", marginBottom: 2 }}>Responsable y publicación</div>
              <div style={{ fontSize: 13, color: "var(--muted)", marginBottom: 22 }}>Asigna un reclutador y elige dónde publicar la vacante.</div>
              <label style={lbl}>Responsable del proceso</label>
              <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 24 }}>
                {recruiters.map((r) => {
                  const on = nv.recruiterId === r.id;
                  return (
                    <div key={r.id} onClick={() => set({ recruiterId: r.id })} style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 14px", borderRadius: 12, cursor: "pointer", background: on ? ACCENT.soft : "rgba(255,255,255,.025)", border: `1px solid ${on ? ACCENT.bd : "var(--edge)"}` }}>
                      <div style={{ width: 36, height: 36, borderRadius: 10, background: avatarColor(r.name), display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 800, color: "#0a0e16", fontSize: 14 }}>{initials(r.name)}</div>
                      <div style={{ flex: 1 }}><div style={{ fontSize: 14, fontWeight: 700, color: "#eef2f9" }}>{r.name}</div><div style={{ fontSize: 11.5, color: "var(--muted)" }}>{r.role}{r.company ? ` · ${r.company}` : ""}</div></div>
                      <div style={{ width: 20, height: 20, borderRadius: "50%", border: `2px solid ${on ? ACCENT.c : "rgba(255,255,255,.2)"}`, display: "flex", alignItems: "center", justifyContent: "center" }}><div style={{ width: 9, height: 9, borderRadius: "50%", background: on ? ACCENT.c : "transparent" }} /></div>
                    </div>
                  );
                })}
                {recruiters.length === 0 && <p style={{ fontSize: 13, color: "var(--muted)" }}>No hay reclutadores. Agrégalos en Equipo.</p>}
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginBottom: 24 }}>
                <div>
                  <label style={lbl}>Líder del proyecto (Fase 2)</label>
                  <select value={nv.leadRecruiterId} onChange={(e) => set({ leadRecruiterId: e.target.value })} style={{ ...field, cursor: "pointer" }}>
                    <option value="">— Sin asignar —</option>
                    {recruiters.map((r) => <option key={r.id} value={r.id}>{r.name}{r.role ? ` · ${r.role}` : ""}</option>)}
                  </select>
                </div>
                <div>
                  <label style={lbl}>Gerencia (Fase 3)</label>
                  <select value={nv.managerRecruiterId} onChange={(e) => set({ managerRecruiterId: e.target.value })} style={{ ...field, cursor: "pointer" }}>
                    <option value="">— Sin asignar —</option>
                    {recruiters.map((r) => <option key={r.id} value={r.id}>{r.name}{r.role ? ` · ${r.role}` : ""}</option>)}
                  </select>
                </div>
              </div>
              <label style={lbl}>Portales de empleo</label>
              <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 24 }}>
                {PORTALS.map(([k, label, color]) => {
                  const on = nv.portals[k];
                  return (
                    <div key={k} onClick={() => set({ portals: { ...nv.portals, [k]: !on } })} style={{ display: "flex", alignItems: "center", gap: 12, padding: "13px 16px", borderRadius: 12, cursor: "pointer", background: on ? "rgba(255,255,255,.04)" : "rgba(255,255,255,.02)", border: `1px solid ${on ? ACCENT.bd : "var(--edge)"}` }}>
                      <div style={{ width: 30, height: 30, borderRadius: 8, background: color, display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 800, fontSize: 13, color: "#fff" }}>{label[0]}</div>
                      <div style={{ flex: 1, fontSize: 14, fontWeight: 700, color: "#eef2f9" }}>{label}</div>
                      <Switch on={!!on} />
                    </div>
                  );
                })}
              </div>
              <div onClick={() => set({ autoAgent: !nv.autoAgent })} style={{ display: "flex", alignItems: "center", gap: 13, padding: "16px 18px", borderRadius: 13, cursor: "pointer", background: nv.autoAgent ? ACCENT.soft : "rgba(255,255,255,.02)", border: `1px solid ${nv.autoAgent ? ACCENT.bd : "var(--edge)"}` }}>
                <div style={{ width: 38, height: 38, borderRadius: 11, background: "var(--ac-soft)", color: ACCENT.c, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 17, flex: "none" }}>✦</div>
                <div style={{ flex: 1 }}><div style={{ fontSize: 14, fontWeight: 700, color: "#eef2f9" }}>Contacto automático del agente</div><div style={{ fontSize: 12, color: "var(--muted)", marginTop: 2 }}>El agente contacta y entrevista a los aptos sin intervención manual.</div></div>
                <Switch on={nv.autoAgent} />
              </div>
            </div>
          )}

          {err && <p style={{ color: "#f87171", fontSize: 13, marginTop: 12 }}>{err}</p>}

          {/* footer nav */}
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginTop: 18, position: "sticky", bottom: 18, padding: "14px 18px", borderRadius: 14, background: "#0f1521", border: "1px solid rgba(255,255,255,.1)", boxShadow: "0 -8px 30px rgba(0,0,0,.4)" }}>
            <div onClick={() => goStep(Math.max(1, step - 1))} style={{ padding: "11px 18px", borderRadius: 11, fontSize: 13.5, fontWeight: 700, cursor: "pointer", background: "rgba(255,255,255,.05)", border: "1px solid rgba(255,255,255,.1)", color: step > 1 ? "#cfd8e8" : "#3a4253" }}>‹ Atrás</div>
            <div style={{ fontSize: 12.5, color: "var(--muted)", fontWeight: 600 }}>Paso {step} de 5</div>
            {step === 5 ? (
              <div onClick={saving ? undefined : create} style={{ display: "flex", alignItems: "center", gap: 8, padding: "12px 22px", borderRadius: 11, fontSize: 14, fontWeight: 800, cursor: "pointer", background: "#34d399", color: "#06231a", boxShadow: "0 8px 22px rgba(52,211,153,.3)", opacity: saving ? 0.6 : 1 }}>{saving ? "Creando…" : "✓ Crear y sincronizar"}</div>
            ) : (
              <div onClick={() => goStep(Math.min(5, step + 1))} style={{ display: "flex", alignItems: "center", gap: 8, padding: "12px 22px", borderRadius: 11, fontSize: 14, fontWeight: 800, cursor: "pointer", background: "linear-gradient(135deg,var(--ac),var(--ac-btn))", color: "#fff" }}>Continuar ›</div>
            )}
          </div>
        </div>
      </div>
    </Shell>
  );
}

function Switch({ on }: { on: boolean }) {
  return (
    <div style={{ width: 40, height: 23, borderRadius: 20, background: on ? ACCENT.c : "rgba(255,255,255,.12)", position: "relative", transition: "background .2s" }}>
      <div style={{ width: 17, height: 17, borderRadius: "50%", background: "#fff", position: "absolute", top: 3, left: on ? 20 : 3, transition: "left .2s" }} />
    </div>
  );
}

function EditableList({ items, onChange, bullet, bulletColor, addLabel, title }: { items: string[]; onChange: (v: string[]) => void; bullet: string; bulletColor: string; addLabel: string; title?: string }) {
  return (
    <div>
      {title && <div style={{ fontSize: 13, fontWeight: 700, color: "#eef2f9", marginBottom: 11 }}>{title}</div>}
      <div style={{ display: "flex", flexDirection: "column", gap: 9, marginBottom: 12 }}>
        {items.map((t, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ color: bulletColor, fontWeight: 700, flex: "none" }}>{bullet}</span>
            <input value={t} onChange={(e) => onChange(items.map((x, j) => j === i ? e.target.value : x))} style={{ flex: 1, padding: "11px 13px", borderRadius: 10, background: "var(--field)", border: "1px solid rgba(255,255,255,.1)", color: "#e8edf6", fontSize: 13.5, outline: "none" }} />
            <div onClick={() => onChange(items.filter((_, j) => j !== i))} style={{ width: 34, height: 34, borderRadius: 9, background: "rgba(255,255,255,.04)", border: "1px solid rgba(255,255,255,.08)", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--muted)", cursor: "pointer", flex: "none" }}>✕</div>
          </div>
        ))}
      </div>
      <div onClick={() => onChange([...items, ""])} style={{ display: "inline-flex", alignItems: "center", gap: 7, fontSize: 13, fontWeight: 700, color: ACCENT.c, cursor: "pointer" }}>{addLabel}</div>
    </div>
  );
}
