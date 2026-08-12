import { toast } from "./toast.js";
import { el } from "../utils.js";

// POC data: a handful of canned prescriptions per specialty so a doctor
// completing a consult (referral or direct-booked appointment) can pick a
// plausible one instead of typing from scratch — there are no real
// prescribing doctors behind this demo, so nothing here should be read as
// real clinical guidance. Shared by the Referral Detail page's Outcome tab
// and the Appointment Detail page's Consultation section — one flow, two
// entry points, not two implementations (a direct-booked appointment
// previously had no consultation-completion mechanism at all).
export const SAMPLE_PRESCRIPTIONS_BY_SPECIALTY = {
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

const PRESCRIPTION_SPECIALTY_KEYWORDS = {
  Orthopedics: ["back", "spine", "joint", "knee", "hip", "shoulder", "fracture", "orthopedic"],
  Cardiology: ["heart", "cardiac", "chest pain", "palpitation", "cardio"],
  Dermatology: ["skin", "rash", "derma", "mole", "eczema"],
};

export function inferPrescriptionSpecialty(text) {
  const lower = (text || "").toLowerCase();
  for (const [specialty, keywords] of Object.entries(PRESCRIPTION_SPECIALTY_KEYWORDS)) {
    if (keywords.some((k) => lower.includes(k))) return specialty;
  }
  return null;
}

// Backs the "⚡ Complete with Defaults" one-click action. Every field is
// still POC-templated text, not a real clinical judgment; it exists so a
// demo/POC completion doesn't require typing four fields by hand.
export function buildDefaultOutcome(reasonText) {
  const reason = (reasonText || "").trim();
  const specialty = inferPrescriptionSpecialty(reason) || "General";
  const prescriptions = SAMPLE_PRESCRIPTIONS_BY_SPECIALTY[specialty] || SAMPLE_PRESCRIPTIONS_BY_SPECIALTY.General;
  return {
    symptoms: reason || "Reported symptoms as described.",
    diagnosis: `Consult completed for: ${reason || "the reported concern"}. Findings reviewed and documented; ` +
      "no acute concerns identified beyond the reported issue.",
    prescription: prescriptions[0],
    follow_up_notes: "Patient advised on next steps and self-care; return or follow up sooner if symptoms persist or worsen.",
  };
}

function infoBlock(label, value) {
  return el("div", {}, [
    el("div", { class: "muted", style: "font-size:11.5px;margin-bottom:2px;" }, label),
    el("div", {}, value),
  ]);
}

function buildOutcomeForm({ postOutcome, onRecorded, failLabel }) {
  const banner = el("div", { class: "form-banner hidden" });
  const symptoms = el("textarea", { name: "symptoms" });
  const diagnosis = el("textarea", { name: "diagnosis" });
  const prescription = el("textarea", { name: "prescription" });
  const followUp = el("textarea", { name: "follow_up_notes" });
  const submitBtn = el("button", { type: "submit", class: "btn-primary" }, "Record Outcome");

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
      await postOutcome({
        symptoms: symptoms.value || null,
        diagnosis: diagnosis.value || null,
        prescription: prescription.value || null,
        follow_up_notes: followUp.value || null,
      });
      toast("Outcome recorded.", "success");
      await onRecorded();
    } catch (err) {
      banner.textContent = err.message || failLabel;
      banner.classList.remove("hidden");
      submitBtn.disabled = false;
    }
  });
  return form;
}

/**
 * Renders the full consultation/outcome section — recorded-outcome display
 * with generated summary, or (if none recorded yet and the caller can
 * record one) a "Complete with Defaults" button plus the full manual form.
 * Used identically by the Referral Detail page's Outcome tab and the
 * Appointment Detail page's Consultation section.
 *
 * @param container - element to render into (its content is replaced)
 * @param getOutcome - async () => outcome row; must throw an error with
 *   `.status === 404` when none exists yet (matches api.js's error shape)
 * @param postOutcome - async (payload) => outcome row
 * @param canRecord - whether the caller holds referral:record_outcome
 *   *and* passes the relevant ownership scoping (checked server-side either
 *   way — this only controls whether the form renders)
 * @param reasonText - the referral's or appointment's `reason` field, used
 *   to seed the "Complete with Defaults" template text
 * @param onRecorded - async () => void, called after a successful record
 *   (e.g. to refresh the parent page's status badge)
 */
export async function renderConsultationSection(container, { getOutcome, postOutcome, canRecord, reasonText, onRecorded }) {
  container.innerHTML = "";

  async function rerender() {
    await renderConsultationSection(container, { getOutcome, postOutcome, canRecord, reasonText, onRecorded });
  }

  try {
    const outcome = await getOutcome();
    container.appendChild(
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
      refreshBtn.addEventListener("click", rerender);
      summaryCard.appendChild(refreshBtn);
    }
    container.appendChild(summaryCard);
  } catch (err) {
    if (err.status === 404) {
      container.appendChild(el("div", { class: "banner banner-info" }, "No outcome recorded yet."));
      if (canRecord) {
        const quickBtn = el("button", { class: "btn-primary btn-sm", style: "margin-bottom:12px;" }, "⚡ Complete with Defaults");
        quickBtn.addEventListener("click", async () => {
          quickBtn.disabled = true;
          try {
            await postOutcome(buildDefaultOutcome(reasonText));
            toast("Completed with default outcome — summary generating…", "success");
            await onRecorded();
            await rerender();
          } catch (err2) {
            toast(err2.message || "Failed to complete.", "error");
            quickBtn.disabled = false;
          }
        });
        container.appendChild(
          el("p", { class: "muted", style: "font-size:12px;" },
            "One click, using templated POC text based on the reason given — or fill in the real details below.")
        );
        container.appendChild(quickBtn);
        container.appendChild(buildOutcomeForm({
          postOutcome, failLabel: "Failed to record outcome.",
          onRecorded: async () => { await onRecorded(); await rerender(); },
        }));
      }
    } else {
      container.appendChild(el("div", { class: "banner banner-error" }, err.message || "Failed to load outcome."));
    }
  }
}
