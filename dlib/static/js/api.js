/** Tiny fetch wrapper for the JSON API. */

export async function api(path, options = {}) {
  const opts = { credentials: "same-origin", ...options };
  opts.headers = { Accept: "application/json", ...(options.headers || {}) };

  if (opts.body && !(opts.body instanceof FormData) && typeof opts.body !== "string") {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.body);
  }

  const response = await fetch(path, opts);
  const contentType = response.headers.get("content-type") || "";
  let payload = null;
  if (contentType.includes("application/json")) {
    payload = await response.json().catch(() => null);
  } else {
    payload = await response.text().catch(() => "");
  }

  if (response.status === 401 && options.redirectOn401 !== false && !location.pathname.startsWith("/login")) {
    location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
    throw new Error("Authentication required");
  }

  if (!response.ok) {
    const message =
      (payload && payload.error) ||
      (payload && payload.errors && payload.errors[0] && payload.errors[0].error) ||
      response.statusText ||
      "Request failed";
    const error = new Error(message);
    error.status = response.status;
    error.payload = payload;
    throw error;
  }

  return payload;
}

export function jsonBody(value) {
  return { body: value };
}
