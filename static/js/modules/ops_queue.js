import { api } from "../api.js";
import { navigate } from "../router.js";
import { renderPager } from "../components/table.js";
import { statTile } from "../charts.js";
import { el, formatDateTime, skeletonBlock, REFERRAL_PROGRESS_INFO } from "../utils.js";
import { renderSpecialistCandidateCards } from "./referrals.js";

const patientNameCache = new Map();
async function resolvePatientName(id) {
  if (patientNameCache.has(id)) return patientNameCache.get(id);
  const name = await api.get(`/patients/${id}`).then((p) => `${p.first_name} ${p.last_name}`).catch(() => `Patient #${id}`);
  patientNameCache.set(id, name);
  return name;
}

// Each referral surfaced by GET /referral/requests/ops-queue is bucketed
// client-side by its raw status — the endpoint already narrows to exactly
// these three cases server-side (see list_ops_queue_referrals), this just
// decides which section a card lands in and how it's labeled.
const BUCKETS = [
  {
    status: "awaiting_specialist_approval",
    title: "Needs specialist approval",
    hint: "Ranked candidates are ready — pick one to move the referral to scheduling.",
    accent: "warning",
  },
  {
    status: "eligibility_denied",
    title: "Eligibility denied",
    hint: "Insurance couldn't be verified — needs a coordinator decision on how to proceed.",
    accent: "serious",
  },
  {
    status: "__outcome_needed__", // catch-all: anything in the queue that isn't the two statuses above is a `scheduled` referral with no outcome recorded yet
    title: "Outcome needed",
    hint: "The consult has happened (or is booked) — record what happened to close it out.",
    accent: null,
  },
];

function bucketFor(status) {
  return BUCKETS.find((b) => b.status === status) || BUCKETS[BUCKETS.length - 1];
}

export async function render(container) {
  container.innerHTML = "";
  const local = { skip: 0, limit: 100, total: 0, items: [] };

  const summaryHost = el("div", { class: "grid-3 view-accent-bar" });
  const bannerHost = el("div", { class: "banner banner-error hidden" });
  const bucketsHost = el("div", {});
  const pagerHost = el("div", {});
  container.appendChild(
    el("div", {}, [
      el("p", { class: "muted", style: "margin:-6px 0 14px;" },
        "Referrals waiting on a coordinator decision — approvals, eligibility denials, and consults with no outcome recorded yet."),
      summaryHost,
      bannerHost,
      bucketsHost,
      pagerHost,
    ])
  );

  function renderSummary() {
    summaryHost.innerHTML = "";
    for (const bucket of BUCKETS) {
      const count = local.items.filter((r) => bucketFor(r.status).status === bucket.status).length;
      statTile(summaryHost, { label: bucket.title, value: count, accent: bucket.accent });
    }
  }

  async function renderBuckets() {
    bucketsHost.innerHTML = "";
    for (const bucket of BUCKETS) {
      const items = local.items.filter((r) => bucketFor(r.status).status === bucket.status);
      const sectionHost = el("div", { class: "card", style: "margin-bottom:16px;" });
      sectionHost.appendChild(
        el("div", { class: "card-header" }, [
          el("h3", {}, `${bucket.title} (${items.length})`),
        ])
      );
      sectionHost.appendChild(el("p", { class: "muted", style: "font-size:12.5px;margin:-4px 0 10px;" }, bucket.hint));

      if (!items.length) {
        sectionHost.appendChild(el("div", { class: "table-empty" }, "Nothing here right now."));
      } else {
        const listHost = el("div", { class: "queue-card-list" });
        sectionHost.appendChild(listHost);
        for (const referral of items) {
          listHost.appendChild(await renderQueueCard(referral, bucket));
        }
      }
      bucketsHost.appendChild(sectionHost);
    }
  }

  async function renderQueueCard(referral, bucket) {
    const patientName = await resolvePatientName(referral.patient_id);
    const progress = REFERRAL_PROGRESS_INFO[referral.status];
    const card = el("div", { class: "queue-card card-interactive", "data-accent": bucket.accent }, [
      el("div", { class: "queue-card-top" }, [
        el("a", { href: `#/referrals/${referral.id}` }, `Referral #${referral.id} — ${patientName}`),
        el("span", { class: "muted", style: "font-size:11.5px;" }, `Updated ${formatDateTime(referral.updated_at || referral.created_at)}`),
      ]),
      el("div", { class: "muted", style: "font-size:12.5px;margin:4px 0 8px;" }, referral.reason || "No reason given"),
    ]);

    if (progress) {
      card.appendChild(el("div", { class: "muted", style: "font-size:12px;" }, progress.nextStep));
    }

    if (bucket.status === "awaiting_specialist_approval") {
      const expandBtn = el("button", { class: "btn-secondary btn-sm", style: "margin-top:8px;" }, "Review candidates");
      const candidatesHost = el("div", { style: "margin-top:10px;" });
      let expanded = false;
      expandBtn.addEventListener("click", async () => {
        expanded = !expanded;
        expandBtn.textContent = expanded ? "Hide candidates" : "Review candidates";
        if (!expanded) {
          candidatesHost.innerHTML = "";
          return;
        }
        candidatesHost.innerHTML = '<div class="loading-line">Loading candidates…</div>';
        try {
          const stateData = await api.get(`/referral-workflow/${referral.id}/state`);
          await renderSpecialistCandidateCards(candidatesHost, {
            referral,
            candidates: stateData.specialist_candidates || [],
            canApprove: true,
            onResumed: async () => load(),
          });
        } catch (err) {
          candidatesHost.innerHTML = "";
          candidatesHost.appendChild(el("div", { class: "banner banner-error" }, err.message || "Failed to load candidates."));
        }
      });
      card.appendChild(expandBtn);
      card.appendChild(candidatesHost);
    } else {
      const openBtn = el("button", { class: "btn-secondary btn-sm", style: "margin-top:8px;" },
        bucket.status === "__outcome_needed__" ? "Record outcome →" : "Open referral →");
      openBtn.addEventListener("click", () => navigate(`/referrals/${referral.id}`));
      card.appendChild(openBtn);
    }

    return card;
  }

  async function load() {
    bucketsHost.innerHTML = "";
    bucketsHost.appendChild(skeletonBlock(4));
    try {
      const page = await api.get(`/referral/requests/ops-queue?skip=${local.skip}&limit=${local.limit}`);
      local.items = page.items || [];
      local.total = page.total || 0;
      bannerHost.classList.add("hidden");
    } catch (err) {
      local.items = [];
      local.total = 0;
      bannerHost.textContent = err.message || "Failed to load the ops queue.";
      bannerHost.classList.remove("hidden");
    }
    renderSummary();
    await renderBuckets();
    renderPager(pagerHost, {
      skip: local.skip, limit: local.limit, total: local.total,
      onChange: (skip) => { local.skip = skip; load(); },
    });
  }

  await load();
}
