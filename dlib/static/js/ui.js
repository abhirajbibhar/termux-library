/** Shared UI helpers: escaping, toasts, formatting, theme toggle. */

import { Toast } from "../vendor/bootstrap.bundle.js";

export function escapeHtml(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function debounce(fn, wait = 250) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

export function formatBytes(bytes) {
  const value = Number(bytes) || 0;
  if (value < 1024) return value + " B";
  const units = ["KB", "MB", "GB"];
  let size = value / 1024;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return size.toFixed(size >= 10 ? 0 : 1) + " " + units[index];
}

export function coverMarkup(book) {
  const title = escapeHtml(book.title || "Untitled");
  return `<div class="position-absolute top-0 start-0 w-100 h-100 d-flex flex-column align-items-center justify-content-center bg-2 fg-secondary p-3 text-center">
    <i class="bi bi-book fs-2xl mb-2"></i>
    <span class="fs-sm text-truncate w-100">${title}</span>
  </div>`;
}

const TOAST_ICONS = {
  success: "bi-check-circle-fill fg-success",
  warning: "bi-exclamation-triangle-fill fg-warning",
  danger: "bi-x-octagon-fill fg-danger",
  info: "bi-info-circle-fill fg-info",
};

export function toast(message, variant = "success", delay = 4200) {
  const container = document.getElementById("toastContainer");
  if (!container) return;
  const wrapper = document.createElement("div");
  wrapper.className = "toast border-0 shadow-sm max-w-100";
  wrapper.setAttribute("role", "alert");
  wrapper.setAttribute("aria-live", "assertive");
  wrapper.setAttribute("aria-atomic", "true");
  wrapper.innerHTML = `
    <div class="toast-body d-flex align-items-center gap-3">
      <i class="bi ${TOAST_ICONS[variant] || TOAST_ICONS.info} fs-lg" aria-hidden="true"></i>
      <span class="flex-grow-1"></span>
      <button type="button" class="btn-close ms-auto" data-bs-dismiss="toast" aria-label="Close"></button>
    </div>`;
  wrapper.querySelector(".toast-body span").textContent = message;
  container.append(wrapper);
  const instance = Toast.getOrCreateInstance(wrapper, { delay });
  wrapper.addEventListener("hidden.bs.toast", () => wrapper.remove());
  instance.show();
}

export function applyTheme(theme) {
  const dark = theme === "dark";
  document.documentElement.setAttribute("data-bs-theme", dark ? "dark" : "light");
  try {
    localStorage.setItem("library-theme", dark ? "dark" : "light");
  } catch (error) {
    /* ignore */
  }
  document.querySelectorAll("[data-theme-icon]").forEach((element) => {
    element.className = dark ? "bi bi-moon-stars" : "bi bi-sun";
  });
  return dark ? "dark" : "light";
}

export function setupThemeToggle(button) {
  if (!button) return;
  const current = document.documentElement.getAttribute("data-bs-theme") === "dark" ? "dark" : "light";
  const icon = button.querySelector("[data-theme-icon]");
  if (icon) icon.className = current === "dark" ? "bi bi-moon-stars" : "bi bi-sun";
  button.addEventListener("click", () => {
    const nowDark = document.documentElement.getAttribute("data-bs-theme") === "dark";
    applyTheme(nowDark ? "light" : "dark");
  });
}
