export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

export function formatDate(value) {
  if (!value) return "—";
  const d = new Date(value);
  return isNaN(d) ? String(value) : d.toLocaleDateString();
}

export function formatDateTime(value) {
  if (!value) return "—";
  const d = new Date(value);
  return isNaN(d) ? String(value) : d.toLocaleString();
}

export function capitalize(value) {
  const s = String(value ?? "").replaceAll("_", " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** datetime-local inputs are local time with no offset — convert carefully
 * both ways so the instant sent to the API matches what the user picked. */
export function toDatetimeLocalValue(isoString) {
  if (!isoString) return "";
  const d = new Date(isoString);
  if (isNaN(d)) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function fromDatetimeLocalValue(value) {
  if (!value) return null;
  const d = new Date(value);
  return isNaN(d) ? null : d.toISOString();
}

const REFERRAL_STATUS_ROLE = {
  submitted: "progress",
  intake_processing: "progress",
  awaiting_documents: "warning",
  eligibility_checking: "progress",
  eligibility_denied: "critical",
  awaiting_specialist_approval: "warning",
  scheduling: "progress",
  scheduled: "good",
  completed: "good",
  cancelled: "neutral",
};
export function referralStatusBadgeClass(status) {
  return `badge badge-${REFERRAL_STATUS_ROLE[status] || "neutral"}`;
}
export const REFERRAL_STATUSES = Object.keys(REFERRAL_STATUS_ROLE);

const APPOINTMENT_STATUS_ROLE = {
  scheduled: "progress",
  confirmed: "good",
  in_progress: "warning",
  completed: "good",
  cancelled: "neutral",
  no_show: "critical",
};
export function appointmentStatusBadgeClass(status) {
  return `badge badge-${APPOINTMENT_STATUS_ROLE[status] || "neutral"}`;
}
export const APPOINTMENT_STATUSES = Object.keys(APPOINTMENT_STATUS_ROLE);

export function extractionStatusBadgeClass(status) {
  const s = String(status || "").toLowerCase();
  if (s.includes("complete")) return "badge badge-good";
  if (s.includes("fail") || s.includes("error")) return "badge badge-critical";
  if (s.includes("queue") || s.includes("pending")) return "badge badge-neutral";
  return "badge badge-progress";
}

export function badgeHtml(label, cls) {
  return `<span class="${cls}">${escapeHtml(label)}</span>`;
}

export function debounce(fn, wait = 250) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child == null) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}
