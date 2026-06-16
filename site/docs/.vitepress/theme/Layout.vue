<script setup>
import DefaultTheme from "vitepress/theme";
import { useData, useRouter } from "vitepress";
import { computed, onMounted } from "vue";
const { Layout } = DefaultTheme;
const { lang } = useData();
const isEn = computed(() => lang.value && lang.value.startsWith("en"));

// Mermaid renders async — when you land on a #anchor, diagrams above the target
// finish drawing afterwards and shift layout, leaving you at the wrong section.
// React to the actual layout change: re-scroll to the target every time the
// page height changes (each diagram finishing), for ~3s, then stop so we don't
// fight the user. scroll-margin-top (brand.css) keeps the heading clear of nav.
function rescrollToHash() {
  if (typeof window === "undefined" || !location.hash) return;
  const id = decodeURIComponent(location.hash.slice(1));
  const scroll = () => {
    const el = document.getElementById(id);
    if (el) el.scrollIntoView();
  };
  scroll();
  if (typeof ResizeObserver === "undefined") return;
  const ro = new ResizeObserver(scroll);
  ro.observe(document.body);
  setTimeout(() => ro.disconnect(), 3000);
}

if (typeof window !== "undefined") {
  const router = useRouter();
  const prev = router.onAfterRouteChanged;
  router.onAfterRouteChanged = (to) => {
    prev && prev(to);
    rescrollToHash();
  };
  onMounted(rescrollToHash);
}
</script>

<template>
  <Layout>
    <!-- Renders between the hero and the feature cards on the home page. -->
    <template #home-hero-after>
      <div class="why-now-wrap">
        <div class="why-now" v-if="isEn">
          <b>Why now:</b> Netflix is betting on generative-AI
          "find a film in one sentence" — with examples like "a crime drama like Money Heist
          but not too bloody" and "an easy comedy for a relaxing weekend", <b>almost exactly the
          queries here</b>. The difference: they're <b>testing</b> it; this hackathon prototype
          already <b>built</b> it.
          <span class="src"> — Shang Media, 2026-06-05</span>
        </div>
        <div class="why-now" v-else>
          <b>為什麼是現在:</b>Netflix 正押注生成式 AI 的「一句話找片」——
          舉例「想看類似《紙房子》的犯罪劇,但不要太血腥」「適合週末放鬆的輕鬆喜劇」,<b>幾乎就是這裡的查詢方式</b>。
          差別是他們在<b>測試</b>,這個 hackathon 原型已經<b>做出來了</b>。
          <span class="src"> —— 商傳媒,2026-06-05</span>
        </div>
      </div>
    </template>
  </Layout>
</template>
