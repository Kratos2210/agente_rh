"use client";

import { useState } from "react";
import { api, errorMessage, Vacancy } from "@/lib/api";

// Editor colapsable del kit de onboarding de una vacante (materiales/guías del primer día).
// Autocontenido (estado propio sembrado del vacancy) para reusarlo en el detalle de la vacante
// y en el panel "Kits por vacante" de la vista Onboarding. `onSaved` refresca al que lo aloja.
export function OnboardingKitEditor({
  vacancy,
  headerLabel = "Kit de onboarding",
  defaultOpen = false,
  onSaved,
}: {
  vacancy: Vacancy;
  headerLabel?: string;
  defaultOpen?: boolean;
  onSaved?: () => void;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [seeded, setSeeded] = useState(false);
  const [welcome, setWelcome] = useState("");
  const [rows, setRows] = useState<{ title: string; url: string; note: string }[]>([]);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  // Se siembra del vacancy al abrir (una vez) para que un refresco externo no pise lo editado.
  const toggle = () => {
    if (!open && !seeded) {
      const kit = vacancy.onboarding_kit || {};
      setWelcome(kit.welcome || "");
      setRows((kit.materials || []).map((m) => ({ title: m.title || "", url: m.url || "", note: m.note || "" })));
      setSeeded(true);
    }
    setOpen((v) => !v);
  };

  const save = async () => {
    setSaving(true);
    setMsg("");
    try {
      const materials = rows
        .filter((r) => r.title.trim())
        .map((r) => ({ title: r.title.trim(), url: r.url.trim(), note: r.note.trim() }));
      await api.setOnboardingKit(vacancy.id, { welcome: welcome.trim(), materials });
      setMsg("Kit guardado ✅");
      onSaved?.();
    } catch (e) {
      setMsg(`Error: ${errorMessage(e)}`);
    } finally {
      setSaving(false);
    }
  };

  const count = vacancy.onboarding_kit?.materials?.length || 0;
  const configured = !!(vacancy.onboarding_kit?.welcome || count);
  const inp = { padding: "9px 12px", borderRadius: 9, background: "rgba(255,255,255,.03)", border: "1px solid var(--edge)", color: "#eef2f9", fontSize: 13 } as const;

  return (
    <div style={{ borderRadius: 15, background: "rgba(255,255,255,.02)", border: "1px solid var(--edge)", overflow: "hidden" }}>
      <div onClick={toggle} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "16px 20px", cursor: "pointer" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 15, fontWeight: 700, color: "#eef2f9" }}>
          <span style={{ color: "var(--ac)" }}>🚀</span>{headerLabel}
          {configured ? (
            <span style={{ fontSize: 12, fontWeight: 500, color: "var(--muted)" }}>
              {count} material(es) — se envía al contratado en su fecha de ingreso
            </span>
          ) : (
            <span style={{ fontSize: 11.5, fontWeight: 700, color: "#fbbf24", background: "rgba(251,191,36,.13)", padding: "2px 9px", borderRadius: 20 }}>Sin kit</span>
          )}
        </div>
        <span style={{ color: "var(--muted)", fontSize: 13, transform: open ? "rotate(0deg)" : "rotate(-90deg)" }}>▾</span>
      </div>
      {open && (
        <div style={{ padding: "4px 24px 24px", borderTop: "1px solid var(--edge-soft)" }}>
          <div style={{ paddingTop: 18, display: "grid", gap: 12 }}>
            <div>
              <label style={{ fontSize: 12.5, color: "var(--muted)", display: "block", marginBottom: 6 }}>Mensaje de bienvenida</label>
              <textarea value={welcome} onChange={(e) => setWelcome(e.target.value)} rows={3}
                placeholder="Hoy comienza tu primer día con nosotros…"
                style={{ width: "100%", padding: "10px 12px", borderRadius: 9, background: "rgba(255,255,255,.03)", border: "1px solid var(--edge)", color: "#eef2f9", fontSize: 13, resize: "vertical" }} />
            </div>
            <div style={{ display: "grid", gap: 8 }}>
              <div style={{ display: "grid", gridTemplateColumns: "2fr 2fr 2fr 32px", gap: 8, fontSize: 12, color: "var(--muted)" }}>
                <span>Título</span><span>URL (opcional)</span><span>Nota (opcional)</span><span />
              </div>
              {rows.map((r, i) => (
                <div key={i} style={{ display: "grid", gridTemplateColumns: "2fr 2fr 2fr 32px", gap: 8 }}>
                  <input value={r.title} placeholder="Guía de bienvenida" onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, title: e.target.value } : x)))} style={inp} />
                  <input value={r.url} placeholder="https://…" onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, url: e.target.value } : x)))} style={inp} />
                  <input value={r.note} placeholder="Léelo antes del primer día" onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, note: e.target.value } : x)))} style={inp} />
                  <button onClick={() => setRows(rows.filter((_, j) => j !== i))} title="Quitar material"
                    style={{ borderRadius: 9, background: "rgba(255,255,255,.03)", border: "1px solid var(--edge)", color: "#cfd8e8", cursor: "pointer" }}>✕</button>
                </div>
              ))}
              <button onClick={() => setRows([...rows, { title: "", url: "", note: "" }])}
                style={{ justifySelf: "start", padding: "8px 14px", borderRadius: 9, background: "rgba(255,255,255,.04)", border: "1px solid var(--edge)", color: "#cfd8e8", fontSize: 12.5, fontWeight: 700, cursor: "pointer" }}>
                + Añadir material
              </button>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <button onClick={save} disabled={saving}
                style={{ padding: "10px 18px", borderRadius: 10, background: "var(--ac)", color: "var(--ac-ink)", fontWeight: 700, border: "none", cursor: "pointer", opacity: saving ? 0.6 : 1 }}>
                {saving ? "Guardando…" : "Guardar kit"}
              </button>
              {msg && <span style={{ fontSize: 13, color: "var(--ac)" }}>{msg}</span>}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
