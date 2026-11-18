import { Drawer, Tab } from "../vendor/bootstrap.bundle.js";
import { api } from "./api.js";
import { escapeHtml, toast } from "./ui.js";

const book = JSON.parse(document.getElementById("book-data").textContent);
const isPdf = book.format === "PDF";
const SETTINGS_KEY = "reader-settings";
const DEFAULTS = {
  theme: "light",
  fontSize: 18,
  fontFamily: "serif",
  lineHeight: 1.6,
  layout: "scrolled",
  align: "left",
  width: 720,
  pdfZoom: 100,
};

const el = (id) => document.getElementById(id);
const drawer = (id) => Drawer.getOrCreateInstance(el(id));

const state = {
  settings: loadSettings(),
  engine: null,
  engineReady: false,
  isPdf,
  annotations: [],
  toc: [],
  currentCfi: "",
  currentPage: 0,
  totalPages: 0,
  currentPercent: Number(book.progress.percent) || 0,
  currentLabel: "",
  selection: null,
  noteContext: null,
  noteColor: "#ffd54f",
  saveTimer: null,
  lastSaved: "",
  lastSaveAt: 0,
  pdfTheme: null,
  pdfZoom: 100,
};

function loadSettings() {
  try {
    const stored = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
    const appTheme = document.documentElement.getAttribute("data-bs-theme") === "dark" ? "dark" : "light";
    return { ...DEFAULTS, theme: appTheme, ...stored };
  } catch (error) {
    return { ...DEFAULTS };
  }
}

function saveSettings() {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(state.settings));
  } catch (error) {
    /* ignore */
  }
}

async function init() {
  document.documentElement.setAttribute("data-bs-theme", state.settings.theme === "dark" ? "dark" : "light");
  syncSettingsControls();
  bindEvents();
  updateProgressUI();
  await loadAnnotations();
  await loadProgress();
  openEngine();
}

async function loadProgress() {
  try {
    const data = await api(`/api/books/${book.id}/progress`);
    const progress = data.progress || {};
    if (progress.location) {
      if (isPdf) {
        state.currentPage = Number(progress.location) || state.currentPage;
        state.currentCfi = "";
      } else {
        state.currentCfi = progress.location;
      }
    }
    if (typeof progress.percent === "number") state.currentPercent = progress.percent;
    updateProgressUI();
  } catch (error) {
    /* book JSON already carries progress; ignore */
  }
}

async function loadAnnotations() {
  try {
    const data = await api(`/api/books/${book.id}/annotations`);
    state.annotations = data.annotations || [];
    renderAnnotationLists();
  } catch (error) {
    state.annotations = [];
  }
}

async function openEngine() {
  try {
    if (isPdf) {
      const { openPdf } = await import("./reader-pdf.js");
      state.pdfTheme = state.settings.theme;
      state.engine = await openPdf({
        book,
        container: el("pdfPages"),
        scroller: el("pdfScroll"),
        initial: {
          page: Number(book.progress.location) || state.currentPage || 1,
          percent: state.currentPercent,
          zoom: state.settings.pdfZoom,
        },
        theme: state.settings.theme,
        callbacks: {
          onLocation: handleLocation,
          onReady: handleReady,
          onError: showError,
          onToc: handleToc,
          onZoom: handleZoom,
        },
      });
      el("progressSlider").disabled = false;
    } else {
      const { openFoliate } = await import("./reader-foliate.js");
      state.engine = await openFoliate({
        book,
        viewElement: el("foliateView"),
        initial: { cfi: book.progress.location || state.currentCfi },
        settings: state.settings,
        callbacks: {
          onLocation: handleLocation,
          onSelection: handleSelection,
          onToc: handleToc,
          onReady: handleReady,
          onError: showError,
        },
      });
      el("progressSlider").disabled = true;
    }
  } catch (error) {
    showError(error);
  }
}

function handleReady() {
  state.engineReady = true;
  el("readerLoading").classList.add("d-none");
  el("readerError").classList.add("d-none");
  if (state.engine && state.engine.setAnnotations) {
    state.engine.setAnnotations(state.annotations);
  }
  updateProgressUI();
}

function showError(error) {
  el("readerLoading").classList.add("d-none");
  el("readerError").classList.remove("d-none");
  el("readerErrorText").textContent = (error && error.message) || "The file could not be loaded from the server.";
}

function handleLocation(info) {
  if (!info) return;
  if (typeof info.percent === "number" && Number.isFinite(info.percent)) {
    state.currentPercent = Math.max(0, Math.min(100, info.percent));
  }
  if (info.location) {
    if (isPdf) {
      state.currentPage = Number(info.location) || state.currentPage;
      state.currentCfi = "";
    } else {
      state.currentCfi = String(info.location);
    }
  }
  if (info.page) state.currentPage = Number(info.page);
  if (info.total_pages) state.totalPages = Number(info.total_pages);
  if (info.label) state.currentLabel = info.label;
  updateProgressUI();
  scheduleSave();
}

function handleSelection(selection) {
  state.selection = selection;
  const bar = el("selectionBar");
  if (!selection || !selection.text) {
    bar.classList.add("d-none");
    el("selectionPreview").textContent = "";
    return;
  }
  el("selectionPreview").textContent = selection.text.slice(0, 90);
  bar.classList.remove("d-none");
}

function handleToc(items) {
  state.toc = items || [];
  renderToc();
}

function handleZoom(percent) {
  state.pdfZoom = Number(percent) || 100;
  state.settings.pdfZoom = state.pdfZoom;
  saveSettings();
  const label = el("pdfZoomValue");
  if (label) label.textContent = `${state.pdfZoom}%`;
}

/* ------------------------------------------------------------------ */
/* Progress                                                           */
/* ------------------------------------------------------------------ */

function progressPayload() {
  const location = isPdf
    ? state.currentPage
      ? String(state.currentPage)
      : ""
    : state.currentCfi;
  return {
    location,
    progress_percent: Math.round(state.currentPercent * 100) / 100,
    page: isPdf ? state.currentPage || 0 : 0,
  };
}

function updateProgressUI() {
  const percent = Math.round(state.currentPercent);
  el("progressSlider").value = String(Math.min(100, Math.max(0, state.currentPercent)));
  el("progressLabel").textContent = isPdf && state.totalPages
    ? `${percent}% · ${state.currentPage}/${state.totalPages}`
    : `${percent}%`;
}

function scheduleSave() {
  if (!state.engineReady) return;
  if (Date.now() - state.lastSaveAt > 5000) {
    saveProgress();
    return;
  }
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(saveProgress, 1800);
}

async function saveProgress() {
  if (!state.engineReady) return;
  const payload = progressPayload();
  if (!payload.location) return;
  const key = JSON.stringify(payload);
  if (key === state.lastSaved) return;
  state.lastSaved = key;
  state.lastSaveAt = Date.now();
  try {
    await api(`/api/books/${book.id}/progress`, { method: "POST", body: payload });
  } catch (error) {
    state.lastSaved = "";
  }
}

function flushProgress() {
  const payload = progressPayload();
  if (!state.engineReady || !payload.location) return;
  try {
    fetch(`/api/books/${book.id}/progress`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      keepalive: true,
      body: JSON.stringify(payload),
    });
  } catch (error) {
    /* ignore */
  }
}

/* ------------------------------------------------------------------ */
/* Annotations                                                        */
/* ------------------------------------------------------------------ */

function currentMark() {
  return {
    cfi: state.currentCfi || null,
    page: isPdf ? state.currentPage || null : null,
    text: "",
    percent: state.currentPercent,
    label: state.currentLabel,
  };
}

async function createAnnotation(payload) {
  try {
    const data = await api(`/api/books/${book.id}/annotations`, { method: "POST", body: payload });
    state.annotations.push(data.annotation);
    state.annotations.sort((a, b) => (a.page || 0) - (b.page || 0));
    renderAnnotationLists();
    if (state.engine && state.engine.setAnnotations) state.engine.setAnnotations(state.annotations);
    toast(payload.kind === "bookmark" ? "Bookmark saved" : "Saved", "success", 2200);
    return data.annotation;
  } catch (error) {
    toast(error.message, "danger");
    return null;
  }
}

async function deleteAnnotation(id) {
  try {
    await api(`/api/annotations/${id}`, { method: "DELETE" });
    state.annotations = state.annotations.filter((annotation) => annotation.id !== id);
    renderAnnotationLists();
    if (state.engine && state.engine.setAnnotations) state.engine.setAnnotations(state.annotations);
    toast("Removed", "success", 1800);
  } catch (error) {
    toast(error.message, "danger");
  }
}

function annotationCard(annotation) {
  const kindLabel = annotation.kind === "bookmark" ? "Bookmark" : annotation.kind === "highlight" ? "Highlight" : "Note";
  const icon = annotation.kind === "bookmark" ? "bi-bookmark" : annotation.kind === "highlight" ? "bi-highlighter" : "bi-chat-left-text";
  const pageLabel = annotation.page ? `Page ${annotation.page}` : "";
  const quote = annotation.text
    ? `<div class="fs-sm fg-secondary fst-italic mt-2">“${escapeHtml(annotation.text.slice(0, 240))}”</div>`
    : "";
  const note = annotation.note ? `<div class="fs-sm mt-2">${escapeHtml(annotation.note)}</div>` : "";
  return `
    <div class="list-group-item p-5" data-annotation-id="${annotation.id}" role="button" tabindex="0">
      <div class="d-flex align-items-center gap-3">
        <span class="badge theme-secondary"><i class="bi ${icon} me-2"></i>${kindLabel}${pageLabel ? " · " + pageLabel : ""}</span>
        <button class="btn btn-text theme-danger btn-sm btn-icon ms-auto" type="button" data-delete-annotation="${annotation.id}" aria-label="Delete">
          <i class="bi bi-trash"></i>
        </button>
      </div>
      ${quote}
      ${note}
    </div>`;
}

function renderAnnotationLists() {
  const bookmarks = state.annotations.filter((annotation) => annotation.kind === "bookmark");
  const notes = state.annotations.filter((annotation) => annotation.kind !== "bookmark");
  el("bookmarkList").innerHTML = bookmarks.length
    ? bookmarks.map(annotationCard).join("")
    : '<div class="fg-secondary fs-sm py-3">No bookmarks yet.</div>';
  el("noteList").innerHTML = notes.length
    ? notes.map(annotationCard).join("")
    : '<div class="fg-secondary fs-sm py-3">Select text to highlight it, or add a note for the current page.</div>';
}

function renderToc() {
  const container = el("tocList");
  if (!state.toc.length) {
    container.innerHTML = '<div class="fg-secondary fs-sm py-3">This book has no table of contents.</div>';
    return;
  }
  container.innerHTML = state.toc
    .map((item) => {
      const depth = Math.min(item.depth || 0, 2);
      const pad = ["ps-3", "ps-5", "ps-7"][depth];
      return `
      <button class="list-group-item list-group-item-action text-start ${pad} py-3" type="button" data-toc-href="${escapeHtml(item.href || "")}" ${item.page ? `data-toc-page="${item.page}"` : ""}>
        ${escapeHtml(item.label || "Untitled")}
      </button>`;
    })
    .join("");
}

/* ------------------------------------------------------------------ */
/* Settings                                                           */
/* ------------------------------------------------------------------ */

function syncSettingsControls() {
  document.querySelectorAll("[data-theme-choice]").forEach((button) => {
    const active = button.dataset.themeChoice === state.settings.theme;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });

  const optional = (id, value) => {
    const node = el(id);
    if (node) node.value = value;
  };
  optional("fontFamilySelect", state.settings.fontFamily);
  optional("fontSizeRange", String(state.settings.fontSize));
  optional("lineHeightRange", String(state.settings.lineHeight));
  optional("layoutSelect", state.settings.layout);
  optional("alignSelect", state.settings.align);
  optional("widthRange", String(state.settings.width));
  if (el("fontSizeValue")) el("fontSizeValue").textContent = `${state.settings.fontSize} px`;
  if (el("lineHeightValue")) el("lineHeightValue").textContent = Number(state.settings.lineHeight).toFixed(1);
  if (el("widthValue")) el("widthValue").textContent = `${state.settings.width} px`;
  if (el("pdfZoomValue")) el("pdfZoomValue").textContent = `${Math.round(state.settings.pdfZoom)}%`;

  const icon = el("readerThemeToggle").querySelector("[data-theme-icon]");
  if (icon) {
    icon.className = state.settings.theme === "dark"
      ? "bi bi-moon-stars"
      : state.settings.theme === "sepia"
        ? "bi bi-circle-half"
        : "bi bi-sun";
  }
  document.documentElement.setAttribute("data-bs-theme", state.settings.theme === "dark" ? "dark" : "light");
}

async function applySettings() {
  saveSettings();
  syncSettingsControls();
  if (!state.engine) return;
  if (isPdf) {
    if (state.pdfTheme !== state.settings.theme && state.engine.setTheme) {
      state.pdfTheme = state.settings.theme;
      try {
        await state.engine.setTheme(state.settings.theme);
      } catch (error) {
        /* keep the current viewer */
      }
    }
    return;
  }
  if (state.engine.applySettings) state.engine.applySettings(state.settings);
}

/* ------------------------------------------------------------------ */
/* Events                                                             */
/* ------------------------------------------------------------------ */

function bindEvents() {
  el("tocBtn").addEventListener("click", () => openDrawer("toc"));
  el("notesBtn").addEventListener("click", () => openDrawer("notes"));
  el("bookmarkBtn").addEventListener("click", addBookmark);
  el("settingsBtn").addEventListener("click", () => drawer("settingsDrawer").show());

  el("addBookmarkBtn").addEventListener("click", addBookmark);
  el("addNoteBtn").addEventListener("click", () => {
    const mark = currentMark();
    openNoteDialog({
      kind: "note",
      cfi: mark.cfi,
      page: mark.page,
      text: "",
    });
  });

  el("prevBtn").addEventListener("click", () => state.engine?.prev?.());
  el("nextBtn").addEventListener("click", () => state.engine?.next?.());
  el("progressSlider").addEventListener("change", (event) => {
    if (!isPdf || !state.engine?.goToPage || !state.totalPages) return;
    const page = Math.max(1, Math.round((Number(event.target.value) / 100) * state.totalPages));
    state.engine.goToPage(page);
  });

  el("selectionBar").addEventListener("click", async (event) => {
    const target = event.target.closest("button");
    if (!target) return;
    if (target.id === "clearSelectionBtn") {
      state.selection = null;
      handleSelection(null);
      return;
    }
    if (!state.selection) return;
    if (target.id === "highlightBtn") {
      const annotation = await createAnnotation({
        kind: "highlight",
        cfi: state.selection.cfi || null,
        page: state.selection.page || null,
        text: state.selection.text,
        color: "#ffd54f",
        note: "",
      });
      if (annotation) {
        state.selection = null;
        handleSelection(null);
      }
    } else if (target.id === "noteSelectionBtn") {
      openNoteDialog({
        kind: "note",
        cfi: state.selection.cfi || null,
        page: state.selection.page || null,
        text: state.selection.text,
      });
    }
  });

  el("noteForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const context = state.noteContext || {};
    const note = el("noteText").value.trim();
    if (!note) {
      toast("Write a note first", "warning");
      return;
    }
    const annotation = await createAnnotation({
      kind: context.kind === "highlight" ? "highlight" : "note",
      cfi: context.cfi || null,
      page: context.page || null,
      text: context.text || "",
      note,
      color: state.noteColor || "#ffd54f",
    });
    if (annotation) {
      drawer("noteDialog").hide();
      el("noteForm").reset();
      state.selection = null;
      handleSelection(null);
    }
  });

  document.querySelectorAll("[data-note-color]").forEach((button) => {
    button.addEventListener("click", () => {
      state.noteColor = button.dataset.noteColor;
      document.querySelectorAll("[data-note-color]").forEach((choice) => {
        choice.classList.toggle("active", choice === button);
        choice.setAttribute("aria-pressed", choice === button ? "true" : "false");
      });
    });
  });

  el("tocList").addEventListener("click", async (event) => {
    const link = event.target.closest("[data-toc-href], [data-toc-page]");
    if (!link) return;
    drawer("contentsDrawer").hide();
    try {
      if (link.dataset.tocPage && state.engine?.goToPage) {
        await state.engine.goToPage(Number(link.dataset.tocPage));
      } else if (link.dataset.tocHref) {
        await state.engine?.goTo?.(link.dataset.tocHref);
      }
    } catch (error) {
      toast("Could not jump to that chapter", "danger");
    }
  });

  for (const containerId of ["bookmarkList", "noteList"]) {
    el(containerId).addEventListener("click", async (event) => {
      const deleteButton = event.target.closest("[data-delete-annotation]");
      if (deleteButton) {
        event.stopPropagation();
        await deleteAnnotation(Number(deleteButton.dataset.deleteAnnotation));
        return;
      }
      const card = event.target.closest("[data-annotation-id]");
      if (!card) return;
      const annotation = state.annotations.find((item) => item.id === Number(card.dataset.annotationId));
      if (!annotation) return;
      drawer("contentsDrawer").hide();
      try {
        if (annotation.cfi && state.engine?.goTo) await state.engine.goTo(annotation.cfi);
        else if (annotation.page && state.engine?.goToPage) await state.engine.goToPage(annotation.page);
      } catch (error) {
        toast("Could not jump to that location", "danger");
      }
    });
  }

  // Settings
  document.querySelectorAll("[data-theme-choice]").forEach((button) => {
    button.addEventListener("click", () => {
      state.settings.theme = button.dataset.themeChoice;
      applySettings();
    });
  });
  el("fontFamilySelect")?.addEventListener("change", (event) => {
    state.settings.fontFamily = event.target.value;
    applySettings();
  });
  el("fontSizeRange")?.addEventListener("input", (event) => {
    state.settings.fontSize = Number(event.target.value);
    el("fontSizeValue").textContent = `${state.settings.fontSize} px`;
    applySettings();
  });
  el("lineHeightRange")?.addEventListener("input", (event) => {
    state.settings.lineHeight = Number(event.target.value);
    el("lineHeightValue").textContent = Number(event.target.value).toFixed(1);
    applySettings();
  });
  el("layoutSelect")?.addEventListener("change", (event) => {
    state.settings.layout = event.target.value;
    applySettings();
  });
  el("alignSelect")?.addEventListener("change", (event) => {
    state.settings.align = event.target.value;
    applySettings();
  });
  el("widthRange")?.addEventListener("input", (event) => {
    state.settings.width = Number(event.target.value);
    el("widthValue").textContent = `${state.settings.width} px`;
    applySettings();
  });
  el("resetSettingsBtn").addEventListener("click", () => {
    state.settings = { ...DEFAULTS, theme: state.settings.theme };
    if (isPdf) state.engine?.fitWidth?.();
    applySettings();
    toast("Reader settings reset", "success", 2000);
  });

  el("readerThemeToggle").addEventListener("click", () => {
    const order = ["light", "sepia", "dark"];
    const current = order.indexOf(state.settings.theme);
    state.settings.theme = order[(current + 1) % order.length];
    applySettings();
  });

  // PDF zoom
  el("pdfZoomIn")?.addEventListener("click", () => state.engine?.zoomIn?.());
  el("pdfZoomOut")?.addEventListener("click", () => state.engine?.zoomOut?.());
  el("pdfFitWidth")?.addEventListener("click", () => state.engine?.fitWidth?.());

  window.addEventListener("pagehide", flushProgress);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") flushProgress();
  });

  document.addEventListener("keydown", (event) => {
    const tag = document.activeElement?.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    if (event.key === "ArrowRight") state.engine?.next?.();
    if (event.key === "ArrowLeft") state.engine?.prev?.();
    if (event.key === "Escape") {
      state.selection = null;
      handleSelection(null);
    }
  });
}

function openDrawer(which) {
  drawer("contentsDrawer").show();
  const tab = which === "notes" ? el("noteTab") : el("tocTab");
  Tab.getOrCreateInstance(tab).show();
}

function addBookmark() {
  const mark = currentMark();
  if (!mark.cfi && !mark.page) {
    toast("Still loading this book…", "warning");
    return;
  }
  createAnnotation({
    kind: "bookmark",
    cfi: mark.cfi,
    page: mark.page,
    text: mark.label || "",
    note: "",
    color: "#ffd54f",
  });
}

async function openNoteDialog(context) {
  state.noteContext = context;
  el("noteQuote").textContent = context.text ? `“${context.text.slice(0, 200)}”` : "";
  el("noteQuote").classList.toggle("d-none", !context.text);
  el("noteText").value = "";

  const open = document.querySelector("dialog.drawer[open]");
  if (open && open !== el("noteDialog")) await Drawer.getOrCreateInstance(open).hide();
  drawer("noteDialog").show();
}

init();
