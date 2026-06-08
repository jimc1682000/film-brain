import{_ as s,o as n,c as p,a1 as e}from"./chunks/framework.CqITaWnH.js";const d=JSON.parse('{"title":"可帶走的 Prompt","description":"","frontmatter":{},"headers":[],"relativePath":"prompts.md","filePath":"prompts.md"}'),t={name:"prompts.md"};function l(o,a,i,r,c,h){return n(),p("div",null,[...a[0]||(a[0]=[e(`<h1 id="可帶走的-prompt" tabindex="-1">可帶走的 Prompt <a class="header-anchor" href="#可帶走的-prompt" aria-label="Permalink to &quot;可帶走的 Prompt&quot;">​</a></h1><p>AI-native 工作流的一個切面:不是每個功能都得做成系統。有些能力可以直接做成一段<strong>可複製的 prompt</strong> — 編輯貼進任何 chatbot(ChatGPT / Claude / Gemini)就能用,零安裝、零後端。</p><blockquote><p>VitePress 程式碼區塊<strong>內建複製按鈕</strong>(右上角 hover 出現)— 不用自己寫。</p></blockquote><p>註:這裡只放<strong>代表性</strong>的維度與標籤當示範;團隊實際使用的是完整 14 維、四百多個標籤的版本(內部文件,不公開)。重點是展示「把判斷力封裝成可攜 prompt」這個做法,不是公開分類體系本身。</p><h2 id="電影自動標籤-prompt" tabindex="-1">電影自動標籤 prompt <a class="header-anchor" href="#電影自動標籤-prompt" aria-label="Permalink to &quot;電影自動標籤 prompt&quot;">​</a></h2><p>點右上角複製,貼到任何 chatbot,把最後兩行換成你的片名與劇情即可。</p><div class="language-text vp-adaptive-theme"><button title="Copy Code" class="copy"></button><span class="lang">text</span><pre class="shiki shiki-themes github-light github-dark vp-code" tabindex="0"><code><span class="line"><span>你是電影標籤助手。我會給你一部片的片名與劇情,你要從下方維度選出貼切的標籤。</span></span>
<span class="line"><span></span></span>
<span class="line"><span>【規則】</span></span>
<span class="line"><span>1. 一部片可橫跨多個維度、每維可多個標籤。</span></span>
<span class="line"><span>2. 優先用清單內的標籤;清單沒有但明顯合理的,可自行補上並標註「(延伸)」。</span></span>
<span class="line"><span>3. 特別注意「情緒」維度 — 有情緒/氛圍訊號務必選。</span></span>
<span class="line"><span>4. 先給一句整體判斷,再逐條輸出:「維度 · 標籤 — 一句理由」。</span></span>
<span class="line"><span></span></span>
<span class="line"><span>【維度與代表標籤(示範,可合理延伸)】</span></span>
<span class="line"><span>- 類型:喜劇、劇情、動作、愛情、恐怖、科幻、犯罪、紀錄片…</span></span>
<span class="line"><span>- 情緒:療癒、催淚、燒腦、紓壓、溫馨、黑暗、浪漫、虐戀…</span></span>
<span class="line"><span>- 主題:復仇、成長、家庭羈絆、職場、求生、東山再起…</span></span>
<span class="line"><span>- 場景:監獄、法庭、太空、水下、鬼屋…</span></span>
<span class="line"><span>- 年代:古裝、二戰、未來世界、英式古裝…</span></span>
<span class="line"><span>- 受眾:闔家觀賞、青少年、成人、銀髮族…</span></span>
<span class="line"><span></span></span>
<span class="line"><span>【待標記影片】</span></span>
<span class="line"><span>片名:(填這裡)</span></span>
<span class="line"><span>劇情:(填這裡)</span></span></code></pre></div><h2 id="用法" tabindex="-1">用法 <a class="header-anchor" href="#用法" aria-label="Permalink to &quot;用法&quot;">​</a></h2><ul><li>劇情貼片商簡介或網路上的劇情大綱都行 — 越完整,標得越準。</li><li>只想要某幾維(例如只要情緒+主題),在規則區加一句「只輸出 X、Y 維度」。</li><li>想要更嚴格?把規則 2 改成「只能用清單內的標籤,不可自創」。</li></ul><h2 id="為什麼這樣做" tabindex="-1">為什麼這樣做 <a class="header-anchor" href="#為什麼這樣做" aria-label="Permalink to &quot;為什麼這樣做&quot;">​</a></h2><p>產品本體有完整的後台自動標籤(會用全套分類做白名單驗證、寫回片庫)。但編輯很多時候只是想「快速試標一部片」,不想開後台。把這個需求封裝成一段 prompt,等於把 AI 能力<strong>下放到每個人手邊的 chatbot</strong> — 這就是 AI-native 工作流的精神:能力跟著人走,而不是綁在一個系統裡。</p><p>想知道「把判斷力寫成好 prompt」的通用心法?見<a href="/film-brain/vp-preview/collab">協作方式</a>。</p>`,12)])])}const u=s(t,[["render",l]]);export{d as __pageData,u as default};
