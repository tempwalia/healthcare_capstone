import { api } from "../api.js";
import { hasPermission } from "../state.js";
import { renderTable, renderPager } from "../components/table.js";
import { openModal } from "../components/modal.js";
import { toast } from "../components/toast.js";
import { el, formatDateTime, capitalize } from "../utils.js";

const WEEKDAYS = [
  { value: 0, label: "Monday" }, { value: 1, label: "Tuesday" }, { value: 2, label: "Wednesday" },
  { value: 3, label: "Thursday" }, { value: 4, label: "Friday" }, { value: 5, label: "Saturday" },
  { value: 6, label: "Sunday" },
];

// Mirrors specialist_node's infer_specialty keyword table (app/agents/nodes/specialist.py) —
// a client-side approximation used only to rank/highlight doctors for direct/manual booking.
// The AI referral workflow's own recommendation (specialist_candidates, surfaced on a referral's
// Workflow State tab) is the authoritative one for AI-routed referrals; this is a lighter-weight
// helper for booking outside that flow.
const SPECIALTY_KEYWORDS = {
  Orthopedics: ["back", "spine", "joint", "knee", "hip", "shoulder", "fracture", "orthopedic"],
  Cardiology: ["heart", "cardiac", "chest pain", "palpitation", "cardio"],
  Dermatology: ["skin", "rash", "derma", "mole", "eczema"],
  Neurology: ["headache", "migraine", "seizure", "numbness", "neuro"],
  "Family Medicine": ["checkup", "general", "wellness", "physical"],
};
function inferSpecialtyFromText(text) {
  const lower = (text || "").toLowerCase();
  for (const [specialty, keywords] of Object.entries(SPECIALTY_KEYWORDS)) {
    if (keywords.some((k) => lower.includes(k))) return specialty;
  }
  return null;
}

function infoBlock(label, value) {
  return el("div", {}, [
    el("div", { class: "muted", style: "font-size:11.5px;margin-bottom:2px;" }, label),
    el("div", {}, value),
  ]);
}

async function fillDoctorOptions(select, { placeholder = "Select a doctor…" } = {}) {
  select.innerHTML = "";
  select.appendChild(el("option", { value: "" }, "Loading doctors…"));
  try {
    const page = await api.get("/doctors/?limit=200");
    select.innerHTML = "";
    select.appendChild(el("option", { value: "" }, placeholder));
    for (const d of page.items || []) {
      select.appendChild(el("option", { value: d.id }, `${d.first_name} ${d.last_name} — ${d.specialization} (#${d.id})`));
    }
  } catch {
    select.innerHTML = "";
    select.appendChild(el("option", { value: "" }, "Failed to load doctors"));
  }
}

async function fillPatientOptions(select) {
  select.innerHTML = "";
  select.appendChild(el("option", { value: "" }, "Loading patients…"));
  try {
    const page = await api.get("/patients/?limit=200");
    select.innerHTML = "";
    select.appendChild(el("option", { value: "" }, "Select a patient…"));
    for (const p of page.items || []) {
      select.appendChild(el("option", { value: p.id }, `${p.first_name} ${p.last_name} (#${p.id})`));
    }
  } catch {
    select.innerHTML = "";
    select.appendChild(el("option", { value: "" }, "Failed to load patients"));
  }
}

export async function render(container) {
  container.innerHTML = "";
  const local = { skip: 0, limit: 20, total: 0, items: [], doctorFilter: "", bookedFilter: "" };

  // ---- Availability & slot generation ----
  const genDoctorSelect = el("select", { style: "max-width:280px;" });
  fillDoctorOptions(genDoctorSelect, { placeholder: "Select a doctor to generate slots for…" });
  const daysAheadInput = el("input", { type: "number", placeholder: "Days ahead", value: "14", style: "max-width:110px;" });
  const genBtn = el("button", { class: "btn-primary btn-sm" }, "Generate Slots");
  const genStatus = el("div", { class: "banner hidden", style: "margin-top:10px;" });

  genBtn.addEventListener("click", async () => {
    const doctorId = Number(genDoctorSelect.value);
    if (!doctorId) {
      toast("Choose a doctor first.", "error");
      return;
    }
    genBtn.disabled = true;
    genStatus.classList.add("hidden");
    try {
      const created = await api.post("/schedule/slots/generate", {
        doctor_id: doctorId, days_ahead: Number(daysAheadInput.value) || 14,
      });
      if (created.length) {
        genStatus.className = "banner banner-info";
        genStatus.textContent = `${created.length} new slot(s) generated.`;
      } else {
        // Zero created is ambiguous on its own — distinguish "nothing configured
        // yet" from "already fully generated for this window" so it isn't a
        // dead-end message.
        const availability = await api.get(`/schedule/availability/?doctor_id=${doctorId}`);
        if (!(availability.items || []).length) {
          genStatus.className = "banner banner-warning";
          genStatus.textContent = "This doctor has no availability configured yet — add availability below first, then generate slots.";
        } else {
          genStatus.className = "banner banner-info";
          genStatus.textContent = "Already up to date — every slot in this window has already been generated.";
        }
      }
      genStatus.classList.remove("hidden");
      slotDoctorSelect.value = String(doctorId);
      local.doctorFilter = String(doctorId);
      local.skip = 0;
      await loadSlots();
    } catch (err) {
      toast(err.message || "Failed to generate slots.", "error");
    } finally {
      genBtn.disabled = false;
    }
  });

  const addAvailBtn = el("button", { class: "btn-secondary btn-sm" }, "+ Add Availability");
  addAvailBtn.addEventListener("click", openAvailabilityForm);

  // Backend now gates POST /schedule/availability/ and /slots/generate
  // behind appointment:manage (previously open to any authenticated user) —
  // mirror that here so a patient session doesn't see controls the API
  // would reject anyway.
  if (hasPermission("appointment:manage")) {
    container.appendChild(
      el("div", { class: "card" }, [
        el("div", { class: "card-header" }, [el("h2", {}, "Availability & Slot Generation")]),
        el("p", { class: "muted" }, "Add a doctor's recurring weekly availability, then generate concrete bookable slots from it — safe to re-run, already-generated slots are skipped."),
        el("div", { class: "toolbar" }, [
          genDoctorSelect,
          el("span", { class: "muted" }, "Days ahead:"), daysAheadInput,
          genBtn, el("div", { class: "spacer" }), addAvailBtn,
        ]),
        genStatus,
      ])
    );
  } else {
    container.appendChild(
      el("div", { class: "card" }, [
        el("div", { class: "card-header" }, [el("h2", {}, "Availability & Slot Generation")]),
        el("p", { class: "muted" }, "Only coordinators and PCPs can create availability or generate slots. You can still book yourself into an existing open slot below."),
      ])
    );
  }

  // ---- Recommended-doctor booking flow ----
  const bookPatientSelect = el("select", { style: "max-width:260px;" });
  fillPatientOptions(bookPatientSelect);
  const symptomsInput = el("textarea", { placeholder: "Describe symptoms / reason for visit — used to suggest a specialty…" });
  const findBtn = el("button", { class: "btn-primary btn-sm" }, "Find Recommended Doctors");
  const doctorResultsHost = el("div", {});
  const slotsHost = el("div", {});
  const receiptHost = el("div", {});

  findBtn.addEventListener("click", async () => {
    const patientId = Number(bookPatientSelect.value);
    if (!patientId) {
      toast("Select a patient first.", "error");
      return;
    }
    slotsHost.innerHTML = "";
    doctorResultsHost.innerHTML = '<div class="loading-line">Loading doctors…</div>';
    let doctors;
    try {
      doctors = (await api.get("/doctors/?limit=200")).items || [];
    } catch (err) {
      doctorResultsHost.innerHTML = "";
      doctorResultsHost.appendChild(el("div", { class: "banner banner-error" }, err.message || "Failed to load doctors."));
      return;
    }
    const suggested = inferSpecialtyFromText(symptomsInput.value);
    const ranked = suggested
      ? [...doctors].sort((a, b) => (b.specialization === suggested ? 1 : 0) - (a.specialization === suggested ? 1 : 0))
      : doctors;
    renderDoctorResults(ranked, suggested, patientId);
  });

  function renderDoctorResults(doctors, suggested, patientId) {
    doctorResultsHost.innerHTML = "";
    doctorResultsHost.appendChild(
      el("div", { class: "banner banner-info" },
        suggested
          ? `Based on the symptoms entered, "${suggested}" looks like the best-matching specialty — matching doctors are listed first.`
          : "No specialty keywords matched that description — showing all doctors."
      )
    );
    if (!doctors.length) {
      doctorResultsHost.appendChild(el("div", { class: "empty-state" }, "No doctors in the directory yet — add one on the Doctors page."));
      return;
    }
    const cardsHost = el("div", { class: "grid-auto" });
    for (const d of doctors.slice(0, 12)) {
      const matched = suggested && d.specialization === suggested;
      const viewSlotsBtn = el("button", { class: "btn-secondary btn-sm", style: "margin-top:8px;" }, "View Available Slots");
      viewSlotsBtn.addEventListener("click", () => loadSlotsForDoctor(d, patientId));
      cardsHost.appendChild(
        el("div", { class: "card" }, [
          matched ? el("span", { class: "badge badge-good", style: "margin-bottom:6px;" }, "Recommended match") : null,
          el("div", { style: "font-weight:650;" }, `${d.first_name} ${d.last_name}`),
          el("div", { class: "muted", style: "font-size:12.5px;" }, d.specialization),
          viewSlotsBtn,
        ])
      );
    }
    doctorResultsHost.appendChild(cardsHost);
  }

  async function loadSlotsForDoctor(doctor, patientId) {
    slotsHost.innerHTML = '<div class="loading-line">Loading available slots…</div>';
    let slots;
    try {
      slots = (await api.get(`/schedule/slots/?doctor_id=${doctor.id}&is_booked=false&limit=20`)).items || [];
    } catch (err) {
      slotsHost.innerHTML = "";
      slotsHost.appendChild(el("div", { class: "banner banner-error" }, err.message || "Failed to load slots."));
      return;
    }
    slotsHost.innerHTML = "";
    slotsHost.appendChild(el("h3", {}, `Available slots — Dr. ${doctor.first_name} ${doctor.last_name}`));
    if (!slots.length) {
      slotsHost.appendChild(
        el("div", { class: "banner banner-warning" },
          "No open slots for this doctor yet — add availability and generate slots above first."
        )
      );
      return;
    }
    const list = el("div", { class: "grid-auto" });
    for (const slot of slots) {
      const btn = el("button", { class: "btn-secondary btn-sm" }, formatDateTime(slot.starts_at));
      btn.addEventListener("click", () => confirmBooking(slot, doctor, patientId));
      list.appendChild(btn);
    }
    slotsHost.appendChild(list);
  }

  async function confirmBooking(slot, doctor, patientId) {
    if (!confirm(`Book ${formatDateTime(slot.starts_at)} with Dr. ${doctor.first_name} ${doctor.last_name}?`)) return;
    try {
      const appointment = await api.post(`/schedule/slots/${slot.id}/book`, {
        patient_id: patientId, reason: symptomsInput.value || null,
      });
      const patient = await api.get(`/patients/${patientId}`).catch(() => null);
      renderReceipt({ appointment, doctor, patient, slot });
      toast("Appointment booked.", "success");
      slotsHost.innerHTML = "";
      doctorResultsHost.innerHTML = "";
      await loadSlots();
    } catch (err) {
      toast(err.message || "Booking failed.", "error");
    }
  }

  function renderReceipt({ appointment, doctor, patient, slot }) {
    receiptHost.innerHTML = "";
    const printBtn = el("button", { class: "btn-secondary btn-sm" }, "Print");
    printBtn.addEventListener("click", () => window.print());
    receiptHost.appendChild(
      el("div", { class: "card" }, [
        el("div", { class: "card-header" }, [el("h2", {}, "Booking Confirmation"), printBtn]),
        el("div", { class: "grid-2" }, [
          infoBlock("Patient", patient ? `${patient.first_name} ${patient.last_name}` : `#${appointment.patient_id}`),
          infoBlock("Doctor", `${doctor.first_name} ${doctor.last_name} (${doctor.specialization})`),
          infoBlock("Date & Time", formatDateTime(slot.starts_at)),
          infoBlock("Reason", appointment.reason || "—"),
          infoBlock("Appointment ID", `#${appointment.id}`),
          infoBlock("Status", capitalize(appointment.status)),
        ]),
        el("p", { class: "muted", style: "margin-top:10px;" },
          "This confirmation is visible to the patient, the assigned doctor, and any care_coordinator from the Appointments page — each sees it according to their own access level, no separate delivery step needed."
        ),
      ])
    );
  }

  container.appendChild(
    el("div", { class: "card" }, [
      el("div", { class: "card-header" }, [el("h2", {}, "Book an Appointment")]),
      el("p", { class: "muted" }, "Pick a patient, describe the reason for the visit, and get doctors ranked by specialty match — then pick a real open slot and confirm."),
      el("div", { class: "toolbar" }, [bookPatientSelect]),
      el("div", { class: "field" }, [el("label", {}, "Symptoms / Reason"), symptomsInput]),
      findBtn,
    ])
  );
  container.appendChild(el("div", {}, [doctorResultsHost]));
  container.appendChild(el("div", {}, [slotsHost]));
  container.appendChild(el("div", {}, [receiptHost]));

  // ---- Slots table ----
  const slotDoctorSelect = el("select", { style: "max-width:260px;" });
  fillDoctorOptions(slotDoctorSelect, { placeholder: "All doctors" });
  slotDoctorSelect.addEventListener("change", () => {
    local.doctorFilter = slotDoctorSelect.value;
    local.skip = 0;
    loadSlots();
  });
  const bookedSelect = el("select", {});
  bookedSelect.appendChild(el("option", { value: "" }, "All slots"));
  bookedSelect.appendChild(el("option", { value: "false" }, "Available only"));
  bookedSelect.appendChild(el("option", { value: "true" }, "Booked only"));
  bookedSelect.addEventListener("change", () => {
    local.bookedFilter = bookedSelect.value;
    local.skip = 0;
    loadSlots();
  });

  const slotBanner = el("div", { class: "banner banner-error hidden" });
  const slotTableHost = el("div", {});
  const slotPagerHost = el("div", {});
  container.appendChild(
    el("div", { class: "card" }, [
      el("div", { class: "card-header" }, [el("h2", {}, "Schedule Slots")]),
      el("div", { class: "toolbar" }, [slotDoctorSelect, bookedSelect, el("div", { class: "spacer" })]),
      slotBanner, slotTableHost, slotPagerHost,
    ])
  );

  async function loadSlots() {
    const params = new URLSearchParams({ skip: local.skip, limit: local.limit });
    if (local.doctorFilter) params.set("doctor_id", local.doctorFilter);
    if (local.bookedFilter) params.set("is_booked", local.bookedFilter);
    try {
      const page = await api.get(`/schedule/slots/?${params.toString()}`);
      local.items = page.items || [];
      local.total = page.total || 0;
      slotBanner.classList.add("hidden");
    } catch (err) {
      local.items = [];
      local.total = 0;
      slotBanner.textContent = err.message || "Failed to load slots.";
      slotBanner.classList.remove("hidden");
    }
    renderSlots();
  }

  function renderSlots() {
    renderTable(slotTableHost, {
      columns: [
        { key: "id", label: "ID" },
        { key: "doctor_id", label: "Doctor", format: (r) => `#${r.doctor_id}` },
        { key: "starts_at", label: "Starts", format: (r) => formatDateTime(r.starts_at) },
        { key: "ends_at", label: "Ends", format: (r) => formatDateTime(r.ends_at) },
        {
          key: "is_booked", label: "Status", html: true,
          format: (r) => (r.is_booked ? '<span class="badge badge-neutral">Booked</span>' : '<span class="badge badge-good">Available</span>'),
        },
      ],
      rows: local.items,
      actions: hasPermission("appointment:manage") ? (row) => (row.is_booked ? [] : [bookButton(row)]) : null,
      emptyMessage: "No slots found — generate some above.",
    });
    renderPager(slotPagerHost, {
      skip: local.skip, limit: local.limit, total: local.total,
      onChange: (skip) => { local.skip = skip; loadSlots(); },
    });
  }

  function bookButton(slot) {
    const btn = el("button", { class: "btn-primary btn-sm" }, "Book");
    btn.addEventListener("click", () => openBookForm(slot));
    return btn;
  }

  function openBookForm(slot) {
    openModal({
      title: `Book Slot #${slot.id}`,
      submitLabel: "Book",
      fields: [
        {
          name: "patient_id", label: "Patient", type: "select-async", source: "/patients", required: true,
          optionLabel: (p) => `${p.first_name} ${p.last_name} (#${p.id})`,
        },
        { name: "reason", label: "Reason", type: "text" },
      ],
      onSubmit: async (payload) => {
        await api.post(`/schedule/slots/${slot.id}/book`, payload);
        toast("Slot booked.", "success");
        await loadSlots();
      },
    });
  }

  function openAvailabilityForm() {
    openModal({
      title: "Add Availability",
      submitLabel: "Add",
      fields: [
        {
          name: "doctor_id", label: "Doctor", type: "select-async", source: "/doctors", required: true,
          optionLabel: (d) => `${d.first_name} ${d.last_name} (#${d.id})`,
        },
        { name: "weekday", label: "Weekday", type: "select", required: true, options: WEEKDAYS },
        { name: "start_time", label: "Start Time (HH:MM)", type: "text", required: true },
        { name: "end_time", label: "End Time (HH:MM)", type: "text", required: true },
        { name: "slot_minutes", label: "Slot Length (minutes)", type: "number" },
      ],
      initial: { slot_minutes: 30, start_time: "09:00", end_time: "17:00" },
      onSubmit: async (payload) => {
        await api.post("/schedule/availability/", payload);
        toast("Availability added.", "success");
      },
    });
  }

  await loadSlots();
}
