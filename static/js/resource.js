import { api } from "./api.js";
import { hasPermission } from "./state.js";
import { renderTable, renderPager } from "./components/table.js";
import { openModal } from "./components/modal.js";
import { toast } from "./components/toast.js";
import { el, debounce } from "./utils.js";

/**
 * Generic CRUD-table module factory, shared by Patients/Doctors/Appointments/
 * Medical Records — the four resources with an identical paginated-table +
 * modal-form shape. Referrals/Schedule/Analytics/Audit/Assistant/Admin are
 * genuinely distinct screens and are hand-written instead.
 */
export function createResourceModule(config) {
  const singular = config.singular || config.title.replace(/s$/, "");

  async function render(container) {
    const local = { skip: 0, limit: 20, total: 0, items: [], search: "", filters: {} };

    container.innerHTML = "";
    const toolbar = el("div", { class: "toolbar" });
    const permBanner = el("div", { class: "banner banner-error hidden" });
    const tableHost = el("div", {});
    const pagerHost = el("div", {});
    const card = el("div", { class: "card" }, [
      el("div", { class: "card-header" }, [el("h2", {}, config.title)]),
      toolbar,
      permBanner,
      tableHost,
      pagerHost,
    ]);
    container.appendChild(card);

    if (config.searchableFields?.length) {
      const searchInput = el("input", { type: "search", placeholder: "Search current page…" });
      searchInput.addEventListener(
        "input",
        debounce(() => {
          local.search = searchInput.value.trim().toLowerCase();
          renderRows();
        }, 200)
      );
      toolbar.appendChild(el("div", { class: "search-box" }, [searchInput]));
    }

    for (const filter of config.serverFilters || []) {
      const select = el("select", {});
      select.appendChild(el("option", { value: "" }, filter.label));
      for (const opt of filter.options) {
        const value = typeof opt === "object" ? opt.value : opt;
        const label = typeof opt === "object" ? opt.label : value;
        select.appendChild(el("option", { value }, String(label)));
      }
      select.addEventListener("change", () => {
        local.filters[filter.param] = select.value;
        local.skip = 0;
        load();
      });
      toolbar.appendChild(select);
    }

    toolbar.appendChild(el("div", { class: "spacer" }));
    if (config.searchableFields?.length) {
      toolbar.appendChild(el("span", { class: "toolbar-hint" }, "Search scans the current page only"));
    }
    if (hasPermission(config.permissions?.create)) {
      const newBtn = el("button", { class: "btn-primary" }, `+ New ${singular}`);
      newBtn.addEventListener("click", openCreateModal);
      toolbar.appendChild(newBtn);
    }

    function buildActions(row) {
      const buttons = [];
      if (hasPermission(config.permissions?.update)) {
        const editBtn = el("button", { class: "btn-ghost btn-sm btn-icon", title: "Edit" }, "✎");
        editBtn.addEventListener("click", () => openEditModal(row));
        buttons.push(editBtn);
      }
      if (hasPermission(config.permissions?.delete)) {
        const delBtn = el("button", { class: "btn-ghost btn-sm btn-icon", title: "Delete" }, "✕");
        delBtn.addEventListener("click", () => handleDelete(row));
        buttons.push(delBtn);
      }
      // Lets a resource offer narrower, ownership-scoped actions (e.g. a
      // patient rescheduling/cancelling their own appointment) to callers
      // who don't hold the blanket update/delete permission above.
      if (config.extraActions) buttons.push(...config.extraActions(row, load));
      return buttons;
    }

    async function load() {
      const params = new URLSearchParams({ skip: local.skip, limit: local.limit });
      for (const [k, v] of Object.entries(local.filters)) if (v) params.set(k, v);
      try {
        const page = await api.get(`${config.basePath}/?${params.toString()}`);
        local.items = page.items || [];
        local.total = page.total || 0;
        permBanner.classList.add("hidden");
      } catch (err) {
        local.items = [];
        local.total = 0;
        permBanner.textContent =
          err.status === 403
            ? `You don't have permission to view ${config.title.toLowerCase()}.`
            : err.message || "Failed to load.";
        permBanner.classList.remove("hidden");
      }
      renderRows();
    }

    function renderRows() {
      let rows = local.items;
      if (local.search && config.searchableFields?.length) {
        rows = rows.filter((row) =>
          config.searchableFields.some((f) => String(row[f] ?? "").toLowerCase().includes(local.search))
        );
      }
      const canEdit = hasPermission(config.permissions?.update);
      const canDelete = hasPermission(config.permissions?.delete);
      renderTable(tableHost, {
        columns: config.columns,
        rows,
        actions: canEdit || canDelete || config.extraActions ? buildActions : null,
        onRowClick: config.onRowClick,
        emptyMessage: `No ${config.title.toLowerCase()} found.`,
      });
      renderPager(pagerHost, {
        skip: local.skip,
        limit: local.limit,
        total: local.total,
        onChange: (skip) => {
          local.skip = skip;
          load();
        },
      });
    }

    function openCreateModal() {
      openModal({
        title: `New ${singular}`,
        fields: config.fields,
        submitLabel: "Create",
        sampleData: config.sampleData,
        onSubmit: async (payload) => {
          await api.post(`${config.basePath}/`, payload);
          toast(`${singular} created.`, "success");
          await load();
        },
      });
    }

    function openEditModal(row) {
      openModal({
        title: `Edit ${singular} #${row.id}`,
        fields: config.editFields || config.fields,
        initial: row,
        submitLabel: "Save changes",
        onSubmit: async (payload) => {
          await api.put(`${config.basePath}/${row.id}`, payload);
          toast("Changes saved.", "success");
          await load();
        },
      });
    }

    async function handleDelete(row) {
      if (!confirm(`Delete this ${singular.toLowerCase()}? This cannot be undone.`)) return;
      try {
        await api.del(`${config.basePath}/${row.id}`);
        toast("Deleted.", "success");
        await load();
      } catch (err) {
        toast(err.message || "Delete failed.", "error");
      }
    }

    await load();
  }

  return { render };
}
