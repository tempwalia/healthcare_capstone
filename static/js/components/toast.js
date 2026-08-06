export function toast(message, type = "info") {
  const root = document.getElementById("toast-root");
  if (!root) return;
  const node = document.createElement("div");
  node.className = `toast${type === "error" ? " error" : type === "success" ? " success" : ""}`;
  node.textContent = message;
  root.appendChild(node);
  setTimeout(() => node.remove(), 4200);
}
