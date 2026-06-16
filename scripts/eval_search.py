"""Stage 1 eval (ADR 0004): score search quality with LLM-as-judge + ranx.

For each query in data/eval-queries.json: run the live search in-process, judge
the top-k with eval_judge, then score with **ranx** (the standard IR eval lib)
— canonical nDCG@k / MAP@k / MRR / P@k instead of a hand-rolled metric, and a
foundation for significance testing (ranx.compare) between runs.

Runs in-process → needs the local stack up (Qdrant populated + the
sentence-transformers embed backend). Example:

    EMBEDDING_BACKEND=sentence-transformers EMBEDDING_MODEL=BAAI/bge-m3 \\
      QDRANT_HOST=localhost python -m scripts.eval_search [--k 5] [--rerank]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from pathlib import Path

import httpx

try:
    from ranx import Qrels, Run, evaluate
except ModuleNotFoundError as e:  # eval-only dep, kept out of the runtime image
    raise SystemExit(
        "ranx not installed — eval deps are separate from the runtime image.\n"
        "Install them with:  pip install -r requirements-eval.txt"
    ) from e

from backend.config import settings
from backend.db import get_db, get_film, get_film_tags
from backend.llm_client import call_llm, select_model
from backend.models import SearchRequest
from backend.routers.search import semantic_search
from backend.services.eval_judge import judge

logger = logging.getLogger(__name__)

QUERIES_PATH = Path("data/eval-queries.json")
REPORT_DIR = Path("docs/reports")


def reset_backend() -> None:
    """Recycle the local llama-server subprocess by unloading + explicitly
    reloading the configured model.

    The Qwen3.6-MoE backend reproducibly crashes around 150 sustained LLM
    calls (multi-factor: grammar parser / MTP draft / Vulkan state). Cycling
    the subprocess every N queries clears that state. We can't rely on
    lemonade JIT-load after `/unload` — for user-imported recipes (e.g.
    `user.moe-q4`) the gateway tries to fetch model info from Hugging Face,
    hits 401, and 500s. Instead we explicitly POST `/api/v1/load` with the
    model_name so lemonade reuses the local checkpoint. No-op on
    non-lemonade backends — the endpoints just 404 and we move on.
    """
    base = settings.openrouter_api_base.rstrip("/")
    # …/v1 → drop the v1 to hit /api/v1/{unload,load} at the gateway root.
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    model = settings.openrouter_primary_model
    try:
        ru = httpx.post(f"{base}/api/v1/unload", timeout=30.0)
        rl = httpx.post(
            f"{base}/api/v1/load",
            json={"model_name": model},
            timeout=120.0,
        )
        logger.info(
            "reset_backend unload=%s load=%s (model=%s)",
            ru.status_code,
            rl.status_code,
            model,
        )
        print(
            f"  -- reset_backend unload={ru.status_code} load={rl.status_code} --",
            flush=True,
        )
    except Exception as e:
        logger.warning("reset_backend failed: %s", e)
        print(f"  -- reset_backend failed: {e} --", flush=True)


def wait_backend_ready(*, attempts: int = 40, interval: float = 3.0) -> None:
    """Block until the LLM backend answers a trivial prompt.

    A local lemonade/llama.cpp backend reports `lemonade load` success before
    its subprocess has finished warming up (allocating the KV cache); a request
    fired into that window comes back as a streamed 'Couldn't connect' error and
    poisons the run. Poll a tiny completion until one succeeds so the eval only
    starts against a live backend. No-op for always-on cloud backends (first
    call just succeeds).
    """
    last = ""
    for i in range(attempts):
        try:
            call_llm("回 ok", "ping", model=select_model(), timeout=60, meta={})
            if i:
                print(f"backend ready after {i + 1} attempts", flush=True)
            return
        except Exception as e:  # backend still warming / unreachable
            last = str(e)[:80]
            print(f"  backend not ready (attempt {i + 1}): {last}", flush=True)
            time.sleep(interval)
    raise RuntimeError(f"LLM backend not ready after {attempts} attempts: {last}")


def _film_for_judge(conn, film_id: str) -> dict:
    row = get_film(conn, film_id) or {"film_id": film_id}
    row = dict(row)
    row["tag_labels"] = [t.get("label_zh_tw") or t["tag_id"] for t in get_film_tags(conn, film_id)]
    return row


async def run_eval(k: int, rerank: bool) -> tuple[dict, dict, int, int]:
    """Return (qrels, run, judged, failed).

    qrels[q][film_id] = LLM-judge relevance (0/1/2); run[q][film_id] = search
    score. Only queries with ≥1 judged result are kept (ranx needs non-empty).
    """
    wait_backend_ready()
    # Recycle the local subprocess every N queries to avoid the ~Q25
    # cumulative-state crash. Set EVAL_RESET_EVERY=0 to disable.
    reset_every = int(os.environ.get("EVAL_RESET_EVERY", "20"))
    queries = json.loads(QUERIES_PATH.read_text(encoding="utf-8"))["queries"]
    # EVAL_QUERY_LIMIT: cap to first N queries. Lets a smaller controlled
    # subset stay under the ~150-call Vulkan accumulation wall while keeping
    # the same eval pipeline + same top_k.
    limit = int(os.environ.get("EVAL_QUERY_LIMIT", "0") or "0")
    if limit > 0:
        queries = queries[:limit]
        print(f"  -- EVAL_QUERY_LIMIT={limit}: using {len(queries)} queries --", flush=True)
    qrels: dict[str, dict[str, int]] = {}
    run: dict[str, dict[str, float]] = {}
    judged = failed = 0
    with get_db() as conn:
        for i, q in enumerate(queries):
            if reset_every and i > 0 and i % reset_every == 0:
                print(f"  -- reset backend after {i} queries --", flush=True)
                reset_backend()
                wait_backend_ready()
            resp = await semantic_search(
                SearchRequest(query=q, top_k=k, min_confidence=0.3, use_llm_rerank=rerank)
            )
            qr: dict[str, int] = {}
            rn: dict[str, float] = {}
            for r in resp.results:
                s = judge(q, _film_for_judge(conn, r.film_id))
                if s is None:
                    failed += 1
                    continue
                judged += 1
                qr[r.film_id] = s
                rn[r.film_id] = float(r.score)
            # ranx needs ≥1 relevant (rel>0) doc in qrels for the query to count.
            if qr and any(v > 0 for v in qr.values()):
                qrels[q] = qr
                run[q] = rn
            print(f"  judged={len(qr)} rel>0={sum(v > 0 for v in qr.values())}  {q}", flush=True)
    total = judged + failed
    if total and failed / total > 0.5:
        raise RuntimeError(
            f"judge failed on {failed}/{total} calls (>50%) — LLM likely rate-limited; aborting"
        )
    return qrels, run, judged, failed


def metrics(qrels: dict, run: dict, k: int) -> dict:
    """Standard IR metrics via ranx. Empty → zeros."""
    if not qrels:
        return {m: 0.0 for m in (f"ndcg@{k}", f"map@{k}", "mrr", f"precision@{k}")}
    res = evaluate(Qrels(qrels), Run(run), [f"ndcg@{k}", f"map@{k}", "mrr", f"precision@{k}"])
    return {m: round(float(v), 4) for m, v in res.items()}


def ndcg(qrels: dict, run: dict, k: int) -> float:
    """Single nDCG@k — used by the tuner/loop."""
    if not qrels:
        return 0.0
    return float(evaluate(Qrels(qrels), Run(run), f"ndcg@{k}"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--rerank", action="store_true", help="eval with cross-encoder on (slow)")
    args = ap.parse_args()

    qrels, run, judged, failed = asyncio.run(run_eval(args.k, args.rerank))
    m = metrics(qrels, run, args.k)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "k": args.k,
        "rerank": args.rerank,
        "queries_scored": len(qrels),
        "judged": judged,
        "judge_failed": failed,
        "metrics": m,
    }
    (REPORT_DIR / "eval-latest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n=== ranx metrics (k={args.k}) ===")
    for name, val in m.items():
        print(f"  {name:14} {val}")
    print(f"report → {REPORT_DIR / 'eval-latest.json'}")


if __name__ == "__main__":
    main()
