import { api } from "./api.js";
import { setupThemeToggle } from "./ui.js";

const loginForm = document.getElementById("loginForm");
const registerForm = document.getElementById("registerForm");
const loginError = document.getElementById("loginError");
const registerError = document.getElementById("registerError");
const tabLogin = document.getElementById("tabLogin");
const tabRegister = document.getElementById("tabRegister");

setupThemeToggle(document.getElementById("themeToggle"));

const configNode = document.getElementById("page-config");
const config = configNode ? JSON.parse(configNode.textContent) : { allowRegistration: false };

function nextUrl() {
  const params = new URLSearchParams(location.search);
  const next = params.get("next") || "/";
  return next.startsWith("/") && !next.startsWith("//") ? next : "/";
}

function mode(register) {
  loginForm.classList.toggle("d-none", register);
  registerForm.classList.toggle("d-none", !register);
  tabLogin.classList.toggle("active", !register);
  setTabAria(tabLogin, !register);
  tabRegister.classList.toggle("active", register);
  setTabAria(tabRegister, register);
  (register ? registerForm : loginForm).querySelector("input")?.focus();
}

function setTabAria(tab, active) {
  tab.setAttribute("aria-selected", active ? "true" : "false");
}

tabLogin.addEventListener("click", () => mode(false));
tabRegister.addEventListener("click", () => mode(true));

function showError(element, message) {
  element.textContent = message;
  element.classList.remove("d-none");
}

function clearError(element) {
  element.textContent = "";
  element.classList.add("d-none");
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearError(loginError);
  const button = loginForm.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    await api("/api/auth/login", {
      method: "POST",
      body: {
        username: document.getElementById("loginUsername").value.trim(),
        password: document.getElementById("loginPassword").value,
      },
      redirectOn401: false,
    });
    location.href = nextUrl();
  } catch (error) {
    showError(loginError, error.message);
    button.disabled = false;
  }
});

registerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearError(registerError);
  const password = document.getElementById("regPassword").value;
  const confirmation = document.getElementById("regPassword2").value;
  if (password !== confirmation) {
    showError(registerError, "Passwords do not match");
    return;
  }
  if (!config.allowRegistration) {
    showError(registerError, "Registration is disabled.");
    return;
  }
  const button = registerForm.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    await api("/api/auth/register", {
      method: "POST",
      body: {
        username: document.getElementById("regUsername").value.trim(),
        display_name: document.getElementById("regDisplayName").value.trim(),
        password,
      },
      redirectOn401: false,
    });
    location.href = nextUrl();
  } catch (error) {
    showError(registerError, error.message);
    button.disabled = false;
  }
});

if (!config.allowRegistration && tabRegister) {
  tabRegister.disabled = true;
}

// Focus the first field on load (mobile keyboards stay closed until tapped).
document.getElementById("loginUsername")?.focus({ preventScroll: true });
