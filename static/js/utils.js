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
  scheduling_delayed: "warning",
  scheduled: "good",
  completed: "good",
  cancelled: "neutral",
};
export function referralStatusBadgeClass(status) {
  return `badge badge-${REFERRAL_STATUS_ROLE[status] || "neutral"}`;
}
export const REFERRAL_STATUSES = Object.keys(REFERRAL_STATUS_ROLE);

// Translates the raw workflow status into "what's happening / who it's
// waiting on / what happens next" — the thing a status word alone doesn't
// convey. Written generically (not patient-only) since every role benefits
// from knowing whose court the ball is in. Mirrors the LangGraph workflow's
// real steps (app/agents/graph.py) and who actually acts at each one
// (see WORKFLOW.md's referral-lifecycle table).
export const REFERRAL_PROGRESS_INFO = {
  submitted: {
    label: "Referral submitted",
    waitingOn: "System",
    nextStep: "Automated review of the referral and any attached documents is starting.",
  },
  intake_processing: {
    label: "Reviewing documents",
    waitingOn: "System",
    nextStep: "Extracting diagnosis/procedure details from the uploaded documents.",
  },
  awaiting_documents: {
    label: "Waiting on documents",
    waitingOn: "Patient / referring doctor",
    nextStep: "This referral has neither a reason nor any document yet — add a Reason or upload a document to continue.",
  },
  eligibility_checking: {
    label: "Verifying insurance coverage",
    waitingOn: "System",
    nextStep: "Confirming the patient's insurance plan covers this referral.",
  },
  eligibility_denied: {
    label: "Insurance verification did not pass",
    waitingOn: "Care coordination staff",
    nextStep: "A care coordinator needs to review this before it can move forward.",
  },
  awaiting_specialist_approval: {
    label: "Selecting a specialist",
    waitingOn: "Care coordination staff or a specialist",
    nextStep: "A coordinator or specialist needs to review the recommended specialists and confirm one — see the Workflow State tab below.",
  },
  scheduling: {
    label: "Booking the appointment",
    waitingOn: "System",
    nextStep: "Finding the next available appointment slot with the selected specialist.",
  },
  scheduling_delayed: {
    label: "No appointment slot available yet",
    waitingOn: "Care coordination staff",
    nextStep: "A coordinator may need to consider an alternative specialist or timeframe.",
  },
  scheduled: {
    label: "Appointment scheduled",
    waitingOn: "Patient",
    nextStep: "Attend the scheduled appointment.",
  },
  completed: {
    label: "Referral completed",
    waitingOn: "—",
    nextStep: "The consult outcome has been recorded. Follow up with the referring doctor as advised.",
  },
  cancelled: {
    label: "Referral cancelled",
    waitingOn: "—",
    nextStep: "Contact the care team if this looks unexpected.",
  },
};

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
