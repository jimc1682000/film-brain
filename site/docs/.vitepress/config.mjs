import fs from "node:fs";
import path from "node:path";
import { withMermaid } from "vitepress-plugin-mermaid";
import { cjkTokenize } from "./cjk-tokenize.js";

// One source of truth for the three groups, reused by nav dropdowns + sidebar.
const zhGroups = [
  { text: "產品", items: [
    { text: "運作原理", link: "/brief" },
    { text: "語意搜尋", link: "/query" },
    { text: "自動標籤", link: "/auto-tag" },
    { text: "誠實匹配", link: "/honest" },
    { text: "可解釋結果", link: "/explainable" },
    { text: "獎項追蹤", link: "/awards" },
    { text: "自評閉環", link: "/self-eval" },
    { text: "搜尋回放", link: "/search" },
  ]},
  { text: "工程", items: [
    { text: "技術決策", link: "/decisions" },
    { text: "評測迭代", link: "/eval" },
    { text: "除錯實錄", link: "/mj-case" },
    { text: "API 文件", link: "/api" },
  ]},
  { text: "協作 & 方法", items: [
    { text: "協作方式", link: "/collab" },
    { text: "凝聚想法", link: "/ideation" },
    { text: "可帶走 Prompt", link: "/prompts" },
    { text: "學到的事", link: "/lessons" },
  ]},
];

const enGroups = [
  { text: "Product", items: [
    { text: "How it works", link: "/en/brief" },
    { text: "Semantic search", link: "/en/query" },
    { text: "Auto-tagging", link: "/en/auto-tag" },
    { text: "Honest match", link: "/en/honest" },
    { text: "Explainable results", link: "/en/explainable" },
    { text: "Award tracking", link: "/en/awards" },
    { text: "Self-eval loop", link: "/en/self-eval" },
    { text: "Search replay", link: "/en/search" },
  ]},
  { text: "Engineering", items: [
    { text: "Decisions", link: "/en/decisions" },
    { text: "Eval", link: "/en/eval" },
    { text: "Debugging log", link: "/en/mj-case" },
    { text: "API reference", link: "/en/api" },
  ]},
  { text: "Collaboration & method", items: [
    { text: "Working with AI", link: "/en/collab" },
    { text: "Converging ideas", link: "/en/ideation" },
    { text: "Take-away prompt", link: "/en/prompts" },
    { text: "Lessons learned", link: "/en/lessons" },
  ]},
];

// CSP: VitePress serializes every function in site config into each page and
// revives it with `new Function()`, which a CSP without 'unsafe-eval' blocks —
// leaving __VP_SITE_DATA__ undefined and breaking mermaid, nav and search.
// The serializer walks Object.keys(), so a non-enumerable `options` stays out
// of the pages while the build-time indexer (direct property read) still sees
// it. The browser gets the same tokenizer back in theme/index.js.
const miniSearch = { searchOptions: { fuzzy: 0.2, prefix: true, combineWith: "AND" } };
Object.defineProperty(miniSearch, "options", { value: { tokenize: cjkTokenize }, enumerable: false });

// Fail the build if any page would still need eval to boot (e.g. a function
// added to config, or a VitePress upgrade changing how options are read).
function assertNoSerializedFunctions(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const file = path.join(dir, entry.name);
    if (entry.isDirectory()) assertNoSerializedFunctions(file);
    else if (file.endsWith(".html") && fs.readFileSync(file, "utf8").includes("_vp-fn_")) {
      throw new Error(`${file} contains a serialized function (_vp-fn_); it needs 'unsafe-eval' under CSP`);
    }
  }
}

export default withMermaid({
  buildEnd: (siteConfig) => assertNoSerializedFunctions(siteConfig.outDir),
  base: "/film-brain/",
  title: "Film Brain",
  description: "AI 片庫大腦 — 語意搜尋、誠實匹配、可解釋結果",
  appearance: "dark",
  cleanUrls: true,
  head: [["link", { rel: "icon", type: "image/svg+xml", href: "/film-brain/favicon.svg" }]],

  themeConfig: {
    search: {
      provider: "local",
      options: {
        miniSearch,
      },
    },
    socialLinks: [{ icon: "github", link: "https://github.com/jimc1682000/film-brain" }],
  },

  locales: {
    root: {
      label: "中文",
      lang: "zh-Hant",
      themeConfig: {
        nav: [{ text: "總覽", link: "/" }, ...zhGroups],
        sidebar: zhGroups,
        outline: { level: [2, 3], label: "目錄" },
        docFooter: { prev: "上一頁", next: "下一頁" },
      },
    },
    en: {
      label: "English",
      lang: "en",
      themeConfig: {
        nav: [{ text: "Overview", link: "/en/" }, ...enGroups],
        sidebar: { "/en/": enGroups },
        outline: { level: [2, 3], label: "On this page" },
      },
    },
  },

  mermaid: {
    theme: "dark",
    securityLevel: "loose",
    flowchart: { htmlLabels: true, padding: 12 },
    themeVariables: { fontFamily: "sans-serif" },
  },
});
