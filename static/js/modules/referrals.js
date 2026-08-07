import { api, streamReferralEvents } from "../api.js";
import { hasPermission } from "../state.js";
import { navigate } from "../router.js";
import { renderTable, renderPager } from "../components/table.js";
import { openModal } from "../components/modal.js";
import { toast } from "../components/toast.js";
import {
  el, escapeHtml, formatDate, formatDateTime, capitalize,
  referralStatusBadgeClass, extractionStatusBadgeClass, REFERRAL_STATUSES, REFERRAL_PROGRESS_INFO,
} from "../utils.js";

const patientNameCache = new Map();
const doctorNameCache = new Map();

// POC data: a handful of canned prescriptions per specialty so a doctor
// completing a referral's outcome can pick a plausible one instead of
// typing from scratch — there are no real prescribing doctors behind this
// demo, so nothing here should be read as real clinical guidance.
const SAMPLE_PRESCRIPTIONS_BY_SPECIALTY = {
  Orthopedics: [
    "Ibuprofen 400mg three times daily for 5 days; physical therapy 2x/week for 4 weeks; follow up in 4 weeks.",
    "Naproxen 500mg twice daily for 7 days; activity modification; re-evaluate if no improvement in 2 weeks.",
  ],
  Cardiology: [
    "Aspirin 81mg once daily; Atorvastatin 20mg at bedtime; follow-up ECG and lipid panel in 2 weeks.",
    "Metoprolol 25mg twice daily; low-sodium diet; follow up in 1 week to reassess symptoms.",
  ],
  Dermatology: [
    "Hydrocortisone 1% cream twice daily for 7 days; Cetirizine 10mg once daily for itching.",
    "Topical clindamycin gel once daily; avoid known irritants; follow up in 3 weeks.",
  ],
  General: [
    "Acetaminophen 500mg as needed for pain; rest and hydration; follow up in 2 weeks if symptoms persist.",
  ],
};

// Mirrors the same keyword table used elsewhere for referral-specialty
// matching (schedule.js / app/agents/nodes/specialist.py) — a lightweight
// client-side approximation used only to sort the sample-prescription
// dropdown, not a real prescribing decision.
const PRESCRIPTION_SPECIALTY_KEYWORDS = {
  Orthopedics: ["back", "spine", "joint", "knee", "hip", "shoulder", "fracture", "orthopedic"],
  Cardiology: ["heart", "cardiac", "chest pain", "palpitation", "cardio"],
  Dermatology: ["skin", "rash", "derma", "mole", "eczema"],
};

function inferPrescriptionSpecialty(text) {
  const lower = (text || "").toLowerCase();
  for (const [specialty, keywords] of Object.entries(PRESCRIPTION_SPECIALTY_KEYWORDS)) {
    if (keywords.some((k) => lower.includes(k))) return specialty;
  }
  return null;
}

async function resolvePatientName(id) {
  if (patientNameCache.has(id)) return patientNameCache.get(id);
  const name = await api
    .get(`/patients/${id}`)
    .then((p) => `${p.first_name} ${p.last_name}`)
    .catch(() => `Patient #${id}`);
  patientNameCache.set(id, name);
  return name;
}
async function resolveDoctorName(id) {
  if (doctorNameCache.has(id)) return doctorNameCache.get(id);
  const name = await api
    .get(`/doctors/${id}`)
    .then((d) => `${d.first_name} ${d.last_name}`)
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

export async function renderList(container) {
  container.innerHTML = "";
  const local = { skip: 0, limit: 20, total: 0, items: [], statusFilter: "" };

  const statusSelect = el("select", {});
  statusSelect.appendChild(el("option", { value: "" }, "All statuses"));
  for (const s of REFERRAL_STATUSES) statusSelect.appendChild(el("option", { value: s }, capitalize(s)));
  statusSelect.addEventListener("change", () => {
    local.statusFilter = statusSelect.value;
    local.skip = 0;
    load();
  });

  const isSelfServicePatient = hasPermission("referral:create") && !hasPermission("patient:view_all");
  const toolbar = el("div", { class: "toolbar" }, [statusSelect, el("div", { class: "spacer" })]);
  if (hasPermission("referral:create")) {
    const newBtn = el("button", { class: "btn-primary" }, isSelfServicePatient ? "+ Request a Referral" : "+ New Referral");
    newBtn.addEventListener("click", openCreateForm);
    toolbar.appendChild(newBtn);
  }

  const capabilityNote = el(
    "p",
    { class: "muted", style: "font-size:12.5px;margin:-6px 0 12px;" },
    isSelfServicePatient
      ? "As a patient, you can request a referral for yourself — your own record is filled in automatically."
      : hasPermission("referral:create")
      ? "You can submit a referral on behalf of any patient."
      : "Your role can view referrals but not submit new ones (needs referral:create)."
  );

  const permBanner = el("div", { class: "banner banner-error hidden" });
  const tableHost = el("div", {});
  const pagerHost = el("div", {});
  container.appendChild(
    el("div", { class: "card" }, [
      el("div", { class: "card-header" }, [el("h2", {}, "Referrals")]),
      capabilityNote,
      toolbar, permBanner, tableHost, pagerHost,
    ])
  );

  async function load() {
    const params = new URLSearchParams({ skip: local.skip, limit: local.limit });
    if (local.statusFilter) params.set("status_filter", local.statusFilter);
    try {
      const page = await api.get(`/referral/requests/?${params.toString()}`);
      local.items = page.items || [];
      local.total = page.total || 0;
      permBanner.classList.add("hidden");
    } catch (err) {
      local.items = [];
      local.total = 0;
      permBanner.textContent = err.message || "Failed to load referrals.";
      permBanner.classList.remove("hidden");
    }
    await renderRows();
  }

  async function renderRows() {
    const rows = await Promise.all(
      local.items.map(async (r) => ({
        ...r,
        _patientName: await resolvePatientName(r.patient_id),
        _doctorName: await resolveDoctorName(r.referring_doctor_id),
      }))
    );
    renderTable(tableHost, {
      columns: [
        { key: "id", label: "ID" },
        { key: "_patientName", label: "Patient" },
        { key: "_doctorName", label: "Referring Doctor" },
        {
          key: "status", label: "Status", html: true,
          format: (r) => `<span class="${referralStatusBadgeClass(r.status)}">${capitalize(r.status)}</span>`,
        },
        { key: "request_date", label: "Requested", format: (r) => formatDate(r.request_date) },
        { key: "target_wait_days", label: "Target Wait (days)" },
      ],
      rows,
      onRowClick: (row) => navigate(`/referrals/${row.id}`),
      emptyMessage: "No referrals found.",
    });
    renderPager(pagerHost, {
      skip: local.skip, limit: local.limit, total: local.total,
      onChange: (skip) => { local.skip = skip; load(); },
    });
  }

  async function openCreateForm() {
    let ownPatient = null;
    if (isSelfServicePatient) {
      try {
        const page = await api.get("/patients/?limit=1");
        ownPatient = (page.items || [])[0] || null;
      } catch {
        alert("Couldn't load your patient record — try again in a moment.");
        return;
      }
      if (!ownPatient) {
        // A blocking alert, not a toast: this is a hard stop (there's no
        // usable form to show without a linked record), and a toast that
        // auto-dismisses in ~4s is too easy to miss for a "here's what to
        // do next" message.
        alert(
          "Your account isn't linked to a patient record yet, so there's nothing to auto-fill.\n\n" +
          "Ask an admin to link one: Admin panel → find your account → \"Link to Patient\"."
        );
        return;
      }
    }

    const patientField = isSelfServicePatient
      ? {
          name: "patient_id", label: "Patient", type: "select", required: true, disabled: true,
          options: [{ value: ownPatient.id, label: `${ownPatient.first_name} ${ownPatient.last_name} (You)` }],
        }
      : {
          name: "patient_id", label: "Patient", type: "select-async", source: "/patients", required: true,
          optionLabel: (p) => `${p.first_name} ${p.last_name} (#${p.id})`,
        };

    openModal({
      title: isSelfServicePatient ? "Request a Referral" : "New Referral",
      submitLabel: "Submit Referral",
      fields: [
        patientField,
        {
          name: "referring_doctor_id",
          label: isSelfServicePatient ? "Your Primary Care Doctor" : "Referring Doctor",
          type: "select-async", source: "/doctors", required: true,
          optionLabel: (d) => `${d.first_name} ${d.last_name} (#${d.id})`,
        },
        {
          name: "specialist_id", label: "Specialist (optional)", type: "select-async", source: "/doctors",
          optionLabel: (d) => `${d.first_name} ${d.last_name} — ${d.specialization} (#${d.id})`,
        },
        { name: "request_date", label: "Request Date", type: "date", required: true },
        { name: "reason", label: "Reason", type: "textarea" },
        { name: "preferred_location", label: "Preferred Location", type: "text" },
        { name: "target_wait_days", label: "Target Wait (days)", type: "number" },
      ],
      initial: {
        request_date: new Date().toISOString().slice(0, 10),
        target_wait_days: 14,
        ...(isSelfServicePatient ? { patient_id: ownPatient.id } : {}),
      },
      onSubmit: async (payload) => {
        const created = await api.post("/referral/requests/", payload);
        toast("Referral submitted — workflow started.", "success");
        navigate(`/referrals/${created.id}`);
      },
    });
  }

  await load();
}

export async function renderDetail(container, { id }) {
  const referralId = Number(id);
  container.innerHTML = "";

  let referral = null;
  let activeTab = "documents";

  const backBtn = el("button", { class: "btn-ghost btn-sm" }, "← Back to Referrals");
  backBtn.addEventListener("click", () => navigate("/referrals"));

  const headerHost = el("div", {});
  const liveStatusHost = el("span", {});
  const liveLog = el("div", { class: "live-log" });
  const liveHost = el("div", { class: "live-indicator" }, [liveStatusHost, liveLog]);

  const tabsHost = el("div", { class: "tabs" });
  const panelHost = el("div", { class: "tab-panel" });

  container.appendChild(el("div", {}, [backBtn]));
  container.appendChild(el("div", { class: "card" }, [headerHost, liveHost]));
  container.appendChild(el("div", { class: "card" }, [tabsHost, panelHost]));

  async function loadReferral() {
    try {
      referral = await api.get(`/referral/requests/${referralId}`);
      await renderHeader();
    } catch (err) {
      headerHost.innerHTML = "";
      headerHost.appendChild(el("div", { class: "banner banner-error" }, err.message || "Referral not found."));
    }
  }

  async function renderHeader() {
    headerHost.innerHTML = "";
    const [patientName, doctorName] = await Promise.all([
      resolvePatientName(referral.patient_id),
      resolveDoctorName(referral.referring_doctor_id),
    ]);
    const specialistName = referral.specialist_id ? await resolveDoctorName(referral.specialist_id) : null;

    // Backend only checks visibility (not a :manage permission) on referral
    // PATCH/DELETE, so a `patient` role could technically edit their own
    // referral — gated more conservatively here so a demo patient session
    // doesn't see a working edit/delete control.
    const canEdit = hasPermission("referral:approve") || hasPermission("referral:override") || hasPermission("admin:*");

    const titleRow = el("div", {}, [
      el("h2", {}, `Referral #${referral.id}`),
      el("span", { class: referralStatusBadgeClass(referral.status) }, capitalize(referral.status)),
    ]);
    const top = el("div", { class: "card-header" }, [titleRow]);
    const editTooltip = "Editing referrals requires coordinator, specialist, or admin privileges.";
    if (canEdit) {
      const editBtn = el("button", { class: "btn-secondary btn-sm", title: editTooltip }, "Edit");
      editBtn.addEventListener("click", openEditForm);
      const delBtn = el("button", { class: "btn-danger btn-sm", title: editTooltip }, "Delete");
      delBtn.addEventListener("click", handleDelete);
      top.appendChild(el("div", { class: "row-actions" }, [editBtn, delBtn]));
    }
    headerHost.appendChild(top);

    // "What's happening right now, whose job is it, what happens next" — a
    // raw status word like `awaiting_specialist_approval` doesn't convey any
    // of that on its own. Shown to every role, not just the patient: a
    // coordinator benefits from the same at-a-glance framing.
    const progress = REFERRAL_PROGRESS_INFO[referral.status];
    if (progress) {
      const needsMyApproval = referral.status === "awaiting_specialist_approval" && hasPermission("referral:approve");
      const bannerChildren = [
        el("div", { style: "font-weight:650;margin-bottom:2px;" }, `Current step: ${progress.label}`),
        el("div", { style: "font-size:12.5px;" }, `Waiting on: ${progress.waitingOn}`),
        el("div", { style: "font-size:12.5px;" }, progress.nextStep),
      ];
      // The action itself lives on the Workflow State tab, a click away and
      // easy to miss — surface a direct jump-to-it button right where the
      // status banner already tells an approver it's their turn.
      if (needsMyApproval) {
        const reviewBtn = el("button", { class: "btn-primary btn-sm", style: "margin-top:8px;" }, "Review & select a specialist →");
        reviewBtn.addEventListener("click", () => {
          activeTab = "workflow";
          renderTabs();
          renderPanel();
        });
        bannerChildren.push(reviewBtn);
      }
      headerHost.appendChild(
        el("div", { class: `banner ${needsMyApproval ? "banner-warning" : "banner-info"}`, style: "margin-bottom:14px;" }, bannerChildren)
      );
    }

    headerHost.appendChild(
      el("div", { class: "grid-3" }, [
        infoBlock("Patient", patientName),
        infoBlock("Referring Doctor", doctorName),
        infoBlock("Specialist", specialistName || "—"),
        infoBlock("Request Date", formatDate(referral.request_date)),
        infoBlock("Target Wait", `${referral.target_wait_days ?? "—"} days`),
        infoBlock("Reason", referral.reason || "—"),
      ])
    );
    if (hasPermission("patient:view_all") || hasPermission("patient:view_own")) {
      const profileLink = el("a", { href: `#/patients/${referral.patient_id}` }, "View full patient profile →");
      profileLink.style.cssText = "display:inline-block;margin-top:10px;font-size:12.5px;";
      headerHost.appendChild(profileLink);
    }
  }

  function openEditForm() {
    openModal({
      title: `Edit Referral #${referral.id}`,
      submitLabel: "Save",
      fields: [
        {
          name: "specialist_id", label: "Specialist", type: "select-async", source: "/doctors",
          optionLabel: (d) => `${d.first_name} ${d.last_name} — ${d.specialization} (#${d.id})`,
        },
        { name: "reason", label: "Reason", type: "textarea" },
        { name: "preferred_location", label: "Preferred Location", type: "text" },
        { name: "target_wait_days", label: "Target Wait (days)", type: "number" },
      ],
      initial: referral,
      onSubmit: async (payload) => {
        await api.patch(`/referral/requests/${referral.id}`, payload);
        toast("Referral updated.", "success");
        await loadReferral();
      },
    });
  }

  async function handleDelete() {
    if (!confirm("Delete this referral? This cannot be undone.")) return;
    try {
      await api.del(`/referral/requests/${referral.id}`);
      toast("Referral deleted.", "success");
      navigate("/referrals");
    } catch (err) {
      toast(err.message || "Delete failed.", "error");
    }
  }

  // Outcome visibility mirrors the referral's own visibility server-side
  // (GET /referral/requests/{id}/outcome uses the same scope as the
  // referral itself) — anyone who can open this detail page at all can
  // also see its recorded outcome/summary once one exists.
  const TABS = [
    { key: "documents", label: "Documents" },
    { key: "notes", label: "Notes" },
    { key: "workflow", label: "Workflow State" },
    { key: "timeline", label: "Timeline" },
    { key: "outcome", label: "Outcome" },
  ];

  function renderTabs() {
    tabsHost.innerHTML = "";
    for (const tab of TABS) {
      const btn = el("button", { class: `tab-btn${activeTab === tab.key ? " active" : ""}` }, tab.label);
      btn.addEventListener("click", () => {
        activeTab = tab.key;
        renderTabs();
        renderPanel();
      });
      tabsHost.appendChild(btn);
    }
  }

  async function renderPanel() {
    panelHost.innerHTML = '<div class="loading-line">Loading…</div>';
    if (activeTab === "documents") await renderDocumentsTab();
    else if (activeTab === "notes") await renderNotesTab();
    else if (activeTab === "workflow") await renderWorkflowTab();
    else if (activeTab === "timeline") await renderTimelineTab();
    else if (activeTab === "outcome") await renderOutcomeTab();
  }

  function renderCodeChips(doc) {
    const codes = [];
    try { codes.push(...JSON.parse(doc.extracted_diagnosis_codes || "[]")); } catch { /* not ready yet */ }
    try { codes.push(...JSON.parse(doc.extracted_procedure_codes || "[]")); } catch { /* not ready yet */ }
    if (!codes.length) return "—";
    return `<div class="chip-row">${codes.map((c) => `<span class="chip">${escapeHtml(c)}</span>`).join("")}</div>`;
  }

  async function renderDocumentsTab() {
    panelHost.innerHTML = "";
    panelHost.appendChild(
      el("div", { class: "banner banner-info" },
        "Documents are optional — a filled-in Reason is enough to move this referral forward. Uploading is still " +
        'useful for more accurate code extraction: one file whose name contains "referral" or "letter", and one ' +
        'whose name contains "mri", "x-ray", "imaging", "lab", "scan", "ultrasound", "radiology", or "ct". If ' +
        "nothing is uploaded, a sample document pair is auto-attached so extraction still has something to work with."
      )
    );

    const fileInput = el("input", { type: "file" });
    const uploadBtn = el("button", { class: "btn-primary btn-sm" }, "Upload");
    uploadBtn.addEventListener("click", async () => {
      if (!fileInput.files[0]) {
        toast("Choose a file first.", "error");
        return;
      }
      const formData = new FormData();
      formData.append("file", fileInput.files[0]);
      uploadBtn.disabled = true;
      try {
        await api.upload(`/referral/requests/${referral.id}/documents`, formData);
        toast("Document uploaded.", "success");
        fileInput.value = "";
        await renderDocumentsTab();
        await loadReferral();
      } catch (err) {
        toast(err.message || "Upload failed.", "error");
      } finally {
        uploadBtn.disabled = false;
      }
    });
    panelHost.appendChild(el("div", { class: "toolbar" }, [fileInput, uploadBtn]));

    const listHost = el("div", {});
    panelHost.appendChild(listHost);
    try {
      const docs = await api.get(`/referral/requests/${referral.id}/documents`);
      renderTable(listHost, {
        columns: [
          { key: "filename", label: "Filename" },
          {
            key: "extraction_status", label: "Extraction", html: true,
            format: (d) => `<span class="${extractionStatusBadgeClass(d.extraction_status)}">${capitalize(d.extraction_status)}</span>`,
          },
          { key: "codes", label: "Extracted Codes", html: true, format: renderCodeChips },
          { key: "created_at", label: "Uploaded", format: (d) => formatDateTime(d.created_at) },
        ],
        rows: docs,
        emptyMessage: "No documents uploaded yet.",
      });
    } catch (err) {
      listHost.appendChild(el("div", { class: "banner banner-error" }, err.message || "Failed to load documents."));
    }
  }

  async function renderNotesTab() {
    panelHost.innerHTML = "";
    panelHost.appendChild(
      el("p", { class: "muted" }, "Specialist notes are generated by the referral workflow — there's no manual \"add note\" action.")
    );
    const listHost = el("div", {});
    panelHost.appendChild(listHost);
    try {
      const notes = await api.get(`/referral/requests/${referral.id}/notes`);
      if (!notes.length) {
        listHost.appendChild(el("div", { class: "empty-state" }, "No notes yet."));
      } else {
        for (const note of notes) {
          listHost.appendChild(
            el("div", { class: "card" }, [
              el("div", { class: "muted", style: "font-size:11.5px;margin-bottom:6px;" }, formatDateTime(note.created_at)),
              el("div", {}, note.note),
            ])
          );
        }
      }
    } catch (err) {
      listHost.appendChild(el("div", { class: "banner banner-error" }, err.message || "Failed to load notes."));
    }
  }

  async function renderWorkflowTab() {
    panelHost.innerHTML = "";
    const refreshBtn = el("button", { class: "btn-secondary btn-sm" }, "Refresh");
    panelHost.appendChild(el("div", { class: "toolbar" }, [el("div", { class: "spacer" }), refreshBtn]));
    const body = el("div", {});
    panelHost.appendChild(body);

    async function load() {
      body.innerHTML = '<div class="loading-line">Loading workflow state…</div>';
      let stateData;
      try {
        stateData = await api.get(`/referral-workflow/${referral.id}/state`);
      } catch (err) {
        body.innerHTML = "";
        body.appendChild(el("div", { class: "banner banner-error" }, err.message || "Failed to load workflow state."));
        return;
      }
      body.innerHTML = "";

      if (Array.isArray(stateData.missing_documents) && stateData.missing_documents.length) {
        body.appendChild(el("div", { class: "banner banner-warning" }, `Waiting on: ${stateData.missing_documents.join(", ")} (or a filled-in Reason)`));
      }
      if (Array.isArray(stateData.diagnosis_codes) && stateData.diagnosis_codes.length) {
        body.appendChild(
          el("div", { class: "chip-row", style: "margin-bottom:12px;" }, stateData.diagnosis_codes.map((c) => el("span", { class: "chip" }, c)))
        );
      }

      if (Array.isArray(stateData.specialist_candidates) && stateData.specialist_candidates.length) {
        const canApprove = referral.status === "awaiting_specialist_approval" && hasPermission("referral:approve");
        body.appendChild(
          el("p", { class: "muted", style: "font-size:12px;margin-bottom:8px;" },
            "Mock external provider directory — recording a consult outcome afterward is done by care coordination staff, not these doctor IDs directly.")
        );
        const cardsHost = el("div", { class: "grid-auto" });
        for (const candidate of stateData.specialist_candidates) {
          const score = Math.round((candidate.score || 0) * 100);
          const card = el("div", { class: "card" }, [
            el("div", { style: "font-weight:650;margin-bottom:4px;" }, `Doctor #${candidate.doctor_id}`),
            el("div", { class: "bar-chart-track", style: "margin-bottom:8px;" }, [
              el("div", { class: "bar-chart-fill", style: `width:${Math.max(score, 2)}%` }),
            ]),
            el("div", { class: "muted", style: "font-size:12px;margin-bottom:8px;" }, `Score: ${score}%`),
            el("ul", { style: "margin:0 0 8px 16px;padding:0;font-size:12.5px;" }, (candidate.reasons || []).map((r) => el("li", {}, r))),
          ]);
          if (canApprove) {
            const selectBtn = el("button", { class: "btn-primary btn-sm" }, "Select this specialist");
            selectBtn.addEventListener("click", async () => {
              selectBtn.disabled = true;
              try {
                await api.post(`/referral-workflow/${referral.id}/resume`, { doctor_id: candidate.doctor_id });
                toast("Specialist approved — referral scheduled.", "success");
                await loadReferral();
                await load();
              } catch (err) {
                toast(err.message || "Resume failed.", "error");
              } finally {
                selectBtn.disabled = false;
              }
            });
            card.appendChild(selectBtn);
          }
          cardsHost.appendChild(card);
        }
        body.appendChild(cardsHost);
      }

      const details = el("details", {});
      details.appendChild(el("summary", { style: "cursor:pointer;font-size:12.5px;color:var(--ink-3);margin-top:12px;" }, "Raw workflow state (JSON)"));
      details.appendChild(
        el("pre", { style: "white-space:pre-wrap;font-size:11.5px;background:var(--plane);padding:10px;border-radius:6px;margin-top:8px;" },
          JSON.stringify(stateData, null, 2))
      );
      body.appendChild(details);
    }
    refreshBtn.addEventListener("click", load);
    await load();
  }

  async function renderTimelineTab() {
    panelHost.innerHTML = "";
    let events;
    try {
      events = await api.get(`/referral/requests/${referral.id}/timeline`);
    } catch (err) {
      panelHost.appendChild(el("div", { class: "banner banner-error" }, err.message || "Failed to load timeline."));
      return;
    }
    if (!events.length) {
      panelHost.appendChild(el("div", { class: "empty-state" }, "No timeline events yet."));
      return;
    }
    const list = el("div", { class: "timeline-list" });
    for (const event of events) {
      const detail = Object.entries(event.payload || {})
        .filter(([key]) => key !== "referral_id")
        .map(([key, value]) => `${key}: ${typeof value === "object" ? JSON.stringify(value) : value}`)
        .join(", ");
      list.appendChild(
        el("div", { class: "timeline-item" }, [
          el("div", { class: "timeline-item-dot" }),
          el("div", { class: "timeline-item-body" }, [
            el("div", { style: "font-weight:600;" }, event.label),
            el("div", { class: "muted", style: "font-size:11.5px;" }, formatDateTime(event.created_at)),
            detail ? el("div", { class: "muted", style: "font-size:12px;margin-top:2px;" }, detail) : null,
          ]),
        ])
      );
    }
    panelHost.appendChild(list);
  }

  function buildOutcomeForm() {
    const banner = el("div", { class: "form-banner hidden" });
    const symptoms = el("textarea", { name: "symptoms" });
    const diagnosis = el("textarea", { name: "diagnosis" });
    const prescription = el("textarea", { name: "prescription" });
    const followUp = el("textarea", { name: "follow_up_notes" });
    const submitBtn = el("button", { type: "submit", class: "btn-primary" }, "Record Outcome");

    // POC convenience for the doctor role completing a referral: pick a
    // sample prescription instead of typing one from scratch. Re-sorted
    // (matched specialty first) as symptoms are typed; selecting an option
    // just fills the Prescription field below — still freely editable.
    const prescriptionSelect = el("select", {});
    function fillPrescriptionOptions() {
      const matched = inferPrescriptionSpecialty(symptoms.value);
      const selected = prescriptionSelect.value;
      const specialties = Object.keys(SAMPLE_PRESCRIPTIONS_BY_SPECIALTY);
      const ordered = matched ? [matched, ...specialties.filter((s) => s !== matched)] : specialties;

      prescriptionSelect.innerHTML = "";
      prescriptionSelect.appendChild(el("option", { value: "" }, "Choose a sample prescription…"));
      for (const specialty of ordered) {
        for (const text of SAMPLE_PRESCRIPTIONS_BY_SPECIALTY[specialty]) {
          const label = specialty === matched ? `★ ${specialty} — ${text}` : `${specialty} — ${text}`;
          prescriptionSelect.appendChild(el("option", { value: text }, label));
        }
      }
      prescriptionSelect.value = selected;
    }
    fillPrescriptionOptions();
    symptoms.addEventListener("input", fillPrescriptionOptions);
    prescriptionSelect.addEventListener("change", () => {
      if (prescriptionSelect.value) prescription.value = prescriptionSelect.value;
    });

    const form = el("form", { class: "card" }, [
      banner,
      el("div", { class: "field" }, [el("label", {}, "Symptoms"), symptoms]),
      el("div", { class: "field" }, [el("label", {}, "Diagnosis"), diagnosis]),
      el("div", { class: "field" }, [
        el("label", {}, "Sample Prescription"),
        prescriptionSelect,
        el("p", { class: "muted", style: "font-size:11.5px;margin:4px 0 0;" },
          "POC data, ★ = matches the symptoms above — pick one to fill Prescription below, or write your own."),
      ]),
      el("div", { class: "field" }, [el("label", {}, "Prescription"), prescription]),
      el("div", { class: "field" }, [el("label", {}, "Follow-up Notes"), followUp]),
      submitBtn,
    ]);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      banner.classList.add("hidden");
      submitBtn.disabled = true;
      try {
        await api.post(`/referral/requests/${referral.id}/outcome`, {
          symptoms: symptoms.value || null,
          diagnosis: diagnosis.value || null,
          prescription: prescription.value || null,
          follow_up_notes: followUp.value || null,
        });
        toast("Outcome recorded.", "success");
        await loadReferral();
        await renderOutcomeTab();
      } catch (err) {
        banner.textContent = err.message || "Failed to record outcome.";
        banner.classList.remove("hidden");
        submitBtn.disabled = false;
      }
    });
    return form;
  }

  async function renderOutcomeTab() {
    panelHost.innerHTML = "";
    try {
      const outcome = await api.get(`/referral/requests/${referral.id}/outcome`);
      panelHost.appendChild(
        el("div", { class: "grid-2" }, [
          infoBlock("Symptoms", outcome.symptoms || "—"),
          infoBlock("Diagnosis", outcome.diagnosis || "—"),
          infoBlock("Prescription", outcome.prescription || "—"),
          infoBlock("Follow-up Notes", outcome.follow_up_notes || "—"),
        ])
      );
      const summaryCard = el("div", { class: "card" }, [el("h3", {}, "Care Journey Summary")]);
      if (outcome.interaction_summary) {
        summaryCard.appendChild(el("p", {}, outcome.interaction_summary));
      } else {
        summaryCard.appendChild(el("p", { class: "muted" }, "Summary is being generated — refresh in a moment."));
        const refreshBtn = el("button", { class: "btn-secondary btn-sm" }, "Refresh");
        refreshBtn.addEventListener("click", renderOutcomeTab);
        summaryCard.appendChild(refreshBtn);
      }
      panelHost.appendChild(summaryCard);
    } catch (err) {
      if (err.status === 404) {
        panelHost.appendChild(el("div", { class: "banner banner-info" }, "No outcome recorded yet."));
        if (hasPermission("referral:record_outcome")) panelHost.appendChild(buildOutcomeForm());
      } else {
        panelHost.appendChild(el("div", { class: "banner banner-error" }, err.message || "Failed to load outcome."));
      }
    }
  }

  function renderLiveIndicator(statusKey) {
    liveStatusHost.innerHTML = "";
    const dotClass = statusKey === "live" ? "live-dot live" : statusKey === "retry" || statusKey === "connecting" ? "live-dot retry" : "live-dot";
    const text = { live: "Live", connecting: "Connecting…", retry: "Reconnecting…", closed: "Disconnected" }[statusKey] || statusKey;
    liveStatusHost.appendChild(el("span", { class: dotClass }));
    liveStatusHost.appendChild(el("span", {}, ` ${text}`));
  }

  const stream = streamReferralEvents(
    referralId,
    async (message) => {
      const time = new Date().toLocaleTimeString();
      liveLog.insertBefore(el("div", {}, `${time} — ${typeof message === "string" ? message : JSON.stringify(message)}`), liveLog.firstChild);
      while (liveLog.children.length > 20) liveLog.removeChild(liveLog.lastChild);
      await loadReferral();
      if (activeTab === "workflow") await renderPanel();
    },
    renderLiveIndicator
  );

  await loadReferral();
  renderTabs();
  await renderPanel();

  return () => stream.close();
}
