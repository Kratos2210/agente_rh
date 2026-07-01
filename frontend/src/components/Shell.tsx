"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowLeft, LogOut } from "lucide-react";
import { clearSession, getToken, getUser } from "@/lib/auth";

const NAV = [
  { label: "Vacantes", href: "/", match: (p: string) => p === "/" || p.startsWith("/vacantes") || p.startsWith("/candidatos") },
  { label: "Pipeline", href: "/pipeline", match: (p: string) => p.startsWith("/pipeline") },
  { label: "Equipo", href: "/equipo", match: (p: string) => p.startsWith("/equipo") },
  { label: "Guía", href: "/guia", match: (p: string) => p.startsWith("/guia") },
];

// Entradas visibles solo para administradores.
const ADMIN_NAV = [
  { label: "Observabilidad", href: "/observabilidad", match: (p: string) => p.startsWith("/observabilidad") },
];

function TopBar() {
  const pathname = usePathname() || "/";
  const router = useRouter();
  const [user, setUser] = useState<{ email: string; name: string; role: string } | null>(null);
  useEffect(() => setUser(getUser() as { email: string; name: string; role: string } | null), []);
  const logout = () => {
    clearSession();
    router.replace("/login");
  };
  const initials = (user?.name || user?.email || "?").trim().slice(0, 2).toUpperCase();
  return (
    <div
      style={{
        position: "sticky", top: 0, zIndex: 60, display: "flex", alignItems: "center", gap: 18,
        padding: "13px 26px", background: "rgba(10,14,22,.78)", backdropFilter: "blur(16px)",
        borderBottom: "1px solid rgba(255,255,255,.06)",
      }}
    >
      <Link href="/" style={{ display: "flex", alignItems: "center", gap: 11, textDecoration: "none" }}>
        <div style={{
          width: 34, height: 34, borderRadius: 11, background: "linear-gradient(135deg,var(--ac),var(--ac-btn))",
          display: "flex", alignItems: "center", justifyContent: "center", boxShadow: "0 6px 18px var(--ac-soft)",
        }}>
          <div style={{ width: 13, height: 13, border: "2.5px solid #fff", borderRadius: "50%", borderRightColor: "transparent" }} />
        </div>
        <div>
          <div style={{ fontSize: 17, fontWeight: 800, letterSpacing: "-.03em", color: "var(--heading)", lineHeight: 1 }}>hira</div>
          <div style={{ fontSize: 9.5, color: "var(--muted-2)", fontWeight: 700, letterSpacing: ".14em", marginTop: 2 }}>AGENTE DE SELECCIÓN</div>
        </div>
      </Link>
      <div style={{ width: 1, height: 26, background: "rgba(255,255,255,.08)", marginLeft: 6 }} />
      {[...NAV, ...(user?.role === "admin" ? ADMIN_NAV : [])].map((n) => {
        const active = n.match(pathname);
        return (
          <Link key={n.href} href={n.href} style={{
            display: "flex", alignItems: "center", gap: 7, padding: "7px 13px", borderRadius: 9,
            background: active ? "var(--ac-soft)" : "transparent", color: active ? "var(--ac)" : "var(--muted)",
            fontSize: 13, fontWeight: 700, textDecoration: "none",
          }}>{n.label}</Link>
        );
      })}
      <div style={{ flex: 1 }} />
      <div style={{
        display: "flex", alignItems: "center", gap: 9, padding: "8px 14px", borderRadius: 10,
        background: "rgba(255,255,255,.04)", border: "1px solid rgba(255,255,255,.07)", minWidth: 210,
        color: "var(--muted-3)", fontSize: 13,
      }}>
        <span style={{ fontSize: 14, opacity: 0.7 }}>⌕</span><span>Buscar vacante o candidato…</span>
      </div>
      <Link href="/configuracion" aria-label="Configuración" style={{
        width: 36, height: 36, borderRadius: 10, background: "rgba(255,255,255,.04)", border: "1px solid rgba(255,255,255,.07)",
        display: "flex", alignItems: "center", justifyContent: "center", color: "#9aa4b8", fontSize: 15, textDecoration: "none",
      }}>⚙</Link>
      <div
        title={user ? `${user.email} · ${user.role}` : ""}
        style={{
          width: 36, height: 36, borderRadius: "50%", background: "linear-gradient(135deg,#2a3346,#1a2233)",
          border: "1px solid rgba(255,255,255,.1)", display: "flex", alignItems: "center", justifyContent: "center",
          fontWeight: 700, fontSize: 13, color: "#cfd8e8",
        }}
      >{initials}</div>
      <button
        onClick={logout}
        aria-label="Cerrar sesión"
        title="Cerrar sesión"
        style={{
          width: 36, height: 36, borderRadius: 10, background: "rgba(255,255,255,.04)", border: "1px solid rgba(255,255,255,.07)",
          display: "flex", alignItems: "center", justifyContent: "center", color: "#9aa4b8", cursor: "pointer",
        }}
      >
        <LogOut size={15} />
      </button>
    </div>
  );
}

export function Shell({ children, width = 1180 }: { children: React.ReactNode; width?: number }) {
  const router = useRouter();
  // Guard de sesión: sin token, al login (y no renderiza contenido protegido).
  const [authed, setAuthed] = useState<boolean | null>(null);
  useEffect(() => {
    if (getToken()) {
      setAuthed(true);
    } else {
      setAuthed(false);
      router.replace("/login");
    }
  }, [router]);
  if (!authed) return null;
  return (
    <div style={{ minHeight: "100vh" }}>
      <TopBar />
      <main style={{ maxWidth: width, margin: "0 auto", padding: "34px 26px 80px" }} className="hpop">{children}</main>
    </div>
  );
}

// Botón "atrás" consistente para vistas con regreso.
export function BackLink({ href, label }: { href: string; label: string }) {
  return (
    <Link href={href} style={{
      display: "inline-flex", alignItems: "center", gap: 8, padding: "8px 14px", borderRadius: 10,
      background: "rgba(255,255,255,.04)", border: "1px solid rgba(255,255,255,.07)", color: "#aeb8cc",
      fontSize: 13, fontWeight: 600, textDecoration: "none", marginBottom: 22,
    }}>
      <ArrowLeft size={15} /> {label}
    </Link>
  );
}

export function Card({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={{ borderRadius: 16, padding: 20, background: "var(--card)", border: "1px solid var(--edge)", ...style }}>
      {children}
    </div>
  );
}
