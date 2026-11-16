// MiniSearch tokenizes on word boundaries — Chinese has none, so
// "運作" never matches "運作原理". Split CJK into single characters
// (indexed + queried the same way) so Chinese search works.
//
// Shared by config.mjs (build-time indexing) and theme/index.js (query-time),
// so the two sides can never drift apart.
export const cjkTokenize = (text) =>
  text
    .toLowerCase()
    .split(/[^\p{L}\p{N}]+/u)
    .flatMap((t) =>
      /[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff]/.test(t) ? Array.from(t) : t ? [t] : []
    );
