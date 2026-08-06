import { createResourceModule } from "../resource.js";
import { formatDateTime, appointmentStatusBadgeClass, capitalize, APPOINTMENT_STATUSES } from "../utils.js";

const baseFields = [
  {
    name: "patient_id", label: "Patient", type: "select-async", source: "/patients", required: true,
    optionLabel: (p) => `${p.first_name} ${p.last_name} (#${p.id})`,
  },
  {
    name: "doctor_id", label: "Doctor", type: "select-async", source: "/doctors", required: true,
    optionLabel: (d) => `${d.first_name} ${d.last_name} — ${d.specialization} (#${d.id})`,
  },
  { name: "appointment_datetime", label: "Date & Time", type: "datetime", required: true },
  { name: "duration_minutes", label: "Duration (minutes)", type: "number" },
  { name: "reason", label: "Reason", type: "text" },
  { name: "notes", label: "Notes", type: "textarea" },
  { name: "appointment_type", label: "Type", type: "select", options: ["in_person", "telehealth", "phone"], numeric: false },
  { name: "location", label: "Location", type: "text" },
  { name: "reminder_sent", label: "Reminder Sent", type: "checkbox" },
  { name: "follow_up_required", label: "Follow-up Required", type: "checkbox" },
];

export default createResourceModule({
  key: "appointments",
  title: "Appointments",
  singular: "Appointment",
  basePath: "/appointments",
  permissions: { create: "appointment:manage", update: "appointment:manage", delete: "appointment:manage" },
  columns: [
    { key: "id", label: "ID" },
    { key: "patient_id", label: "Patient", format: (r) => `#${r.patient_id}` },
    { key: "doctor_id", label: "Doctor", format: (r) => `#${r.doctor_id}` },
    { key: "appointment_datetime", label: "When", format: (r) => formatDateTime(r.appointment_datetime) },
    {
      key: "status", label: "Status", html: true,
      format: (r) => `<span class="${appointmentStatusBadgeClass(r.status)}">${capitalize(r.status)}</span>`,
    },
    { key: "appointment_type", label: "Type", format: (r) => capitalize(r.appointment_type) },
  ],
  fields: baseFields,
  editFields: [
    ...baseFields,
    { name: "status", label: "Status", type: "select", options: APPOINTMENT_STATUSES, numeric: false },
  ],
});
