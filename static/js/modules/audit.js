import { api } from "../api.js";
import { renderTable, renderPager } from "../components/table.js";
import { el, formatDateTime } from "../utils.js";

function summarizeDetails(details) {
  try {
    const parsed = JSON.parse(details);
    return Object.entries(parsed).map(([k, v]) => `${k}: ${v}`).join(", ");
  } catch {
    return details;
  }
}

export async function render(container) {
  container.innerHTML = "";
  const local = { skip: 0, limit: 25, total: 0, items: [] };
  const banner = el("div", { class: "banner banner-error hidden" });
  const tableHost = el("div", {});
  const pagerHost = el("div", {});
  container.appendChild(
    el("div", { class: "card" }, [el("div", { class: "card-header" }, [el("h2", {}, "Audit Log")]), banner, tableHost, pagerHost])
  );

  async function load() {
    try {
      const page = await api.get(`/audit/?skip=${local.skip}&limit=${local.limit}`);
      local.items = page.items || [];
      local.total = page.total || 0;
      banner.classList.add("hidden");
    } catch (err) {
      local.items = [];
      local.total = 0;
      banner.textContent =
        err.status === 403 ? "You don't have permission to view the audit log (needs audit:view)." : err.message || "Failed to load audit log.";
      banner.classList.remove("hidden");
    }
    renderTable(tableHost, {
      columns: [
        { key: "id", label: "ID" },
        { key: "timestamp", label: "When", format: (r) => formatDateTime(r.timestamp) },
        { key: "user_id", label: "Actor", format: (r) => (r.user_id != null ? `User #${r.user_id}` : "system") },
        { key: "action", label: "Action" },
        { key: "details", label: "Details", format: (r) => (r.details ? summarizeDetails(r.details) : "—") },
      ],
      rows: local.items,
      emptyMessage: "No audit entries yet.",
    });
    renderPager(pagerHost, {
      skip: local.skip, limit: local.limit, total: local.total,
      onChange: (skip) => { local.skip = skip; load(); },
    });
  }

  await load();
}
