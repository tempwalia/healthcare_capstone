import { api } from "../api.js";
import { setTokens, setMe, clearTokens, getState } from "../state.js";
import { navigate } from "../router.js";
import { toast } from "../components/toast.js";
import { el } from "../utils.js";
import { resolveLandingRoute } from "../landing.js";

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
        const me = await fetchMe();
        navigate(resolveLandingRoute(me.roles));
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

    // Patients can complete their own account in one step (auto-linked
    // Patient record + patient role, granted server-side). Staff roles stay
    // admin-provisioned — that's a credentialing decision, not a preference,
    // so this branch collects nothing extra and changes no backend behavior.
    const patientRadio = el("input", { type: "radio", name: "account-kind", value: "patient", checked: "checked" });
    const staffRadio = el("input", { type: "radio", name: "account-kind", value: "staff" });
    const kindChoice = el("div", { class: "radio-row" }, [
      el("label", { class: "radio-option" }, [patientRadio, " I am a patient"]),
      el("label", { class: "radio-option" }, [staffRadio, " I am hospital staff"]),
    ]);

    const firstName = el("input", { type: "text", name: "first_name" });
    const lastName = el("input", { type: "text", name: "last_name" });
    const dob = el("input", { type: "date", name: "date_of_birth" });
    const gender = el("select", { name: "gender" }, [
      el("option", { value: "" }, "Select…"),
      el("option", { value: "male" }, "Male"),
      el("option", { value: "female" }, "Female"),
      el("option", { value: "other" }, "Other"),
    ]);
    // Optional at registration — a blank insurance pair still gets a random
    // demo policy server-side (POST /auth/register), so referral eligibility
    // checks work either way; phone/allergies stay genuinely blank if
    // skipped. "Fill Sample Data" below populates all of these (and the
    // core fields above) from GET /auth/sample-patient-data — purely
    // synthetic, and still editable before submitting.
    const phone = el("input", { type: "text", name: "phone" });
    const insuranceProvider = el("input", { type: "text", name: "insurance_provider" });
    const insurancePolicyNumber = el("input", {
      type: "text", name: "insurance_policy_number",
    });
    const allergies = el("input", { type: "text", name: "allergies", placeholder: "e.g. Penicillin, or leave blank" });
    const fillSampleBtn = el("button", { type: "button", class: "btn-ghost btn-sm" }, "🎲 Fill Sample Data");
    fillSampleBtn.addEventListener("click", async () => {
      fillSampleBtn.disabled = true;
      try {
        const sample = await api.get("/auth/sample-patient-data");
        firstName.value = sample.first_name;
        lastName.value = sample.last_name;
        dob.value = sample.date_of_birth;
        gender.value = sample.gender;
        phone.value = sample.phone;
        insuranceProvider.value = sample.insurance_provider;
        insurancePolicyNumber.value = sample.insurance_policy_number;
        allergies.value = sample.allergies;
        if (!email.value) email.value = sample.email;
      } catch (err) {
        toast(err.message || "Couldn't load sample data.", "error");
      } finally {
        fillSampleBtn.disabled = false;
      }
    });
    const patientFields = el("div", { class: "patient-fields" }, [
      fillSampleBtn,
      el("div", { class: "field" }, [el("label", {}, "First name"), firstName]),
      el("div", { class: "field" }, [el("label", {}, "Last name"), lastName]),
      el("div", { class: "field" }, [el("label", {}, "Date of birth"), dob]),
      el("div", { class: "field" }, [el("label", {}, "Gender"), gender]),
      el("div", { class: "field" }, [el("label", {}, "Phone (optional)"), phone]),
      el("div", { class: "field" }, [el("label", {}, "Insurance provider (optional)"), insuranceProvider]),
      el("div", { class: "field" }, [
        el("label", {}, "Policy number (optional)"), insurancePolicyNumber,
        el("div", { class: "muted", style: "font-size:11px;margin-top:2px;" },
          "Left blank, we'll assign a demo policy at random so referral eligibility checks have something real to verify against."),
      ]),
      el("div", { class: "field" }, [el("label", {}, "Allergies (optional)"), allergies]),
    ]);
    const staffNote = el(
      "p",
      { class: "muted hidden" },
      "An admin needs to grant your role and link your account from the Admin panel before you can do much."
    );

    function syncAccountKind() {
      const isPatient = patientRadio.checked;
      patientFields.classList.toggle("hidden", !isPatient);
      staffNote.classList.toggle("hidden", isPatient);
      for (const field of [firstName, lastName, dob, gender]) {
        if (isPatient) field.setAttribute("required", "required");
        else field.removeAttribute("required");
      }
    }
    patientRadio.addEventListener("change", syncAccountKind);
    staffRadio.addEventListener("change", syncAccountKind);

    const submitBtn = el("button", { type: "submit", class: "btn-primary" }, "Register");
    submitBtn.style.width = "100%";

    const form = el("form", {}, [
      banner,
      el("div", { class: "field" }, [el("label", {}, "Email"), email]),
      el("div", { class: "field" }, [el("label", {}, "Username"), username]),
      el("div", { class: "field" }, [el("label", {}, "Password"), password]),
      kindChoice,
      patientFields,
      staffNote,
      submitBtn,
    ]);
    syncAccountKind();

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      banner.classList.add("hidden");
      submitBtn.disabled = true;
      try {
        const isPatient = patientRadio.checked;
        const payload = { email: email.value, username: username.value, password: password.value };
        if (isPatient) {
          Object.assign(payload, {
            register_as_patient: true,
            first_name: firstName.value,
            last_name: lastName.value,
            date_of_birth: dob.value,
            gender: gender.value,
            phone: phone.value || null,
            insurance_provider: insuranceProvider.value || null,
            insurance_policy_number: insurancePolicyNumber.value || null,
            allergies: allergies.value || null,
          });
        }
        await api.post("/auth/register", payload);
        toast(isPatient ? "Account created — log in to see your care." : "Account created — log in below.", "success");
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
