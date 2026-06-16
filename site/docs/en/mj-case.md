# The Michael Jackson Incident — anatomy of a fake 100%

> On the semantic-search demo, searching "Michael Jackson" returned a war-crime
> drama as the #1 result — labeled **100%**. This page records the full path
> from symptom to root cause, to measured calibration, to the fix.

## 1. Symptom

<img src="/assets/mj-before.png" alt="Before: the Michael Jackson query returned One Battle After Another at 100%" style="max-width:360px;border:1px solid #333;border-radius:8px">

*The actual screen at the time: #1 was the war-crime epic "One Battle After Another", at 100%.*

- All 600+ titles in the library are films — **nothing related to MJ at all** (no concert films, no music documentaries)
- Yet the query returned a war-crime blockbuster showing a **100% match**
- The card's tags (thriller / crime / on-the-run) had no visible connection to Michael Jackson — users had no way to make sense of it

## 2. Root causes

**(a) The 100% was a display artifact.** Internal ranking scores are relative —
the RRF fusion score is min-max normalized, and so is the cross-encoder blend —
so "the best of this batch" always maps to 1.0. Even when the whole batch is
irrelevant, rank #1 still shows 100%.

**(b) Why this particular film?** During query expansion the LLM's HyDE step
imagines a hypothetical plot — for Michael Jackson it dreamed up a
"legendary rise-and-struggle" narrative, whose embedding drifted recall toward
the most dramatically charged award winners in the library. The expansion also
emitted an "American" tag, handing the award-laden American epic an extra
condition boost.

## 3. Measure first: can "no true match" be detected?

Take the best cosine between the **user's own query vector** (never the HyDE
vector — that one is high by construction) and the library. Good and bad
queries separate cleanly:

| Query | top-1 cosine |
|---|---|
| Korean crime thriller | 0.626 |
| heartwarming Mother's Day film | 0.603 |
| family adventure | 0.578 |
| dark comedy | 0.531 |
| something like Parasite | 0.488 |
| **Michael Jackson** | **0.371** |

→ Use that cosine to split into **three confidence tiers** (thresholds 0.52 / 0.45), hot-reloadable config.

## 4. The fix (evolved into three confidence tiers)

1. **Three tiers (cosine)**: best original-query cosine splits into high (≥0.52)
   / mid (0.45–0.52) / low (<0.45). low → honest warning "no strongly relevant
   results," while still offering best-guess results as starting points.
2. **The ceiling carries the honest signal**: high caps at 95% / mid at 68% /
   low at 42% — #1's % reflects "is it really in the library," **no more fake
   100%**; tag boosts can't push it back up.
3. **CE is not absolute quality**: later measurement showed the cross-encoder
   rates unrelated films highly too (MJ top = 0.88), so CE only decides
   *ordering*; confidence is always anchored to cosine.
4. **Min-max over the full candidate pool** (not the visible slice) → a film's
   % is stable no matter how many results you show.
5. **Explainable cards**: every result states which conditions it matched and
   how it was found (semantic / imagined / lexical).

> Full design + calibration: [Honest match](/en/honest); decision record: ADR 0009.

## 5. Outcome

The same query after the fix: it lands in the low tier, the warning banner
appears, scores cap at 42% — and because the "music / dance" condition boosts
now work properly, the films that surface are actually music-related.
**Better than before the fix.** Honesty didn't cost the experience; it made the
guesses usable.

![search demo](/assets/brief-demo-search.gif)

## Takeaways

- **Never present relative scores as absolute confidence** — after min-max, rank #1 is always 1.0
- **"One more AI scoring layer" isn't absolute quality** — the cross-encoder rates out-of-domain films highly too (MJ 0.88); trust its ordering, not its numbers
- Generative query expansion (HyDE) hallucinates drift vectors on out-of-domain
  queries; confidence must anchor on the user's own words
- Don't guess thresholds — measure real queries once and the gap speaks for itself
- "Honestly telling the user there's no match" is a feature, not a flaw
