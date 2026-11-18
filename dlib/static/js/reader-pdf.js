/**
 * Self-hosted PDF.js adapter.
 *
 * The reader chrome (toolbar, footer, drawers, dialogs) lives in reader.html
 * and is shared with the EPUB engine. This module only renders pages, tracks
 * the current page, exposes zoom, and reports locations to reader.js.
 */

import * as pdfjsLib from "../vendor/pdfjs/pdf.min.mjs";

const asset = (path) => new URL(`../vendor/pdfjs/${path}`, import.meta.url).href;
pdfjsLib.GlobalWorkerOptions.workerSrc = asset("pdf.worker.min.mjs");

export async function openPdf({ book, container, scroller, initial, theme, callbacks }) {
  const source = new URL(book.file_url || `/api/books/${book.id}/file`, location.origin).href;
  let doc = null;
  let destroyed = false;
  let currentPage = 0;
  let totalPages = 0;
  let zoom = Math.max(50, Math.min(300, Number(initial.zoom) || 100));
  let scale = 1;
  let fitScale = 1;
  let ready = false;
  let renderObserver = null;
  let trackObserver = null;
  let scrollFrame = null;
  let firstViewport = null;
  const pages = [];
  const visibility = new Map();

  function notifyLocation() {
    const percent = totalPages ? Math.min(100, (currentPage / totalPages) * 100) : 0;
    callbacks.onLocation({
      location: String(currentPage || ""),
      page: currentPage,
      total_pages: totalPages,
      percent,
    });
  }

  function setCurrentPage(page) {
    const next = Math.max(1, Math.min(totalPages || 1, Number(page) || 1));
    if (next === currentPage) return;
    currentPage = next;
    notifyLocation();
  }

  function updatePageBox(record) {
    const base = record.base || firstViewport;
    record.el.style.width = `${Math.round(base.width * scale)}px`;
    record.el.style.aspectRatio = `${base.width} / ${base.height}`;
  }

  function resetRendered() {
    for (const record of pages) {
      if (!record) continue;
      try {
        record.task?.cancel();
      } catch (error) {
        /* ignore */
      }
      record.task = null;
      record.rendering = false;
      record.rendered = false;
      record.holder.replaceChildren();
    }
  }

  function applyScale() {
    if (!firstViewport) return;
    const available = Math.max(200, scroller.clientWidth - 32);
    fitScale = available / firstViewport.width;
    scale = fitScale * (zoom / 100);
    for (const record of pages) {
      if (!record) continue;
      updatePageBox(record);
    }
    callbacks.onZoom?.(Math.round(zoom));
  }

  async function renderPage(pageNumber) {
    const record = pages[pageNumber - 1];
    if (!record || record.rendered || record.rendering || destroyed) return;
    record.rendering = true;
    try {
      const page = await doc.getPage(pageNumber);
      if (destroyed) return;
      record.base = page.getViewport({ scale: 1 });
      const viewport = page.getViewport({ scale });
      updatePageBox(record);

      const outputScale = Math.min(2, window.devicePixelRatio || 1);
      const canvas = document.createElement("canvas");
      canvas.className = "d-block w-100 h-auto";
      canvas.width = Math.max(1, Math.floor(viewport.width * outputScale));
      canvas.height = Math.max(1, Math.floor(viewport.height * outputScale));
      canvas.setAttribute("role", "img");
      canvas.setAttribute("aria-label", `Page ${pageNumber}`);

      const context = canvas.getContext("2d", { alpha: false });
      context.fillStyle = "#ffffff";
      context.fillRect(0, 0, canvas.width, canvas.height);
      record.holder.replaceChildren(canvas);

      record.task = page.render({
        canvas,
        canvasContext: context,
        viewport,
        transform: outputScale === 1 ? null : [outputScale, 0, 0, outputScale, 0, 0],
      });
      await record.task.promise;
      record.task = null;
      record.rendered = true;
    } catch (error) {
      if (error?.name !== "RenderingCancelledException" && !destroyed) {
        console.error(`Could not render PDF page ${pageNumber}`, error);
      }
    } finally {
      record.rendering = false;
    }
  }

  function renderAround(pageNumber) {
    for (let page = pageNumber - 1; page <= pageNumber + 1; page += 1) {
      if (page >= 1 && page <= totalPages) renderPage(page);
    }
  }

  function buildPages() {
    const fragment = document.createDocumentFragment();
    for (let page = 1; page <= totalPages; page += 1) {
      const wrapper = document.createElement("div");
      wrapper.className = "position-relative bg-white shadow-sm overflow-hidden";
      wrapper.dataset.page = String(page);
      const holder = document.createElement("div");
      holder.className = "d-block w-100";
      const label = document.createElement("span");
      label.className = "badge theme-secondary position-absolute top-0 end-0 m-3";
      label.textContent = String(page);
      wrapper.append(holder, label);
      fragment.append(wrapper);
      const record = { el: wrapper, holder, base: firstViewport, rendered: false, rendering: false, task: null };
      pages.push(record);
      updatePageBox(record);
      visibility.set(page, 0);
    }
    container.append(fragment);
  }

  function observePages() {
    renderObserver = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) renderPage(Number(entry.target.dataset.page));
        }
      },
      { root: scroller, rootMargin: "800px 0px", threshold: 0.01 }
    );
    trackObserver = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          visibility.set(Number(entry.target.dataset.page), entry.intersectionRatio);
        }
        let best = currentPage;
        let bestRatio = 0;
        for (const [page, ratio] of visibility) {
          if (ratio > bestRatio) {
            best = page;
            bestRatio = ratio;
          }
        }
        if (bestRatio > 0) setCurrentPage(best);
      },
      { root: scroller, threshold: [0, 0.1, 0.25, 0.5, 0.75, 1] }
    );
    for (const record of pages) {
      renderObserver.observe(record.el);
      trackObserver.observe(record.el);
    }
  }

  async function resolveDestination(dest) {
    let target = dest;
    if (typeof target === "string") target = await doc.getDestination(target);
    if (!Array.isArray(target) || !target.length) return null;
    const ref = target[0];
    try {
      if (ref && typeof ref === "object") return (await doc.getPageIndex(ref)) + 1;
      if (typeof ref === "number") return ref + 1;
    } catch (error) {
      return null;
    }
    return null;
  }

  async function flattenOutline(items, depth = 0, output = []) {
    for (const item of items || []) {
      if (!item) continue;
      let page = null;
      try {
        page = await resolveDestination(item.dest);
      } catch (error) {
        page = null;
      }
      output.push({ label: item.title || "Untitled", page, depth });
      if (item.items?.length) await flattenOutline(item.items, depth + 1, output);
    }
    return output;
  }

  function scrollToPage(page) {
    const record = pages[page - 1];
    if (!record) return;
    const scrollerRect = scroller.getBoundingClientRect();
    const pageRect = record.el.getBoundingClientRect();
    const top = Math.max(0, scroller.scrollTop + pageRect.top - scrollerRect.top - 16);
    scroller.scrollTo({ top, behavior: "auto" });
    setCurrentPage(page);
    renderAround(page);
  }

  function markReady() {
    if (ready || destroyed) return;
    ready = true;
    callbacks.onReady();
  }

  try {
    doc = await pdfjsLib.getDocument({
      url: source,
      cMapUrl: asset("cmaps/"),
      cMapPacked: true,
      standardFontDataUrl: asset("standard_fonts/"),
      wasmUrl: asset("wasm/"),
      iccUrl: asset("iccs/"),
    }).promise;

    totalPages = doc.numPages;
    const firstPage = await doc.getPage(1);
    firstViewport = firstPage.getViewport({ scale: 1 });
    applyScale();
    buildPages();
    observePages();

    let startPage = Math.max(1, Number(initial.page) || 1);
    const percent = Number(initial.percent) || 0;
    if (startPage <= 1 && percent > 0) {
      startPage = Math.max(1, Math.round((percent / 100) * totalPages));
    }
    currentPage = Math.min(totalPages, startPage);
    await renderPage(currentPage);
    scrollToPage(currentPage);
    notifyLocation();
    markReady();
    renderAround(currentPage);

    if (doc.getOutline) {
      try {
        const outline = await doc.getOutline();
        callbacks.onToc(await flattenOutline(outline));
      } catch (error) {
        callbacks.onToc([]);
      }
    } else {
      callbacks.onToc([]);
    }
  } catch (error) {
    callbacks.onError(new Error(`Could not open the PDF: ${error.message}`));
    throw error;
  }

  scroller.addEventListener(
    "scroll",
    () => {
      if (scrollFrame !== null) return;
      scrollFrame = requestAnimationFrame(() => {
        scrollFrame = null;
        if (!ready || destroyed) return;
        // Keep memory bounded: only pages near the viewport stay rasterized.
        for (let page = 1; page <= totalPages; page += 1) {
          const record = pages[page - 1];
          if (record?.rendered && Math.abs(page - currentPage) > 5) {
            record.rendered = false;
            record.holder.replaceChildren();
          }
        }
      });
    },
    { passive: true }
  );

  const onResize = () => {
    if (destroyed || !ready) return;
    const page = currentPage;
    applyScale();
    resetRendered();
    renderAround(page);
    requestAnimationFrame(() => scrollToPage(page));
  };
  window.addEventListener("resize", onResize);

  return {
    setAnnotations() {
      /* Page-linked notes and bookmarks are listed in the shared drawer. */
    },
    applySettings() {
      /* PDF typography is fixed by the document. */
    },
    async setTheme() {
      /* Chrome follows data-bs-theme; pages stay paper-white. */
    },
    zoomIn() {
      zoom = Math.min(300, zoom + 10);
      applyScale();
      resetRendered();
      renderAround(currentPage);
      requestAnimationFrame(() => scrollToPage(currentPage));
    },
    zoomOut() {
      zoom = Math.max(50, zoom - 10);
      applyScale();
      resetRendered();
      renderAround(currentPage);
      requestAnimationFrame(() => scrollToPage(currentPage));
    },
    fitWidth() {
      zoom = 100;
      applyScale();
      resetRendered();
      renderAround(currentPage);
      requestAnimationFrame(() => scrollToPage(currentPage));
    },
    async goToPage(page) {
      const next = Math.max(1, Math.min(totalPages, Number(page) || 1));
      await renderPage(next);
      scrollToPage(next);
    },
    async next() {
      await this.goToPage(currentPage + 1);
    },
    async prev() {
      await this.goToPage(currentPage - 1);
    },
    async goTo() {
      /* CFIs do not apply to PDFs. */
    },
    destroy() {
      destroyed = true;
      window.removeEventListener("resize", onResize);
      renderObserver?.disconnect();
      trackObserver?.disconnect();
      if (scrollFrame !== null) cancelAnimationFrame(scrollFrame);
      resetRendered();
      try {
        doc?.destroy();
      } catch (error) {
        /* ignore */
      }
    },
  };
}
