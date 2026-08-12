import { api } from "../api.js";
import { hasPermission } from "../state.js";
import { renderTable, renderPager } from "../components/table.js";
import { openModal } from "../components/modal.js";
import { toast } from "../components/toast.js";
import { navigate } from "../router.js";
import { el, formatDateTime, capitalize, skeletonBlock, appointmentStatusBadgeClass } from "../utils.js";
import appointmentsModule, { selfServiceActions } from "./appointments.js";

const WEEKDAYS = [
  { value: 0, label: "Monday" }, { value: 1, label: "Tuesday" }, { value: 2, label: "Wednesday" },
  { value: 3, label: "Thursday" }, { value: 4, label: "Friday" }, { value: 5, label: "Saturday" },
  { value: 6, label: "Sunday" },
];

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

export async function render(container) {
  container.innerHTML = "";
  const local = { skip: 0, limit: 20, total: 0, items: [], doctorFilter: "", bookedFilter: "" };

  // ---- My Upcoming Appointments (self-service patients only) ----
  // Same "modify or cancel from the Scheduling page too, not just
  // Appointments/Home" ask this whole flow already supports elsewhere —
  // reuses appointments.js's exact reschedule/cancel controls rather than
  // re-implementing them a third time.
  const isSelfServicePatient = hasPermission("appointment:view_own") && !hasPermission("appointment:manage");
  if (isSelfServicePatient) {
    const myApptsHost = el("div", {});
    container.appendChild(
      el("div", { class: "card" }, [
        el("div", { class: "card-header" }, [el("h2", {}, "My Upcoming Appointments")]),
        myApptsHost,
      ])
    );

    async function loadMyAppointments() {
      myApptsHost.innerHTML = "";
      myApptsHost.appendChild(skeletonBlock(2));
      try {
        const page = await api.get("/appointments/?upcoming_only=true&limit=10");
        renderMyAppointments(page.items || []);
      } catch (err) {
        myApptsHost.innerHTML = "";
        myApptsHost.appendChild(el("div", { class: "banner banner-error" }, err.message || "Failed to load your appointments."));
      }
    }

    function renderMyAppointments(items) {
      myApptsHost.innerHTML = "";
      if (!items.length) {
        myApptsHost.appendChild(el("div", { class: "empty-state" }, "No upcoming appointments — book one below."));
        return;
      }
      for (const appt of items) {
        const actionsHost = el("div", { style: "display:flex;gap:6px;align-items:center;" }, [
          el("span", { class: appointmentStatusBadgeClass(appt.status) }, capitalize(appt.status)),
          ...selfServiceActions(appt, loadMyAppointments),
        ]);
        // Buttons inside actionsHost shouldn't also trigger the card's own
        // click-through to the detail page — same stopPropagation
        // convention table.js uses for its row-actions cell.
        actionsHost.addEventListener("click", (e) => e.stopPropagation());

        const card = el("div", { class: "card card-interactive", style: "margin-bottom:8px;cursor:pointer;" }, [
          el("div", { style: "display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;" }, [
            el("div", {}, [
              el("span", { style: "font-weight:600;" }, formatDateTime(appt.appointment_datetime)),
              el("span", { class: "muted", style: "margin-left:8px;font-size:12.5px;" }, appt.reason || "—"),
            ]),
            actionsHost,
          ]),
        ]);
        card.addEventListener("click", () => navigate(`/appointments/${appt.id}`));
        myApptsHost.appendChild(card);
      }
    }

    await loadMyAppointments();
  }

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

  // ---- Book an appointment ----
  // Doctor recommendation + booking now lives in the unified "New Request"
  // flow (patient/symptoms/medical-record intake shared with referrals) —
  // see static/js/modules/new_request.js.
  const newRequestBtn = el("button", { class: "btn-primary" }, "+ Book a New Appointment");
  newRequestBtn.addEventListener("click", () => navigate("/requests/new"));
  container.appendChild(
    el("div", { class: "card" }, [
      el("div", { class: "card-header" }, [el("h2", {}, "Book an Appointment")]),
      el("p", { class: "muted" }, "Use the unified New Request flow to get doctor recommendations and book a slot."),
      newRequestBtn,
    ])
  );

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

  // ---- All Appointments (merged from the old standalone Appointments page) ----
  // Full manageable list — search, pagination, edit/delete for staff — as
  // opposed to "My Upcoming Appointments" above, which is just the
  // at-a-glance next-few. Reuses the exact same resource module the old
  // /appointments route rendered (its own card/header included), not a
  // second implementation.
  const appointmentsHost = el("div", {});
  container.appendChild(appointmentsHost);
  await appointmentsModule.render(appointmentsHost);

  await loadSlots();
}
