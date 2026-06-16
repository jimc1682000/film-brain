"""Stage 2 auto-tune (ADR 0004): sweep search-config knobs, pick best by eval.

Random-search over the tunable knobs in search-config.json, scoring each
candidate with the Stage-1 LLM-judge eval (nDCG). The judge cache makes this
affordable — relevance is per (query, film), independent of config, so films
seen under one config are free under the next.

Does NOT overwrite the live config: writes the winner to
data/search-config.candidate.json + a comparison report for review (Stage 3
decides whether to promote it).

    EMBEDDING_BACKEND=sentence-transformers EMBEDDING_MODEL=BAAI/bge-m3 \\
      QDRANT_HOST=localhost python -m scripts.tune_search [--k 5] [--samples 12]
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
from scripts.eval_search import ndcg, run_eval

# Search space — keep small; product is sampled, not exhausted.
SPACE: dict[str, list] = {
    # Unified weighted-boost model knobs (ADR 0004). dim weights live in
    # search-config.dimensions (tuned by hand); here we sweep the global knobs.
    "weights.vector": [1.0, 1.3],
    "weights.bm25": [1.0, 1.2, 1.5],
    "weights.hyde": [0.0, 0.2, 0.4],
    "tag_boost_scale": [0.1, 0.15, 0.25],
    "rrf_k": [10, 20, 30],
    "min_display_score": [0.05, 0.1],
}

CANDIDATE_PATH = Path("data/search-config.candidate.json")
REPORT_PATH = Path("docs/reports/tune-latest.json")


def _apply(base: dict, combo: dict) -> dict:
    cfg = copy.deepcopy(base)
    for key, val in combo.items():
        if "." in key:
            a, b = key.split(".")
            cfg.setdefault(a, {})[b] = val
        else:
            cfg[key] = val
    return cfg


def _inject(cfg: dict) -> None:
    """Force get_config() to return cfg without touching the file on disk."""
    try:
        mtime = search_config._PATH.stat().st_mtime
    except OSError:
        mtime = 0
    search_config._cache = {"mtime": mtime, "data": cfg}


async def _score(cfg: dict, k: int) -> float:
    _inject(cfg)
    qrels, run, *_ = await run_eval(k, rerank=False)
    return ndcg(qrels, run, k)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--samples", type=int, default=12)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    random.seed(args.seed)

    base = copy.deepcopy(search_config.get_config())
    keys = list(SPACE)
    combos = [dict(zip(keys, vals, strict=False)) for vals in itertools.product(*SPACE.values())]
    random.shuffle(combos)
    combos = combos[: args.samples]

    base_ndcg = asyncio.run(_score(base, args.k))
    print(f"[tune] baseline nDCG = {base_ndcg:.4f}", flush=True)

    trials = []
    for i, combo in enumerate(combos, 1):
        cfg = _apply(base, combo)
        ndcg = asyncio.run(_score(cfg, args.k))
        trials.append({"combo": combo, "ndcg": round(ndcg, 4)})
        print(f"[tune] {i}/{len(combos)} nDCG={ndcg:.4f}  {combo}", flush=True)

    trials.sort(key=lambda t: -t["ndcg"])
    best = trials[0]
    improved = best["ndcg"] > base_ndcg

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(
            {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "k": args.k,
                "baseline_ndcg": round(base_ndcg, 4),
                "best_ndcg": best["ndcg"],
                "improved": improved,
                "best_combo": best["combo"],
                "trials": trials,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    if improved:
        winner = _apply(base, best["combo"])
        winner.pop("_help", None)
        CANDIDATE_PATH.write_text(
            json.dumps(winner, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n[tune] best {best['ndcg']:.4f} > baseline {base_ndcg:.4f} → {CANDIDATE_PATH}")
    else:
        print(f"\n[tune] no improvement over baseline {base_ndcg:.4f}; keeping current config")
    print(f"report → {REPORT_PATH}")


if __name__ == "__main__":
    main()
