import { api } from "../api.js";
import { getState } from "../state.js";
import { el } from "../utils.js";
import { renderMarkdown } from "../markdown.js";

function sessionId() {
  let id = sessionStorage.getItem("assistant_session_id");
  if (!id) {
    id = `dash-${Date.now()}`;
    sessionStorage.setItem("assistant_session_id", id);
  }
  return id;
}

// Mirrors app/agents/assistant_graph.py's resolve_role_for_tools precedence
// exactly (care_coordinator > specialist > pcp > patient) so these
// suggestion chips only ever appear for a role the backend would actually
// hand the matching tools to — not just "any role with the right permission
// name", which could also include payer_admin (has analytics:view too, but
// isn't in the backend's precedence list and silently falls back to
// patient-level tools there — a separate, pre-existing gap, not touched here).
const _ROLE_PRECEDENCE = ["care_coordinator", "specialist", "pcp", "patient"];
function resolveRoleForTools(roles) {
  for (const role of _ROLE_PRECEDENCE) {
    if (roles.includes(role)) return role;
  }
  return "patient";
}

const COORDINATOR_SUGGESTIONS = [
  "Give me the referral funnel summary",
  "Show me referral #1's timeline",
];

export async function render(container) {
  container.innerHTML = "";
  const chatWindow = el("div", { class: "chat-window" });
  const suggestionsHost = el("div", { class: "chip-row", style: "margin-bottom:10px;" });
  const input = el("input", { type: "text", placeholder: "Ask about a referral, document upload, scheduling…" });
  const sendBtn = el("button", { class: "btn-primary" }, "Send");

  container.appendChild(
    el("div", { class: "card" }, [
      el("div", { class: "card-header" }, [el("h2", {}, "Assistant")]),
      chatWindow,
      suggestionsHost,
      el("div", { class: "chat-input-row" }, [input, sendBtn]),
    ])
  );

  function appendMessage(role, text) {
    const bubble = el("div", { class: `chat-msg ${role}` });
    if (role === "assistant") {
      // Backend prompts the model to answer in markdown (headings, bold,
      // lists, tables) instead of raw JSON — render that structure instead
      // of dumping the raw "**bold**" / "| a | b |" syntax as plain text.
      bubble.appendChild(el("div", { class: "chat-msg-md", html: renderMarkdown(text) }));
    } else {
      bubble.textContent = text;
    }
    chatWindow.appendChild(bubble);
    chatWindow.scrollTop = chatWindow.scrollHeight;
  }

  appendMessage("assistant", "Hi! Ask me about referral status, document uploads, scheduling, or consult outcomes.");

  const { me } = getState();
  if (me && resolveRoleForTools(me.roles) === "care_coordinator") {
    for (const suggestion of COORDINATOR_SUGGESTIONS) {
      const chip = el("button", { class: "chip", style: "cursor:pointer;" }, suggestion);
      chip.addEventListener("click", () => {
        input.value = suggestion;
        send();
      });
      suggestionsHost.appendChild(chip);
    }
  }

  async function send() {
    const text = input.value.trim();
    if (!text) return;
    appendMessage("user", text);
    input.value = "";
    sendBtn.disabled = true;
    try {
      const res = await api.post("/assistant/chat", { message: text, session_id: sessionId() });
      appendMessage("assistant", res.reply);
    } catch (err) {
      appendMessage("assistant", `Sorry, something went wrong: ${err.message || "unknown error"}`);
    } finally {
      sendBtn.disabled = false;
      input.focus();
    }
  }

  sendBtn.addEventListener("click", send);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") send();
  });
}
