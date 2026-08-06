import { api } from "../api.js";
import { setTokens, setMe, clearTokens, getState } from "../state.js";
import { navigate } from "../router.js";
import { toast } from "../components/toast.js";
import { el } from "../utils.js";

export async function fetchMe() {
  const me = await api.get("/auth/me");
  setMe(me);
  return me;
}

/** Called once at page load if a token is already stored, so a refresh
 * doesn't force a re-login. */
export async function bootstrapSession() {
  const { accessToken } = getState();
  if (!accessToken) return false;
  try {
    await fetchMe();
    return true;
  } catch {
    clearTokens();
    return false;
  }
}

export async function logout() {
  clearTokens();
  navigate("/login");
}

function checkHealth(dot, label) {
  fetch("/health/ready")
    .then((res) =>
      res.json().then(() => {
        dot.className = `health-dot ${res.ok ? "ok" : "down"}`;
        label.textContent = res.ok ? "API online" : "API degraded";
      })
    )
    .catch(() => {
      dot.className = "health-dot down";
      label.textContent = "API unreachable";
    });
}

export function render(container, { mode = "login" } = {}) {
  container.innerHTML = "";

  const loginTab = el("button", { type: "button" }, "Log in");
  const registerTab = el("button", { type: "button" }, "Register");
  const tabs = el("div", { class: "auth-tabs" }, [loginTab, registerTab]);
  const formHost = el("div", {});

  const dot = el("span", { class: "health-dot" });
  const label = el("span", { class: "muted" }, " checking API…");
  const healthRow = el("div", { class: "auth-footer-health" }, [
    el("span", { class: "live-indicator" }, [dot, label]),
  ]);

  container.appendChild(
    el("div", { class: "auth-card" }, [
      el("div", { class: "brand" }, [
        el("span", { class: "brand-mark" }, "CC"),
        el("span", { class: "brand-name" }, "Care Coordination"),
      ]),
      tabs,
      formHost,
      healthRow,
    ])
  );
  checkHealth(dot, label);

  function showLogin() {
    loginTab.classList.add("active");
    registerTab.classList.remove("active");
    formHost.innerHTML = "";
    formHost.appendChild(buildLoginForm());
  }
  function showRegister() {
    registerTab.classList.add("active");
    loginTab.classList.remove("active");
    formHost.innerHTML = "";
    formHost.appendChild(buildRegisterForm());
  }
  loginTab.addEventListener("click", showLogin);
  registerTab.addEventListener("click", showRegister);

  function buildLoginForm() {
    const banner = el("div", { class: "form-banner hidden" });
    const username = el("input", { type: "text", name: "username", autocomplete: "username", required: "required" });
    const password = el("input", {
      type: "password", name: "password", autocomplete: "current-password", required: "required",
    });
    const submitBtn = el("button", { type: "submit", class: "btn-primary" }, "Log in");
    submitBtn.style.width = "100%";

    const form = el("form", {}, [
      banner,
      el("div", { class: "field" }, [el("label", {}, "Username"), username]),
      el("div", { class: "field" }, [el("label", {}, "Password"), password]),
      submitBtn,
    ]);

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      banner.classList.add("hidden");
      submitBtn.disabled = true;
      try {
        const body = new URLSearchParams({ username: username.value, password: password.value });
        const token = await api.postForm("/auth/login", body);
        setTokens(token.access_token, token.refresh_token);
        await fetchMe();
        navigate("/patients");
      } catch (err) {
        banner.textContent = err.message || "Login failed.";
        banner.classList.remove("hidden");
      } finally {
        submitBtn.disabled = false;
      }
    });
    return form;
  }

  function buildRegisterForm() {
    const banner = el("div", { class: "form-banner hidden" });
    const email = el("input", { type: "email", name: "email", required: "required" });
    const username = el("input", { type: "text", name: "username", required: "required" });
    const password = el("input", { type: "password", name: "password", required: "required", minlength: "8" });
    const submitBtn = el("button", { type: "submit", class: "btn-primary" }, "Register");
    submitBtn.style.width = "100%";

    const form = el("form", {}, [
      banner,
      el("div", { class: "field" }, [el("label", {}, "Email"), email]),
      el("div", { class: "field" }, [el("label", {}, "Username"), username]),
      el("div", { class: "field" }, [el("label", {}, "Password"), password]),
      el("p", { class: "muted" }, "New accounts have no role yet — an admin needs to grant one from the Admin panel before you can do much."),
      submitBtn,
    ]);

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      banner.classList.add("hidden");
      submitBtn.disabled = true;
      try {
        await api.post("/auth/register", { email: email.value, username: username.value, password: password.value });
        toast("Account created — log in below.", "success");
        showLogin();
      } catch (err) {
        banner.textContent = err.message || "Registration failed.";
        banner.classList.remove("hidden");
      } finally {
        submitBtn.disabled = false;
      }
    });
    return form;
  }

  if (mode === "register") showRegister();
  else showLogin();
}
