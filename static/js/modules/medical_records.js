import { createResourceModule } from "../resource.js";
import { formatDateTime } from "../utils.js";

const baseFields = [
  {
    name: "patient_id", label: "Patient", type: "select-async", source: "/patients", required: true,
    optionLabel: (p) => `${p.first_name} ${p.last_name} (#${p.id})`,
  },
  {
    name: "doctor_id", label: "Doctor", type: "select-async", source: "/doctors", required: true,
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
    { key: "doctor_id", label: "Doctor", format: (r) => `#${r.doctor_id}` },
    { key: "visit_date", label: "Visit Date", format: (r) => formatDateTime(r.visit_date) },
    { key: "diagnosis", label: "Diagnosis" },
    { key: "treatment", label: "Treatment" },
  ],
  fields: baseFields,
});
