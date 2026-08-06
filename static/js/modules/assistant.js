import { api } from "../api.js";
import { el } from "../utils.js";

function sessionId() {
  let id = sessionStorage.getItem("assistant_session_id");
  if (!id) {
    id = `dash-${Date.now()}`;
    sessionStorage.setItem("assistant_session_id", id);
  }
  return id;
}

export async function render(container) {
  container.innerHTML = "";
  const chatWindow = el("div", { class: "chat-window" });
  const input = el("input", { type: "text", placeholder: "Ask about a referral, document upload, scheduling…" });
  const sendBtn = el("button", { class: "btn-primary" }, "Send");

  container.appendChild(
    el("div", { class: "card" }, [
      el("div", { class: "card-header" }, [el("h2", {}, "Assistant")]),
      chatWindow,
      el("div", { class: "chat-input-row" }, [input, sendBtn]),
    ])
  );

  function appendMessage(role, text) {
    chatWindow.appendChild(el("div", { class: `chat-msg ${role}` }, text));
    chatWindow.scrollTop = chatWindow.scrollHeight;
  }

  appendMessage("assistant", "Hi! Ask me about referral status, document uploads, scheduling, or consult outcomes.");

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
