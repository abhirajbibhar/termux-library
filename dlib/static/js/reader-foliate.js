/**
 * Foliate.js adapter for reflowable EPUB/MOBI/AZW3/FB2 and fixed-layout CBZ.
 * Loaded on demand from jsDelivr so the app itself needs no Node build step.
 */

const CDN = "https://cdn.jsdelivr.net/gh/johnfactotum/foliate-js@latest";

const SEPIA = { bg: "#f4ecd8", fg: "#4a3f2f", link: "#8a5a1f" };

function resolvedColor(property, cssProperty = "color") {
  const probe = document.createElement("span");
  probe.style.position = "fixed";
  probe.style.visibility = "hidden";
  probe.style[cssProperty] = `var(${property})`;
  document.body.append(probe);
  const value = getComputedStyle(probe)[cssProperty];
  probe.remove();
  return value;
}

function palette(name) {
  if (name === "sepia") return SEPIA;
  return {
    bg: resolvedColor("--bs-bg-body", "backgroundColor") || "#ffffff",
    fg: resolvedColor("--bs-fg-body") || "#212529",
    link: resolvedColor("--bs-link-color") || "#0d6efd",
  };
}

const FONTS = {
  serif: '"Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif',
  sans: 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif',
  georgia: 'Georgia, "Times New Roman", serif',
  literata: '"Literata", Georgia, "Times New Roman", serif',
  mono: 'ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace',
};

function debounce(fn, wait) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

export async function openFoliate({ book, viewElement, initial, settings, callbacks }) {
  await import(`${CDN}/view.js`);
  const { Overlayer } = await import(`${CDN}/overlayer.js`);

  const view = viewElement;
  const docs = new Map();
  let latestSettings = { ...settings };
  let isFixed = book.format === "CBZ";
  let previousHighlights = [];
  let selectionTimer = null;
  let selection = null;

  function cssFor(options) {
    const theme = palette(options.theme);
    const font = FONTS[options.fontFamily] || FONTS.serif;
    const base = `
      html, body { background: ${theme.bg} !important; color: ${theme.fg} !important; }
      a { color: ${theme.link} !important; }
      ::selection { background: rgba(255, 213, 79, 0.45) !important; }
      img, svg, video, image { max-width: 100% !important; height: auto !important; }
    `;
    if (isFixed) return base;
    const column = options.layout === "paginated"
      ? ""
      : `max-width: ${options.width}px; margin: 0 auto; padding: 0.5rem 1rem 4rem;`;
    return base + `
      body {
        font-family: ${font} !important;
        font-size: ${options.fontSize}px !important;
        line-height: ${options.lineHeight} !important;
        text-align: ${options.align} !important;
        ${column}
      }
      p, li, blockquote, dd { text-align: ${options.align} !important; }
    `;
  }

  function applyStylesTo(doc) {
    if (!doc || !doc.head) return;
    let style = doc.getElementById("library-reader-style");
    if (!style) {
      style = doc.createElement("style");
      style.id = "library-reader-style";
      doc.head.append(style);
    }
    style.textContent = cssFor(latestSettings);
  }

  function applyRendererSettings() {
    const renderer = view.renderer;
    if (!renderer || isFixed) return;
    try {
      renderer.setAttribute("flow", latestSettings.layout === "paginated" ? "paginated" : "scrolled");
      if (latestSettings.layout === "paginated") {
        renderer.setAttribute("max-inline-size", `${latestSettings.width}px`);
        renderer.setAttribute("gap", "7%");
        renderer.setAttribute("margin", "0px");
      } else {
        renderer.removeAttribute("max-inline-size");
        renderer.removeAttribute("gap");
        renderer.removeAttribute("margin");
      }
    } catch (error) {
      /* renderer API drifted; CSS styling still works */
    }
  }

  function overallFraction(detail) {
    try {
      const sections = view.book?.sections || [];
      const total = sections.reduce((sum, section) => sum + (section.size || 0), 0);
      if (total > 0) {
        let before = 0;
        for (let i = 0; i < (detail.index || 0); i += 1) before += sections[i]?.size || 0;
        const current = sections[detail.index]?.size || 0;
        return Math.min(1, Math.max(0, (before + current * (detail.fraction || 0)) / total));
      }
      if (sections.length) {
        return Math.min(1, ((detail.index || 0) + (detail.fraction || 0)) / sections.length);
      }
    } catch (error) {
      /* fall through */
    }
    return Math.min(1, Math.max(0, detail.fraction || 0));
  }

  view.addEventListener("draw-annotation", ({ detail }) => {
    if (!detail || typeof detail.draw !== "function") return;
    const color = (detail.annotation && detail.annotation.color) || "#ffd54f";
    try {
      detail.draw(Overlayer.highlight, { color });
    } catch (error) {
      console.error("Could not draw highlight", error);
    }
  });

  view.addEventListener("load", ({ detail }) => {
    if (!detail || !detail.doc) return;
    docs.set(detail.index, detail.doc);
    applyStylesTo(detail.doc);
    detail.doc.addEventListener("selectionchange", () => {
      clearTimeout(selectionTimer);
      selectionTimer = setTimeout(() => handleSelection(detail), 180);
    });
  });

  const refreshHighlights = debounce(() => {
    for (const annotation of previousHighlights) {
      view.addAnnotation({ value: annotation.cfi, color: annotation.color }).catch(() => {});
    }
  }, 350);

  view.addEventListener("relocate", (event) => {
    const detail = event.detail || {};
    callbacks.onLocation({
      location: detail.cfi || "",
      cfi: detail.cfi || "",
      percent: Math.round(overallFraction(detail) * 10000) / 100,
      label: detail.tocItem?.label || "",
    });
    refreshHighlights();
  });

  function handleSelection(detail) {
    const doc = detail.doc;
    let sel = null;
    try {
      sel = doc.getSelection();
    } catch (error) {
      sel = null;
    }
    if (!sel || sel.isCollapsed) {
      if (selection) {
        selection = null;
        callbacks.onSelection(null);
      }
      return;
    }
    const text = sel.toString().replace(/\s+/g, " ").trim();
    if (!text) {
      if (selection) {
        selection = null;
        callbacks.onSelection(null);
      }
      return;
    }
    let cfi = "";
    try {
      const range = sel.getRangeAt(0);
      cfi = view.getCFI(detail.index, range) || "";
    } catch (error) {
      cfi = "";
    }
    selection = { text, cfi, page: null, index: detail.index };
    callbacks.onSelection(selection);
  }

  function flattenToc(items, depth = 0, output = []) {
    for (const item of items || []) {
      if (!item) continue;
      output.push({ label: item.label || "Untitled", href: item.href || "", depth });
      if (item.subitems && item.subitems.length) flattenToc(item.subitems, depth + 1, output);
    }
    return output;
  }

  applyRendererSettings();

  // The URL keeps the real filename so Foliate can identify CBZ/FB2 by
  // extension in addition to its content sniffing.
  const fileUrl = new URL(book.file_url || `/api/books/${book.id}/file`, location.origin).href;
  try {
    await view.open(fileUrl);
  } catch (error) {
    callbacks.onError(new Error(`Could not open the ${book.format} file: ${error.message}`));
    throw error;
  }

  isFixed = Boolean(view.isFixedLayout) || book.format === "CBZ";
  applyRendererSettings();
  docs.forEach((doc) => applyStylesTo(doc));

  try {
    callbacks.onToc(flattenToc(view.book?.toc || []));
  } catch (error) {
    callbacks.onToc([]);
  }

  const lastLocation = initial && initial.cfi;
  try {
    await view.init(lastLocation ? { lastLocation, showTextStart: true } : {});
  } catch (error) {
    if (lastLocation) {
      try {
        await view.goTo(lastLocation);
      } catch (goToError) {
        /* start at the beginning */
      }
    }
  }

  applyRendererSettings();
  callbacks.onReady();

  const api = {
    applySettings(nextSettings) {
      latestSettings = { ...nextSettings };
      applyRendererSettings();
      docs.forEach((doc) => applyStylesTo(doc));
    },
    setAnnotations(list) {
      const highlights = (list || []).filter((annotation) => annotation.kind === "highlight" && annotation.cfi);
      const nextCfis = new Set(highlights.map((annotation) => annotation.cfi));
      for (const annotation of previousHighlights) {
        if (!nextCfis.has(annotation.cfi)) {
          try {
            view.deleteAnnotation({ value: annotation.cfi });
          } catch (error) {
            /* ignore */
          }
        }
      }
      previousHighlights = highlights;
      refreshHighlights();
    },
    async goTo(target) {
      if (!target) return;
      await view.goTo(target);
    },
    async next() {
      await view.next();
    },
    async prev() {
      await view.prev();
    },
    async goToPage() {
      /* reflowable books have no stable page numbers */
    },
    async setTheme(theme) {
      latestSettings.theme = theme;
      docs.forEach((doc) => applyStylesTo(doc));
    },
    destroy() {
      try {
        view.close();
      } catch (error) {
        /* ignore */
      }
    },
  };
  return api;
}
