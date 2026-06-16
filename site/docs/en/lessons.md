# Lessons Learned

This prototype entered the first round of an internal company AI Hackathon — and **didn't advance**.
But an honest failure is often worth writing down more than a polished demo. Here are the lessons we actually took away, from the judges' feedback and our own retrospective.

## 1. "It runs" is not "it wins"

Technical completeness was genuinely our strength: end-to-end semantic search, auto-tagging, and honest scoring, all built on our own data.
But the feedback was consistent — the problem wasn't whether it ran, it was that we **didn't explain it clearly, it wasn't interactive enough, and it didn't hit the real pain point.**

> Takeaway: the tech is just the entry ticket. Whether people can **understand it, trust it, and feel it** is what actually decides the outcome.

## 2. Showing "how the answer was reached" matters more than stacking techniques

Late in the project we piled technique on technique into the search pipeline (vectors, keywords, re-ranking, weighting…), tuned finely for individual cases —
but it got so complex that **we couldn't explain in one sentence how a result was produced.** In the deck, the "technical explanation" ended up as just an external link. Looking back, I default to expressing everything by *building one more thing* — I even turned the "explanation" into a whole showcase site and let it speak for itself, forgetting that on stage you have to say it out loud yourself.

> Takeaway: late-stage prototypes need a gate — "Can I explain in 60 seconds, to a non-expert, how this result came about?" If not, don't add that layer. **Explainability beats one more scoring layer.**

## 3. AI should *collaborate*, not be a one-shot black box

What the judges wanted to see was AI first laying out its understanding of the request, letting a human confirm or correct the direction, and *then* proceeding —
not spitting out a list from a single sentence. Our demo did the latter.

> Takeaway: put the human back in the loop — brainstorm, confirm direction, then execute. We later added exactly this "confirm direction" interaction.

## 4. The most convincing moves must be performed live, on stage

Our product could actually do the very thing the judges most wanted to see — turn "films for the weekend" into "action films for the weekend" and watch the results visibly diverge, or deliberately search a name that isn't in the library and watch the honest scoring dutifully push the score down. But none of these "make-them-go-wow" moves got performed live in the demo; the most important interaction was glossed over with one sentence in Q&A, never actually shown.

> Takeaway: the few actions that answer the question in the judge's head must be **performed live on the main stage, driven by whoever knows the product best** — never left to Q&A or reduced to a verbal description.

## 5. A niche pain has to make the room hurt first

This product solves a content editor's pain (manual classification is slow; finding the right title is like a needle in a haystack) — real, but niche. Most people in the room aren't editors and have no visceral sense of "classifying films is a chore." We treated "everyone gets this pain" as a given and jumped straight into features, so no one in the room felt any urgency on our behalf.

> Takeaway: a pain isn't useful just because it's real — it also has to **land with the people in the room**. The more niche the pain, the more you need a concrete story that lets an outsider instantly picture it and hurt on the user's behalf.

## 6. Verify key technical assumptions before concluding

We'd decided that "visual emotion is unreliable, so it has to come back to text," and dropped the image-based route — but that was intuition, not verification.
Checking the literature afterwards, we found: vision is actually usable for **coarse** sentiment (CLIP gets ~72–80% on positive/negative),
but weak on **fine-grained** multi-emotion (~40–56% even after fine-tuning) — and fine-grained emotion tags were exactly what we needed.

> Takeaway: the direction wasn't wrong, but **it was "I think" rather than "the data shows."** Spend 30 minutes verifying key assumptions — then the conclusion holds, and you can answer the judge's "how do you know?"

## What we'd do next

- **A smaller MVP**: make one flow that truly hits the pain point usable every day, then add dimensions.
- **A zero-backend prompt version**: package the core capability as a set of prompts anyone can run in their own ChatGPT / Claude — no infrastructure, and the easiest to explain.
- **Vision as an enhancement layer**: text semantics as the foundation, images handling coarse sentiment — complementary, not a replacement.

> Not advancing stings. But these lessons make the next version look more like a **product that can actually ship** — not just a demo that runs.
