import { Drawer, Menu } from "../vendor/bootstrap.bundle.js";
import { api } from "./api.js";
import {
  coverMarkup,
  debounce,
  escapeHtml,
  formatBytes,
  setupThemeToggle,
  toast,
} from "./ui.js";

const el = (id) => document.getElementById(id);
const drawer = (id) => Drawer.getOrCreateInstance(el(id));
const SUPPORTED = /\.(epub|mobi|azw3|fb2|cbz|pdf)$/i;
const PER_PAGE = 24;

const state = {
  page: 1,
  pages: 1,
  q: "",
  format: "",
  status: "",
  collection: "",
  sort: "added_desc",
  view: "grid",
  selected: new Set(),
  books: new Map(),
  collections: [],
  editingBook: null,
  pendingCoverUrl: "",
  metadataResults: [],
  confirmAction: null,
  uploading: false,
  scanTimer: null,
};

function init() {
  try {
    const savedView = localStorage.getItem("library-view");
    if (savedView === "grid" || savedView === "list") state.view = savedView;
  } catch (error) {
    /* ignore */
  }
  setupThemeToggle(el("themeToggle"));
  bindEvents();
  loadBooks(true);
  loadContinue();
  loadCollections();
}

/* ------------------------------------------------------------------ */
/* Data loading                                                       */
/* ------------------------------------------------------------------ */

async function loadBooks(reset = true) {
  if (reset) {
    state.page = 1;
    state.books.clear();
  }
  setLoading(true);
  const params = new URLSearchParams({
    page: String(state.page),
    per_page: String(PER_PAGE),
    sort: state.sort,
  });
  if (state.q) params.set("q", state.q);
  if (state.format) params.set("format", state.format);
  if (state.status) params.set("status", state.status);
  if (state.collection) params.set("collection", state.collection);

  try {
    const data = await api("/api/books?" + params.toString());
    data.books.forEach((book) => state.books.set(book.id, book));
    state.pages = data.pages;
    appendBooks(data.books, reset);
    el("bookCount").textContent = `${data.total} book${data.total === 1 ? "" : "s"}`;
    el("libraryHeading").textContent = hasFilters() ? "Results" : "Library";
    const empty = data.total === 0;
    el("emptyState").classList.toggle("d-none", !empty);
    el("emptyMessage").textContent = hasFilters()
      ? "Nothing matches those filters. Try clearing them."
      : "Upload a book or run a folder scan to get started.";
    el("loadMoreBtn").classList.toggle("d-none", state.page >= data.pages || empty);
    el("pageInfo").textContent = empty ? "" : `Showing ${data.books.length ? (state.page - 1) * PER_PAGE + 1 : 0}–${(state.page - 1) * PER_PAGE + data.books.length} of ${data.total}`;
    el("clearFiltersBtn").hidden = !hasFilters();
  } catch (error) {
    toast(error.message, "danger");
    el("emptyState").classList.remove("d-none");
    el("emptyMessage").textContent = "Could not load the library.";
  } finally {
    setLoading(false);
    setView(state.view, false);
  }
}

async function loadContinue() {
  try {
    const data = await api("/api/books?status=reading&sort=recent&per_page=12");
    const books = data.books.filter((book) => book.progress.percent > 0);
    el("continueSection").classList.toggle("d-none", books.length === 0);
    el("continueShelf").innerHTML = books.map(shelfMarkup).join("");
  } catch (error) {
    el("continueSection").classList.add("d-none");
  }
}

async function loadCollections() {
  try {
    const data = await api("/api/collections");
    state.collections = data.collections;
    syncCollectionOptions(data.collections);
    renderCollections(data.collections);
  } catch (error) {
    toast(error.message, "danger");
  }
}

async function refreshAll() {
  await Promise.all([loadBooks(true), loadContinue(), loadCollections()]);
}

function setLoading(loading) {
  el("loadingState").classList.toggle("d-none", !loading);
  if (loading) {
    el("emptyState").classList.add("d-none");
    el("bookGrid").classList.add("d-none");
    el("bookList").classList.add("d-none");
  }
}

/* ------------------------------------------------------------------ */
/* Rendering                                                          */
/* ------------------------------------------------------------------ */

function hasFilters() {
  return Boolean(state.q || state.format || state.status || state.collection || state.sort !== "added_desc");
}

function statusText(book) {
  const percent = Math.round(book.progress.percent);
  if (book.progress.status === "finished") return "Finished";
  if (percent > 0) return `${percent}% read`;
  return "Unread";
}

function coverCacheUrl(book) {
  if (!book.cover_url) return "";
  const version = encodeURIComponent(book.updated_at || book.added_at || "");
  return `${book.cover_url}?v=${version}`;
}

function coverFor(book) {
  if (!book.cover_url) return coverMarkup(book);
  const title = escapeHtml(book.title || "Untitled");
  return `<img class="position-absolute top-0 start-0 w-100 h-100 object-fit-cover" src="${escapeHtml(coverCacheUrl(book))}" alt="${title}" loading="lazy" decoding="async" data-cover-for="${book.id}">`;
}

function bookMenu(book) {
  return `
    <div class="position-relative flex-shrink-0">
      <button class="btn btn-outline theme-secondary btn-sm btn-icon" data-bs-toggle="menu" data-bs-placement="bottom-end" aria-expanded="false" aria-label="More actions" type="button">
        <i class="bi bi-three-dots-vertical"></i>
      </button>
      <div class="menu">
        <button class="menu-item" type="button" data-action="edit" data-id="${book.id}">
          <i class="bi bi-pencil menu-item-icon"></i>Edit details
        </button>
        <a class="menu-item" href="/api/books/${book.id}/file?download=1">
          <i class="bi bi-download menu-item-icon"></i>Download
        </a>
        <hr class="menu-divider">
        <button class="menu-item theme-danger" type="button" data-action="delete" data-id="${book.id}">
          <i class="bi bi-trash menu-item-icon"></i>Delete
        </button>
      </div>
    </div>`;
}

function cardMarkup(book) {
  const percent = Math.round(book.progress.percent);
  const checked = state.selected.has(book.id) ? "checked" : "";
  const tags = book.tags.slice(0, 3).join(" · ");
  return `
  <div class="col" data-book-col="${book.id}">
    <article class="card h-100 shadow-sm" data-id="${book.id}">
      <div class="position-relative">
        <a class="d-block text-decoration-none" href="/read/${book.id}" aria-label="Read ${escapeHtml(book.title)}">
          <div class="ratio-auto position-relative rounded-top overflow-hidden bg-2" style="--bs-ratio: 2 / 3;">
            ${coverFor(book)}
          </div>
        </a>
        <span class="badge theme-secondary position-absolute top-0 start-0 m-3">${escapeHtml(book.format)}</span>
        <label class="position-absolute top-0 end-0 m-3 d-inline-flex align-items-center justify-content-center bg-body rounded-5 p-2 shadow-sm" title="Select book">
          <input class="check m-0" type="checkbox" data-select="${book.id}" ${checked} aria-label="Select ${escapeHtml(book.title)}">
        </label>
        ${percent > 0 ? `<div class="progress position-absolute bottom-0 start-0 end-0 rounded-0" role="progressbar" aria-label="Reading progress" aria-valuenow="${percent}" aria-valuemin="0" aria-valuemax="100"><div class="progress-bar" style="width: ${Math.min(100, percent)}%"></div></div>` : ""}
      </div>
      <div class="card-body d-flex flex-column gap-3">
        <div class="min-w-0">
          <a class="d-block fw-semibold text-truncate text-decoration-none" href="/read/${book.id}">${escapeHtml(book.title)}</a>
          <div class="fg-secondary fs-sm text-truncate">${escapeHtml(book.author || "Unknown author")}</div>
        </div>
        <div class="fg-secondary fs-sm text-truncate">${escapeHtml(statusText(book))}${tags ? " · " + escapeHtml(tags) : ""}</div>
        <div class="d-flex align-items-center gap-3 mt-auto">
          <a class="btn btn-solid theme-primary btn-sm flex-grow-1" href="/read/${book.id}">Read</a>
          ${bookMenu(book)}
        </div>
      </div>
    </article>
  </div>`;
}

function listMarkup(book) {
  const percent = Math.round(book.progress.percent);
  const checked = state.selected.has(book.id) ? "checked" : "";
  return `
  <div class="list-group-item p-5" data-id="${book.id}">
    <div class="d-flex align-items-center gap-5">
      <input class="check flex-shrink-0" type="checkbox" data-select="${book.id}" ${checked} aria-label="Select ${escapeHtml(book.title)}">
      <a class="flex-shrink-0 rounded-3 overflow-hidden" href="/read/${book.id}" tabindex="-1" aria-hidden="true">
        ${book.cover_url
          ? `<img class="d-block object-fit-cover" width="48" height="72" src="${escapeHtml(coverCacheUrl(book))}" alt="" loading="lazy" decoding="async" data-cover-for="${book.id}">`
          : `<span class="d-flex align-items-center justify-content-center bg-2 fg-secondary" style="width: 48px; height: 72px;"><i class="bi bi-book"></i></span>`}
      </a>
      <div class="flex-grow-1 min-w-0">
        <a class="d-block fw-semibold text-truncate text-decoration-none" href="/read/${book.id}">${escapeHtml(book.title)}</a>
        <div class="fg-secondary fs-sm text-truncate">${escapeHtml(book.author || "Unknown author")} · ${escapeHtml(book.format)} · ${formatBytes(book.file_size)} · ${escapeHtml(statusText(book))}</div>
        <div class="progress mt-3" role="progressbar" aria-label="Reading progress" aria-valuenow="${percent}" aria-valuemin="0" aria-valuemax="100">
          <div class="progress-bar" style="width: ${Math.min(100, percent)}%"></div>
        </div>
      </div>
      <div class="d-flex align-items-center gap-3 flex-shrink-0">
        <a class="btn btn-solid theme-primary btn-sm d-none sm:d-inline-flex" href="/read/${book.id}">Read</a>
        ${bookMenu(book)}
      </div>
    </div>
  </div>`;
}

function shelfMarkup(book) {
  const percent = Math.round(book.progress.percent);
  return `
  <a class="col-6 sm:col-4 md:col-3 xl:col-2 text-decoration-none" href="/read/${book.id}">
    <div class="card h-100 shadow-sm">
      <div class="ratio-auto position-relative rounded-top overflow-hidden bg-2" style="--bs-ratio: 2 / 3;">
        ${coverFor(book)}
      </div>
      <div class="card-body p-5">
        <div class="fw-semibold fs-sm text-truncate">${escapeHtml(book.title)}</div>
        <div class="fg-secondary fs-sm mt-2"><i class="bi bi-bookmark me-2"></i>${percent}%</div>
      </div>
    </div>
  </a>`;
}

function appendBooks(books, reset) {
  const grid = el("bookGrid");
  const list = el("bookList");
  if (reset) {
    grid.innerHTML = "";
    list.innerHTML = "";
  }
  grid.insertAdjacentHTML("beforeend", books.map(cardMarkup).join(""));
  list.insertAdjacentHTML("beforeend", books.map(listMarkup).join(""));
  grid.classList.remove("d-none");
  list.classList.add("d-none");
  if (state.view === "list") {
    grid.classList.add("d-none");
    list.classList.remove("d-none");
  }
  updateBulkBar();
}

function setView(view, scroll = false) {
  state.view = view === "list" ? "list" : "grid";
  try {
    localStorage.setItem("library-view", state.view);
  } catch (error) {
    /* ignore */
  }
  el("bookGrid").classList.toggle("d-none", state.view !== "grid");
  el("bookList").classList.toggle("d-none", state.view !== "list");
  el("viewGridBtn").classList.toggle("active", state.view === "grid");
  el("viewGridBtn").setAttribute("aria-pressed", state.view === "grid" ? "true" : "false");
  el("viewListBtn").classList.toggle("active", state.view === "list");
  el("viewListBtn").setAttribute("aria-pressed", state.view === "list" ? "true" : "false");
  if (scroll) window.scrollTo({ top: 0, behavior: "smooth" });
}

function syncCollectionOptions(collections) {
  const options = collections
    .map((collection) => `<option value="${collection.id}">${escapeHtml(collection.name)} (${collection.book_count})</option>`)
    .join("");
  const filter = el("collectionFilter");
  const bulk = el("bulkCollectionSelect");
  const filterValue = filter.value;
  const bulkValue = bulk.value;
  filter.innerHTML = '<option value="">All shelves</option>' + options;
  bulk.innerHTML = '<option value="">Choose shelf…</option>' + options;
  if ([...filter.options].some((option) => option.value === filterValue)) filter.value = filterValue;
  if ([...bulk.options].some((option) => option.value === bulkValue)) bulk.value = bulkValue;
}

function renderCollections(collections) {
  const container = el("collectionsList");
  if (!collections.length) {
    container.innerHTML = '<div class="fg-secondary fs-sm py-3">No shelves yet. Create one above.</div>';
    return;
  }
  container.innerHTML = collections
    .map(
      (collection) => `
      <div class="list-group-item d-flex align-items-center gap-3 p-5">
        <div class="flex-grow-1 min-w-0">
          <div class="fw-semibold text-truncate">${escapeHtml(collection.name)}</div>
          <div class="fg-secondary fs-sm text-truncate">${collection.book_count} book${collection.book_count === 1 ? "" : "s"}${collection.description ? " · " + escapeHtml(collection.description) : ""}</div>
        </div>
        <button class="btn btn-sm btn-outline theme-secondary" type="button" data-collection-action="rename" data-id="${collection.id}">Rename</button>
        <button class="btn btn-sm btn-outline theme-danger" type="button" data-collection-action="delete" data-id="${collection.id}">Delete</button>
      </div>`
    )
    .join("");
}

/* ------------------------------------------------------------------ */
/* Selection & bulk actions                                           */
/* ------------------------------------------------------------------ */

function updateBulkBar() {
  const count = state.selected.size;
  el("selectedCount").textContent = String(count);
  el("bulkBar").classList.toggle("d-none", count === 0);
  document.querySelectorAll("[data-select]").forEach((checkbox) => {
    checkbox.checked = state.selected.has(Number(checkbox.dataset.select));
  });
}

function clearSelection() {
  state.selected.clear();
  updateBulkBar();
}

async function bulkRequest(action, extra = {}) {
  const ids = [...state.selected];
  if (!ids.length) return;
  await api("/api/books/bulk", { method: "POST", body: { action, ids, ...extra } });
}

function bulkCollectionId() {
  const value = el("bulkCollectionSelect").value;
  if (!value) {
    toast("Choose a shelf first", "warning");
    return null;
  }
  return Number(value);
}

/* ------------------------------------------------------------------ */
/* Edit panel                                                         */
/* ------------------------------------------------------------------ */

function openEdit(book) {
  state.editingBook = book;
  state.pendingCoverUrl = "";
  state.metadataResults = [];
  el("editForm").reset();
  el("editTitle").value = book.title || "";
  el("editAuthor").value = book.author || "";
  el("editYear").value = book.published_year || "";
  el("editLanguage").value = book.language || "";
  el("editPublisher").value = book.publisher || "";
  el("editIsbn").value = book.isbn || "";
  el("editTags").value = (book.tags || []).join(", ");
  el("editDescription").value = book.description || "";
  el("metadataQuery").value = [book.title, book.author].filter(Boolean).join(" ");
  el("metadataResults").innerHTML = '<div class="fg-secondary fs-sm">Search to pull title, author, description and cover.</div>';
  el("coverFileInput").value = "";
  setCoverPreview(book.cover_url ? coverCacheUrl(book) : "", book.title);
  drawer("editDialog").show();
}

function setCoverPreview(url, title = "") {
  const img = el("editCoverImg");
  const placeholder = el("editCoverPlaceholder");
  if (url) {
    img.src = url;
    img.classList.remove("d-none");
    placeholder.classList.add("d-none");
  } else {
    img.classList.add("d-none");
    img.removeAttribute("src");
    placeholder.classList.remove("d-none");
    placeholder.innerHTML = `<i class="bi bi-image fs-2xl mb-2"></i><span class="fs-sm">${escapeHtml(title || "No cover")}</span>`;
  }
}

async function searchMetadata() {
  const query = el("metadataQuery").value.trim();
  if (query.length < 2) {
    toast("Type a title, author or ISBN", "warning");
    return;
  }
  const button = el("metadataSearchBtn");
  button.disabled = true;
  button.textContent = "Searching…";
  el("metadataResults").innerHTML = '<div class="fg-secondary fs-sm p-3">Looking up Google Books and OpenLibrary…</div>';
  try {
    const data = await api("/api/metadata/search?q=" + encodeURIComponent(query));
    state.metadataResults = data.results;
    if (!data.results.length) {
      el("metadataResults").innerHTML = '<div class="fg-secondary fs-sm p-3">No matches found.</div>';
      return;
    }
    el("metadataResults").innerHTML = data.results
      .map(
        (result, index) => `
        <button type="button" class="list-group-item list-group-item-action p-5" data-meta-index="${index}">
          <div class="fw-semibold">${escapeHtml(result.title)}</div>
          <div class="fs-sm">${escapeHtml(result.author || "Unknown author")}${result.published_year ? " · " + escapeHtml(result.published_year) : ""}</div>
          <div class="fg-secondary fs-sm">${escapeHtml(result.source)}${result.publisher ? " · " + escapeHtml(result.publisher) : ""}</div>
        </button>`
      )
      .join("");
  } catch (error) {
    el("metadataResults").innerHTML = "";
    toast(error.message, "danger");
  } finally {
    button.disabled = false;
    button.textContent = "Search";
  }
}

function applyMetadataResult(index) {
  const result = state.metadataResults[index];
  if (!result) return;
  el("editTitle").value = result.title || el("editTitle").value;
  el("editAuthor").value = result.author || el("editAuthor").value;
  el("editYear").value = result.published_year || el("editYear").value;
  el("editPublisher").value = result.publisher || el("editPublisher").value;
  el("editLanguage").value = result.language || el("editLanguage").value;
  el("editIsbn").value = result.isbn || el("editIsbn").value;
  if (result.description) el("editDescription").value = result.description;
  if (result.tags && result.tags.length) el("editTags").value = result.tags.join(", ");
  if (result.cover_url) {
    state.pendingCoverUrl = result.cover_url;
    setCoverPreview(result.cover_url, result.title);
  }
  toast("Details filled in. Save to apply.", "info");
}

/* ------------------------------------------------------------------ */
/* Upload                                                             */
/* ------------------------------------------------------------------ */

async function readDirectory(entry, prefix, output) {
  const reader = entry.createReader();
  while (true) {
    const entries = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
    if (!entries.length) break;
    for (const child of entries) {
      await walkEntry(child, prefix, output);
    }
  }
}

async function walkEntry(entry, prefix, output) {
  if (entry.isFile) {
    const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
    output.push({ file, path: `${prefix}${file.name}` });
  } else if (entry.isDirectory) {
    await readDirectory(entry, `${prefix}${entry.name}/`, output);
  }
}

async function filesFromDrop(dataTransfer) {
  const entries = [];
  if (dataTransfer.items && dataTransfer.items.length) {
    for (const item of dataTransfer.items) {
      const entry = item.webkitGetAsEntry && item.webkitGetAsEntry();
      if (entry) entries.push(entry);
    }
  }
  if (!entries.length) {
    return Array.from(dataTransfer.files || []).map((file) => ({ file, path: file.name }));
  }
  const output = [];
  for (const entry of entries) {
    await walkEntry(entry, "", output);
  }
  return output;
}

function uploadOne(file, path, autoMeta, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/books/upload");
    xhr.responseType = "text";
    xhr.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    });
    xhr.addEventListener("load", () => {
      let payload = null;
      try {
        payload = JSON.parse(xhr.responseText);
      } catch (error) {
        payload = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(payload);
      } else {
        const message =
          (payload && payload.error) ||
          (payload && payload.errors && payload.errors[0] && payload.errors[0].error) ||
          `Upload failed (HTTP ${xhr.status})`;
        reject(new Error(message));
      }
    });
    xhr.addEventListener("error", () => reject(new Error("Network error during upload")));
    const form = new FormData();
    form.append("files", file, file.name);
    form.append("paths", path || file.name);
    form.append("auto_meta", autoMeta ? "1" : "0");
    xhr.send(form);
  });
}

async function uploadFiles(items) {
  if (state.uploading) return;
  const supported = items.filter((item) => SUPPORTED.test(item.file.name || item.path));
  const queue = el("uploadQueue");
  queue.innerHTML = "";
  if (!supported.length) {
    toast("No supported book files in that drop", "warning");
    return;
  }
  state.uploading = true;
  el("uploadProgressWrap").classList.remove("d-none");
  el("uploadCounter").textContent = `0 / ${supported.length}`;
  el("uploadCurrent").textContent = "Starting…";
  const autoMeta = supported.length <= 3;
  let done = 0;
  const addedBooks = [];

  for (const item of supported) {
    const row = document.createElement("li");
    row.className = "list-group-item d-flex justify-content-between align-items-center gap-2";
    row.innerHTML = `<span class="text-truncate">${escapeHtml(item.path || item.file.name)}</span><span class="badge theme-secondary flex-shrink-0">Queued</span>`;
    queue.append(row);
    queue.scrollTop = queue.scrollHeight;
    const badge = row.querySelector(".badge");
    el("uploadCurrent").textContent = item.file.name;
    try {
      const result = await uploadOne(item.file, item.path, autoMeta, (progress) => {
        badge.textContent = `${Math.round(progress * 100)}%`;
        el("uploadProgressBar").style.width = `${Math.round(((done + progress) / supported.length) * 100)}%`;
      });
      done += 1;
      badge.textContent = "Added";
      badge.className = "badge theme-success flex-shrink-0";
      if (result && result.added && result.added.length) addedBooks.push(result.added[0]);
      if (result && result.errors && result.errors.length) {
        badge.textContent = "Skipped";
        badge.className = "badge theme-warning flex-shrink-0";
      }
    } catch (error) {
      done += 1;
      badge.textContent = "Failed";
      badge.className = "badge theme-danger flex-shrink-0";
      row.title = error.message;
      toast(`${item.file.name}: ${error.message}`, "danger", 6000);
    }
    el("uploadCounter").textContent = `${done} / ${supported.length}`;
    el("uploadProgressBar").style.width = `${Math.round((done / supported.length) * 100)}%`;
  }

  state.uploading = false;
  el("uploadCurrent").textContent = "Finished";
  if (addedBooks.length) {
    toast(`${addedBooks.length} book${addedBooks.length === 1 ? "" : "s"} added`, "success");
    await refreshAll();
    const needsMetadata = addedBooks.find((entry) => entry.needs_metadata);
    if (needsMetadata) {
      await drawer("uploadDialog").hide();
      openEdit(needsMetadata.book);
    }
  } else {
    toast("No books were added", "warning");
  }
}

/* ------------------------------------------------------------------ */
/* Scan                                                               */
/* ------------------------------------------------------------------ */

async function startScan() {
  try {
    await api("/api/scan", { method: "POST" });
    showScanBanner("Scanning library folder…");
    if (state.scanTimer) clearInterval(state.scanTimer);
    state.scanTimer = setInterval(pollScan, 1500);
    pollScan();
  } catch (error) {
    toast(error.message, "danger");
  }
}

function showScanBanner(message, variant = "info") {
  const banner = el("scanBanner");
  banner.className = `alert theme-${variant} d-flex align-items-center gap-3`;
  const spinner = banner.querySelector(".spinner-border");
  if (spinner) spinner.classList.toggle("d-none", variant !== "info");
  el("scanMessage").textContent = message;
}

async function pollScan() {
  try {
    const status = await api("/api/scan/status");
    if (status.running) {
      const processed = status.total ? Math.min(status.processed, status.total) : status.processed;
      showScanBanner(`Scanning ${processed}${status.total ? " / " + status.total : ""} files · ${status.added} added · ${status.removed} removed`);
      return;
    }
    clearInterval(state.scanTimer);
    state.scanTimer = null;
    if (status.error) {
      showScanBanner("Scan failed: " + status.error, "danger");
      toast("Scan failed: " + status.error, "danger", 7000);
      return;
    }
    showScanBanner(`Scan complete · ${status.added} added · ${status.updated} updated · ${status.removed} removed`, "success");
    setTimeout(() => el("scanBanner").classList.add("d-none"), 6000);
    await refreshAll();
  } catch (error) {
    clearInterval(state.scanTimer);
    state.scanTimer = null;
    toast(error.message, "danger");
  }
}

/* ------------------------------------------------------------------ */
/* Events                                                             */
/* ------------------------------------------------------------------ */

function bindEvents() {
  const search = el("searchInput");
  const runSearch = debounce(() => {
    state.q = search.value.trim();
    el("clearSearchBtn").classList.toggle("d-none", !state.q);
    loadBooks(true);
  }, 300);
  search.addEventListener("input", runSearch);
  el("clearSearchBtn").addEventListener("click", () => {
    search.value = "";
    state.q = "";
    el("clearSearchBtn").classList.add("d-none");
    loadBooks(true);
    search.focus();
  });

  for (const [id, key] of [
    ["formatFilter", "format"],
    ["statusFilter", "status"],
    ["collectionFilter", "collection"],
    ["sortSelect", "sort"],
  ]) {
    el(id).addEventListener("change", (event) => {
      state[key] = event.target.value;
      loadBooks(true);
    });
  }
  el("clearFiltersBtn").addEventListener("click", () => {
    state.q = "";
    state.format = "";
    state.status = "";
    state.collection = "";
    state.sort = "added_desc";
    el("searchInput").value = "";
    el("formatFilter").value = "";
    el("statusFilter").value = "";
    el("collectionFilter").value = "";
    el("sortSelect").value = "added_desc";
    loadBooks(true);
  });

  el("viewGridBtn").addEventListener("click", () => setView("grid", true));
  el("viewListBtn").addEventListener("click", () => setView("list", true));
  el("loadMoreBtn").addEventListener("click", () => {
    state.page += 1;
    loadBooks(false);
  });
  el("showAllReadingBtn").addEventListener("click", () => {
    state.status = "reading";
    el("statusFilter").value = "reading";
    loadBooks(true);
  });

  document.addEventListener(
    "click",
    (event) => {
      if (!event.target.closest(".menu")) return;
      const openToggle = document.querySelector('[data-bs-toggle="menu"][aria-expanded="true"]');
      if (openToggle) Menu.getOrCreateInstance(openToggle).hide();
    },
    true,
  );

  document.addEventListener("click", async (event) => {
    const actionButton = event.target.closest("[data-action]");
    if (actionButton) {
      const id = Number(actionButton.dataset.id);
      const book = state.books.get(id);
      if (actionButton.dataset.action === "edit" && book) openEdit(book);
      if (actionButton.dataset.action === "delete" && book) {
        askConfirm(
          {
            title: "Delete book?",
            message: `“${book.title}” and its file will be removed from the library folder. This cannot be undone.`,
            confirmLabel: "Delete",
          },
          async () => {
            await api(`/api/books/${book.id}`, { method: "DELETE" });
            state.books.delete(book.id);
            state.selected.delete(book.id);
            toast("Book deleted", "success");
            refreshAll();
          }
        );
      }
      return;
    }

    const metaItem = event.target.closest("[data-meta-index]");
    if (metaItem) applyMetadataResult(Number(metaItem.dataset.metaIndex));

    const collectionButton = event.target.closest("[data-collection-action]");
    if (collectionButton) {
      const id = Number(collectionButton.dataset.id);
      const collection = state.collections.find((item) => item.id === id);
      if (!collection) return;
      if (collectionButton.dataset.collectionAction === "rename") {
        const name = window.prompt("New shelf name", collection.name);
        if (!name || !name.trim()) return;
        try {
          await api(`/api/collections/${id}`, { method: "PATCH", body: { name: name.trim() } });
          await loadCollections();
          toast("Shelf renamed", "success");
        } catch (error) {
          toast(error.message, "danger");
        }
      } else {
        askConfirm(
          {
            title: "Delete shelf?",
            message: `“${collection.name}” will be removed. The books themselves stay in your library.`,
            confirmLabel: "Delete shelf",
          },
          async () => {
            await api(`/api/collections/${id}`, { method: "DELETE" });
            if (state.collection === String(id)) {
              state.collection = "";
              el("collectionFilter").value = "";
            }
            toast("Shelf deleted", "success");
            refreshAll();
          }
        );
      }
    }
  });

  document.addEventListener("error", (event) => {
    const img = event.target;
    if (!(img instanceof HTMLImageElement) || !img.dataset.coverFor) return;
    const fallback = document.createElement("div");
    fallback.className = "d-flex flex-column align-items-center justify-content-center bg-2 fg-secondary p-3 text-center rounded-3";
    if (img.closest(".ratio-auto")) fallback.classList.add("position-absolute", "top-0", "start-0");
    fallback.style.width = img.getAttribute("width") ? `${img.getAttribute("width")}px` : "100%";
    fallback.style.height = img.getAttribute("height") ? `${img.getAttribute("height")}px` : "100%";
    fallback.innerHTML = '<i class="bi bi-book fs-2xl mb-2"></i>';
    const label = document.createElement("span");
    label.className = "fs-sm text-truncate w-100";
    label.textContent = img.alt || "Untitled";
    fallback.append(label);
    img.replaceWith(fallback);
  }, true);

  // Selection checkboxes
  document.addEventListener("change", (event) => {
    const box = event.target.closest("[data-select]");
    if (!box) return;
    const id = Number(box.dataset.select);
    if (box.checked) state.selected.add(id);
    else state.selected.delete(id);
    updateBulkBar();
  });

  el("bulkClearBtn").addEventListener("click", clearSelection);
  el("bulkDownloadBtn").addEventListener("click", () => {
    if (!state.selected.size) return;
    location.href = "/api/books/bulk/download?ids=" + encodeURIComponent([...state.selected].join(","));
  });
  el("bulkAddBtn").addEventListener("click", async () => {
    const collectionId = bulkCollectionId();
    if (!collectionId) return;
    await bulkRequest("add_to_collection", { collection_id: collectionId });
    toast("Added to shelf", "success");
    clearSelection();
    refreshAll();
  });
  el("bulkMoveBtn").addEventListener("click", async () => {
    const collectionId = bulkCollectionId();
    if (!collectionId) return;
    await bulkRequest("move_to_collection", { collection_id: collectionId });
    toast("Moved to shelf", "success");
    clearSelection();
    refreshAll();
  });
  el("bulkRemoveBtn").addEventListener("click", async () => {
    const collectionId = bulkCollectionId();
    if (!collectionId) return;
    await bulkRequest("remove_from_collection", { collection_id: collectionId });
    toast("Removed from shelf", "success");
    clearSelection();
    refreshAll();
  });
  el("bulkDeleteBtn").addEventListener("click", () => {
    if (!state.selected.size) return;
    const count = state.selected.size;
    askConfirm(
      {
        title: "Delete selected books?",
        message: `${count} file${count === 1 ? "" : "s"} will be deleted from storage. This cannot be undone.`,
        confirmLabel: `Delete ${count}`,
      },
      async () => {
        await bulkRequest("delete", { delete_files: true });
        toast(`Deleted ${count} book${count === 1 ? "" : "s"}`, "success");
        clearSelection();
        refreshAll();
      }
    );
  });

  // Upload panel
  el("uploadDialog").addEventListener("show.bs.drawer", () => {
    if (state.uploading) return;
    el("uploadQueue").innerHTML = "";
    el("uploadProgressWrap").classList.add("d-none");
    el("uploadProgressBar").style.width = "0%";
    el("uploadHint").textContent = "Metadata and covers are extracted automatically.";
  });
  el("fileInput").addEventListener("change", (event) => {
    uploadFiles(Array.from(event.target.files || []).map((file) => ({ file, path: file.name })));
    event.target.value = "";
  });
  el("folderInput").addEventListener("change", (event) => {
    const files = Array.from(event.target.files || []);
    uploadFiles(files.map((file) => ({ file, path: file.webkitRelativePath || file.name })));
    event.target.value = "";
  });
  const dropzone = el("dropzone");
  dropzone.addEventListener("dragover", (event) => {
    event.preventDefault();
    dropzone.classList.replace("bg-1", "bg-2");
  });
  dropzone.addEventListener("dragleave", () => dropzone.classList.replace("bg-2", "bg-1"));
  dropzone.addEventListener("drop", async (event) => {
    event.preventDefault();
    dropzone.classList.replace("bg-2", "bg-1");
    try {
      const items = await filesFromDrop(event.dataTransfer);
      await uploadFiles(items);
    } catch (error) {
      toast("Could not read the dropped files", "danger");
    }
  });

  // Edit panel
  el("metadataSearchBtn").addEventListener("click", searchMetadata);
  el("metadataQuery").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      searchMetadata();
    }
  });
  el("coverFileInput").addEventListener("change", (event) => {
    const file = (event.target.files || [])[0];
    if (!file) return;
    state.pendingCoverUrl = "";
    setCoverPreview(URL.createObjectURL(file), el("editTitle").value);
  });
  el("editForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const book = state.editingBook;
    if (!book) return;
    const submit = el("editDialog").querySelector("button[type=submit]");
    submit.disabled = true;
    try {
      await api(`/api/books/${book.id}`, {
        method: "PATCH",
        body: {
          title: el("editTitle").value,
          author: el("editAuthor").value,
          published_year: el("editYear").value,
          language: el("editLanguage").value,
          publisher: el("editPublisher").value,
          isbn: el("editIsbn").value,
          tags: el("editTags").value,
          description: el("editDescription").value,
        },
      });
      const coverFile = (el("coverFileInput").files || [])[0];
      if (coverFile) {
        const form = new FormData();
        form.append("cover", coverFile);
        await api(`/api/books/${book.id}/cover`, { method: "POST", body: form });
      } else if (state.pendingCoverUrl) {
        await api(`/api/books/${book.id}/cover`, {
          method: "POST",
          body: { url: state.pendingCoverUrl },
        });
      }
      drawer("editDialog").hide();
      toast("Book updated", "success");
      refreshAll();
    } catch (error) {
      toast(error.message, "danger");
    } finally {
      submit.disabled = false;
    }
  });
  el("editDeleteBtn").addEventListener("click", async () => {
    const book = state.editingBook;
    if (!book) return;
    await drawer("editDialog").hide();
    askConfirm(
      {
        title: "Delete book?",
        message: `“${book.title}” and its file will be removed. This cannot be undone.`,
        confirmLabel: "Delete",
      },
      async () => {
        await api(`/api/books/${book.id}`, { method: "DELETE" });
        toast("Book deleted", "success");
        refreshAll();
      }
    );
  });

  // Collections
  el("collectionForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/api/collections", {
        method: "POST",
        body: {
          name: el("collectionName").value.trim(),
          description: el("collectionDescription").value.trim(),
        },
      });
      el("collectionForm").reset();
      await loadCollections();
      toast("Shelf created", "success");
    } catch (error) {
      toast(error.message, "danger");
    }
  });

  // Password
  el("passwordForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/api/auth/password", {
        method: "POST",
        body: {
          current_password: el("currentPassword").value,
          new_password: el("newPassword").value,
        },
      });
      el("passwordForm").reset();
      drawer("passwordDialog").hide();
      toast("Password updated", "success");
    } catch (error) {
      toast(error.message, "danger");
    }
  });

  async function logout() {
    try {
      await api("/api/auth/logout", { method: "POST" });
    } finally {
      location.href = "/login";
    }
  }
  el("logoutBtn").addEventListener("click", logout);
  el("mobileLogoutBtn").addEventListener("click", logout);

  el("scanBtn").addEventListener("click", startScan);
  el("emptyScanBtn").addEventListener("click", startScan);
  el("mobileScanBtn").addEventListener("click", () => {
    Drawer.getOrCreateInstance(el("libraryMenuDrawer")).hide();
    startScan();
  });


  el("confirmActionBtn").addEventListener("click", async () => {
    const action = state.confirmAction;
    state.confirmAction = null;
    drawer("confirmDialog").hide();
    if (!action) return;
    try {
      await action();
    } catch (error) {
      toast(error.message, "danger");
    }
  });

  el("editDialog").addEventListener("hidden.bs.drawer", () => {
    state.editingBook = null;
    state.pendingCoverUrl = "";
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "/" && document.activeElement.tagName !== "INPUT" && document.activeElement.tagName !== "TEXTAREA") {
      event.preventDefault();
      search.focus();
    }
  });
}

function askConfirm({ title, message, confirmLabel = "Delete" }, action) {
  el("confirmDialogLabel").textContent = title;
  el("confirmMessage").textContent = message;
  el("confirmActionBtn").textContent = confirmLabel;
  state.confirmAction = action;
  drawer("confirmDialog").show();
}

init();
