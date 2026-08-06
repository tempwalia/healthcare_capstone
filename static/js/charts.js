import { el, escapeHtml } from "./utils.js";

export function statTile(container, { label, value, accent = null }) {
  container.appendChild(
    el("div", { class: `stat-tile${accent ? ` accent-${accent}` : ""}` }, [
      el("div", { class: "label" }, label),
      el("div", { class: "value" }, String(value)),
    ])
  );
}

/** Horizontal bar chart, single sequential hue — used for both by-status and
 * top-specialties (both are magnitude comparisons over categories, not
 * part-to-whole, so a donut would misleadingly imply full coverage,
 * especially for the "top 5" specialties list). No chart library — plain
 * width-scaled divs, same "no CDN dependency" ethos as the rest of the app. */
export function barChart(container, { title, data, labelKey, valueKey, formatValue = String }) {
  const card = el("div", { class: "card" });
  if (title) card.appendChild(el("div", { class: "chart-title" }, title));

  if (!data.length) {
    card.appendChild(el("div", { class: "bar-chart-empty" }, "No data yet."));
    container.appendChild(card);
    return;
  }

  const max = Math.max(...data.map((d) => Number(d[valueKey]) || 0), 1);
  for (const d of data) {
    const value = Number(d[valueKey]) || 0;
    const pct = Math.max((value / max) * 100, 2);
    const label = String(d[labelKey]);
    card.appendChild(
      el("div", { class: "bar-chart-row" }, [
        el("div", { class: "bar-chart-label", title: label }, label),
        el("div", { class: "bar-chart-track" }, [el("div", { class: "bar-chart-fill", style: `width:${pct}%` })]),
        el("div", { class: "bar-chart-value" }, formatValue(value)),
      ])
    );
  }
  container.appendChild(card);
}
