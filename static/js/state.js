const listeners = new Set();

const state = {
  accessToken: localStorage.getItem("access_token") || null,
  refreshToken: localStorage.getItem("refresh_token") || null,
  me: null, // { id, username, email, roles: [], permissions: Set }
};

export function getState() {
  return state;
}

export function setTokens(access, refresh) {
  state.accessToken = access;
  state.refreshToken = refresh;
  localStorage.setItem("access_token", access);
  localStorage.setItem("refresh_token", refresh);
  notify();
}

export function clearTokens() {
  state.accessToken = null;
  state.refreshToken = null;
  state.me = null;
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
  notify();
}

export function setMe(me) {
  state.me = { ...me, permissions: new Set(me.permissions) };
  notify();
}

export function hasPermission(name) {
  if (!state.me) return false;
  return state.me.permissions.has(name) || state.me.permissions.has("admin:*");
}

export function isAdmin() {
  return hasPermission("admin:*");
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function notify() {
  listeners.forEach((fn) => fn(state));
}
