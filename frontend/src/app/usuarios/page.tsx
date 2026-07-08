"use client";

import { useEffect, useState } from "react";
import { Shell, Card, BackLink } from "@/components/Shell";
import { api, errorMessage, User } from "@/lib/api";
import { isAdmin, getUser } from "@/lib/auth";

// Roles jerárquicos del backend (api/auth.ROLES) con etiqueta legible.
const ROLES: { value: string; label: string; hint: string }[] = [
  { value: "viewer", label: "Viewer", hint: "solo lectura (observabilidad)" },
  { value: "recruiter", label: "Recruiter", hint: "opera el proceso (contactar, decidir)" },
  { value: "admin", label: "Admin", hint: "configuración, costos y usuarios" },
];

const ROLE_LABEL: Record<string, string> = Object.fromEntries(ROLES.map((r) => [r.value, r.label]));

const inputStyle: React.CSSProperties = {
  background: "var(--surface-2)",
  border: "1px solid var(--edge)",
  color: "var(--foreground)",
};

function when(iso?: string): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString("es-PE", {
      day: "2-digit", month: "short", year: "numeric", timeZone: "America/Lima",
    });
  } catch {
    return iso;
  }
}

export default function UsuariosPage() {
  const [allowed, setAllowed] = useState<boolean | null>(null);
  const [meId, setMeId] = useState<string>("");
  const [users, setUsers] = useState<User[] | null>(null);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState<string | null>(null); // id de la fila en curso

  // Formulario de alta.
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState("viewer");
  const [password, setPassword] = useState("");
  const [creating, setCreating] = useState(false);

  const load = () => {
    api.getUsers().then(setUsers).catch((e) => setError(errorMessage(e)));
  };

  useEffect(() => {
    setAllowed(isAdmin());
    setMeId(getUser()?.id || "");
    if (isAdmin()) load();
  }, []);

  const create = async () => {
    setCreating(true);
    setMsg("");
    setError("");
    try {
      await api.createUser({ email: email.trim().toLowerCase(), password, name: name.trim(), role });
      setMsg(`Usuario ${email.trim().toLowerCase()} creado ✅`);
      setEmail(""); setName(""); setPassword(""); setRole("viewer");
      load();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setCreating(false);
    }
  };

  const patch = async (u: User, body: Partial<{ active: boolean; role: string; password: string }>, ok: string) => {
    setBusy(u.id);
    setMsg("");
    setError("");
    try {
      await api.updateUser(u.id, body);
      setMsg(ok);
      load();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  const resetPassword = (u: User) => {
    const next = window.prompt(`Nueva contraseña para ${u.email} (mínimo 8 caracteres):`);
    if (next === null) return; // cancelado
    if (next.length < 8) { setError("La contraseña debe tener al menos 8 caracteres"); return; }
    patch(u, { password: next }, `Contraseña de ${u.email} actualizada ✅`);
  };

  if (allowed === false)
    return (
      <Shell>
        <BackLink href="/" label="Vacantes" />
        <p style={{ color: "var(--muted)" }}>Esta sección es solo para administradores.</p>
      </Shell>
    );

  const canCreate = email.includes("@") && password.length >= 8 && !creating;

  return (
    <Shell>
      <BackLink href="/" label="Vacantes" />
      <h1 className="text-2xl font-bold mb-1">Usuarios</h1>
      <p className="text-sm mb-6" style={{ color: "var(--muted)" }}>
        Da de alta al segundo operador y gestiona los accesos de tu empresa. Desactivar a un usuario
        corta su sesión de inmediato. Las contraseñas se guardan cifradas y nunca se muestran.
      </p>

      {error && <p style={{ color: "#f87171", marginBottom: 14 }}>Error: {error}</p>}
      {msg && <p className="text-sm mb-4" style={{ color: "var(--accent)" }}>{msg}</p>}

      {/* ── Alta de usuario ─────────────────────────────────────────── */}
      <Card style={{ marginBottom: 18 }}>
        <h2 className="font-semibold mb-1">Nuevo usuario</h2>
        <p className="text-sm mb-4" style={{ color: "var(--muted)" }}>
          Se crea en tu empresa. El email es único; la contraseña debe tener al menos 8 caracteres.
        </p>
        <div className="grid gap-3" style={{ gridTemplateColumns: "2fr 2fr 1.3fr 1.5fr", maxWidth: 720 }}>
          <div>
            <label className="text-sm block mb-1" style={{ color: "var(--muted)" }}>Email</label>
            <input value={email} placeholder="operador@empresa.com" type="email"
              onChange={(e) => setEmail(e.target.value)}
              className="px-3 py-2 rounded-lg w-full" style={inputStyle} />
          </div>
          <div>
            <label className="text-sm block mb-1" style={{ color: "var(--muted)" }}>Nombre (opcional)</label>
            <input value={name} placeholder="Nombre y apellido"
              onChange={(e) => setName(e.target.value)}
              className="px-3 py-2 rounded-lg w-full" style={inputStyle} />
          </div>
          <div>
            <label className="text-sm block mb-1" style={{ color: "var(--muted)" }}>Rol</label>
            <select value={role} onChange={(e) => setRole(e.target.value)}
              className="px-3 py-2 rounded-lg w-full" style={inputStyle}>
              {ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
            </select>
          </div>
          <div>
            <label className="text-sm block mb-1" style={{ color: "var(--muted)" }}>Contraseña</label>
            <input value={password} type="password" placeholder="mín. 8 caracteres"
              onChange={(e) => setPassword(e.target.value)}
              className="px-3 py-2 rounded-lg w-full" style={inputStyle} />
          </div>
        </div>
        <p className="text-xs mt-2" style={{ color: "var(--muted)" }}>
          {ROLES.find((r) => r.value === role)?.hint}
        </p>
        <div className="mt-4">
          <button onClick={create} disabled={!canCreate} className="px-4 py-2 rounded-lg font-medium"
            style={{ background: "var(--accent)", color: "var(--accent-ink)", opacity: canCreate ? 1 : 0.5 }}>
            {creating ? "Creando…" : "Crear usuario"}
          </button>
        </div>
      </Card>

      {/* ── Listado ─────────────────────────────────────────────────── */}
      <Card>
        <h2 className="font-semibold mb-3">Usuarios de la empresa</h2>
        {!users ? (
          <p className="text-sm" style={{ color: "var(--muted)" }}>Cargando…</p>
        ) : users.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--muted)" }}>Aún no hay usuarios.</p>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table className="w-full text-sm" style={{ borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ color: "var(--muted)", textAlign: "left" }}>
                  <th style={{ padding: "8px 10px" }}>Usuario</th>
                  <th style={{ padding: "8px 10px" }}>Rol</th>
                  <th style={{ padding: "8px 10px" }}>Estado</th>
                  <th style={{ padding: "8px 10px" }}>Alta</th>
                  <th style={{ padding: "8px 10px" }}>Acciones</th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => {
                  const self = u.id === meId;
                  const rowBusy = busy === u.id;
                  return (
                    <tr key={u.id} style={{ borderTop: "1px solid var(--edge)" }}>
                      <td style={{ padding: "10px" }}>
                        <div style={{ fontWeight: 600 }}>{u.name || u.email}</div>
                        <div style={{ color: "var(--muted)", fontSize: 12 }}>
                          {u.email}{self && " · tú"}
                        </div>
                      </td>
                      <td style={{ padding: "10px" }}>
                        {/* Un admin no puede quitarse el rol admin a sí mismo (guard del backend). */}
                        <select value={u.role} disabled={rowBusy || self}
                          onChange={(e) => patch(u, { role: e.target.value }, `Rol de ${u.email} → ${ROLE_LABEL[e.target.value]} ✅`)}
                          className="px-2 py-1 rounded-lg" style={{ ...inputStyle, opacity: self ? 0.6 : 1 }}>
                          {ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                        </select>
                      </td>
                      <td style={{ padding: "10px" }}>
                        <span style={{
                          padding: "2px 10px", borderRadius: 999, fontSize: 12, fontWeight: 600,
                          color: u.active ? "#34d399" : "#f87171",
                          background: u.active ? "rgba(52,211,153,.12)" : "rgba(248,113,113,.12)",
                        }}>
                          {u.active ? "Activo" : "Inactivo"}
                        </span>
                      </td>
                      <td style={{ padding: "10px", color: "var(--muted)" }}>{when(u.created_at)}</td>
                      <td style={{ padding: "10px" }}>
                        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                          <button onClick={() => resetPassword(u)} disabled={rowBusy}
                            className="px-3 py-1 rounded-lg" style={{ border: "1px solid var(--edge)", color: "var(--foreground)" }}>
                            Resetear contraseña
                          </button>
                          {/* Un admin no puede desactivarse a sí mismo (guard del backend). */}
                          <button
                            onClick={() => patch(u, { active: !u.active }, `${u.email} ${u.active ? "desactivado" : "activado"} ✅`)}
                            disabled={rowBusy || self}
                            className="px-3 py-1 rounded-lg"
                            style={{
                              border: "1px solid var(--edge)",
                              color: self ? "var(--muted)" : (u.active ? "#f87171" : "#34d399"),
                              opacity: self ? 0.5 : 1,
                            }}
                            title={self ? "No puedes desactivar tu propio usuario" : ""}>
                            {u.active ? "Desactivar" : "Activar"}
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </Shell>
  );
}
