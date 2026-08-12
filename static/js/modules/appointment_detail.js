import { api } from "../api.js";
import { hasPermission } from "../state.js";
import { navigate } from "../router.js";
import { el, formatDateTime, capitalize, appointmentStatusBadgeClass } from "../utils.js";
import { renderConsultationSection } from "../components/consultation.js";
import { selfServiceActions } from "./appointments.js";

// The page this whole "add a process for the doctor to take on a
// direct-booked appointment" pass was built around: a patient books an
// appointment (no referral involved at all), and previously there was
// nowhere for either of them to click through to — the appointments table
// had no row-click, and the doctor had no way to record what happened at
// the visit. This page is that "somewhere": doctor/patient details plus the
// same consultation-outcome flow (app/api/routes/appointments.py's
// POST/GET .../outcome, reusing the referral flow's exact model/summary
// generation — see static/js/components/consultation.js) a referral
// consult already had.

const doctorCache = new Map();
async function resolveDoctor(id) {
  if (doctorCache.has(id)) return doctorCache.get(id);
  const doctor = await api.get(`/doctors/${id}`).catch(() => null);
  doctorCache.set(id, doctor);
  return doctor;
}

function infoBlock(label, value) {
  return el("div", {}, [
    el("div", { class: "muted", style: "font-size:11.5px;margin-bottom:2px;" }, label),
    el("div", {}, value),
  ]);
}

export async function render(container, { id }) {
  const appointmentId = Number(id);
  container.innerHTML = "";

  const backBtn = el("button", { class: "btn-ghost btn-sm" }, "← Back to Scheduling & Appointments");
  backBtn.addEventListener("click", () => navigate("/schedule"));
  container.appendChild(el("div", {}, [backBtn]));

  const headerHost = el("div", {});
  const doctorHost = el("div", {});
  const patientHost = el("div", {});
  const consultHost = el("div", {});

  container.appendChild(el("div", { class: "card" }, [headerHost]));
  container.appendChild(
    el("div", { class: "grid-2" }, [
      el("div", { class: "card" }, [el("div", { class: "card-header" }, [el("h2", {}, "Doctor")]), doctorHost]),
      el("div", { class: "card" }, [el("div", { class: "card-header" }, [el("h2", {}, "Patient")]), patientHost]),
    ])
  );
  container.appendChild(
    el("div", { class: "card" }, [
      el("div", { class: "card-header" }, [
        el("h2", {}, "Consultation"),
        el("span", { class: "muted", style: "font-size:11.5px;" }, "Record what happened and generate a summary"),
      ]),
      consultHost,
    ])
  );

  let appointment = null;

  async function loadHeader() {
    headerHost.innerHTML = '<div class="loading-line">Loading…</div>';
    try {
      appointment = await api.get(`/appointments/${appointmentId}`);
    } catch (err) {
      headerHost.innerHTML = "";
      headerHost.appendChild(el("div", { class: "banner banner-error" }, err.message || "Appointment not found."));
      return false;
    }
    renderHeader();
    return true;
  }

  function renderHeader() {
    headerHost.innerHTML = "";
    headerHost.appendChild(
      el("div", { class: "card-header" }, [
        el("h2", {}, `Appointment #${appointment.id}`),
        el("span", { class: appointmentStatusBadgeClass(appointment.status) }, capitalize(appointment.status)),
      ])
    );
    headerHost.appendChild(
      el("div", { class: "grid-3" }, [
        infoBlock("Date & Time", formatDateTime(appointment.appointment_datetime)),
        infoBlock("Type", capitalize(appointment.appointment_type || "in_person")),
        infoBlock("Reason", appointment.reason || "—"),
        infoBlock("Location", appointment.location || "—"),
        infoBlock("Notes", appointment.notes || "—"),
        appointment.referral_id
          ? el("div", {}, [
              el("div", { class: "muted", style: "font-size:11.5px;margin-bottom:2px;" }, "From Referral"),
              el("a", { href: `#/referrals/${appointment.referral_id}` }, `#${appointment.referral_id} →`),
            ])
          : infoBlock("From Referral", "— (booked directly)"),
      ])
    );
    const actions = selfServiceActions(appointment, loadHeader);
    if (actions.length) {
      headerHost.appendChild(el("div", { class: "row-actions", style: "margin-top:10px;" }, actions));
    }
  }

  async function loadDoctor() {
    doctorHost.innerHTML = '<div class="loading-line">Loading…</div>';
    const doctor = await resolveDoctor(appointment.doctor_id);
    doctorHost.innerHTML = "";
    if (!doctor) {
      doctorHost.appendChild(el("div", { class: "muted" }, `Doctor #${appointment.doctor_id}`));
      return;
    }
    doctorHost.appendChild(
      el("div", { class: "grid-2" }, [
        infoBlock("Name", `${doctor.first_name} ${doctor.last_name}`),
        infoBlock("Specialty", doctor.specialization),
        infoBlock("Phone", doctor.phone || "—"),
        infoBlock("Department", doctor.department || "—"),
      ])
    );
  }

  async function loadPatient() {
    patientHost.innerHTML = '<div class="loading-line">Loading…</div>';
    try {
      const patient = await api.get(`/patients/${appointment.patient_id}`);
      patientHost.innerHTML = "";
      patientHost.appendChild(
        el("a", { href: `#/patients/${patient.id}` }, `${patient.first_name} ${patient.last_name} →`)
      );
    } catch {
      patientHost.innerHTML = "";
      patientHost.appendChild(el("div", { class: "muted" }, `Patient #${appointment.patient_id}`));
    }
  }

  async function loadConsultation() {
    await renderConsultationSection(consultHost, {
      getOutcome: () => api.get(`/appointments/${appointment.id}/outcome`),
      postOutcome: (payload) => api.post(`/appointments/${appointment.id}/outcome`, payload),
      canRecord: hasPermission("referral:record_outcome"),
      reasonText: appointment.reason,
      onRecorded: loadHeader,
    });
  }

  if (await loadHeader()) {
    await Promise.all([loadDoctor(), loadPatient(), loadConsultation()]);
  }
}
