import { api } from "../api.js";
import { createResourceModule } from "../resource.js";
import { openModal } from "../components/modal.js";
import { toast } from "../components/toast.js";
import { hasPermission } from "../state.js";
import { el, formatDateTime } from "../utils.js";

const baseFields = [
  {
    name: "patient_id", label: "Patient", type: "select-async", source: "/patients", required: true,
    optionLabel: (p) => `${p.first_name} ${p.last_name} (#${p.id})`,
  },
  {
    name: "doctor_id", label: "Doctor (optional)", type: "select-async", source: "/doctors",
    optionLabel: (d) => `${d.first_name} ${d.last_name} (#${d.id})`,
  },
  { name: "visit_date", label: "Visit Date & Time", type: "datetime", required: true },
  { name: "diagnosis", label: "Diagnosis", type: "text" },
  { name: "symptoms", label: "Symptoms", type: "textarea" },
  { name: "treatment", label: "Treatment", type: "textarea" },
  { name: "prescription", label: "Prescription", type: "textarea" },
  { name: "notes", label: "Notes", type: "textarea" },
  { name: "blood_pressure_systolic", label: "BP Systolic", type: "number" },
  { name: "blood_pressure_diastolic", label: "BP Diastolic", type: "number" },
  { name: "heart_rate", label: "Heart Rate", type: "number" },
  { name: "temperature", label: "Temperature", type: "number" },
  { name: "weight", label: "Weight", type: "number" },
  { name: "height", label: "Height", type: "number" },
  { name: "record_type", label: "Record Type", type: "text" },
];

// A patient's own "upload a document straight into my medical records" —
// standalone (not tied to any referral/appointment), same
// POST /medical-records/quick-upload the unified New Request flow's inline
// upload button uses. Creates a new, doctor-less record with the file
// attached in one call.
function extraToolbarButtons({ reload }) {
  if (!hasPermission("medical_record:manage")) return [];
  const isSelfServicePatient = hasPermission("patient:view_own") && !hasPermission("patient:view_all");

  const btn = el("button", { class: "btn-secondary" }, "Upload Document");
  btn.addEventListener("click", async () => {
    let patientField;
    if (isSelfServicePatient) {
      let own = null;
      try {
        own = ((await api.get("/patients/?limit=1")).items || [])[0] || null;
      } catch {
        toast("Couldn't load your patient record.", "error");
        return;
      }
      if (!own) {
        toast("Your account isn't linked to a patient record yet.", "error");
        return;
      }
      patientField = {
        name: "patient_id", label: "Patient", type: "select", required: true, disabled: true,
        options: [{ value: own.id, label: `${own.first_name} ${own.last_name} (You)` }],
      };
    } else {
      patientField = {
        name: "patient_id", label: "Patient", type: "select-async", source: "/patients", required: true,
        optionLabel: (p) => `${p.first_name} ${p.last_name} (#${p.id})`,
      };
    }

    openModal({
      title: "Upload Document",
      submitLabel: "Upload",
      fields: [
        patientField,
        { name: "file", label: "Document", type: "file", required: true },
        { name: "record_type", label: "Record Type (optional)", type: "text" },
        { name: "notes", label: "Notes (optional)", type: "textarea" },
      ],
      initial: isSelfServicePatient ? { patient_id: patientField.options[0].value } : {},
      onSubmit: async (payload) => {
        const formData = new FormData();
        formData.append("file", payload.file);
        formData.append("patient_id", String(payload.patient_id));
        if (payload.record_type) formData.append("record_type", payload.record_type);
        if (payload.notes) formData.append("notes", payload.notes);
        await api.upload("/medical-records/quick-upload", formData);
        toast("Document uploaded.", "success");
        await reload();
      },
    });
  });
  return [btn];
}

export default createResourceModule({
  key: "medical_records",
  title: "Medical Records",
  singular: "Medical Record",
  basePath: "/medical-records",
  permissions: { create: "medical_record:manage", update: "medical_record:manage", delete: "medical_record:manage" },
  searchableFields: ["diagnosis", "treatment", "record_type"],
  columns: [
    { key: "id", label: "ID" },
    { key: "patient_id", label: "Patient", format: (r) => `#${r.patient_id}` },
    { key: "doctor_id", label: "Doctor", format: (r) => (r.doctor_id ? `#${r.doctor_id}` : "—") },
    { key: "visit_date", label: "Visit Date", format: (r) => formatDateTime(r.visit_date) },
    { key: "diagnosis", label: "Diagnosis" },
    { key: "treatment", label: "Treatment" },
  ],
  fields: baseFields,
  extraToolbarButtons,
});
