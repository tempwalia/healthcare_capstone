import { api } from "../api.js";
import { statTile, barChart } from "../charts.js";
import { el, capitalize, REFERRAL_STATUSES } from "../utils.js";

export async function render(container) {
  container.innerHTML = "";
  const body = el("div", {});
  container.appendChild(el("div", { class: "card-header" }, [el("h2", {}, "Analytics")]));
  container.appendChild(body);
  body.innerHTML = '<div class="loading-line">Loading analytics…</div>';

  let summary;
  try {
    summary = await api.get("/analytics/referrals/summary");
  } catch (err) {
    body.innerHTML = "";
    body.appendChild(
      el("div", { class: "banner banner-error" },
        err.status === 403 ? "You don't have permission to view analytics (needs analytics:view)." : err.message || "Failed to load analytics."
      )
    );
    return;
  }
  body.innerHTML = "";

  const tiles = el("div", { class: "grid-3" });
  statTile(tiles, { label: "Avg. time to schedule", value: `${summary.avg_time_to_schedule_hours} hrs` });
  statTile(tiles, {
    label: "Referrals at delay risk",
    value: summary.delay_risk_referrals,
    accent: summary.delay_risk_referrals === 0 ? "good" : summary.delay_risk_referrals < 5 ? "warning" : "serious",
  });
  statTile(tiles, { label: "Eligibility denial rate", value: `${(summary.eligibility_denial_rate * 100).toFixed(1)}%` });
  body.appendChild(tiles);
  body.appendChild(
    el("p", { class: "muted", style: "font-size:11.5px;margin:8px 0 16px;" },
      "\"Avg. time to schedule\" includes every referral that ever reached \"scheduled\", not just ones currently sitting in that status.")
  );

  const statusData = REFERRAL_STATUSES.filter((s) => summary.by_status[s]).map((s) => ({
    status: capitalize(s),
    count: summary.by_status[s],
  }));
  barChart(body, { title: "Referrals by Status", data: statusData, labelKey: "status", valueKey: "count" });
  barChart(body, {
    title: "Top Specialties Requested",
    data: summary.top_specialties_requested,
    labelKey: "specialty",
    valueKey: "count",
  });
}
