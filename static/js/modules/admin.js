import { api } from "../api.js";
import { toast } from "../components/toast.js";
import { openModal } from "../components/modal.js";
import { el } from "../utils.js";

export async function render(container) {
  container.innerHTML = "";
  const banner = el("div", { class: "banner banner-error hidden" });
  const body = el("div", {});
  container.appendChild(
    el("div", { class: "card" }, [
      el("div", { class: "card-header" }, [el("h2", {}, "Admin — Users & Roles")]),
      el("p", { class: "muted" },
        "Grant/revoke roles, and link a user account to a Patient or Doctor record — this is what makes patient:view_own / doctor-scoped views show real, role-specific data instead of an empty list."
      ),
      banner,
      body,
    ])
  );

  let roleNames = [];
  try {
    roleNames = await api.get("/admin/roles");
  } catch {
    /* surfaced by load()'s own error handling below */
  }

  async function load() {
    body.innerHTML = '<div class="loading-line">Loading users…</div>';
    let users;
    try {
      users = await api.get("/admin/users");
      banner.classList.add("hidden");
    } catch (err) {
      body.innerHTML = "";
      banner.textContent =
        err.status === 403 ? "You don't have permission to manage users (needs admin:*)." : err.message || "Failed to load users.";
      banner.classList.remove("hidden");
      return;
    }
    body.innerHTML = "";
    for (const user of users) body.appendChild(renderUserCard(user));
  }

  function renderUserCard(user) {
    const roleChips = el("div", { class: "chip-row" });
    for (const roleName of user.roles) {
      const removeBtn = el("button", { class: "btn-ghost btn-sm", style: "padding:0 2px;font-size:12px;" }, "×");
      removeBtn.addEventListener("click", async () => {
        try {
          await api.del(`/admin/users/${user.id}/roles/${roleName}`);
          toast(`Removed ${roleName} from ${user.username}.`, "success");
          await load();
        } catch (err) {
          toast(err.message || "Failed to revoke role.", "error");
        }
      });
      roleChips.appendChild(el("span", { class: "chip" }, [roleName + " ", removeBtn]));
    }
    if (!user.roles.length) roleChips.appendChild(el("span", { class: "muted" }, "No roles yet"));

    const addRoleSelect = el("select", { style: "max-width:170px;" });
    addRoleSelect.appendChild(el("option", { value: "" }, "Add role…"));
    for (const roleName of roleNames) {
      if (!user.roles.includes(roleName)) addRoleSelect.appendChild(el("option", { value: roleName }, roleName));
    }
    addRoleSelect.addEventListener("change", async () => {
      if (!addRoleSelect.value) return;
      const roleName = addRoleSelect.value;
      try {
        await api.post(`/admin/users/${user.id}/roles`, { role_name: roleName });
        toast(`Granted ${roleName} to ${user.username}.`, "success");
        await load();
      } catch (err) {
        toast(err.message || "Failed to grant role.", "error");
      }
    });

    const linkPatientBtn = el("button", { class: "btn-secondary btn-sm" }, "Link to Patient");
    linkPatientBtn.addEventListener("click", () => openLinkForm(user, "patient"));
    const linkDoctorBtn = el("button", { class: "btn-secondary btn-sm" }, "Link to Doctor");
    linkDoctorBtn.addEventListener("click", () => openLinkForm(user, "doctor"));
    const resetPwBtn = el("button", { class: "btn-secondary btn-sm" }, "Reset Password");
    resetPwBtn.addEventListener("click", () => openResetPasswordForm(user));

    return el("div", { class: "card" }, [
      el("div", { class: "card-header" }, [
        el("div", {}, [
          el("div", { style: "font-weight:650;" }, `${user.username}${user.is_active ? "" : " (inactive)"}`),
          el("div", { class: "muted", style: "font-size:12px;" }, user.email),
        ]),
        el("div", { class: "row-actions" }, [addRoleSelect, linkPatientBtn, linkDoctorBtn, resetPwBtn]),
      ]),
      roleChips,
    ]);
  }

  function openResetPasswordForm(user) {
    openModal({
      title: `Reset Password — ${user.username}`,
      submitLabel: "Reset Password",
      fields: [
        { name: "new_password", label: "New Password", type: "password", required: true },
      ],
      onSubmit: async ({ new_password }) => {
        await api.post(`/admin/users/${user.id}/reset-password`, { new_password });
        toast(`Password reset for ${user.username}. Their existing sessions were logged out.`, "success");
      },
    });
  }

  function openLinkForm(user, kind) {
    const source = kind === "patient" ? "/patients" : "/doctors";
    openModal({
      title: `Link ${user.username} to a ${kind}`,
      submitLabel: "Link",
      fields: [
        {
          name: "record_id", label: kind === "patient" ? "Patient" : "Doctor", type: "select-async", source, required: true,
          optionLabel: (r) => `${r.first_name} ${r.last_name} (#${r.id})`,
        },
      ],
      onSubmit: async ({ record_id }) => {
        await api.post(`/admin/users/${user.id}/link-${kind}/${record_id}`);
        toast(`Linked ${user.username} to ${kind} #${record_id}.`, "success");
      },
    });
  }

  await load();
}
