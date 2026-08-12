import { escapeHtml } from "./utils.js";

// Minimal, dependency-free markdown -> HTML renderer for assistant chat
// replies. The backend prompt (app/agents/assistant_graph.py's
// _FORMATTING_INSTRUCTIONS) asks the model for headings/bold/lists/tables
// instead of raw JSON; this is the frontend half — actually rendering that
// markdown instead of dumping the raw "**bold**" / "| a | b |" syntax as
// plain text into the chat bubble.
//
// Safety: the ENTIRE input is HTML-escaped up front, then block/inline
// markers are turned into tags by matching against the now-escaped text
// (markdown punctuation like `*`, `#`, `|`, `` ` `` survives escaping
// untouched). No raw user/model text ever reaches innerHTML unescaped.

function isTableSeparatorLine(line) {
  const trimmed = line.trim();
  return trimmed.includes("|") && trimmed.includes("-") && /^[\s|:-]+$/.test(trimmed);
}

function splitTableRow(line) {
  let t = line.trim();
  if (t.startsWith("|")) t = t.slice(1);
  if (t.endsWith("|")) t = t.slice(0, -1);
  return t.split("|").map((cell) => cell.trim());
}

function renderInline(text) {
  return text
    .replace(/`([^`]+?)`/g, (_, code) => `<code>${code}</code>`)
    .replace(/\*\*(?!\s)([^*]+?)(?<!\s)\*\*/g, "<strong>$1</strong>")
    // Single-asterisk emphasis: CommonMark-style guards (no adjacent
    // whitespace inside the markers) plus excluding digit-adjacent
    // asterisks so stray multiplication like "3*4" in a dosage/quantity
    // sentence isn't mistaken for emphasis.
    .replace(/(?<![*\d])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![*\d])/g, "<em>$1</em>");
}

export function renderMarkdown(raw) {
  const lines = escapeHtml(raw).split("\n");
  const blocks = [];
  let para = [];
  let i = 0;

  function flushPara() {
    if (para.length) {
      blocks.push(`<p>${renderInline(para.join(" "))}</p>`);
      para = [];
    }
  }

  while (i < lines.length) {
    const line = lines[i];

    if (/^\s*```/.test(line)) {
      flushPara();
      const codeLines = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) {
        codeLines.push(lines[i]);
        i++;
      }
      i++;
      blocks.push(`<pre><code>${codeLines.join("\n")}</code></pre>`);
      continue;
    }

    if (line.includes("|") && i + 1 < lines.length && isTableSeparatorLine(lines[i + 1])) {
      flushPara();
      const headerCells = splitTableRow(line);
      i += 2;
      const bodyRows = [];
      while (i < lines.length && lines[i].trim() !== "" && lines[i].includes("|")) {
        bodyRows.push(splitTableRow(lines[i]));
        i++;
      }
      const thead = `<thead><tr>${headerCells.map((c) => `<th>${renderInline(c)}</th>`).join("")}</tr></thead>`;
      const tbody = `<tbody>${bodyRows
        .map((row) => `<tr>${row.map((c) => `<td>${renderInline(c)}</td>`).join("")}</tr>`)
        .join("")}</tbody>`;
      blocks.push(`<div class="table-wrap"><table>${thead}${tbody}</table></div>`);
      continue;
    }

    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      flushPara();
      blocks.push("<hr>");
      i++;
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      flushPara();
      const level = heading[1].length;
      blocks.push(`<h${level}>${renderInline(heading[2].trim())}</h${level}>`);
      i++;
      continue;
    }

    if (/^\s*[-*+]\s+/.test(line)) {
      flushPara();
      const items = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*+]\s+/, ""));
        i++;
      }
      blocks.push(`<ul>${items.map((it) => `<li>${renderInline(it)}</li>`).join("")}</ul>`);
      continue;
    }

    if (/^\s*\d+[.)]\s+/.test(line)) {
      flushPara();
      const items = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+[.)]\s+/, ""));
        i++;
      }
      blocks.push(`<ol>${items.map((it) => `<li>${renderInline(it)}</li>`).join("")}</ol>`);
      continue;
    }

    if (line.trim() === "") {
      flushPara();
      i++;
      continue;
    }

    para.push(line.trim());
    i++;
  }
  flushPara();
  return blocks.join("\n") || "<p></p>";
}
