"""Stage 3 self-correcting loop (ADR 0004).

Closes the loop: eval current config → sweep candidates → promote the winner to
the live search-config.json → re-eval → repeat, until mean LLM-judge nDCG
reaches the target (default 0.80) or it converges (a round with no improvement).

Guard rails:
  - candidates stay within the bounded search space (tune_search.SPACE)
  - a candidate is promoted only if it beats the current config by a margin
  - stops at target, at convergence, or at --max-rounds (never runs away)

Every round is appended to docs/reports/autocorrect-log.json for audit. The
judge cache makes repeated rounds cheap (relevance is per query+film).

    EMBEDDING_BACKEND=sentence-transformers EMBEDDING_MODEL=BAAI/bge-m3 \\
      QDRANT_HOST=localhost python -m scripts.autocorrect_loop \\
      [--target 0.80] [--max-rounds 5] [--samples 12] [--k 5]
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import itertools
import json
import random
import time
from pathlib import Path

from backend.services import search_config
from scripts.tune_search import SPACE, _apply, _score

LOG_PATH = Path("docs/reports/autocorrect-log.json")
MARGIN = 0.005  # require a real gain before promoting


def _promote(cfg: dict) -> None:
    """Write the winning config to the live file + force a reload next call."""
    out = copy.deepcopy(cfg)
    out.pop("_help", None)
    search_config._PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    search_config._cache = {"mtime": None, "data": search_config._DEFAULTS}  # force reload


def _sweep(base: dict, combos: list[dict], k: int) -> tuple[float, dict | None]:
    best_ndcg, best_combo = -1.0, None
    for combo in combos:
        ndcg = asyncio.run(_score(_apply(base, combo), k))
        if ndcg > best_ndcg:
            best_ndcg, best_combo = ndcg, combo
    return best_ndcg, best_combo


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=float, default=0.80)
    ap.add_argument("--max-rounds", type=int, default=5)
    ap.add_argument("--samples", type=int, default=12)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    random.seed(args.seed)

    keys = list(SPACE)
    all_combos = [dict(zip(keys, v, strict=False)) for v in itertools.product(*SPACE.values())]

    rounds = []
    reason = "max-rounds"
    for rnd in range(1, args.max_rounds + 1):
        current = copy.deepcopy(search_config.get_config())
        base_ndcg = asyncio.run(_score(current, args.k))
        print(f"\n[loop] round {rnd}: current nDCG = {base_ndcg:.4f}", flush=True)
        if base_ndcg >= args.target:
            rounds.append({"round": rnd, "ndcg": round(base_ndcg, 4), "action": "target-met"})
            reason = "target-met"
            break

        random.shuffle(all_combos)
        best_ndcg, best_combo = _sweep(current, all_combos[: args.samples], args.k)
        print(f"[loop] best candidate nDCG = {best_ndcg:.4f}  {best_combo}", flush=True)

        if best_ndcg > base_ndcg + MARGIN:
            _promote(_apply(current, best_combo))
            rounds.append(
                {
                    "round": rnd,
                    "ndcg": round(base_ndcg, 4),
                    "promoted_to": round(best_ndcg, 4),
                    "combo": best_combo,
                    "action": "promote",
                }
            )
            print(f"[loop] promoted: {base_ndcg:.4f} → {best_ndcg:.4f}", flush=True)
        else:
            rounds.append({"round": rnd, "ndcg": round(base_ndcg, 4), "action": "converged"})
            reason = "converged"
            print("[loop] no gain beyond margin — converged", flush=True)
            break

    final = asyncio.run(_score(copy.deepcopy(search_config.get_config()), args.k))
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text(
        json.dumps(
            {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "target": args.target,
                "final_ndcg": round(final, 4),
                "stop_reason": reason,
                "rounds": rounds,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n=== final nDCG = {final:.4f} (target {args.target}, stop: {reason}) ===")
    print(f"log → {LOG_PATH}")


if __name__ == "__main__":
    main()
