import { getState, subscribe, hasPermission } from "./state.js";
import { createRouter, navigate } from "./router.js";
import { el } from "./utils.js";
import * as authModule from "./modules/auth.js";
import patientsModule from "./modules/patients.js";
import doctorsModule from "./modules/doctors.js";
import appointmentsModule from "./modules/appointments.js";
import medicalRecordsModule from "./modules/medical_records.js";
import * as referralsModule from "./modules/referrals.js";
import * as scheduleModule from "./modules/schedule.js";
import * as analyticsModule from "./modules/analytics.js";
import * as auditModule from "./modules/audit.js";
import * as assistantModule from "./modules/assistant.js";
import * as adminModule from "./modules/admin.js";

const NAV_ITEMS = [
  { path: "/patients", label: "Patients", icon: "🧑" },
  { path: "/doctors", label: "Doctors", icon: "🩺" },
  { path: "/appointments", label: "Appointments", icon: "📅" },
  { path: "/medical-records", label: "Medical Records", icon: "📋" },
  { path: "/referrals", label: "Referrals", icon: "🔁" },
  { path: "/schedule", label: "Scheduling", icon: "🗓" },
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
  for (const item of NAV_ITEMS) {
    if (item.permission && !hasPermission(item.permission)) continue;
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

  userCard.appendChild(
    el("div", { class: "who" }, [
      el("span", { class: "avatar" }, initials),
      el("div", {}, [
        el("div", { class: "name" }, me.username),
        el("div", { class: "roles" }, me.roles.length ? me.roles.join(", ") : "no role assigned yet"),
      ]),
    ])
  );
  userCard.appendChild(logoutBtn);
}

function setTopbarTitle(title) {
  topbar.innerHTML = "";
  topbar.appendChild(el("h1", {}, title));
}

subscribe(() => {
  renderNav();
  renderUserCard();
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

const router = createRouter();
router.add("/login", authRoute("login"));
router.add("/register", authRoute("register"));
router.add("/", guarded("Patients", patientsModule.render));
router.add("/patients", guarded("Patients", patientsModule.render));
router.add("/doctors", guarded("Doctors", doctorsModule.render));
router.add("/appointments", guarded("Appointments", appointmentsModule.render));
router.add("/medical-records", guarded("Medical Records", medicalRecordsModule.render));
router.add("/referrals", guarded("Referrals", referralsModule.renderList));
router.add("/referrals/:id", guarded("Referral Detail", referralsModule.renderDetail));
router.add("/schedule", guarded("Scheduling", scheduleModule.render));
router.add("/analytics", guarded("Analytics", analyticsModule.render));
router.add("/audit", guarded("Audit Log", auditModule.render));
router.add("/assistant", guarded("Assistant", assistantModule.render));
router.add("/admin", guarded("Admin", adminModule.render));
router.notFound(() => navigate("/patients"));

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
