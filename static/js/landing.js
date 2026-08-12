// Where each role lands right after login (or on `/`, or on an unmatched
// route) instead of the old one-size-fits-all redirect to Patients.
//
// Precedence mirrors app/agents/assistant_graph.py's role resolution
// (care_coordinator > specialist > pcp > patient) so "which role am I, for
// UI purposes" answers the same way across the assistant and the dashboard
// shell. admin/payer_admin are slotted in ahead of/after that core clinical
// chain since the assistant graph never needs to rank them.
const LANDING_BY_ROLE = {
  admin: "/admin",
  care_coordinator: "/ops-queue",
  payer_admin: "/analytics",
  specialist: "/my-day",
  pcp: "/my-day",
  patient: "/home",
};

const LANDING_PRECEDENCE = ["admin", "care_coordinator", "payer_admin", "specialist", "pcp", "patient"];

export function resolveLandingRoute(roles = []) {
  const held = new Set(roles);
  for (const role of LANDING_PRECEDENCE) {
    if (held.has(role)) return LANDING_BY_ROLE[role];
  }
  return "/patients"; // no recognized role yet (e.g. freshly registered, unlinked staff account)
}

// Same precedence, collapsed to the three broad "which space are you in"
// categories the UI gives a distinct accent color — admin/payer_admin/
// unroled accounts get no accent override (the default chrome).
const ACCENT_BY_ROLE = {
  care_coordinator: "coordinator",
  specialist: "provider",
  pcp: "provider",
  patient: "patient",
};

export function resolveRoleAccent(roles = []) {
  const held = new Set(roles);
  for (const role of LANDING_PRECEDENCE) {
    if (held.has(role) && ACCENT_BY_ROLE[role]) return ACCENT_BY_ROLE[role];
  }
  return null;
}
