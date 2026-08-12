import { api } from "../api.js";
import { openModal } from "../components/modal.js";
import { toast } from "../components/toast.js";
import { createResourceModule } from "../resource.js";
import { navigate } from "../router.js";
import { hasPermission } from "../state.js";
import { el, formatDateTime, appointmentStatusBadgeClass, capitalize, APPOINTMENT_STATUSES } from "../utils.js";

const NON_RESCHEDULABLE_STATUSES = ["completed", "cancelled", "no_show"];

// Patients hold appointment:view_own but not appointment:manage, so the
// generic edit/delete actions below never render for them — every row a
// patient session sees is already their own appointment (that's what
// appointment:view_own scopes to), so no extra ownership check is needed
// here, just the permission split itself. Exported so the home dashboard's
// upcoming-appointment card and the Scheduling page's "My Upcoming
// Appointments" section can reuse the exact same reschedule/cancel
// controls instead of re-implementing them a second and third time.
export function selfServiceActions(row, reload) {
  if (hasPermission("appointment:manage") || !hasPermission("appointment:view_own")) return [];
  if (NON_RESCHEDULABLE_STATUSES.includes(row.status)) return [];

  const buttons = [];

  const rescheduleBtn = el("button", { class: "btn-ghost btn-sm btn-icon", title: "Reschedule" }, "🕓");
  rescheduleBtn.addEventListener("click", () => {
    openModal({
      title: `Reschedule Appointment #${row.id}`,
      submitLabel: "Save",
      fields: [{ name: "appointment_datetime", label: "New Date & Time", type: "datetime", required: true }],
      initial: row,
      onSubmit: async (payload) => {
        await api.put(`/appointments/${row.id}`, { appointment_datetime: payload.appointment_datetime });
        toast("Appointment rescheduled.", "success");
        await reload();
      },
    });
  });
  buttons.push(rescheduleBtn);

  const cancelBtn = el("button", { class: "btn-ghost btn-sm btn-icon", title: "Cancel appointment" }, "✕");
  cancelBtn.addEventListener("click", async () => {
    if (!confirm("Cancel this appointment?")) return;
    try {
      await api.put(`/appointments/${row.id}`, { status: "cancelled" });
      toast("Appointment cancelled.", "success");
      await reload();
    } catch (err) {
      toast(err.message || "Cancel failed.", "error");
    }
  });
  buttons.push(cancelBtn);

  return buttons;
}

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
  extraActions: selfServiceActions,
  onRowClick: (row) => navigate(`/appointments/${row.id}`),
});
