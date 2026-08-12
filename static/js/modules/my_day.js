import { api } from "../api.js";
import { renderTable } from "../components/table.js";
import { navigate } from "../router.js";
import { el, escapeHtml, formatDateTime, capitalize, appointmentStatusBadgeClass, skeletonBlock } from "../utils.js";

const patientNameCache = new Map();
async function resolvePatientName(id) {
  if (patientNameCache.has(id)) return patientNameCache.get(id);
  const name = await api.get(`/patients/${id}`).then((p) => `${p.first_name} ${p.last_name}`).catch(() => `Patient #${id}`);
  patientNameCache.set(id, name);
  return name;
}

export async function render(container) {
  container.innerHTML = "";
  const bannerHost = el("div", { class: "banner banner-info hidden" });
  const tableHost = el("div", {});
  container.appendChild(
    el("div", { class: "card view-accent-bar" }, [
      el("div", { class: "card-header" }, [el("h2", {}, "My Day")]),
      el("p", { class: "muted", style: "margin:-6px 0 14px;" }, "Your upcoming assigned appointments."),
      bannerHost,
      tableHost,
    ])
  );
  tableHost.appendChild(skeletonBlock(4));

  let doctor;
  try {
    doctor = await api.get("/doctors/me");
  } catch {
    bannerHost.textContent =
      "Your account isn't linked to a doctor record yet — ask an admin to link one " +
      '(Admin panel → find your account → "Link to Doctor").';
    bannerHost.classList.remove("hidden");
    tableHost.appendChild(el("div", { class: "table-empty" }, "Nothing to show until your account is linked."));
    return;
  }

  let appointments = [];
  try {
    const page = await api.get(`/appointments/?doctor_id=${doctor.id}&upcoming_only=true&limit=100`);
    appointments = page.items || [];
  } catch (err) {
    bannerHost.textContent = err.message || "Failed to load your appointments.";
    bannerHost.classList.remove("hidden");
  }

  const rows = await Promise.all(
    appointments.map(async (a) => ({ ...a, _patientName: await resolvePatientName(a.patient_id) }))
  );
  rows.sort((a, b) => new Date(a.appointment_datetime) - new Date(b.appointment_datetime));

  renderTable(tableHost, {
    columns: [
      { key: "appointment_datetime", label: "Time", format: (a) => formatDateTime(a.appointment_datetime) },
      {
        // onclick stops propagation inline — table.js's row-click handler
        // would otherwise also fire on top of this link's own navigation
        // (only the row-actions <td> gets that protection generically).
        key: "_patientName", label: "Patient", html: true,
        format: (a) => `<a href="#/patients/${a.patient_id}" onclick="event.stopPropagation()">${escapeHtml(a._patientName)}</a>`,
      },
      { key: "appointment_type", label: "Type", format: (a) => capitalize(a.appointment_type || "") },
      {
        key: "status", label: "Status", html: true,
        format: (a) => `<span class="${appointmentStatusBadgeClass(a.status)}">${capitalize(a.status)}</span>`,
      },
      { key: "reason", label: "Reason", format: (a) => a.reason || "—" },
    ],
    rows,
    // The fix for "doctor has no process to take on a patient who books an
    // appointment" — a doctor's My Day row now clicks through to the
    // appointment detail page's Consultation section (record outcome,
    // generate summary), the same place a referral-linked appointment's
    // consult already had.
    onRowClick: (row) => navigate(`/appointments/${row.id}`),
    emptyMessage: "No upcoming appointments.",
  });
}
