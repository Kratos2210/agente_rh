"use client";

import { useEffect, useState } from "react";
import { Shell, Card, BackLink } from "@/components/Shell";
import { api, AutoContactConfig, InactivityConfig, SchedulingConfig } from "@/lib/api";

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

  useEffect(() => {
    api
      .getAutoContact()
      .then((c) => {
        setCfg(c);
        setTimesText((c.times || []).join(", "));
      })
      .catch((e) => setError(String(e)));
    api.getInactivity().then(setInact).catch((e) => setError(String(e)));
    api.getScheduling().then(setSched).catch((e) => setError(String(e)));
  }, []);

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
      setMsgSched("Error: " + String(e));
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
      setMsgInact("Error: " + String(e));
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
      setMsg("Error: " + String(e));
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
    </Shell>
  );
}
