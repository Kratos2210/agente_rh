"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { api, Recruiter } from "@/lib/api";
import { avatarColor, initials } from "@/lib/stages";

const MONO = "var(--font-jetbrains), monospace";
const EMPTY = { name: "", role: "Reclutador", company: "", email: "", phone: "", telegram_chat_id: "", calendar_id: "primary", location: "", active: true };

const field: React.CSSProperties = {
  width: "100%", padding: "11px 13px", borderRadius: 10, background: "var(--field)",
  border: "1px solid rgba(255,255,255,.1)", color: "#e8edf6", fontSize: 13.5, outline: "none",
};

export default function EquipoPage() {
  const [recruiters, setRecruiters] = useState<Recruiter[]>([]);
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState({ ...EMPTY });
  const [saving, setSaving] = useState(false);

  const load = () => { api.listRecruiters().then(setRecruiters).catch(() => {}); };
  useEffect(load, []);

  const openNew = () => { setForm({ ...EMPTY }); setEditingId(null); setAdding(true); };
  const openEdit = (r: Recruiter) => {
    setForm({ name: r.name, role: r.role, company: r.company, email: r.email, phone: r.phone,
      telegram_chat_id: r.telegram_chat_id, calendar_id: r.calendar_id || "primary", location: r.location || "", active: r.active });
    setEditingId(r.id); setAdding(true);
  };
  const save = async () => {
    if (!form.name.trim()) return;
    setSaving(true);
    try {
      if (editingId) await api.updateRecruiter(editingId, form);
      else await api.createRecruiter(form);
      setForm({ ...EMPTY }); setEditingId(null); setAdding(false); load();
    } finally { setSaving(false); }
  };

  return (
    <Shell width={1080}>
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 16, marginBottom: 24 }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, color: "var(--ac)", marginBottom: 6 }}>Reclutadores</div>
          <h1 style={{ margin: 0, fontSize: 30, fontWeight: 800, letterSpacing: "-.03em", color: "var(--heading)" }}>Equipo de RR.HH.</h1>
          <p style={{ margin: "8px 0 0", color: "var(--muted)", fontSize: 14 }}>Asigna responsables a cada vacante y sigue su carga de trabajo.</p>
        </div>
        <button onClick={openNew} style={{
          display: "flex", alignItems: "center", gap: 9, padding: "12px 18px", borderRadius: 11,
          background: "linear-gradient(135deg,var(--ac),var(--ac-btn))", color: "#fff", fontWeight: 700, fontSize: 13.5, cursor: "pointer", border: "none",
        }}>+ Invitar miembro</button>
      </div>

      {adding && (
        <div style={{ padding: 20, borderRadius: 16, background: "var(--card)", border: "1px solid var(--edge)", marginBottom: 16 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: "#eef2f9", marginBottom: 12 }}>{editingId ? "Editar miembro" : "Nuevo miembro"}</div>
          <div style={{ display: "grid", gap: 10 }}>
            <input placeholder="Nombre *" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} style={field} />
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <input placeholder="Cargo" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} style={field} />
              <input placeholder="Empresa" value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })} style={field} />
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <input placeholder="Email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} style={field} />
              <input placeholder="Teléfono" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} style={field} />
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <input placeholder="Chat de Telegram (id)" value={form.telegram_chat_id} onChange={(e) => setForm({ ...form, telegram_chat_id: e.target.value })} style={field} />
              <input placeholder="Google Calendar (id/email)" value={form.calendar_id} onChange={(e) => setForm({ ...form, calendar_id: e.target.value })} style={field} />
            </div>
            <div style={{ marginTop: 10 }}>
              <input placeholder="Dirección de oficina (entrevistas presenciales)" value={form.location} onChange={(e) => setForm({ ...form, location: e.target.value })} style={{ ...field, width: "100%" }} />
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <button onClick={save} disabled={saving} style={{ padding: "10px 18px", borderRadius: 10, background: "var(--ac)", color: "var(--ac-ink)", fontWeight: 700, border: "none", cursor: "pointer", opacity: saving ? 0.6 : 1 }}>
                {saving ? "Guardando…" : editingId ? "Guardar cambios" : "Guardar"}
              </button>
              <button onClick={() => { setAdding(false); setEditingId(null); }} style={{ fontSize: 13, color: "var(--muted)", background: "none", border: "none", cursor: "pointer" }}>Cancelar</button>
            </div>
          </div>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        {recruiters.map((m) => {
          const active = m.active;
          return (
            <div key={m.id} style={{ padding: 20, borderRadius: 16, background: "linear-gradient(180deg,rgba(255,255,255,.035),rgba(255,255,255,.012))", border: "1px solid var(--edge)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 13, marginBottom: 16 }}>
                <div style={{ width: 48, height: 48, borderRadius: 13, background: avatarColor(m.name), display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 800, color: "#0a0e16", fontSize: 17 }}>{initials(m.name)}</div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 16, fontWeight: 700, color: "var(--heading)" }}>{m.name}</div>
                  <div style={{ fontSize: 12.5, color: "var(--muted)" }}>{m.role}{m.company ? ` · ${m.company}` : ""}</div>
                </div>
                <div onClick={() => openEdit(m)} title="Editar" style={{ padding: "4px 10px", borderRadius: 7, fontSize: 11, fontWeight: 700, background: active ? "rgba(52,211,153,.12)" : "rgba(148,163,184,.14)", color: active ? "#34d399" : "#94a3b8", cursor: "pointer" }}>
                  {active ? "Activa" : "Inactiva"}
                </div>
              </div>
              <div style={{ display: "flex", gap: 10, marginBottom: 14 }}>
                <div style={{ flex: 1, padding: 12, borderRadius: 11, background: "rgba(255,255,255,.025)", textAlign: "center" }}>
                  <div style={{ fontFamily: MONO, fontSize: 20, fontWeight: 700, color: "var(--ac)" }}>{m.open_vacancies ?? 0}</div>
                  <div style={{ fontSize: 10.5, color: "var(--muted)", fontWeight: 600, marginTop: 2 }}>vacantes abiertas</div>
                </div>
                <div style={{ flex: 1, padding: 12, borderRadius: 11, background: "rgba(255,255,255,.025)", textAlign: "center" }}>
                  <div style={{ fontFamily: MONO, fontSize: 20, fontWeight: 700, color: "#34d399" }}>{m.active_candidates ?? 0}</div>
                  <div style={{ fontSize: 10.5, color: "var(--muted)", fontWeight: 600, marginTop: 2 }}>candidatos activos</div>
                </div>
              </div>
              <div style={{ fontSize: 12, color: "#8a94a8", lineHeight: 1.7, borderTop: "1px solid var(--edge-soft)", paddingTop: 12 }}>
                ✉ {m.email || "—"}<br />☎ {m.phone || "—"}
              </div>
            </div>
          );
        })}
      </div>
      {recruiters.length === 0 && <p style={{ color: "var(--muted)" }}>Sin reclutadores aún. Invita al primero.</p>}
    </Shell>
  );
}
