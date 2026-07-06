"use client";

import { useEffect, useState } from "react";
import { Shell, Card, BackLink } from "@/components/Shell";
import { api, errorMessage, AutoContactConfig, InactivityConfig, LlmBudgetConfig, LlmPricingConfig, MedicalExamConfig, QualityAlertsConfig, SchedulingConfig, SlaAlertsConfig } from "@/lib/api";

// Fila editable del precio de un modelo (los montos se editan como texto y se parsean al guardar).
type PriceRow = { model: string; input: string; output: string };

export default function ConfiguracionPage() {
  const [cfg, setCfg] = useState<AutoContactConfig | null>(null);
  const [timesText, setTimesText] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  const [inact, setInact] = useState<InactivityConfig | null>(null);
  const [savingInact, setSavingInact] = useState(false);
  const [msgInact, setMsgInact] = useState("");

  const [sched, setSched] = useState<SchedulingConfig | null>(null);
  const [savingSched, setSavingSched] = useState(false);
  const [msgSched, setMsgSched] = useState("");

  const [priceRows, setPriceRows] = useState<PriceRow[] | null>(null);
  const [defPrice, setDefPrice] = useState<{ input: string; output: string }>({ input: "0", output: "0" });
  const [budget, setBudget] = useState<LlmBudgetConfig | null>(null);
  const [savingCost, setSavingCost] = useState(false);
  const [msgCost, setMsgCost] = useState("");

  const [sla, setSla] = useState<SlaAlertsConfig | null>(null);
  const [savingSla, setSavingSla] = useState(false);
  const [msgSla, setMsgSla] = useState("");
  const [quality, setQuality] = useState<QualityAlertsConfig | null>(null);
  const [savingQuality, setSavingQuality] = useState(false);
  const [msgQuality, setMsgQuality] = useState("");
  const [medical, setMedical] = useState<MedicalExamConfig | null>(null);
  const [savingMedical, setSavingMedical] = useState(false);
  const [msgMedical, setMsgMedical] = useState("");

  useEffect(() => {
    api
      .getAutoContact()
      .then((c) => {
        setCfg(c);
        setTimesText((c.times || []).join(", "));
      })
      .catch((e) => setError(errorMessage(e)));
    api.getInactivity().then(setInact).catch((e) => setError(errorMessage(e)));
    api.getScheduling().then(setSched).catch((e) => setError(errorMessage(e)));
    api
      .getLlmPricing()
      .then((p) => {
        setPriceRows(
          Object.entries(p.models || {}).map(([model, v]) => ({
            model, input: String(v.input_per_1m ?? 0), output: String(v.output_per_1m ?? 0),
          })),
        );
        setDefPrice({ input: String(p.default?.input_per_1m ?? 0), output: String(p.default?.output_per_1m ?? 0) });
      })
      .catch((e) => setError(errorMessage(e)));
    api.getLlmBudget().then(setBudget).catch((e) => setError(errorMessage(e)));
    api.getSlaAlerts().then(setSla).catch((e) => setError(errorMessage(e)));
    api.getQualityAlerts().then(setQuality).catch((e) => setError(errorMessage(e)));
    api.getMedicalExamSettings().then(setMedical).catch((e) => setError(errorMessage(e)));
  }, []);

  const saveMedical = async () => {
    if (!medical) return;
    setSavingMedical(true);
    setMsgMedical("");
    try {
      const saved = await api.setMedicalExamSettings(medical);
      setMedical(saved);
      setMsgMedical("Configuración guardada ✅");
    } catch (e) {
      setMsgMedical("Error: " + errorMessage(e));
    } finally {
      setSavingMedical(false);
    }
  };

  const saveSla = async () => {
    if (!sla) return;
    setSavingSla(true);
    setMsgSla("");
    try {
      const saved = await api.setSlaAlerts({
        ...sla,
        turn_p95_ms: Math.max(0, Number(sla.turn_p95_ms) || 0),
      });
      setSla(saved);
      setMsgSla("Configuración guardada ✅");
    } catch (e) {
      setMsgSla("Error: " + errorMessage(e));
    } finally {
      setSavingSla(false);
    }
  };

  const saveQuality = async () => {
    if (!quality) return;
    setSavingQuality(true);
    setMsgQuality("");
    try {
      const saved = await api.setQualityAlerts({
        ...quality,
        sample: Math.min(200, Math.max(1, Number(quality.sample) || 20)),
        min_rate: Math.min(1, Math.max(0, Number(quality.min_rate) || 0.9)),
      });
      setQuality(saved);
      setMsgQuality("Configuración guardada ✅");
    } catch (e) {
      setMsgQuality("Error: " + errorMessage(e));
    } finally {
      setSavingQuality(false);
    }
  };

  const saveCost = async () => {
    if (priceRows === null || !budget) return;
    setSavingCost(true);
    setMsgCost("");
    try {
      const models: LlmPricingConfig["models"] = {};
      for (const r of priceRows) {
        if (!r.model.trim()) continue;
        models[r.model.trim()] = {
          input_per_1m: Math.max(0, Number(r.input) || 0),
          output_per_1m: Math.max(0, Number(r.output) || 0),
        };
      }
      await api.setLlmPricing({
        models,
        default: {
          input_per_1m: Math.max(0, Number(defPrice.input) || 0),
          output_per_1m: Math.max(0, Number(defPrice.output) || 0),
        },
      });
      const savedBudget = await api.setLlmBudget({
        ...budget,
        monthly_usd: Math.max(0, Number(budget.monthly_usd) || 0),
        alert_pct: Math.min(100, Math.max(1, Number(budget.alert_pct) || 80)),
      });
      setBudget(savedBudget);
      setMsgCost("Configuración guardada ✅");
    } catch (e) {
      setMsgCost("Error: " + errorMessage(e));
    } finally {
      setSavingCost(false);
    }
  };

  const saveSched = async () => {
    if (!sched) return;
    setSavingSched(true);
    setMsgSched("");
    try {
      const saved = await api.setScheduling({
        ...sched,
        slot_minutes: Math.max(15, Number(sched.slot_minutes) || 45),
        horizon_days: Math.max(1, Number(sched.horizon_days) || 7),
        options: Math.min(5, Math.max(1, Number(sched.options) || 3)),
      });
      setSched(saved);
      setMsgSched("Configuración guardada ✅");
    } catch (e) {
      setMsgSched("Error: " + errorMessage(e));
    } finally {
      setSavingSched(false);
    }
  };

  const saveInact = async () => {
    if (!inact) return;
    setSavingInact(true);
    setMsgInact("");
    try {
      const saved = await api.setInactivity({
        ...inact,
        reminder_minutes: Math.max(1, Number(inact.reminder_minutes) || 1),
        max_reminders: Math.max(0, Number(inact.max_reminders) || 0),
      });
      setInact(saved);
      setMsgInact("Configuración guardada ✅");
    } catch (e) {
      setMsgInact("Error: " + errorMessage(e));
    } finally {
      setSavingInact(false);
    }
  };

  const save = async () => {
    if (!cfg) return;
    // Parsea "11:00, 15:00" → ["11:00","15:00"], validando HH:MM.
    const times = timesText
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
    const valid = times.every((t) => /^([01]?\d|2[0-3]):[0-5]\d$/.test(t));
    if (!valid) {
      setMsg("Formato de horas inválido. Usa HH:MM separadas por coma (ej. 11:00, 15:00).");
      return;
    }
    setSaving(true);
    setMsg("");
    try {
      const saved = await api.setAutoContact({ ...cfg, times });
      setCfg(saved);
      setTimesText((saved.times || []).join(", "));
      setMsg("Configuración guardada ✅");
    } catch (e) {
      setMsg("Error: " + errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  const inputStyle: React.CSSProperties = {
    background: "var(--surface-2)",
    border: "1px solid var(--edge)",
    color: "var(--foreground)",
  };

  return (
    <Shell>
      <BackLink href="/" label="Vacantes" />
      <h1 className="text-2xl font-bold mb-1">Configuración</h1>
      <p className="text-sm mb-6" style={{ color: "var(--muted)" }}>
        Ajustes del agente de selección.
      </p>

      {error && <p style={{ color: "#dc2626" }}>Error: {error}</p>}

      {cfg && (
        <Card style={{ marginBottom: 16 }}>
          <h2 className="font-semibold mb-1">Contacto automático</h2>
          <p className="text-sm mb-4" style={{ color: "var(--muted)" }}>
            Cuando está activo, el agente contacta solo (por Telegram) a los candidatos aptos
            (que pasaron el filtro de CV) en los horarios indicados. No re-contacta a quien ya fue contactado.
          </p>

          <label className="flex items-center gap-3 mb-4 cursor-pointer">
            <input
              type="checkbox"
              checked={cfg.enabled}
              onChange={(e) => setCfg({ ...cfg, enabled: e.target.checked })}
              style={{ width: 18, height: 18, accentColor: "var(--accent)" }}
            />
            <span className="text-sm font-medium">Activar contacto automático</span>
          </label>

          <div className="grid gap-3" style={{ maxWidth: 360 }}>
            <div>
              <label className="text-sm block mb-1" style={{ color: "var(--muted)" }}>
                Horarios (HH:MM, separados por coma)
              </label>
              <input
                value={timesText}
                onChange={(e) => setTimesText(e.target.value)}
                placeholder="11:00, 15:00"
                className="px-3 py-2 rounded-lg w-full"
                style={inputStyle}
              />
            </div>
            <div>
              <label className="text-sm block mb-1" style={{ color: "var(--muted)" }}>
                Zona horaria
              </label>
              <input
                value={cfg.timezone}
                onChange={(e) => setCfg({ ...cfg, timezone: e.target.value })}
                placeholder="America/Lima"
                className="px-3 py-2 rounded-lg w-full"
                style={inputStyle}
              />
            </div>
          </div>

          <div className="mt-4 flex items-center gap-3">
            <button
              onClick={save}
              disabled={saving}
              className="px-4 py-2 rounded-lg font-medium"
              style={{ background: "var(--accent)", color: "var(--accent-ink)", opacity: saving ? 0.6 : 1 }}
            >
              {saving ? "Guardando…" : "Guardar"}
            </button>
            {msg && <span className="text-sm" style={{ color: "var(--accent)" }}>{msg}</span>}
          </div>
        </Card>
      )}

      {inact && (
        <Card>
          <h2 className="font-semibold mb-1">Inactividad del candidato</h2>
          <p className="text-sm mb-4" style={{ color: "var(--muted)" }}>
            Si el candidato deja de responder durante la entrevista o al enviar sus documentos, el
            agente le envía recordatorios y, si el silencio continúa, cierra la conversación
            marcándola como “No respondió”. No aplica al saludo inicial.
          </p>

          <label className="flex items-center gap-3 mb-4 cursor-pointer">
            <input
              type="checkbox"
              checked={inact.enabled}
              onChange={(e) => setInact({ ...inact, enabled: e.target.checked })}
              style={{ width: 18, height: 18, accentColor: "var(--accent)" }}
            />
            <span className="text-sm font-medium">Activar manejo de inactividad</span>
          </label>

          <div className="grid gap-3" style={{ maxWidth: 360 }}>
            <div>
              <label className="text-sm block mb-1" style={{ color: "var(--muted)" }}>
                Minutos de espera antes de recordar
              </label>
              <input
                type="number"
                min={1}
                value={inact.reminder_minutes}
                onChange={(e) => setInact({ ...inact, reminder_minutes: Number(e.target.value) })}
                className="px-3 py-2 rounded-lg w-full"
                style={inputStyle}
              />
            </div>
            <div>
              <label className="text-sm block mb-1" style={{ color: "var(--muted)" }}>
                Máximo de recordatorios antes de cerrar
              </label>
              <input
                type="number"
                min={0}
                value={inact.max_reminders}
                onChange={(e) => setInact({ ...inact, max_reminders: Number(e.target.value) })}
                className="px-3 py-2 rounded-lg w-full"
                style={inputStyle}
              />
            </div>
          </div>

          <div className="mt-4 flex items-center gap-3">
            <button
              onClick={saveInact}
              disabled={savingInact}
              className="px-4 py-2 rounded-lg font-medium"
              style={{ background: "var(--accent)", color: "var(--accent-ink)", opacity: savingInact ? 0.6 : 1 }}
            >
              {savingInact ? "Guardando…" : "Guardar"}
            </button>
            {msgInact && <span className="text-sm" style={{ color: "var(--accent)" }}>{msgInact}</span>}
          </div>
        </Card>
      )}

      {sched && (
        <Card style={{ marginTop: 16 }}>
          <h2 className="font-semibold mb-1">Agendamiento de entrevista</h2>
          <p className="text-sm mb-4" style={{ color: "var(--muted)" }}>
            Cuando RR.HH. pulsa “Continuar”, el agente coordina por Telegram un horario con el candidato
            según la disponibilidad del reclutador (Google Calendar) y crea la reunión con enlace.
          </p>

          <label className="flex items-center gap-3 mb-4 cursor-pointer">
            <input type="checkbox" checked={sched.enabled}
              onChange={(e) => setSched({ ...sched, enabled: e.target.checked })}
              style={{ width: 18, height: 18, accentColor: "var(--accent)" }} />
            <span className="text-sm font-medium">Activar agendamiento automático al continuar</span>
          </label>

          <div className="grid gap-3" style={{ maxWidth: 460 }}>
            <div>
              <label className="text-sm block mb-1" style={{ color: "var(--muted)" }}>Proveedor</label>
              <select value={sched.provider} onChange={(e) => setSched({ ...sched, provider: e.target.value })}
                className="px-3 py-2 rounded-lg w-full" style={inputStyle}>
                <option value="simulated">Simulado (sin credenciales)</option>
                <option value="google">Google Calendar + Sheets</option>
              </select>
            </div>
            <div className="grid gap-3" style={{ gridTemplateColumns: "1fr 1fr" }}>
              <div>
                <label className="text-sm block mb-1" style={{ color: "var(--muted)" }}>Duración (min)</label>
                <input type="number" min={15} value={sched.slot_minutes}
                  onChange={(e) => setSched({ ...sched, slot_minutes: Number(e.target.value) })}
                  className="px-3 py-2 rounded-lg w-full" style={inputStyle} />
              </div>
              <div>
                <label className="text-sm block mb-1" style={{ color: "var(--muted)" }}>Opciones a proponer</label>
                <input type="number" min={1} max={5} value={sched.options}
                  onChange={(e) => setSched({ ...sched, options: Number(e.target.value) })}
                  className="px-3 py-2 rounded-lg w-full" style={inputStyle} />
              </div>
            </div>
            <div>
              <label className="text-sm block mb-1" style={{ color: "var(--muted)" }}>
                Franjas de auto-contacto (HH:MM-HH:MM, separadas por coma)
              </label>
              <input
                value={(sched.work_windows || []).map((w) => `${w[0]}-${w[1]}`).join(", ")}
                placeholder="10:30-12:00, 15:00-17:00"
                onChange={(e) =>
                  setSched({
                    ...sched,
                    work_windows: e.target.value
                      .split(",")
                      .map((s) => s.trim().split("-").map((t) => t.trim()))
                      .filter((p) => p.length === 2 && p[0] && p[1]),
                  })
                }
                className="px-3 py-2 rounded-lg w-full" style={inputStyle} />
              <p className="text-xs mt-1" style={{ color: "var(--muted)" }}>
                Solo se contacta automáticamente a los aptos dentro de estas franjas (el fin es exclusivo).
              </p>
            </div>
            <div className="grid gap-3" style={{ gridTemplateColumns: "1fr 1fr" }}>
              <div>
                <label className="text-sm block mb-1" style={{ color: "var(--muted)" }}>Días hábiles (1=Lun..7=Dom)</label>
                <input value={(sched.work_days || []).join(",")}
                  onChange={(e) => setSched({ ...sched, work_days: e.target.value.split(",").map((s) => parseInt(s.trim(), 10)).filter((n) => n >= 1 && n <= 7) })}
                  className="px-3 py-2 rounded-lg w-full" style={inputStyle} />
              </div>
              <div>
                <label className="text-sm block mb-1" style={{ color: "var(--muted)" }}>Horizonte (días)</label>
                <input type="number" min={1} value={sched.horizon_days}
                  onChange={(e) => setSched({ ...sched, horizon_days: Number(e.target.value) })}
                  className="px-3 py-2 rounded-lg w-full" style={inputStyle} />
              </div>
            </div>
            <div>
              <label className="text-sm block mb-1" style={{ color: "var(--muted)" }}>Zona horaria</label>
              <input value={sched.timezone} placeholder="America/Lima"
                onChange={(e) => setSched({ ...sched, timezone: e.target.value })}
                className="px-3 py-2 rounded-lg w-full" style={inputStyle} />
            </div>
          </div>

          <div className="mt-4 flex items-center gap-3">
            <button onClick={saveSched} disabled={savingSched} className="px-4 py-2 rounded-lg font-medium"
              style={{ background: "var(--accent)", color: "var(--accent-ink)", opacity: savingSched ? 0.6 : 1 }}>
              {savingSched ? "Guardando…" : "Guardar"}
            </button>
            {msgSched && <span className="text-sm" style={{ color: "var(--accent)" }}>{msgSched}</span>}
          </div>
        </Card>
      )}

      {medical && (
        <Card style={{ marginTop: 16 }}>
          <h2 className="font-semibold mb-1">Examen médico pre-contratación</h2>
          <p className="text-sm mb-4" style={{ color: "var(--muted)" }}>
            Cuando está activo, aprobar la entrevista con gerencia NO contrata directo: el candidato
            pasa a “Examen médico”, RR.HH. programa la cita (fecha + clínica, notificada por correo y
            Telegram) y con el resultado apto se concreta la contratación. Apagado, el proceso cierra
            en gerencia como siempre.
          </p>
          <label className="flex items-center gap-3 mb-4 cursor-pointer">
            <input type="checkbox" checked={medical.enabled}
              onChange={(e) => setMedical({ ...medical, enabled: e.target.checked })}
              style={{ width: 18, height: 18, accentColor: "var(--accent)" }} />
            <span className="text-sm font-medium">Exigir examen médico antes de contratar</span>
          </label>
          <div className="mt-2 flex items-center gap-3">
            <button onClick={saveMedical} disabled={savingMedical} className="px-4 py-2 rounded-lg font-medium"
              style={{ background: "var(--accent)", color: "var(--accent-ink)", opacity: savingMedical ? 0.6 : 1 }}>
              {savingMedical ? "Guardando…" : "Guardar"}
            </button>
            {msgMedical && <span className="text-sm" style={{ color: "var(--accent)" }}>{msgMedical}</span>}
          </div>
        </Card>
      )}

      {priceRows !== null && budget && (
        <Card style={{ marginTop: 16 }}>
          <h2 className="font-semibold mb-1">Costos y presupuesto LLM</h2>
          <p className="text-sm mb-4" style={{ color: "var(--muted)" }}>
            Precio en USD por millón de tokens de cada modelo (los publica tu proveedor: Groq, OpenAI, etc.).
            Con esto el dashboard estima el costo real por vacante y global. El presupuesto mensual
            genera una alerta operativa (y un correo, si lo configuras) al alcanzar el umbral.
          </p>

          <div className="grid gap-2" style={{ maxWidth: 560 }}>
            <div className="grid gap-2 text-xs" style={{ gridTemplateColumns: "2fr 1fr 1fr 32px", color: "var(--muted)" }}>
              <span>Modelo</span><span>Entrada $/1M</span><span>Salida $/1M</span><span />
            </div>
            {priceRows.map((r, i) => (
              <div key={i} className="grid gap-2" style={{ gridTemplateColumns: "2fr 1fr 1fr 32px" }}>
                <input value={r.model} placeholder="qwen/qwen3-32b"
                  onChange={(e) => setPriceRows(priceRows.map((x, j) => (j === i ? { ...x, model: e.target.value } : x)))}
                  className="px-3 py-2 rounded-lg w-full" style={inputStyle} />
                <input value={r.input} inputMode="decimal"
                  onChange={(e) => setPriceRows(priceRows.map((x, j) => (j === i ? { ...x, input: e.target.value } : x)))}
                  className="px-3 py-2 rounded-lg w-full" style={inputStyle} />
                <input value={r.output} inputMode="decimal"
                  onChange={(e) => setPriceRows(priceRows.map((x, j) => (j === i ? { ...x, output: e.target.value } : x)))}
                  className="px-3 py-2 rounded-lg w-full" style={inputStyle} />
                <button onClick={() => setPriceRows(priceRows.filter((_, j) => j !== i))} title="Quitar modelo"
                  className="rounded-lg" style={{ ...inputStyle, cursor: "pointer" }}>✕</button>
              </div>
            ))}
            <button onClick={() => setPriceRows([...priceRows, { model: "", input: "0", output: "0" }])}
              className="px-3 py-2 rounded-lg text-sm" style={{ ...inputStyle, cursor: "pointer", justifySelf: "start" }}>
              + Añadir modelo
            </button>
            <div className="grid gap-2 mt-2" style={{ gridTemplateColumns: "2fr 1fr 1fr 32px" }}>
              <span className="text-sm self-center" style={{ color: "var(--muted)" }}>Default (modelos sin fila)</span>
              <input value={defPrice.input} inputMode="decimal"
                onChange={(e) => setDefPrice({ ...defPrice, input: e.target.value })}
                className="px-3 py-2 rounded-lg w-full" style={inputStyle} />
              <input value={defPrice.output} inputMode="decimal"
                onChange={(e) => setDefPrice({ ...defPrice, output: e.target.value })}
                className="px-3 py-2 rounded-lg w-full" style={inputStyle} />
              <span />
            </div>
          </div>

          <div className="mt-5 pt-4" style={{ borderTop: "1px solid var(--edge)" }}>
            <label className="flex items-center gap-3 mb-3 cursor-pointer">
              <input type="checkbox" checked={budget.enabled}
                onChange={(e) => setBudget({ ...budget, enabled: e.target.checked })}
                style={{ width: 18, height: 18, accentColor: "var(--accent)" }} />
              <span className="text-sm font-medium">Activar presupuesto mensual</span>
            </label>
            <div className="grid gap-3" style={{ gridTemplateColumns: "1fr 1fr 2fr", maxWidth: 560 }}>
              <div>
                <label className="text-sm block mb-1" style={{ color: "var(--muted)" }}>Presupuesto (USD/mes)</label>
                <input type="number" min={0} value={budget.monthly_usd}
                  onChange={(e) => setBudget({ ...budget, monthly_usd: Number(e.target.value) })}
                  className="px-3 py-2 rounded-lg w-full" style={inputStyle} />
              </div>
              <div>
                <label className="text-sm block mb-1" style={{ color: "var(--muted)" }}>Alertar al (%)</label>
                <input type="number" min={1} max={100} value={budget.alert_pct}
                  onChange={(e) => setBudget({ ...budget, alert_pct: Number(e.target.value) })}
                  className="px-3 py-2 rounded-lg w-full" style={inputStyle} />
              </div>
              <div>
                <label className="text-sm block mb-1" style={{ color: "var(--muted)" }}>Correo de aviso (opcional)</label>
                <input value={budget.notify_email} placeholder="ops@empresa.com"
                  onChange={(e) => setBudget({ ...budget, notify_email: e.target.value })}
                  className="px-3 py-2 rounded-lg w-full" style={inputStyle} />
              </div>
            </div>
          </div>

          <div className="mt-4 flex items-center gap-3">
            <button onClick={saveCost} disabled={savingCost} className="px-4 py-2 rounded-lg font-medium"
              style={{ background: "var(--accent)", color: "var(--accent-ink)", opacity: savingCost ? 0.6 : 1 }}>
              {savingCost ? "Guardando…" : "Guardar"}
            </button>
            {msgCost && <span className="text-sm" style={{ color: "var(--accent)" }}>{msgCost}</span>}
          </div>
        </Card>
      )}

      {sla && (
        <Card style={{ marginTop: 16 }}>
          <h2 className="font-semibold mb-1">Alertas SLA (push)</h2>
          <p className="text-sm mb-4" style={{ color: "var(--muted)" }}>
            Envía un correo cuando una condición operativa se incumple (una vez por condición por día):
            alertas del panel de observabilidad y, si defines un umbral, la latencia p95 del turno del
            candidato en las últimas 24 horas.
          </p>
          <label className="flex items-center gap-3 mb-3 cursor-pointer">
            <input type="checkbox" checked={sla.enabled}
              onChange={(e) => setSla({ ...sla, enabled: e.target.checked })}
              style={{ width: 18, height: 18, accentColor: "var(--accent)" }} />
            <span className="text-sm font-medium">Activar alertas SLA</span>
          </label>
          <label className="flex items-center gap-3 mb-3 cursor-pointer">
            <input type="checkbox" checked={sla.ops_alerts}
              onChange={(e) => setSla({ ...sla, ops_alerts: e.target.checked })}
              style={{ width: 18, height: 18, accentColor: "var(--accent)" }} />
            <span className="text-sm font-medium">Incluir alertas operativas (envíos detenidos, reuniones sin enlace, …)</span>
          </label>
          <div className="grid gap-3" style={{ gridTemplateColumns: "1fr 2fr", maxWidth: 560 }}>
            <div>
              <label className="text-sm block mb-1" style={{ color: "var(--muted)" }}>Umbral p95 del turno (ms, 0 = sin umbral)</label>
              <input type="number" min={0} value={sla.turn_p95_ms}
                onChange={(e) => setSla({ ...sla, turn_p95_ms: Number(e.target.value) })}
                className="px-3 py-2 rounded-lg w-full" style={inputStyle} />
            </div>
            <div>
              <label className="text-sm block mb-1" style={{ color: "var(--muted)" }}>Correo de aviso</label>
              <input value={sla.notify_email} placeholder="ops@empresa.com"
                onChange={(e) => setSla({ ...sla, notify_email: e.target.value })}
                className="px-3 py-2 rounded-lg w-full" style={inputStyle} />
            </div>
          </div>
          <div className="mt-4 flex items-center gap-3">
            <button onClick={saveSla} disabled={savingSla} className="px-4 py-2 rounded-lg font-medium"
              style={{ background: "var(--accent)", color: "var(--accent-ink)", opacity: savingSla ? 0.6 : 1 }}>
              {savingSla ? "Guardando…" : "Guardar"}
            </button>
            {msgSla && <span className="text-sm" style={{ color: "var(--accent)" }}>{msgSla}</span>}
          </div>
        </Card>
      )}

      {quality && (
        <Card style={{ marginTop: 16 }}>
          <h2 className="font-semibold mb-1">Calidad de las respuestas (IA)</h2>
          <p className="text-sm mb-4" style={{ color: "var(--muted)" }}>
            Un juez LLM revisa a diario una muestra de las respuestas reales del bot y mide su
            <strong> fundamentación</strong> (¿se apoya solo en la info de la vacante?) y
            <strong> relevancia</strong>. Persiste la tendencia en Observabilidad y envía un correo
            si la fundamentación cae bajo el mínimo. Requiere trazas activas (LLM_TRACE_ENABLED).
          </p>
          <label className="flex items-center gap-3 mb-3 cursor-pointer">
            <input type="checkbox" checked={quality.enabled}
              onChange={(e) => setQuality({ ...quality, enabled: e.target.checked })}
              style={{ width: 18, height: 18, accentColor: "var(--accent)" }} />
            <span className="text-sm font-medium">Activar medición continua de calidad</span>
          </label>
          <div className="grid gap-3" style={{ gridTemplateColumns: "1fr 1fr 2fr", maxWidth: 640 }}>
            <div>
              <label className="text-sm block mb-1" style={{ color: "var(--muted)" }}>Muestra por día</label>
              <input type="number" min={1} max={200} value={quality.sample}
                onChange={(e) => setQuality({ ...quality, sample: Number(e.target.value) })}
                className="px-3 py-2 rounded-lg w-full" style={inputStyle} />
            </div>
            <div>
              <label className="text-sm block mb-1" style={{ color: "var(--muted)" }}>Mínimo fundamentación (%)</label>
              <input type="number" min={0} max={100} value={Math.round(quality.min_rate * 100)}
                onChange={(e) => setQuality({ ...quality, min_rate: Number(e.target.value) / 100 })}
                className="px-3 py-2 rounded-lg w-full" style={inputStyle} />
            </div>
            <div>
              <label className="text-sm block mb-1" style={{ color: "var(--muted)" }}>Correo de aviso</label>
              <input value={quality.notify_email} placeholder="ops@empresa.com"
                onChange={(e) => setQuality({ ...quality, notify_email: e.target.value })}
                className="px-3 py-2 rounded-lg w-full" style={inputStyle} />
            </div>
          </div>
          <div className="mt-4 flex items-center gap-3">
            <button onClick={saveQuality} disabled={savingQuality} className="px-4 py-2 rounded-lg font-medium"
              style={{ background: "var(--accent)", color: "var(--accent-ink)", opacity: savingQuality ? 0.6 : 1 }}>
              {savingQuality ? "Guardando…" : "Guardar"}
            </button>
            {msgQuality && <span className="text-sm" style={{ color: "var(--accent)" }}>{msgQuality}</span>}
          </div>
        </Card>
      )}
    </Shell>
  );
}
