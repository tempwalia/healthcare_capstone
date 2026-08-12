import { api } from "../api.js";
import { navigate } from "../router.js";
import { getState } from "../state.js";
import {
  el, formatDateTime, capitalize, skeletonBlock,
  appointmentStatusBadgeClass, referralStatusBadgeClass, REFERRAL_PROGRESS_INFO,
} from "../utils.js";
import { selfServiceActions } from "./appointments.js";

// The patient landing page (see static/js/landing.js) — last-modify feedback
// asked for a single dynamic "what's next" view instead of dropping straight
// into the Referrals list: the soonest upcoming appointment (editable right
// here, same reschedule/cancel controls the Appointments page uses — not a
// second implementation of them), a quick referral status glance, and CTAs
// into the rest of the portal.

const doctorNameCache = new Map();
async function resolveDoctorName(id) {
  if (doctorNameCache.has(id)) return doctorNameCache.get(id);
  const name = await api
    .get(`/doctors/${id}`)
    .then((d) => `${d.first_name} ${d.last_name} — ${d.specialization}`)
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

function ctaButton(label, path, icon) {
  const btn = el("button", { class: "btn-secondary", style: "text-align:left;" }, `${icon} ${label}`);
  btn.addEventListener("click", () => navigate(path));
  return btn;
}

export async function render(container) {
  container.innerHTML = "";
  const { me } = getState();

  container.appendChild(
    el("div", { class: "card" }, [
      el("h2", {}, `Welcome back${me && me.username ? ", " + me.username : ""}`),
      el("p", { class: "muted" }, "Here's what's happening with your care."),
    ])
  );

  const apptHost = el("div", {});
  container.appendChild(
    el("div", { class: "card" }, [
      el("div", { class: "card-header" }, [el("h2", {}, "Upcoming Appointment")]),
      apptHost,
    ])
  );

  const referralsHost = el("div", {});
  container.appendChild(
    el("div", { class: "card" }, [
      el("div", { class: "card-header" }, [
        el("h2", {}, "My Referrals"),
        el("a", { href: "#/referrals", style: "font-size:12.5px;" }, "View all →"),
      ]),
      referralsHost,
    ])
  );

  container.appendChild(
    el("div", { class: "card" }, [
      el("div", { class: "card-header" }, [el("h2", {}, "Quick Actions")]),
      el("div", { class: "grid-auto" }, [
        ctaButton("New Request", "/requests/new", "📝"),
        ctaButton("My Appointments", "/appointments", "📅"),
        ctaButton("My Medical Records", "/medical-records", "🗂️"),
        ctaButton("Ask the Assistant", "/assistant", "💬"),
      ]),
    ])
  );

  async function loadAppointment() {
    apptHost.innerHTML = "";
    apptHost.appendChild(skeletonBlock(2));
    try {
      const page = await api.get("/appointments/?upcoming_only=true&limit=1");
      await renderAppointmentCard((page.items || [])[0] || null);
    } catch (err) {
      apptHost.innerHTML = "";
      apptHost.appendChild(el("div", { class: "banner banner-error" }, err.message || "Failed to load appointments."));
    }
  }

  async function renderAppointmentCard(appt) {
    apptHost.innerHTML = "";
    if (!appt) {
      const link = el("a", { href: "#/schedule" }, "Book one on the Scheduling page →");
      apptHost.appendChild(el("div", { class: "empty-state" }, ["No upcoming appointments. ", link]));
      return;
    }
    const doctorName = await resolveDoctorName(appt.doctor_id);
    const detailGrid = el("div", { class: "grid-3", style: "margin-top:10px;cursor:pointer;" }, [
      infoBlock("When", formatDateTime(appt.appointment_datetime)),
      infoBlock("Doctor", doctorName),
      infoBlock("Reason", appt.reason || "—"),
    ]);
    detailGrid.addEventListener("click", () => navigate(`/appointments/${appt.id}`));
    apptHost.appendChild(
      el("div", {}, [
        el("span", { class: appointmentStatusBadgeClass(appt.status) }, capitalize(appt.status)),
        detailGrid,
        el("div", { class: "row-actions", style: "margin-top:10px;" }, [
          ...selfServiceActions(appt, loadAppointment),
          (() => {
            const btn = el("button", { class: "btn-secondary btn-sm" }, "View details →");
            btn.addEventListener("click", () => navigate(`/appointments/${appt.id}`));
            return btn;
          })(),
        ]),
        el("a", { href: "#/appointments", style: "font-size:12.5px;display:inline-block;margin-top:8px;" }, "View all appointments →"),
      ])
    );
  }

  async function loadReferrals() {
    referralsHost.innerHTML = "";
    referralsHost.appendChild(skeletonBlock(3));
    try {
      const page = await api.get("/referral/requests/?limit=5");
      renderReferrals(page.items || []);
    } catch (err) {
      referralsHost.innerHTML = "";
      referralsHost.appendChild(el("div", { class: "banner banner-error" }, err.message || "Failed to load referrals."));
    }
  }

  function renderReferrals(referrals) {
    referralsHost.innerHTML = "";
    if (!referrals.length) {
      const link = el("a", { href: "#/requests/new" }, "Request your first referral →");
      referralsHost.appendChild(el("div", { class: "empty-state" }, ["No referrals yet. ", link]));
      return;
    }
    for (const r of referrals) {
      const progress = REFERRAL_PROGRESS_INFO[r.status] || {};
      const row = el("div", { class: "card card-interactive", style: "margin-bottom:8px;cursor:pointer;" }, [
        el("div", { style: "display:flex;justify-content:space-between;align-items:center;gap:8px;" }, [
          el("span", { style: "font-weight:600;" }, `#${r.id} — ${r.reason || "Referral"}`),
          el("span", { class: referralStatusBadgeClass(r.status) }, capitalize(r.status)),
        ]),
        el("div", { class: "muted", style: "font-size:12px;margin-top:4px;" },
          `${progress.label || ""}${progress.waitingOn ? " · Waiting on: " + progress.waitingOn : ""}`),
        el("div", { class: "muted", style: "font-size:11px;margin-top:2px;" }, `Submitted ${formatDateTime(r.created_at)}`),
      ]);
      row.addEventListener("click", () => navigate(`/referrals/${r.id}`));
      referralsHost.appendChild(row);
    }
  }

  await Promise.all([loadAppointment(), loadReferrals()]);
}
