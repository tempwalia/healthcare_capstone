import { el } from "../utils.js";

export function renderTable(container, { columns, rows, onRowClick, actions, emptyMessage = "No records found." }) {
  container.innerHTML = "";
  if (!rows.length) {
    container.appendChild(el("div", { class: "table-empty" }, emptyMessage));
    return;
  }

  const wrap = el("div", { class: "table-wrap" });
  const table = el("table");

  const headRow = el("tr");
  for (const col of columns) headRow.appendChild(el("th", {}, col.label));
  if (actions) headRow.appendChild(el("th", {}, ""));
  table.appendChild(el("thead", {}, [headRow]));

  const tbody = el("tbody");
  for (const row of rows) {
    const tr = el("tr", { class: onRowClick ? "clickable" : "" });
    if (onRowClick) tr.addEventListener("click", () => onRowClick(row));

    for (const col of columns) {
      const value = col.format ? col.format(row) : row[col.key];
      const td = el("td");
      if (col.html) td.innerHTML = value ?? "—";
      else td.textContent = value === undefined || value === null || value === "" ? "—" : value;
      tr.appendChild(td);
    }

    if (actions) {
      const td = el("td");
      td.addEventListener("click", (e) => e.stopPropagation());
      const rowActions = el("div", { class: "row-actions" }, actions(row));
      td.appendChild(rowActions);
      tr.appendChild(td);
    }

    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  wrap.appendChild(table);
  container.appendChild(wrap);
}

export function renderPager(container, { skip, limit, total, onChange }) {
  container.innerHTML = "";
  if (total <= limit && skip === 0) return;

  const from = total === 0 ? 0 : skip + 1;
  const to = Math.min(skip + limit, total);

  const prevBtn = el(
    "button",
    { class: "btn-secondary btn-sm", onclick: () => onChange(Math.max(skip - limit, 0)) },
    "Prev"
  );
  if (skip <= 0) prevBtn.disabled = true;

  const nextBtn = el(
    "button",
    { class: "btn-secondary btn-sm", onclick: () => onChange(skip + limit) },
    "Next"
  );
  if (skip + limit >= total) nextBtn.disabled = true;

  container.appendChild(
    el("div", { class: "pager" }, [
      el("div", {}, `${from}–${to} of ${total}`),
      el("div", { class: "pager-buttons" }, [prevBtn, nextBtn]),
    ])
  );
}
