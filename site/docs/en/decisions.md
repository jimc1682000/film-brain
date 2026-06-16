# Technical Decisions — stack, collaboration, and pivots

## Constraints first — every choice grew out of these

- **Timeline**: hackathon scale — measured in weeks, not quarters
- **Team**: four people — 1 PM, 2 editors, 1 engineer; engineering itself ran as one person + AI
- **Hardware**: the demo box is a 2-vCPU, GPU-less small VPS; budget $0
- **Content**: a Chinese film library — every model choice starts with "is it good at Chinese?"

Guiding principle: **simplify the choices — spend the entire complexity budget on "how the service integrates AI", and pick the most boring, least likely-to-page-you infrastructure for everything else** (boring technology). The point was never stacking tech; it's how search, tagging and operations workflows change once AI is in the loop.

## Initial stack — what and why

| Choice | Why |
|---|---|
| **Python + FastAPI** | Auto-generated API docs (Swagger) — zero-cost interface conversations with PM / editors; one language across the project, same ecosystem as the AI tooling |
| **NiceGUI** (pure-Python UI) | The framework absorbs the frontend details — no second tech stack; one person covers full stack |
| **SQLite** | Simple, right-sized for a small project — zero ops, a single portable file |
| **Qdrant** | Used it before; active community, easy to find answers when things break |
| **bge-m3** (embedding) | Mainstream open-source embedding model, strong in Chinese, runs fully local |
| **LLM: free cloud first, local on tap** (LLM) | The heavy lifting (embedding, rerank, eval judge) all stays local; only the large-model LLM reasoning (tagging films, reading queries) goes to the cloud. The route looped: cloud free tier → too unstable (429s, models delisted), briefly went fully-local Ollama → then back to OpenRouter free (auto-switches among available models), keeping "one switch back to a fully-local LLM" as the fallback — zero cost, yet not locked to any single cloud |
| **Docker Compose + Traefik @ VPS** | Own VPS at zero marginal cost, public URL, no cold starts; four services in one compose file |

## How we collaborated — not vibe coding

Engineering ran as "one person + AI", but every step had verifiable discipline: AI produces, the human steers and gates:

| Mechanism | Practice | What it prevents |
|---|---|---|
| **ADRs (decision records)** | every major pivot gets one: context, options, reasons, impact (8 in total) | amnesia about "why we did it this way"; AI bulldozing old decisions during refactors |
| **Scored eval loop** | 45 real queries auto-scored; every change measured — winners stay, losers roll back (see Eval) | tuning by vibes; the "it looks better" illusion |
| **Commit gates** | lint, formatting, secret scanning, tests — even "no hardcoded UI copy" is enforced automatically | the low-grade mistakes AI sneaks in when producing code at volume |
| **Git-based deploys** | always commit → push → server pulls → rebuild; no editing files on the box | drift between demo box and repo; incidents with no rollback or trace |
| **Human review loop** | AI suggests tags → editors approve/reject each → feedback accumulates into a wiki | AI's taste problems sliding through silently |
| **AI-native workflows** | operational chores (importing films, ingesting awards, library health checks) packaged as natural-language commands (agent skills) | repetitive ops eating engineering time |

## The mid-project pivots (ADR digest)

<details class="note"><summary>① Pure vector search → Hybrid (BM25 + vectors + RRF + rerank)</summary>

**Pain:** with cosine alone, scores huddled in a narrow 0.55–0.65 band and proper nouns (titles) kept missing.

**Pivot:** following the open-source project qmd — lexical (BM25) and semantic (vector) tracks recall independently, RRF fuses the rankings, a reranker finishes.

**Why:** the two retrievals fail in complementary ways — lexical rescues proper nouns, semantic rescues intent; fusing by rank instead of score sidesteps incomparable score scales.
</details>

<details class="note"><summary>② Hand-written keyword parser → LLM query understanding (with a safety net)</summary>

**Pain:** the hand-built keyword table covered 3 dimensions; sentences like "what heals a brutal breakup" had no chance.

**Pivot:** one LLM call expands the query into: 14-dim tag signals + an imagined plot (HyDE) + retrieval keywords.

**Why:** LLMs understand meaning but fail (rate limits / bad output) — so the design falls back to the keyword parser whenever the LLM fails. Search never goes dark because the AI did. Caching makes repeat queries free.
</details>

<details class="note"><summary>③ Knobs hardcoded in code → hot-reloaded config + explainable output</summary>

**Pain:** changing one weight meant code changes and rebuilds; search was a black box.

**Pivot:** all weights/thresholds in one JSON, effective on save; every result carries "why it surfaced" (matched conditions + retrieval path).

**Why:** tuning speed = number of experiments; explainability serves debugging and product trust at once.
</details>

<details class="note"><summary>④ Hand-set weights → self-correcting eval loop (LLM-as-judge)</summary>

**Pain:** config values were educated guesses; a demo has no real traffic, hence no click data to learn from.

**Pivot:** let an LLM judge score search results, build automated evals — the system measures and tunes itself without traffic.

**Why:** when there's no signal, manufacture signal. This loop powered the entire v1–v8 tuning story (see Eval).
</details>

<details class="note"><summary>⑤ The trench series: stabilizing the local LLM judge (a four-ADR saga)</summary>

Moving the judge to a local model tripped a chain of pitfalls, one ADR each:

- **Cancellation never reached the server** → switch LLM calls to streaming, so client disconnects can stop runaway generation
- **Hitting the model mid-warmup** → first a workaround, then dissecting how a mature open-source agent talks to the same service — settled on "SDK + retries absorb the warmup". Borrowing beats reinventing.
- **Responses mysteriously empty** → not a crash: the thinking-style model spent its whole output budget on thinking; raising the budget fixed it

**Why (the series' shared lesson):** chase infrastructure problems until you find how others solved them — don't settle for the first workaround that runs. Every misdiagnosis was recorded in the ADRs.
</details>

::: info
The full ADRs (including the overturned misdiagnoses) live in the private repository; this page is the public digest.
:::
