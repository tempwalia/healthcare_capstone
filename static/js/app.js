import { getState, subscribe, hasPermission } from "./state.js";
import { createRouter, navigate } from "./router.js";
import { el } from "./utils.js";
import { resolveLandingRoute, resolveRoleAccent } from "./landing.js";
import * as authModule from "./modules/auth.js";
import patientsModule from "./modules/patients.js";
import * as patientDetailModule from "./modules/patient_detail.js";
import doctorsModule from "./modules/doctors.js";
import * as appointmentDetailModule from "./modules/appointment_detail.js";
import medicalRecordsModule from "./modules/medical_records.js";
import * as referralsModule from "./modules/referrals.js";
import * as homeModule from "./modules/home.js";
import * as opsQueueModule from "./modules/ops_queue.js";
import * as myDayModule from "./modules/my_day.js";
import * as scheduleModule from "./modules/schedule.js";
import * as analyticsModule from "./modules/analytics.js";
import * as auditModule from "./modules/audit.js";
import * as assistantModule from "./modules/assistant.js";
import * as adminModule from "./modules/admin.js";
import { mountNotificationBell } from "./components/notifications.js";

const NAV_ITEMS = [
  { path: "/home", label: "Home", icon: "🏠", roles: ["patient"] },
  { path: "/ops-queue", label: "Ops Queue", icon: "📥", roles: ["care_coordinator"] },
  { path: "/my-day", label: "My Day", icon: "🗓️", roles: ["pcp", "specialist"] },
  { path: "/patients", label: "Patients", icon: "🧑" },
  { path: "/doctors", label: "Doctors", icon: "🩺" },
  { path: "/medical-records", label: "Medical Records", icon: "📋" },
  { path: "/referrals", label: "Referrals", icon: "🔁" },
  { path: "/schedule", label: "Scheduling & Appointments", icon: "🗓" },
  { path: "/analytics", label: "Analytics", icon: "📊", permission: "analytics:view" },
  { path: "/audit", label: "Audit Log", icon: "🧾", permission: "audit:view" },
  { path: "/assistant", label: "Assistant", icon: "💬" },
  { path: "/admin", label: "Admin", icon: "⚙", permission: "admin:*" },
];

const authScreen = document.getElementById("auth-screen");
const shell = document.getElementById("shell");
const view = document.getElementById("view");
const navHost = document.getElementById("nav");
const topbar = document.getElementById("topbar");
const userCard = document.getElementById("user-card");
const healthIndicator = document.getElementById("health-indicator");
const sidebarToggle = document.getElementById("sidebar-toggle");
const sidebarBackdrop = document.getElementById("sidebar-backdrop");

sidebarToggle.addEventListener("click", () => shell.classList.toggle("nav-open"));
sidebarBackdrop.addEventListener("click", () => shell.classList.remove("nav-open"));

function currentPath() {
  const raw = location.hash.slice(1).split("?")[0];
  return "/" + raw.replace(/^\/+|\/+$/g, "");
}

function pollHealth() {
  fetch("/health/ready")
    .then((res) => {
      healthIndicator.innerHTML = "";
      healthIndicator.appendChild(el("span", { class: `health-dot ${res.ok ? "ok" : "down"}` }));
      healthIndicator.appendChild(el("span", {}, res.ok ? " API online" : " API degraded"));
    })
    .catch(() => {
      healthIndicator.innerHTML = "";
      healthIndicator.appendChild(el("span", { class: "health-dot down" }));
      healthIndicator.appendChild(el("span", {}, " API unreachable"));
    });
}

function renderNav() {
  navHost.innerHTML = "";
  const path = currentPath();
  const { me } = getState();
  const myRoles = new Set(me ? me.roles : []);
  for (const item of NAV_ITEMS) {
    if (item.permission && !hasPermission(item.permission)) continue;
    if (item.roles && !item.roles.some((r) => myRoles.has(r))) continue;
    const active = path === item.path || path.startsWith(item.path + "/");
    const link = el("a", { class: `nav-item${active ? " active" : ""}`, href: `#${item.path}` }, [
      el("span", { class: "nav-icon" }, item.icon),
      el("span", {}, item.label),
    ]);
    link.addEventListener("click", () => shell.classList.remove("nav-open"));
    navHost.appendChild(link);
  }
}

function renderUserCard() {
  const { me } = getState();
  userCard.innerHTML = "";
  if (!me) return;
  const initials = me.username.slice(0, 2).toUpperCase();
  const logoutBtn = el("button", { class: "btn-secondary btn-sm" }, "Log out");
  logoutBtn.style.width = "100%";
  logoutBtn.addEventListener("click", authModule.logout);

  const rolesHost = me.roles.length
    ? el(
        "div",
        { class: "role-badges" },
        me.roles.map((r) => el("span", { class: `role-badge role-badge-${r}` }, r.replace(/_/g, " ")))
      )
    : el("div", { class: "roles" }, "no role assigned yet");

  userCard.appendChild(
    el("div", { class: "who" }, [
      el("span", { class: "avatar" }, initials),
      el("div", {}, [el("div", { class: "name" }, me.username), rolesHost]),
    ])
  );
  userCard.appendChild(logoutBtn);
}

function applyRoleAccent() {
  const { me } = getState();
  const accent = resolveRoleAccent(me ? me.roles : []);
  if (accent) document.body.dataset.roleAccent = accent;
  else delete document.body.dataset.roleAccent;
}

// Two persistent children so a route change (which only needs to update the
// title) doesn't wipe out the notification bell mounted alongside it.
const topbarTitleHost = el("div", {});
const topbarActionsHost = el("div", { class: "topbar-actions" });
topbar.appendChild(topbarTitleHost);
topbar.appendChild(topbarActionsHost);
const notificationBell = mountNotificationBell(topbarActionsHost);

function setTopbarTitle(title) {
  topbarTitleHost.innerHTML = "";
  topbarTitleHost.appendChild(el("h1", {}, title));
}

subscribe(() => {
  renderNav();
  renderUserCard();
  applyRoleAccent();
  // Same state-change pub/sub login already notifies through (setTokens/
  // setMe) — closes the gap where the bell would otherwise wait up to 30s
  // after login for its first authenticated poll.
  notificationBell.refresh();
});

function guarded(title, renderFn) {
  return async (params) => {
    const { accessToken } = getState();
    if (!accessToken) {
      navigate("/login");
      return;
    }
    authScreen.classList.add("hidden");
    shell.classList.remove("hidden");
    setTopbarTitle(title);
    // Restart the fade-in on every route change — every guarded route
    // passes through this one choke point, so this is the only place a
    // route-transition animation needs to be wired.
    view.classList.remove("view-enter");
    void view.offsetWidth; // force reflow so the class removal above actually registers before re-adding it
    view.classList.add("view-enter");
    return renderFn(view, params);
  };
}

function authRoute(mode) {
  return () => {
    shell.classList.add("hidden");
    authScreen.classList.remove("hidden");
    authModule.render(authScreen, { mode });
  };
}

function redirectToLanding() {
  const { accessToken, me } = getState();
  if (!accessToken) {
    navigate("/login");
    return;
  }
  // Same shell-reveal `guarded()` would do — avoids a one-frame flash of
  // the login screen while this redirect resolves to the real route.
  authScreen.classList.add("hidden");
  shell.classList.remove("hidden");
  navigate(resolveLandingRoute(me ? me.roles : []));
}

const router = createRouter();
router.add("/login", authRoute("login"));
router.add("/register", authRoute("register"));
router.add("/", redirectToLanding);
router.add("/patients", guarded("Patients", patientsModule.render));
router.add("/patients/:id", guarded("Patient", patientDetailModule.render));
router.add("/doctors", guarded("Doctors", doctorsModule.render));
// /appointments merged into /schedule (Scheduling & Appointments) — kept as
// its own route (not just a redirect) so existing #/appointments links
// (home.js's "My Appointments" CTA, bookmarks) still land on a real page.
router.add("/appointments", guarded("Scheduling & Appointments", scheduleModule.render));
router.add("/appointments/:id", guarded("Appointment Detail", appointmentDetailModule.render));
router.add("/medical-records", guarded("Medical Records", medicalRecordsModule.render));
router.add("/home", guarded("Home", homeModule.render));
router.add("/referrals", guarded("Referrals", referralsModule.renderList));
router.add("/referrals/:id", guarded("Referral Detail", referralsModule.renderDetail));
router.add("/ops-queue", guarded("Ops Queue", opsQueueModule.render));
router.add("/my-day", guarded("My Day", myDayModule.render));
router.add("/schedule", guarded("Scheduling & Appointments", scheduleModule.render));
router.add("/analytics", guarded("Analytics", analyticsModule.render));
router.add("/audit", guarded("Audit Log", auditModule.render));
router.add("/assistant", guarded("Assistant", assistantModule.render));
router.add("/admin", guarded("Admin", adminModule.render));
router.notFound(redirectToLanding);

async function boot() {
  pollHealth();
  setInterval(pollHealth, 30000);

  const { accessToken } = getState();
  if (accessToken) {
    await authModule.bootstrapSession();
  }
  renderNav();
  renderUserCard();
  router.start();
}

boot();
