import { api } from "../api.js";
import { hasPermission } from "../state.js";
import { navigate } from "../router.js";
import { toast } from "../components/toast.js";
import { el, formatDateTime } from "../utils.js";

// The unified "New Request" flow — replaces the old separate "New Referral"
// form (referrals.js) and "Book an Appointment" panel (schedule.js). One
// combined intake: pick Referral vs Direct Appointment, pick the patient,
// describe symptoms and/or pick or upload a medical record, get doctors
// recommended (ranked by specialty match + same-city priority + insurance +
// availability, with a name-search fallback), then submit. Picking a real
// doctor for a Referral books it immediately — see
// app/agents/nodes/scheduling.py::book_real_appointment_node — no separate
// coordinator-approval step for a self-picked real doctor.

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

// Shared by both submission modes — ScheduleSlot rows are already 30-minute
// increments (however the doctor's DoctorAvailability was configured), so
// this is a real time-of-day picker, not just a day picker. `onSelectSlot`
// decides what a click does: appointment mode books immediately, referral
// mode just records the preference (toggle-selectable, highlighted).
async function renderSlotList(hostEl, doctorId, { onSelectSlot, selectedSlotId = null }) {
  hostEl.innerHTML = '<div class="loading-line">Loading available slots…</div>';
  try {
    const page = await api.get(`/schedule/slots/?doctor_id=${doctorId}&is_booked=false&upcoming_only=true&limit=20`);
    const slots = page.items || [];
    hostEl.innerHTML = "";
    if (!slots.length) {
      hostEl.appendChild(el("div", { class: "banner banner-warning" }, "No open slots for this doctor yet."));
      return;
    }
    const grid = el("div", { class: "grid-auto" });
    for (const slot of slots) {
      const isSelected = selectedSlotId === slot.id;
      const btn = el("button", { class: isSelected ? "btn-primary btn-sm" : "btn-secondary btn-sm" }, formatDateTime(slot.starts_at));
      btn.addEventListener("click", () => onSelectSlot(slot));
      grid.appendChild(btn);
    }
    hostEl.appendChild(grid);
  } catch (err) {
    hostEl.innerHTML = "";
    hostEl.appendChild(el("div", { class: "banner banner-error" }, err.message || "Failed to load slots."));
  }
}

export async function render(container) {
  container.innerHTML = "";

  const state = { mode: "referral", patientId: null, medicalRecordId: null, selectedDoctor: null, selectedSlotId: null };
  const isSelfServicePatient = hasPermission("patient:view_own") && !hasPermission("patient:view_all");

  // ---- Mode + Patient ----
  const modeSelect = el("select", {}, [
    el("option", { value: "referral" }, "Referral"),
    el("option", { value: "appointment" }, "Direct Appointment"),
  ]);
  modeSelect.addEventListener("change", () => {
    state.mode = modeSelect.value;
    renderSubmission();
  });

  const patientHost = el("div", {});

  // ---- Symptoms + medical record ----
  const symptomsInput = el("textarea", { placeholder: "Describe symptoms / reason for the visit…" });
  const recordSelect = el("select", {});
  recordSelect.appendChild(el("option", { value: "" }, "No record selected"));
  recordSelect.addEventListener("change", () => {
    state.medicalRecordId = recordSelect.value ? Number(recordSelect.value) : null;
    checkStale();
  });

  // "Find Recommended Doctors" runs once per click, not live — this tracks
  // whether symptoms/record have drifted from what the last search actually
  // used, so the user gets a visible nudge to re-run it instead of silently
  // acting on stale results.
  let lastSearchSignature = null;
  function searchSignature() {
    return `${symptomsInput.value.trim()}|${state.medicalRecordId || ""}`;
  }
  const staleHint = el(
    "p",
    { class: "banner banner-warning", style: "display:none;font-size:12.5px;margin:8px 0;" },
    'Symptoms or medical record changed since your last search — click "Find Recommended Doctors" again to refresh.'
  );
  function checkStale() {
    staleHint.style.display = lastSearchSignature !== null && searchSignature() !== lastSearchSignature ? "" : "none";
  }
  symptomsInput.addEventListener("input", checkStale);

  async function loadPatientRecords() {
    recordSelect.innerHTML = "";
    recordSelect.appendChild(el("option", { value: "" }, "No record selected"));
    if (!state.patientId) return;
    try {
      const page = await api.get(`/medical-records/?patient_id=${state.patientId}&limit=100`);
      for (const r of page.items || []) {
        recordSelect.appendChild(
          el("option", { value: r.id }, `#${r.id} — ${r.record_type || r.diagnosis || "record"} (${formatDateTime(r.visit_date)})`)
        );
      }
    } catch {
      // Leave the dropdown at "No record selected" — free-text symptoms still work.
    }
  }

  const uploadFileInput = el("input", { type: "file" });
  const uploadRecordType = el("input", { type: "text", placeholder: "Record type (optional)", style: "max-width:200px;" });
  const uploadNotes = el("input", { type: "text", placeholder: "Notes (optional)", style: "max-width:220px;" });
  const uploadBtn = el("button", { class: "btn-secondary btn-sm" }, "Upload New Document");
  uploadBtn.addEventListener("click", async () => {
    if (!state.patientId) {
      toast("Select a patient first.", "error");
      return;
    }
    if (!uploadFileInput.files[0]) {
      toast("Choose a file first.", "error");
      return;
    }
    const formData = new FormData();
    formData.append("file", uploadFileInput.files[0]);
    formData.append("patient_id", String(state.patientId));
    if (uploadRecordType.value.trim()) formData.append("record_type", uploadRecordType.value.trim());
    if (uploadNotes.value.trim()) formData.append("notes", uploadNotes.value.trim());
    uploadBtn.disabled = true;
    try {
      const doc = await api.upload("/medical-records/quick-upload", formData);
      toast("Document uploaded to your medical records.", "success");
      uploadFileInput.value = "";
      uploadRecordType.value = "";
      uploadNotes.value = "";
      await loadPatientRecords();
      recordSelect.value = String(doc.medical_record_id);
      state.medicalRecordId = doc.medical_record_id;
      checkStale();
    } catch (err) {
      toast(err.message || "Upload failed.", "error");
    } finally {
      uploadBtn.disabled = false;
    }
  });

  async function setupPatientSelector() {
    patientHost.innerHTML = "";
    if (isSelfServicePatient) {
      let own = null;
      try {
        own = ((await api.get("/patients/?limit=1")).items || [])[0] || null;
      } catch {
        patientHost.appendChild(el("div", { class: "banner banner-error" }, "Couldn't load your patient record — try again in a moment."));
        return;
      }
      if (!own) {
        patientHost.appendChild(
          el("div", { class: "banner banner-error" },
            'Your account isn\'t linked to a patient record yet — ask an admin to "Link to Patient" first.')
        );
        return;
      }
      state.patientId = own.id;
      patientHost.appendChild(
        el("div", { class: "field" }, [
          el("label", {}, "Patient"),
          el("select", { disabled: "disabled" }, [el("option", {}, `${own.first_name} ${own.last_name} (You)`)]),
        ])
      );
      await loadPatientRecords();
    } else {
      const select = el("select", {});
      select.appendChild(el("option", { value: "" }, "Select a patient…"));
      try {
        const page = await api.get("/patients/?limit=200");
        for (const p of page.items || []) {
          select.appendChild(el("option", { value: p.id }, `${p.first_name} ${p.last_name} (#${p.id})`));
        }
      } catch {
        select.innerHTML = "";
        select.appendChild(el("option", { value: "" }, "Failed to load patients"));
      }
      select.addEventListener("change", async () => {
        state.patientId = select.value ? Number(select.value) : null;
        state.medicalRecordId = null;
        recordSelect.value = "";
        await loadPatientRecords();
        renderSubmission();
      });
      patientHost.appendChild(el("div", { class: "field" }, [el("label", {}, "Patient"), select]));
    }
  }

  // ---- Doctor recommendation + name-search fallback ----
  const doctorResultsHost = el("div", {});
  const searchNameInput = el("input", { type: "search", placeholder: "Search doctor by name…" });
  const searchNameBtn = el("button", { class: "btn-secondary btn-sm" }, "Search");
  const searchResultsHost = el("div", {});
  const selectedDoctorHost = el("div", {});

  function doctorCard(c, { showScore }) {
    const selectBtn = el("button", { class: "btn-primary btn-sm", style: "margin-top:8px;" }, "Select this doctor");
    selectBtn.addEventListener("click", () => {
      state.selectedDoctor = { doctor_id: c.doctor_id, first_name: c.first_name, last_name: c.last_name, specialization: c.specialization };
      state.selectedSlotId = null; // slots are doctor-specific — a prior pick no longer applies
      renderSelectedDoctor();
      toast(`Dr. ${c.first_name} ${c.last_name} selected.`, "success");
    });
    const badges = [];
    if (c.in_network) badges.push(el("span", { class: "badge badge-good" }, "In-network"));
    if (c.distance_mi === 0) badges.push(el("span", { class: "badge badge-good" }, "Same city"));
    if (c.next_available_days != null) badges.push(el("span", { class: "badge badge-good" }, `Next slot in ${c.next_available_days}d`));
    const children = [
      badges.length ? el("div", { class: "chip-row", style: "margin-bottom:6px;" }, badges) : null,
      el("div", { style: "font-weight:650;" }, `${c.first_name} ${c.last_name}`),
      el("div", { class: "muted", style: "font-size:12.5px;margin-bottom:6px;" }, c.specialization || ""),
    ];
    if (showScore) {
      const score = Math.round((c.score || 0) * 100);
      children.push(el("div", { class: "bar-chart-track" }, [el("div", { class: "bar-chart-fill", style: `width:${Math.max(score, 2)}%` })]));
      children.push(el("ul", { style: "margin:6px 0 0 16px;padding:0;font-size:12px;" }, (c.reasons || []).map((r) => el("li", {}, r))));
    }
    children.push(selectBtn);
    return el("div", { class: "card" }, children);
  }

  const findBtn = el("button", { class: "btn-primary btn-sm" }, "Find Recommended Doctors");
  findBtn.addEventListener("click", async () => {
    if (!state.patientId) {
      toast("Select a patient first.", "error");
      return;
    }
    doctorResultsHost.innerHTML = '<div class="loading-line">Finding recommended doctors…</div>';
    try {
      const params = new URLSearchParams({ reason: symptomsInput.value || "", patient_id: String(state.patientId) });
      if (state.medicalRecordId) params.set("medical_record_id", String(state.medicalRecordId));
      const candidates = await api.get(`/doctors/recommend?${params.toString()}`);
      lastSearchSignature = searchSignature();
      staleHint.style.display = "none";
      doctorResultsHost.innerHTML = "";
      if (!candidates.length) {
        doctorResultsHost.appendChild(el("div", { class: "empty-state" }, "No matching doctors — try Search by Name below."));
        return;
      }
      const grid = el("div", { class: "grid-auto" });
      for (const c of candidates) grid.appendChild(doctorCard(c, { showScore: true }));
      doctorResultsHost.appendChild(grid);
    } catch (err) {
      doctorResultsHost.innerHTML = "";
      doctorResultsHost.appendChild(el("div", { class: "banner banner-error" }, err.message || "Failed to load recommendations."));
    }
  });

  searchNameBtn.addEventListener("click", async () => {
    const q = searchNameInput.value.trim();
    if (!q) {
      toast("Type a name to search.", "error");
      return;
    }
    searchResultsHost.innerHTML = '<div class="loading-line">Searching…</div>';
    try {
      const page = await api.get(`/doctors/?q=${encodeURIComponent(q)}&limit=20`);
      const items = page.items || [];
      searchResultsHost.innerHTML = "";
      if (!items.length) {
        searchResultsHost.appendChild(el("div", { class: "empty-state" }, "No doctors match that name."));
        return;
      }
      const grid = el("div", { class: "grid-auto" });
      for (const d of items) {
        grid.appendChild(
          doctorCard({ doctor_id: d.id, first_name: d.first_name, last_name: d.last_name, specialization: d.specialization }, { showScore: false })
        );
      }
      searchResultsHost.appendChild(grid);
    } catch (err) {
      searchResultsHost.innerHTML = "";
      searchResultsHost.appendChild(el("div", { class: "banner banner-error" }, err.message || "Search failed."));
    }
  });

  function renderSelectedDoctor() {
    selectedDoctorHost.innerHTML = "";
    if (state.selectedDoctor) {
      const d = state.selectedDoctor;
      const clearBtn = el("button", { class: "btn-ghost btn-sm" }, "Clear selection");
      clearBtn.addEventListener("click", () => {
        state.selectedDoctor = null;
        state.selectedSlotId = null;
        renderSelectedDoctor();
      });
      selectedDoctorHost.appendChild(
        el("div", { class: "banner banner-info", style: "display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;" }, [
          el("span", {}, `Selected: Dr. ${d.first_name} ${d.last_name}${d.specialization ? " — " + d.specialization : ""}`),
          clearBtn,
        ])
      );
    }
    renderSubmission();
  }

  // ---- Submission (mode-specific) ----
  const submissionHost = el("div", {});

  function renderSubmission() {
    submissionHost.innerHTML = "";
    if (!state.patientId) {
      submissionHost.appendChild(el("div", { class: "muted" }, "Select a patient above to continue."));
      return;
    }
    if (state.mode === "referral") renderReferralSubmission();
    else renderAppointmentSubmission();
  }

  function renderReferralSubmission() {
    const referringDoctorSelect = el("select", {});
    fillDoctorOptions(referringDoctorSelect, {
      placeholder: isSelfServicePatient ? "Select your primary care doctor…" : "Select referring doctor…",
    });
    const requestDateInput = el("input", { type: "date", value: new Date().toISOString().slice(0, 10) });
    const targetWaitInput = el("input", { type: "number", value: "14" });
    const preferredLocationInput = el("input", { type: "text", placeholder: "Preferred location (optional)" });
    const submitBtn = el("button", { class: "btn-primary" }, "Submit Referral");

    const banner = el(
      "div",
      { class: `banner ${state.selectedDoctor ? "banner-info" : "banner-warning"}` },
      state.selectedDoctor
        ? "A doctor is selected — this referral will be booked immediately, no coordinator approval step needed."
        : "No doctor selected yet — this referral will go to a care coordinator to pick a specialist after submission."
    );

    submitBtn.addEventListener("click", async () => {
      const referringDoctorId = Number(referringDoctorSelect.value);
      if (!referringDoctorId) {
        toast(isSelfServicePatient ? "Select your primary care doctor." : "Select a referring doctor.", "error");
        return;
      }
      submitBtn.disabled = true;
      try {
        const created = await api.post("/referral/requests/", {
          patient_id: state.patientId,
          referring_doctor_id: referringDoctorId,
          specialist_id: state.selectedDoctor ? state.selectedDoctor.doctor_id : null,
          medical_record_id: state.medicalRecordId,
          preferred_slot_id: state.selectedDoctor ? state.selectedSlotId : null,
          request_date: requestDateInput.value,
          reason: symptomsInput.value || null,
          preferred_location: preferredLocationInput.value || null,
          target_wait_days: Number(targetWaitInput.value) || 14,
        });
        toast("Referral submitted.", "success");
        navigate(`/referrals/${created.id}`);
      } catch (err) {
        toast(err.message || "Failed to submit referral.", "error");
      } finally {
        submitBtn.disabled = false;
      }
    });

    const children = [
      banner,
      el("div", { class: "field" }, [el("label", {}, isSelfServicePatient ? "Your Primary Care Doctor" : "Referring Doctor"), referringDoctorSelect]),
      el("div", { class: "field" }, [el("label", {}, "Request Date"), requestDateInput]),
      el("div", { class: "field" }, [el("label", {}, "Target Wait (days)"), targetWaitInput]),
      el("div", { class: "field" }, [el("label", {}, "Preferred Location"), preferredLocationInput]),
    ];

    // Only meaningful once a real, bookable specialist is chosen — a
    // referral with no pre-picked doctor goes through the coordinator
    // queue, where the concept of "this platform's open slots" doesn't
    // apply yet (see book_real_appointment_node vs recommend_specialist).
    if (state.selectedDoctor) {
      const slotsHost = el("div", {});
      const refreshSlots = () =>
        renderSlotList(slotsHost, state.selectedDoctor.doctor_id, {
          selectedSlotId: state.selectedSlotId,
          onSelectSlot: (slot) => {
            state.selectedSlotId = state.selectedSlotId === slot.id ? null : slot.id; // click again to deselect
            refreshSlots();
          },
        });
      refreshSlots();
      children.push(
        el("div", { style: "margin:14px 0;" }, [
          el("h3", {}, `Preferred slot — Dr. ${state.selectedDoctor.first_name} ${state.selectedDoctor.last_name} (optional)`),
          el("p", { class: "muted", style: "font-size:12px;" },
            "Pick a specific time, or leave unselected to get the soonest available slot automatically."),
          slotsHost,
        ])
      );
    }

    children.push(submitBtn);
    submissionHost.appendChild(el("div", {}, children));
  }

  function renderAppointmentSubmission() {
    if (!state.selectedDoctor) {
      submissionHost.appendChild(el("div", { class: "banner banner-warning" }, "Select a doctor above to see open slots and book."));
      return;
    }
    const slotsHost = el("div", {});
    submissionHost.appendChild(
      el("div", {}, [el("h3", {}, `Available slots — Dr. ${state.selectedDoctor.first_name} ${state.selectedDoctor.last_name}`), slotsHost])
    );

    async function bookSlot(slot) {
      if (!confirm(`Book ${formatDateTime(slot.starts_at)} with Dr. ${state.selectedDoctor.first_name} ${state.selectedDoctor.last_name}?`)) return;
      try {
        const appointment = await api.post(`/schedule/slots/${slot.id}/book`, {
          patient_id: state.patientId,
          reason: symptomsInput.value || null,
          medical_record_id: state.medicalRecordId,
        });
        toast("Appointment booked.", "success");
        navigate(`/appointments/${appointment.id}`);
      } catch (err) {
        toast(err.message || "Booking failed.", "error");
      }
    }

    renderSlotList(slotsHost, state.selectedDoctor.doctor_id, { onSelectSlot: bookSlot });
  }

  // ---- Assemble ----
  container.appendChild(
    el("div", { class: "card" }, [
      el("div", { class: "card-header" }, [el("h2", {}, "New Request")]),
      el("p", { class: "muted" },
        "Choose whether you're requesting a referral or booking a direct appointment, pick the patient, and describe the reason for the visit."),
      el("div", { class: "field" }, [el("label", {}, "Request Type"), modeSelect]),
      patientHost,
    ])
  );
  container.appendChild(
    el("div", { class: "card" }, [
      el("div", { class: "card-header" }, [el("h2", {}, "Symptoms & Medical Record")]),
      el("div", { class: "field" }, [el("label", {}, "Symptoms / Reason"), symptomsInput]),
      el("div", { class: "field" }, [el("label", {}, "Existing Medical Record (optional)"), recordSelect]),
      el("p", { class: "muted", style: "font-size:12px;margin:6px 0;" }, "Or upload a new document straight into your medical records:"),
      el("div", { class: "toolbar" }, [uploadFileInput, uploadRecordType, uploadNotes, uploadBtn]),
    ])
  );
  container.appendChild(
    el("div", { class: "card" }, [
      el("div", { class: "card-header" }, [el("h2", {}, "Find a Doctor")]),
      el("p", { class: "muted" }, "Ranked by specialty match, same-city priority, insurance network, and soonest availability."),
      findBtn,
      staleHint,
      doctorResultsHost,
      el("div", { style: "margin-top:14px;" }, [
        el("div", { class: "toolbar" }, [searchNameInput, searchNameBtn]),
        el("p", { class: "muted", style: "font-size:12px;" }, "Can't find who you're looking for? Search by name — irrespective of specialty or location."),
        searchResultsHost,
      ]),
      el("div", { style: "margin-top:10px;" }, [selectedDoctorHost]),
    ])
  );
  container.appendChild(el("div", { class: "card" }, [el("div", { class: "card-header" }, [el("h2", {}, "Submit")]), submissionHost]));

  await setupPatientSelector();
  renderSelectedDoctor();
}
