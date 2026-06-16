<script setup>
import { ref, onMounted } from "vue";

const data = ref({});
const active = ref("");
const SRC = { vector: "語意", hyde: "推想", bm25: "字面" };

onMounted(async () => {
  try {
    const r = await fetch(import.meta.env.BASE_URL + "data/searches.json");
    data.value = await r.json();
    active.value = Object.keys(data.value)[0];
  } catch (e) {
    console.error(e);
  }
});

function scoreClass(s) {
  return s >= 0.7 ? "hi" : s >= 0.4 ? "mid" : "lo";
}

// Rank tiers — top results render larger, mirroring the app's film_list.
function tier(i) {
  return i < 3 ? "t1" : i < 7 ? "t2" : "t3";
}

// One reason line, same shape as the app's _reason(): the matched preference
// tags (bracketed) plus how it was found (語意/推想/字面). No separate tag
// chips on the card face — the bubble stays clean, like the app.
function reasonText(r) {
  const ex = r.explain || {};
  const srcs = (ex.sources || []).filter((s) => SRC[s]).map((s) => SRC[s]);
  const prefs = (ex.matched_prefs || []).slice(0, 4);
  if (prefs.length) {
    const prefix = (ex.sources || []).length ? "符合" : "共同";
    let txt = prefix + " " + prefs.map((p) => `[${p}]`).join("");
    if (srcs.length) txt += " · " + srcs.join("+");
    return txt;
  }
  return srcs.length ? "命中 " + srcs.join("+") : "";
}
</script>

<template>
  <div class="sr">
    <div class="chips">
      <button
        v-for="q in Object.keys(data)"
        :key="q"
        :class="['chip', { on: q === active }]"
        @click="active = q"
      >{{ q }}</button>
    </div>

    <div v-if="data[active]">
      <div v-if="data[active].understanding?.confidence === 'high'" class="conf hi">
        ✅ 此搜尋:高度相關
      </div>
      <div v-else-if="data[active].understanding?.confidence === 'mid'" class="conf mid">
        ◐ 此搜尋:部分相關,以下依接近程度排序
      </div>
      <div v-else-if="data[active].understanding?.confidence === 'low'" class="conf lo">
        ⚠ 片庫中沒有高度相關的結果,以下為 AI 語意聯想,分數僅供參考
      </div>
      <div class="understanding">
        <span class="lbl">🔎 AI 怎麼理解你</span>
        <span v-for="f in data[active].understanding?.filters || []" :key="f" class="tag">{{ f }}</span>
        <span v-if="data[active].understanding?.keywords?.length" class="kw">
          關鍵字: {{ data[active].understanding.keywords.join("、") }}
        </span>
        <div v-if="data[active].understanding?.hyde_text" class="hyde">
          AI 推想劇情:「{{ data[active].understanding.hyde_text }}」
        </div>
      </div>

      <!-- Bubble result wall — faithfully mirrors the app's "泡泡" film_list:
           rank-tiered sizes (top 3 large → smaller), clean poster face with
           only a dark score badge; title + match-reason reveal on HOVER, not
           always-on; no tag chips on the card. -->
      <div class="fl-wall">
        <div
          v-for="(r, i) in data[active].results"
          :key="r.film_id"
          :class="['fl-cell', tier(i)]"
        >
          <div class="fl-p">
            <img v-if="r.poster_url" :src="r.poster_url" :alt="r.title_zh" loading="lazy" />
            <span :class="['fl-badge', scoreClass(r.score)]">{{ Math.round(r.score * 100) }}%</span>
            <div class="fl-ov">
              <div class="t">{{ r.title_zh }}</div>
              <div class="w">{{ reasonText(r) }}</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chips { display: flex; gap: 10px; flex-wrap: wrap; margin: 18px 0; }
.chip { background: var(--vp-c-bg-soft); color: var(--vp-c-text-1); border: 1px solid var(--vp-c-divider); border-radius: 999px; padding: 8px 16px; cursor: pointer; font-size: .9rem; }
.chip:hover { border-color: var(--vp-c-brand-1); color: var(--vp-c-brand-1); }
.chip.on { background: var(--vp-c-brand-1); color: #000; border-color: var(--vp-c-brand-1); font-weight: 600; }
.conf { border-radius: 8px; padding: 10px 14px; margin: 14px 0; font-size: .92rem; font-weight: 600; }
.conf.hi { border: 1px solid #1ac130; background: rgba(26,193,48,.10); color: #1ac130; }
.conf.mid { border: 1px solid #f2a93b; background: rgba(242,169,59,.10); color: #f2a93b; }
.conf.lo { border: 1px solid #f26f21; background: rgba(242,111,33,.10); color: #f26f21; }
.understanding { border: 1px solid #00a3d9; background: rgba(0,163,217,.08); border-radius: 8px; padding: 10px 14px; margin: 14px 0; font-size: .9rem; }
.understanding .lbl { color: #00a3d9; font-weight: 700; margin-right: 6px; }
.understanding .kw { color: var(--vp-c-text-2); }
.understanding .hyde { color: var(--vp-c-text-2); margin-top: 6px; }
.tag { display: inline-block; border: 1px solid #00a3d9; color: #00a3d9; border-radius: 4px; padding: 0 6px; font-size: .78rem; margin: 0 3px 3px 0; }

/* Rank-tiered bubble wall — same flex-basis tiers as the app's film_list. */
.fl-wall { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 18px; }
.fl-cell { flex-grow: 0; flex-shrink: 0; min-width: 0; }
.fl-cell.t1 { flex-basis: calc(33.333% - 8px); }
.fl-cell.t2 { flex-basis: calc(25% - 9px); }
.fl-cell.t3 { flex-basis: calc(16.666% - 10px); }
@media (max-width: 560px) {
  .fl-wall { gap: 8px; }
  .fl-cell.t1 { flex-basis: 100%; }
  .fl-cell.t2, .fl-cell.t3 { flex-basis: calc(50% - 4px); }
}
.fl-p { position: relative; aspect-ratio: 16/9; border-radius: 12px; overflow: hidden; border: 1px solid #262626; background: #111;
  transition: border-color .18s ease, box-shadow .18s ease; }
.fl-p img { width: 100%; height: 100%; object-fit: cover; display: block; }
/* One dark translucent badge — the poster never tints it (no colour bleed);
   the score rides on opaque brand-family colour. Same recipe as the app. */
.fl-badge { position: absolute; top: 6px; right: 6px; z-index: 3; padding: 2px 7px; border-radius: 7px;
  background: rgba(16,16,16,.74); backdrop-filter: blur(3px); font-size: .62rem; font-weight: 800;
  letter-spacing: .2px; line-height: 1.35; text-shadow: 0 1px 2px rgba(0,0,0,.5); }
.fl-badge.hi { color: #6ed496; } .fl-badge.mid { color: #e8c45e; } .fl-badge.lo { color: #e0655c; }
/* Overlay hidden by default → reveal on hover, exactly like the app. */
.fl-ov { position: absolute; inset: 0; display: flex; flex-direction: column; justify-content: flex-end;
  padding: 10px 12px; background: linear-gradient(transparent 35%, rgba(0,0,0,.9)); opacity: 0;
  transition: opacity .18s ease; }
.fl-ov .t { font-weight: 700; font-size: .9rem; line-height: 1.2; color: #fff; }
.fl-ov .w { color: #ccc; font-size: .72rem; margin-top: 4px; }
.fl-cell:hover .fl-ov { opacity: 1; }
.fl-cell:hover .fl-p { border-color: rgba(242,111,33,.6); box-shadow: 0 12px 30px rgba(0,0,0,.55); }
</style>
