import{_ as n,o as s,c as e,a1 as t}from"./chunks/framework.CqITaWnH.js";const u=JSON.parse('{"title":"A Take-away Prompt","description":"","frontmatter":{},"headers":[],"relativePath":"en/prompts.md","filePath":"en/prompts.md"}'),p={name:"en/prompts.md"};function i(o,a,l,r,c,m){return s(),e("div",null,[...a[0]||(a[0]=[t(`<h1 id="a-take-away-prompt" tabindex="-1">A Take-away Prompt <a class="header-anchor" href="#a-take-away-prompt" aria-label="Permalink to &quot;A Take-away Prompt&quot;">​</a></h1><p>One slice of an AI-native workflow: some capabilities can be packaged as a <strong>copy-paste prompt</strong> — paste into any chatbot, zero install, zero backend.</p><blockquote><p>VitePress code blocks have a <strong>built-in copy button</strong> (top-right on hover).</p></blockquote><p>Only <strong>representative</strong> dimensions/tags are shown; the team uses the full 14-dimension, 400-plus-tag version internally. The point is packaging judgment into a portable prompt, not publishing the taxonomy.</p><div class="language-text vp-adaptive-theme"><button title="Copy Code" class="copy"></button><span class="lang">text</span><pre class="shiki shiki-themes github-light github-dark vp-code" tabindex="0"><code><span class="line"><span>You are a film-tagging assistant. I&#39;ll give you a film&#39;s title and plot;</span></span>
<span class="line"><span>pick fitting tags from the dimensions below.</span></span>
<span class="line"><span></span></span>
<span class="line"><span>[Rules]</span></span>
<span class="line"><span>1. A film can span multiple dimensions, with multiple tags per dimension.</span></span>
<span class="line"><span>2. Prefer tags from the list; if something obviously fits but isn&#39;t listed, add it and mark it &quot;(extended)&quot;.</span></span>
<span class="line"><span>3. Pay special attention to the &quot;Emotion&quot; dimension — always pick one if there&#39;s a mood signal.</span></span>
<span class="line"><span>4. Give a one-line overall read first, then one line per tag: &quot;Dimension · Tag — one-line reason&quot;.</span></span>
<span class="line"><span></span></span>
<span class="line"><span>[Dimensions and representative tags (examples, extend sensibly)]</span></span>
<span class="line"><span>- Genre: comedy, drama, action, romance, horror, sci-fi, crime, documentary…</span></span>
<span class="line"><span>- Emotion: healing, tear-jerker, mind-bending, stress-relief, heartwarming, dark, romantic, toxic-romance…</span></span>
<span class="line"><span>- Theme: revenge, coming-of-age, family bonds, workplace, survival, comeback…</span></span>
<span class="line"><span>- Setting: prison, courtroom, outer space, underwater, haunted house…</span></span>
<span class="line"><span>- Era: period, WWII, future, British period…</span></span>
<span class="line"><span>- Audience: family, teens, adults, seniors…</span></span>
<span class="line"><span></span></span>
<span class="line"><span>[Film to tag]</span></span>
<span class="line"><span>Title: (fill here)</span></span>
<span class="line"><span>Plot: (fill here)</span></span></code></pre></div>`,5)])])}const g=n(p,[["render",i]]);export{u as __pageData,g as default};
