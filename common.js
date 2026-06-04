/* Shared helpers: markdown + mermaid rendering with the exact config that
   survived the CJK-clipping wars (htmlLabels + loose security + generic
   font, typography scoped to .md-body so node <p>s stay default-sized). */

const MERMAID_CONFIG = {
  startOnLoad: false,
  theme: "dark",
  securityLevel: "loose",
  flowchart: { htmlLabels: true, padding: 12 },
  themeVariables: { fontFamily: "sans-serif" },
};

async function renderMarkdownInto(el, mdText) {
  // Pull mermaid fences out before marked sees them, re-insert as divs.
  const blocks = [];
  const replaced = mdText.replace(/```mermaid\s*\n([\s\S]*?)```/g, (_, code) => {
    blocks.push(code);
    return `\n<div class="mermaid" data-idx="${blocks.length - 1}"></div>\n`;
  });
  el.innerHTML = marked.parse(replaced);
  el.querySelectorAll(".mermaid[data-idx]").forEach((d) => {
    d.textContent = blocks[Number(d.dataset.idx)];
  });
  mermaid.initialize(MERMAID_CONFIG);
  await mermaid.run({ nodes: el.querySelectorAll(".mermaid") });
}

function scoreClass(s) {
  if (s >= 0.7) return "hi";
  if (s >= 0.4) return "mid";
  return "lo";
}

const SRC_LABEL = { vector: "語意", hyde: "推想", bm25: "字面" };
