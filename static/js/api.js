import { getState, setTokens, clearTokens } from "./state.js";

let refreshPromise = null;

async function toApiError(res) {
  let detail = res.statusText || `HTTP ${res.status}`;
  try {
    const data = await res.json();
    if (data && data.detail !== undefined) detail = data.detail;
  } catch {
    /* body wasn't JSON — keep statusText */
  }
  const message = typeof detail === "string" ? detail : summarizeValidationErrors(detail);
  const err = new Error(message);
  err.status = res.status;
  err.detail = detail;
  return err;
}

function summarizeValidationErrors(detail) {
  if (Array.isArray(detail)) {
    return detail.map((d) => `${(d.loc || []).slice(-1)[0] || "field"}: ${d.msg}`).join("; ");
  }
  return JSON.stringify(detail);
}

async function doRefresh() {
  const { refreshToken } = getState();
  if (!refreshToken) throw new Error("No refresh token available");
  const res = await fetch("/auth/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  if (!res.ok) throw new Error("Session refresh failed");
  const data = await res.json();
  setTokens(data.access_token, data.refresh_token);
  return data.access_token;
}

async function request(method, path, { body, isForm = false, allowRetry = true } = {}) {
  const { accessToken } = getState();
  const headers = {};
  if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;

  let fetchBody;
  if (isForm) {
    fetchBody = body; // URLSearchParams (login) or FormData (upload) — browser sets Content-Type itself
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    fetchBody = JSON.stringify(body);
  }

  const res = await fetch(path, { method, headers, body: fetchBody });

  if (res.status === 401 && allowRetry && path !== "/auth/login" && path !== "/auth/refresh") {
    try {
      refreshPromise = refreshPromise || doRefresh().finally(() => (refreshPromise = null));
      await refreshPromise;
      return request(method, path, { body, isForm, allowRetry: false });
    } catch {
      clearTokens();
      if (location.hash !== "#/login") location.hash = "#/login";
      throw await toApiError(res);
    }
  }

  if (!res.ok) throw await toApiError(res);
  if (res.status === 204) return null;
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body) => request("POST", path, { body }),
  put: (path, body) => request("PUT", path, { body }),
  patch: (path, body) => request("PATCH", path, { body }),
  del: (path) => request("DELETE", path),
  postForm: (path, formBody) => request("POST", path, { body: formBody, isForm: true }),
  upload: (path, formData) => request("POST", path, { body: formData, isForm: true }),
};

/** Native EventSource can't carry an Authorization header, so referral
 * status events are read via a manual fetch + ReadableStream instead — with
 * our own reconnect backoff, since that's not free without EventSource. */
export function streamReferralEvents(referralId, onMessage, onStatusChange) {
  const controller = new AbortController();
  let closed = false;
  let attempt = 0;

  async function connect() {
    if (closed) return;
    onStatusChange?.("connecting");
    try {
      const { accessToken } = getState();
      const res = await fetch(`/referral/requests/${referralId}/events`, {
        headers: { Authorization: `Bearer ${accessToken}` },
        signal: controller.signal,
      });
      if (!res.ok || !res.body) throw new Error("stream failed to open");
      onStatusChange?.("live");
      attempt = 0;
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (!closed) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let sep;
        while ((sep = buffer.indexOf("\n\n")) !== -1) {
          const chunk = buffer.slice(0, sep);
          buffer = buffer.slice(sep + 2);
          const dataLine = chunk.split("\n").find((l) => l.startsWith("data:"));
          if (dataLine) {
            const raw = dataLine.slice(5).trim();
            try {
              onMessage(JSON.parse(raw));
            } catch {
              onMessage(raw);
            }
          }
        }
      }
    } catch {
      if (closed || controller.signal.aborted) return;
    }
    if (closed) return;
    onStatusChange?.("retry");
    attempt += 1;
    const delay = Math.min(2000 * 2 ** (attempt - 1), 30000);
    setTimeout(connect, delay);
  }

  connect();

  return {
    close() {
      closed = true;
      controller.abort();
      onStatusChange?.("closed");
    },
  };
}
