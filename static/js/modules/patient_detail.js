import { api } from "../api.js";
import { hasPermission } from "../state.js";
import { navigate } from "../router.js";
import { renderTable } from "../components/table.js";
import { openModal } from "../components/modal.js";
import { toast } from "../components/toast.js";
import { patientFields, samplePatientData } from "./patients.js";
import {
  el, escapeHtml, formatDate, formatDateTime, capitalize,
  referralStatusBadgeClass, appointmentStatusBadgeClass, extractionStatusBadgeClass,
} from "../utils.js";

// Referral consult completion records (see
// app/services/referral_outcome.py::generate_completion_summary) carry the
// whole-care-journey summary in `notes` — label them distinctly from a
// routine visit so a patient reading their chart knows which entry to read.
const RECORD_TYPE_LABELS = { referral_consult: "Referral Consult Summary" };

function renderExtractedCodes(doc) {
  const codes = [];
  try { codes.push(...JSON.parse(doc.extracted_diagnosis_codes || "[]")); } catch { /* not ready yet */ }
  try { codes.push(...JSON.parse(doc.extracted_procedure_codes || "[]")); } catch { /* not ready yet */ }
  if (!codes.length) return "—";
  return `<div class="chip-row">${codes.map((c) => `<span class="chip">${escapeHtml(c)}</span>`).join("")}</div>`;
}

const doctorNameCache = new Map();
async function resolveDoctorName(id) {
  if (id == null) return "—";
  if (doctorNameCache.has(id)) return doctorNameCache.get(id);
  const name = await api
    .get(`/doctors/${id}`)
    .then((d) => `${d.first_name} ${d.last_name} — ${d.specialization}`)
    .catch(() => `Doctor #${id}`);
  doctorNameCache.set(id, name);
  return name;
}

function infoBlock(label, value) {
  return el("div", {}, [
    el("div", { class: "muted", style: "font-size:11.5px;margin-bottom:2px;" }, label),
    el("div", {}, value),
  ]);
}

export async function render(container, { id }) {
  const patientId = Number(id);
  container.innerHTML = "";

  const backBtn = el("button", { class: "btn-ghost btn-sm" }, "← Back to Patients");
  backBtn.addEventListener("click", () => navigate("/patients"));
  container.appendChild(el("div", {}, [backBtn]));

  const headerHost = el("div", {});
  const careTeamHost = el("div", {});
  const referralsHost = el("div", {});
  const appointmentsHost = el("div", {});
  const recordsHost = el("div", {});

  container.appendChild(el("div", { class: "card" }, [headerHost]));
  container.appendChild(
    el("div", { class: "card" }, [el("div", { class: "card-header" }, [el("h2", {}, "Care Team")]), careTeamHost])
  );
  container.appendChild(
    el("div", { class: "card" }, [el("div", { class: "card-header" }, [el("h2", {}, "Referrals")]), referralsHost])
  );
  container.appendChild(
    el("div", { class: "card" }, [
      el("div", { class: "card-header" }, [
        el("h2", {}, "Recent Appointments"),
        (() => {
          const link = el("a", { href: "#/appointments" }, "View all in Appointments →");
          link.style.fontSize = "12.5px";
          return link;
        })(),
      ]),
      appointmentsHost,
    ])
  );
  container.appendChild(
    el("div", { class: "card" }, [
      el("div", { class: "card-header" }, [
        el("h2", {}, "Recent Medical Records"),
        (() => {
          const link = el("a", { href: "#/medical-records" }, "View all →");
          link.style.fontSize = "12.5px";
          return link;
        })(),
      ]),
      recordsHost,
    ])
  );
  const documentsHost = el("div", {});
  container.appendChild(
    el("div", { class: "card" }, [
      el("div", { class: "card-header" }, [
        el("h2", {}, "Lab & Imaging Documents"),
        el("div", { class: "muted", style: "font-size:11.5px;" }, "From this patient's referrals"),
      ]),
      documentsHost,
    ])
  );

  let patient = null;

  async function loadHeader() {
    try {
      patient = await api.get(`/patients/${patientId}`);
    } catch (err) {
      headerHost.innerHTML = "";
      headerHost.appendChild(el("div", { class: "banner banner-error" }, err.message || "Patient not found."));
      return false;
    }
    renderHeader();
    return true;
  }

  function renderHeader() {
    headerHost.innerHTML = "";
    const top = el("div", { class: "card-header" }, [
      el("h2", {}, `${patient.first_name} ${patient.last_name}`),
    ]);
    if (hasPermission("patient:manage")) {
      const editBtn = el("button", { class: "btn-secondary btn-sm" }, "Edit");
      editBtn.addEventListener("click", openEditForm);
      top.appendChild(el("div", { class: "row-actions" }, [editBtn]));
    }
    headerHost.appendChild(top);
    headerHost.appendChild(
      el("div", { class: "grid-3" }, [
        infoBlock("Date of Birth", formatDate(patient.date_of_birth)),
        infoBlock("Gender", capitalize(patient.gender)),
        infoBlock("Phone", patient.phone || "—"),
        infoBlock("Insurance Provider", patient.insurance_provider || "—"),
        infoBlock("Policy Number", patient.insurance_policy_number || "—"),
        infoBlock("Allergies", patient.allergies || "—"),
      ])
    );
  }

  function openEditForm() {
    openModal({
      title: `Edit Patient #${patient.id}`,
      submitLabel: "Save changes",
      fields: patientFields,
      initial: patient,
      sampleData: samplePatientData,
      onSubmit: async (payload) => {
        await api.put(`/patients/${patient.id}`, payload);
        toast("Changes saved.", "success");
        await loadHeader();
      },
    });
  }

  async function renderCareTeam(careTeam) {
    careTeamHost.innerHTML = "";
    if (!careTeam.length) {
      careTeamHost.appendChild(el("div", { class: "empty-state" }, "No doctors on file for this patient yet."));
      return;
    }
    const names = await Promise.all(careTeam.map((entry) => resolveDoctorName(entry.doctor_id)));
    const chips = el(
      "div",
      { class: "chip-row" },
      careTeam.map((entry, i) => el("span", { class: "chip" }, `${names[i]} (${entry.role})`))
    );
    careTeamHost.appendChild(chips);
    careTeamHost.appendChild(
      el("div", { class: "muted", style: "font-size:11.5px;margin-top:8px;" },
        "Derived from this patient's referrals and appointments — not a separate assignment list.")
    );
  }

  function renderReferrals(referrals) {
    renderTable(referralsHost, {
      columns: [
        { key: "id", label: "ID" },
        {
          key: "status", label: "Status", html: true,
          format: (r) => `<span class="${referralStatusBadgeClass(r.status)}">${capitalize(r.status)}</span>`,
        },
        { key: "request_date", label: "Requested for", format: (r) => formatDate(r.request_date) },
        { key: "created_at", label: "Submitted", format: (r) => formatDateTime(r.created_at) },
      ],
      rows: referrals,
      onRowClick: (row) => navigate(`/referrals/${row.id}`),
      emptyMessage: "No referrals for this patient yet.",
    });
  }

  async function renderAppointments(appointments) {
    const doctorNames = await Promise.all(appointments.map((a) => resolveDoctorName(a.doctor_id)));
    renderTable(appointmentsHost, {
      columns: [
        { key: "appointment_datetime", label: "When", format: (r) => formatDateTime(r.appointment_datetime) },
        { key: "_doctor", label: "Doctor" },
        { key: "appointment_type", label: "Type", format: (r) => capitalize(r.appointment_type) },
        {
          key: "status", label: "Status", html: true,
          format: (r) => `<span class="${appointmentStatusBadgeClass(r.status)}">${capitalize(r.status)}</span>`,
        },
      ],
      rows: appointments.map((a, i) => ({ ...a, _doctor: doctorNames[i] })),
      emptyMessage: "No appointments on file.",
    });
  }

  async function renderMedicalRecords(records) {
    const doctorNames = await Promise.all(records.map((r) => resolveDoctorName(r.doctor_id)));
    renderTable(recordsHost, {
      columns: [
        { key: "visit_date", label: "Visit Date", format: (r) => formatDateTime(r.visit_date) },
        { key: "_doctor", label: "Doctor" },
        {
          key: "record_type", label: "Type",
          format: (r) => RECORD_TYPE_LABELS[r.record_type] || capitalize(r.record_type || "visit"),
        },
        { key: "diagnosis", label: "Diagnosis" },
        { key: "treatment", label: "Treatment" },
        { key: "prescription", label: "Prescription" },
        // A referral-completion record (record_type "referral_consult" —
        // see app/services/referral_outcome.py) carries the AI/template
        // whole-care-journey summary here, meant for whoever handles this
        // patient's next follow-up to read right on their chart.
        { key: "notes", label: "Follow-up Summary" },
      ],
      rows: records.map((r, i) => ({ ...r, _doctor: doctorNames[i] })),
      emptyMessage: "No medical records on file.",
    });
  }

  function renderDocuments(documents) {
    renderTable(documentsHost, {
      columns: [
        { key: "filename", label: "Filename" },
        {
          key: "extraction_status", label: "Status", html: true,
          format: (d) => `<span class="${extractionStatusBadgeClass(d.extraction_status)}">${capitalize(d.extraction_status)}</span>`,
        },
        { key: "codes", label: "Extracted Codes", html: true, format: renderExtractedCodes },
        { key: "created_at", label: "Uploaded", format: (d) => formatDateTime(d.created_at) },
        {
          key: "referral_request_id", label: "Referral", html: true,
          format: (d) => `<a href="#/referrals/${d.referral_request_id}">#${d.referral_request_id}</a>`,
        },
      ],
      rows: documents,
      emptyMessage: "No lab, imaging, or referral documents on file.",
    });
  }

  async function loadContext() {
    let context;
    try {
      context = await api.get(`/patients/${patientId}/context`);
    } catch (err) {
      careTeamHost.innerHTML = "";
      careTeamHost.appendChild(el("div", { class: "banner banner-error" }, err.message || "Failed to load patient context."));
      return;
    }
    await renderCareTeam(context.care_team);
    renderReferrals(context.referrals);
    await renderAppointments(context.appointments);
    await renderMedicalRecords(context.medical_records);
    renderDocuments(context.documents);
  }

  if (await loadHeader()) await loadContext();
}
