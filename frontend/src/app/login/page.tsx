"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { setSession } from "@/lib/auth";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);

  // U4: si nos trajo un 401 con sesión previa (?expired=1), avisar por qué está aquí.
  // Se lee de window (y no de useSearchParams) para no exigir un Suspense boundary.
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("expired")) {
      setNotice("Tu sesión expiró. Vuelve a iniciar sesión para continuar.");
    }
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await api.login(email.trim(), password);
      setSession(res.access_token, res.user);
      router.push("/");
    } catch {
      setError("Credenciales inválidas. Verifica tu correo y contraseña.");
    } finally {
      setLoading(false);
    }
  }

  const field: React.CSSProperties = {
    width: "100%", padding: "11px 13px", borderRadius: 10, fontSize: 14,
    background: "var(--field)", border: "1px solid var(--edge)", color: "var(--body-text)", outline: "none",
  };
  const label: React.CSSProperties = { fontSize: 12, fontWeight: 700, color: "var(--muted)", marginBottom: 6, display: "block" };

  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: 24 }}>
      <form onSubmit={submit} style={{ width: "100%", maxWidth: 380 }}>
        {/* Marca */}
        <div style={{ display: "flex", alignItems: "center", gap: 11, marginBottom: 26, justifyContent: "center" }}>
          <div style={{
            width: 38, height: 38, borderRadius: 12, background: "linear-gradient(135deg,var(--ac),var(--ac-btn))",
            display: "flex", alignItems: "center", justifyContent: "center", boxShadow: "0 6px 18px var(--ac-soft)",
          }}>
            <div style={{ width: 15, height: 15, border: "2.5px solid #fff", borderRadius: "50%", borderRightColor: "transparent" }} />
          </div>
          <div>
            <div style={{ fontSize: 20, fontWeight: 800, letterSpacing: "-.03em", color: "var(--heading)", lineHeight: 1 }}>hira</div>
            <div style={{ fontSize: 9.5, color: "var(--muted-2)", fontWeight: 700, letterSpacing: ".14em", marginTop: 2 }}>AGENTE DE SELECCIÓN</div>
          </div>
        </div>

        <div style={{ padding: 26, borderRadius: 16, background: "var(--card)", border: "1px solid var(--edge)" }}>
          <h1 style={{ fontSize: 18, fontWeight: 800, color: "var(--heading)", margin: "0 0 4px" }}>Iniciar sesión</h1>
          <p style={{ fontSize: 13, color: "var(--muted)", margin: "0 0 20px" }}>Panel del reclutador</p>

          {notice && (
            <div style={{ margin: "0 0 16px", fontSize: 13, color: "#d97706", background: "rgba(217,119,6,.1)",
              border: "1px solid rgba(217,119,6,.3)", borderRadius: 9, padding: "9px 12px" }}>{notice}</div>
          )}

          <label style={label}>Correo</label>
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus
            placeholder="tu@empresa.com" style={{ ...field, marginBottom: 14 }} />

          <label style={label}>Contraseña</label>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required
            placeholder="••••••••" style={field} />

          {error && (
            <div style={{ marginTop: 14, fontSize: 13, color: "var(--bad)", background: "rgba(248,113,113,.1)",
              border: "1px solid rgba(248,113,113,.3)", borderRadius: 9, padding: "9px 12px" }}>{error}</div>
          )}

          <button type="submit" disabled={loading} style={{
            width: "100%", marginTop: 20, padding: "11px 16px", borderRadius: 10, fontSize: 14, fontWeight: 700,
            cursor: loading ? "default" : "pointer", border: "none", color: "#fff",
            background: loading ? "var(--muted-3)" : "linear-gradient(135deg,var(--ac),var(--ac-btn))",
          }}>{loading ? "Entrando…" : "Entrar"}</button>
        </div>
      </form>
    </div>
  );
}
