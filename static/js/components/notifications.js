import { api } from "../api.js";
import { getState } from "../state.js";
import { navigate } from "../router.js";
import { el, formatDateTime } from "../utils.js";

const POLL_INTERVAL_MS = 30000;

/** A self-contained bell + dropdown, mounted once into a persistent host
 * (unlike per-route content, this must survive navigation) and polled on
 * the same cadence as app.js's own pollHealth(), so no new interval
 * infrastructure is introduced. */
export function mountNotificationBell(host) {
  const bell = el("button", { class: "notif-bell", title: "Notifications" }, "🔔");
  const badge = el("span", { class: "notif-badge hidden" });
  bell.appendChild(badge);
  const panel = el("div", { class: "notif-panel hidden" });
  const wrap = el("div", { class: "notif-wrap" }, [bell, panel]);
  host.appendChild(wrap);

  let open = false;
  let items = [];

  function renderPanel() {
    panel.innerHTML = "";
    if (!items.length) {
      panel.appendChild(el("div", { class: "notif-empty" }, "No notifications yet."));
      return;
    }
    for (const item of items) {
      const row = el(
        "div",
        { class: `notif-item${item.read_at ? "" : " unread"}` },
        [
          el("div", { class: "notif-item-title" }, item.title),
          el("div", { class: "notif-item-time" }, formatDateTime(item.created_at)),
        ]
      );
      row.addEventListener("click", async () => {
        if (!item.read_at) {
          try {
            await api.post(`/notifications/${item.id}/read`, {});
          } catch {
            /* best-effort — still navigate even if marking read fails */
          }
        }
        setOpen(false);
        if (item.referral_id) navigate(`/referrals/${item.referral_id}`);
        await refresh();
      });
      panel.appendChild(row);
    }
  }

  function setOpen(next) {
    open = next;
    panel.classList.toggle("hidden", !open);
  }

  bell.addEventListener("click", async (e) => {
    e.stopPropagation();
    setOpen(!open);
    if (open) {
      renderPanel();
      await refresh();
    }
  });
  document.addEventListener("click", (e) => {
    if (open && !wrap.contains(e.target)) setOpen(false);
  });

  async function refresh() {
    if (!getState().accessToken) return; // logged out — nothing to poll yet
    try {
      const page = await api.get("/notifications/?limit=15");
      items = page.items || [];
    } catch {
      return; // transient error — leave the last-known state showing
    }
    const unreadCount = items.filter((i) => !i.read_at).length;
    badge.textContent = unreadCount > 9 ? "9+" : String(unreadCount);
    badge.classList.toggle("hidden", unreadCount === 0);
    if (open) renderPanel();
  }

  refresh();
  setInterval(refresh, POLL_INTERVAL_MS);

  return { refresh };
}
