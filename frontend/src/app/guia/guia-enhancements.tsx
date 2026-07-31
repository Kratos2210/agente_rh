"use client";

// Progressive enhancements for the static guide. The guide body is injected with
// dangerouslySetInnerHTML (scripts inside it never execute), so every behavior —
// in-page search, deep-links that open collapsed <details>, ¶ copy-link anchors,
// prev/next section nav and print expand/restore — lives in this sibling client
// component. It mounts inside <Shell> next to #guia-doc, after the session guard,
// so the DOM is already there when the effect runs (defensive early-return anyway).
// All DOM insertions are idempotent (guarded) to survive StrictMode double-mount.
import { useEffect } from "react";

const HIT_MS = 2600;
const DEBOUNCE_MS = 200;
const MAX_RESULTS = 12;

export function GuiaEnhancements() {
  useEffect(() => {
    const doc = document.getElementById("guia-doc");
    if (!doc) return;
    const cleanups: Array<() => void> = [];

    const openAncestors = (el: Element) => {
      let details = el.closest("details");
      while (details) {
        details.open = true;
        details = details.parentElement?.closest("details") ?? null;
      }
    };

    const flash = (el: HTMLElement) => {
      el.classList.remove("hit");
      void el.offsetWidth; // restart the animation if re-triggered
      el.classList.add("hit");
      window.setTimeout(() => el.classList.remove("hit"), HIT_MS);
    };

    const goTo = (el: HTMLElement) => {
      openAncestors(el);
      requestAnimationFrame(() => {
        el.scrollIntoView({ behavior: "smooth", block: "start" });
        flash(el);
      });
    };

    // --- Deep-links: open collapsed <details> ancestors before jumping ---
    const revealHash = () => {
      const id = decodeURIComponent(window.location.hash.slice(1));
      if (!id) return;
      const target = document.getElementById(id);
      if (target && doc.contains(target)) goTo(target);
    };
    window.addEventListener("hashchange", revealHash);
    cleanups.push(() => window.removeEventListener("hashchange", revealHash));
    revealHash();

    // --- Section titles (h2 minus the number badge), reused by nav and search ---
    const sections = Array.from(doc.querySelectorAll<HTMLElement>("main > section[id]"));
    const titleOf = (sec: HTMLElement) => {
      const h2 = sec.querySelector("h2");
      if (!h2) return sec.id;
      const clone = h2.cloneNode(true) as HTMLElement;
      clone.querySelector(".num")?.remove();
      clone.querySelector(".anch")?.remove();
      return clone.textContent?.trim() ?? sec.id;
    };

    // --- ¶ anchors that copy a deep-link to the heading ---
    const addAnchor = (host: Element, id: string) => {
      if (host.querySelector(":scope > .anch")) return;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "anch";
      btn.title = "Copiar enlace a esta sección";
      btn.textContent = "¶";
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const url = `${window.location.origin}${window.location.pathname}#${id}`;
        navigator.clipboard?.writeText(url).catch(() => {});
        window.history.replaceState(null, "", `#${id}`);
        btn.classList.add("copied");
        btn.textContent = "✓";
        window.setTimeout(() => {
          btn.classList.remove("copied");
          btn.textContent = "¶";
        }, 1200);
      });
      host.appendChild(btn);
    };
    sections.forEach((sec) => {
      const h2 = sec.querySelector("h2");
      if (h2) addAnchor(h2, sec.id);
    });
    doc.querySelectorAll<HTMLElement>("h3[id],h4[id]").forEach((h) => addAnchor(h, h.id));
    doc.querySelectorAll<HTMLElement>("details[id] > summary").forEach((s) => {
      const id = s.parentElement?.id;
      if (id) addAnchor(s, id);
    });

    // --- Prev/next nav at the end of each section (built from the live DOM,
    //     immune to numbering drift in the HTML) ---
    const mkNavLink = (target: HTMLElement, label: string, cls: string) => {
      const a = document.createElement("a");
      a.href = `#${target.id}`;
      if (cls) a.className = cls;
      const tag = document.createElement("span");
      tag.textContent = label;
      a.appendChild(tag);
      a.appendChild(document.createTextNode(titleOf(target)));
      return a;
    };
    sections.forEach((sec, i) => {
      if (sec.querySelector(":scope > .secnav")) return;
      const prev = sections[i - 1];
      const next = sections[i + 1];
      if (!prev && !next) return;
      const nav = document.createElement("div");
      nav.className = "secnav";
      if (prev) nav.appendChild(mkNavLink(prev, "‹ Anterior", ""));
      if (next) nav.appendChild(mkNavLink(next, "Siguiente ›", "next"));
      sec.appendChild(nav);
    });

    // --- In-page search (input in the sticky TOC bar; results also match text
    //     hidden inside collapsed <details> and open them on click) ---
    type Entry = { el: HTMLElement; text: string; label: string; section: string };
    let index: Entry[] | null = null;
    const buildIndex = (): Entry[] => {
      const entries: Entry[] = [];
      sections.forEach((sec) => {
        const secTitle = titleOf(sec);
        entries.push({ el: sec, text: secTitle.toLowerCase(), label: secTitle, section: secTitle });
        sec.querySelectorAll<HTMLElement>("h3,h4,summary,.card,dt").forEach((el) => {
          if (el.closest(".secnav")) return;
          const text = (el.textContent ?? "").trim();
          if (!text) return;
          const labelSrc = el.matches(".card")
            ? (el.querySelector("h4,h3,b")?.textContent ?? text)
            : text;
          entries.push({
            el,
            text: text.toLowerCase(),
            label: labelSrc.trim().replace(/\s+/g, " ").slice(0, 90),
            section: secTitle,
          });
        });
      });
      return entries;
    };

    const tocWrap = doc.querySelector("nav.toc .wrap");
    if (tocWrap && !tocWrap.querySelector(".gsearch")) {
      const box = document.createElement("div");
      box.className = "gsearch";
      const input = document.createElement("input");
      input.type = "search";
      input.placeholder = "Buscar en la guía…";
      input.setAttribute("aria-label", "Buscar en la guía");
      const res = document.createElement("div");
      res.className = "res";
      res.hidden = true;
      box.appendChild(input);
      box.appendChild(res);
      tocWrap.insertBefore(box, tocWrap.firstChild);

      const close = () => {
        res.hidden = true;
        res.replaceChildren();
      };
      const run = () => {
        const q = input.value.trim().toLowerCase();
        if (q.length < 2) {
          close();
          return;
        }
        index ??= buildIndex();
        const hits = index.filter((e) => e.text.includes(q)).slice(0, MAX_RESULTS);
        res.replaceChildren();
        if (!hits.length) {
          const empty = document.createElement("div");
          empty.className = "empty";
          empty.textContent = "Sin resultados en la guía.";
          res.appendChild(empty);
        } else {
          hits.forEach((hit) => {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.textContent = hit.label;
            if (hit.section !== hit.label) {
              const sec = document.createElement("span");
              sec.className = "sec";
              sec.textContent = hit.section;
              btn.appendChild(sec);
            }
            btn.addEventListener("click", () => {
              close();
              goTo(hit.el);
            });
            res.appendChild(btn);
          });
        }
        res.hidden = false;
      };

      let timer = 0;
      input.addEventListener("input", () => {
        window.clearTimeout(timer);
        timer = window.setTimeout(run, DEBOUNCE_MS);
      });
      input.addEventListener("keydown", (e) => {
        if (e.key === "Escape") close();
        if (e.key === "Enter") {
          window.clearTimeout(timer);
          run();
          res.querySelector("button")?.focus();
        }
      });
      const onDocClick = (e: MouseEvent) => {
        if (!box.contains(e.target as Node)) close();
      };
      document.addEventListener("click", onDocClick);
      cleanups.push(() => {
        window.clearTimeout(timer);
        document.removeEventListener("click", onDocClick);
      });
    }

    // --- Print: open every <details> before printing, restore after ---
    let printTouched: HTMLDetailsElement[] = [];
    const beforePrint = () => {
      printTouched = Array.from(doc.querySelectorAll<HTMLDetailsElement>("details:not([open])"));
      printTouched.forEach((d) => {
        d.open = true;
      });
    };
    const afterPrint = () => {
      printTouched.forEach((d) => {
        d.open = false;
      });
      printTouched = [];
    };
    window.addEventListener("beforeprint", beforePrint);
    window.addEventListener("afterprint", afterPrint);
    cleanups.push(() => {
      window.removeEventListener("beforeprint", beforePrint);
      window.removeEventListener("afterprint", afterPrint);
    });

    return () => cleanups.forEach((fn) => fn());
  }, []);

  return null;
}
