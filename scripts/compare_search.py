"""Compare two search variants with statistical significance (ADR 0004).

Runs the LLM-judge eval for two variants over the same query set, then uses
ranx.compare (paired Student t-test by default) to say whether the nDCG@k
difference is *significant* — not just noise. This is the rigour the bare
mean nDCG lacks (the judge has ~±0.04 run-to-run variance).

Variants:
  --variant ce        : cross-encoder OFF vs ON (same config) — "does CE help?"
  --variant config    : current search-config.json vs a baseline config file
                        (--baseline path) — "is the tuned config better?"

Shares one qrels = union of judged (query, film) across both runs; each run
scores the films it returned (others absent). Example:

    EMBEDDING_BACKEND=sentence-transformers EMBEDDING_MODEL=BAAI/bge-m3 \\
      QDRANT_HOST=localhost LLM_BACKEND=openrouter \\
      OPENROUTER_API_BASE=http://localhost:13305/api/v1 \\
      OPENROUTER_PRIMARY_MODEL=moe-q4 python -m scripts.compare_search --variant ce
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

try:
    from ranx import Qrels, Run, compare
except ModuleNotFoundError as e:  # eval-only dep, kept out of the runtime image
    raise SystemExit(
        "ranx not installed — eval deps are separate from the runtime image.\n"
        "Install them with:  pip install -r requirements-eval.txt"
    ) from e

from backend.services import search_config
from scripts.eval_search import run_eval
from scripts.tune_search import _inject

REPORT_PATH = Path("docs/reports/compare-latest.json")


async def _collect(k: int, rerank: bool) -> tuple[dict, dict]:
    qrels, run, _judged, _failed = await run_eval(k, rerank)
    return qrels, run


def _merge_qrels(*qrels_list: dict) -> dict:
    merged: dict[str, dict[str, int]] = {}
    for qr in qrels_list:
        for q, docs in qr.items():
            merged.setdefault(q, {}).update(docs)
    return merged


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=["ce", "config"], default="ce")
    ap.add_argument("--baseline", help="baseline search-config.json for --variant config")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--max-p", type=float, default=0.05)
    args = ap.parse_args()

    if args.variant == "ce":
        name_a, name_b = "CE-off", "CE-on"
        qr_a, run_a = asyncio.run(_collect(args.k, rerank=False))
        qr_b, run_b = asyncio.run(_collect(args.k, rerank=True))
    else:
        name_a, name_b = "baseline", "tuned"
        base_cfg = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        _inject(base_cfg)
        qr_a, run_a = asyncio.run(_collect(args.k, rerank=False))
        search_config._cache = {"mtime": None, "data": search_config._DEFAULTS}  # reload tuned
        qr_b, run_b = asyncio.run(_collect(args.k, rerank=False))

    qrels = Qrels(_merge_qrels(qr_a, qr_b))
    runs = [Run(run_a, name=name_a), Run(run_b, name=name_b)]
    report = compare(
        qrels,
        runs,
        metrics=[f"ndcg@{args.k}", f"map@{args.k}"],
        max_p=args.max_p,
        stat_test="student",
    )
    print(report)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(
            {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "variant": args.variant,
                "k": args.k,
                "max_p": args.max_p,
                "result": report.to_dict() if hasattr(report, "to_dict") else str(report),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"\nreport → {REPORT_PATH}")


if __name__ == "__main__":
    main()
