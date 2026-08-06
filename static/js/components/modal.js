import { api } from "../api.js";
import { el, fromDatetimeLocalValue, toDatetimeLocalValue } from "../utils.js";

function htmlInputType(type) {
  if (type === "email") return "email";
  if (type === "password") return "password";
  if (type === "number") return "number";
  if (type === "date") return "date";
  if (type === "datetime") return "datetime-local";
  return "text";
}

function optionLabel(opt) {
  if (typeof opt === "object") return opt.label;
  return String(opt).replaceAll("_", " ").replace(/^\w/, (c) => c.toUpperCase());
}
function optionValue(opt) {
  return typeof opt === "object" ? opt.value : opt;
}

export function closeModal() {
  const overlay = document.querySelector(".modal-overlay");
  if (overlay) overlay.remove();
}

/**
 * Field-def-driven modal form.
 * field: { name, label, type: text|email|password|number|date|datetime|textarea|checkbox|select|select-async,
 *          required?, disabled?, options? (for select), source?/optionLabel? (for select-async), checkboxLabel? }
 * `sampleData`, if given, adds a "Fill Sample Data" button that calls it (sync or async) for a
 * values object and applies it the same way `initial` is applied — used to speed up demo data entry.
 */
export function openModal({ title, fields, initial = {}, submitLabel = "Save", onSubmit, sampleData }) {
  closeModal();

  const overlay = el("div", { class: "modal-overlay" });
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) closeModal();
  });

  const banner = el("div", { class: "form-banner hidden" });
  const grid = el("div", { class: "form-grid" });
  const inputs = {};

  for (const field of fields) {
    const wide = field.type === "textarea";
    const wrapper = el("div", { class: `field${wide ? " field-wide" : ""}` });

    let input;
    let checkboxHost = null;
    if (field.type === "select" || field.type === "select-async") {
      input = el("select", { name: field.name });
    } else if (field.type === "textarea") {
      input = el("textarea", { name: field.name });
    } else if (field.type === "checkbox") {
      checkboxHost = el("div", { class: "checkbox-field" });
      input = el("input", { type: "checkbox" });
      checkboxHost.appendChild(input);
      checkboxHost.appendChild(el("span", {}, field.checkboxLabel || field.label));
    } else {
      input = el("input", { type: htmlInputType(field.type), name: field.name });
    }
    if (field.required && field.type !== "checkbox") input.required = true;
    if (field.disabled) input.disabled = true;

    if (field.type === "checkbox") {
      wrapper.appendChild(checkboxHost);
    } else {
      wrapper.appendChild(el("label", {}, field.label + (field.required ? " *" : "")));
      wrapper.appendChild(input);
    }
    if (field.hint) wrapper.appendChild(el("div", { class: "muted", style: "font-size:11.5px;margin-top:4px;" }, field.hint));
    const err = el("div", { class: "field-error hidden" });
    wrapper.appendChild(err);

    inputs[field.name] = { field, input, wrapper, err };
    grid.appendChild(wrapper);
  }

  // populate static/select-async options
  for (const field of fields) {
    if (field.type === "select") {
      const { input } = inputs[field.name];
      if (!field.required) input.appendChild(el("option", { value: "" }, "—"));
      for (const opt of field.options) {
        input.appendChild(el("option", { value: optionValue(opt) }, optionLabel(opt)));
      }
    } else if (field.type === "select-async") {
      const { input } = inputs[field.name];
      input.appendChild(el("option", { value: "" }, "Loading…"));
      api
        .get(`${field.source}?limit=200`)
        .then((page) => {
          input.innerHTML = "";
          if (!field.required) input.appendChild(el("option", { value: "" }, "—"));
          for (const item of page.items || []) {
            input.appendChild(el("option", { value: item.id }, field.optionLabel(item)));
          }
          if (initial[field.name] != null) input.value = String(initial[field.name]);
        })
        .catch(() => {
          input.innerHTML = "";
          input.appendChild(el("option", { value: "" }, "Failed to load"));
        });
    }
  }

  function applyValues(values) {
    for (const [name, { field, input }] of Object.entries(inputs)) {
      const value = values[name];
      if (value === undefined || value === null) continue;
      if (field.type === "checkbox") input.checked = !!value;
      else if (field.type === "datetime") input.value = toDatetimeLocalValue(value);
      else input.value = value;
    }
  }

  applyValues(initial);

  const form = el("form", {}, [banner, grid]);
  const cancelBtn = el("button", { type: "button", class: "btn-secondary" }, "Cancel");
  cancelBtn.addEventListener("click", closeModal);
  const submitBtn = el("button", { type: "submit", class: "btn-primary" }, submitLabel);
  const actionButtons = [cancelBtn, submitBtn];
  if (sampleData) {
    const sampleBtn = el("button", { type: "button", class: "btn-secondary" }, "Fill Sample Data");
    sampleBtn.addEventListener("click", async () => {
      sampleBtn.disabled = true;
      try {
        applyValues(await sampleData());
      } finally {
        sampleBtn.disabled = false;
      }
    });
    actionButtons.unshift(sampleBtn);
  }
  form.appendChild(el("div", { class: "form-actions" }, actionButtons));

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    banner.classList.add("hidden");
    banner.textContent = "";
    for (const { err, wrapper } of Object.values(inputs)) {
      err.classList.add("hidden");
      wrapper.classList.remove("has-error");
    }

    const payload = {};
    for (const [name, { field, input }] of Object.entries(inputs)) {
      let value;
      if (field.type === "checkbox") value = input.checked;
      else if (field.type === "number") value = input.value === "" ? null : Number(input.value);
      else if (field.type === "datetime") value = fromDatetimeLocalValue(input.value);
      else if (field.type === "select" || field.type === "select-async") {
        value = input.value === "" ? null : field.numeric === false ? input.value : Number(input.value);
      } else value = input.value === "" ? null : input.value;
      payload[name] = value;
    }

    submitBtn.disabled = true;
    try {
      await onSubmit(payload);
      closeModal();
    } catch (err) {
      if (err.status === 422 && Array.isArray(err.detail)) {
        const unmatched = [];
        for (const item of err.detail) {
          const fieldName = (item.loc || []).slice(-1)[0];
          if (inputs[fieldName]) {
            inputs[fieldName].err.textContent = item.msg;
            inputs[fieldName].err.classList.remove("hidden");
            inputs[fieldName].wrapper.classList.add("has-error");
          } else {
            unmatched.push(item.msg);
          }
        }
        if (unmatched.length) {
          banner.textContent = unmatched.join("; ");
          banner.classList.remove("hidden");
        }
      } else {
        banner.textContent = err.message || "Something went wrong.";
        banner.classList.remove("hidden");
      }
      submitBtn.disabled = false;
    }
  });

  const modal = el("div", { class: "modal" }, [
    el("div", { class: "modal-header" }, [el("h3", {}, title), (() => {
      const btn = el("button", { type: "button", class: "modal-close" }, "×");
      btn.addEventListener("click", closeModal);
      return btn;
    })()]),
    form,
  ]);
  overlay.appendChild(modal);
  document.body.appendChild(overlay);

  const first = form.querySelector("input, select, textarea");
  if (first) first.focus();
}
