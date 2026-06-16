"""Reciprocal Rank Fusion for combining multiple ranked recall lists.

RRF merges lists by position, not by raw scores (which live on different
scales — cosine vs BM25), so vector + lexical recall can be combined without
calibration. The optional top_bonus nudges the very top of each list to protect
high-precision exact matches (qmd's top-rank bonus idea).
"""

from __future__ import annotations


def rrf_fuse(
    ranked_lists: list[list[str]],
    *,
    k: int = 60,
    weights: list[float] | None = None,
    top_bonus: tuple[float, ...] = (),
) -> list[tuple[str, float]]:
    """Fuse ranked id lists. Returns [(id, score)] best-first.

    score(id) = Σ_lists weight * 1/(k + rank + 1), rank 0-based, plus top_bonus
    on the leading positions of each list.
    """
    if weights is None:
        weights = [1.0] * len(ranked_lists)
    scores: dict[str, float] = {}
    for lst, w in zip(ranked_lists, weights, strict=False):
        for rank, fid in enumerate(lst):
            scores[fid] = scores.get(fid, 0.0) + w * (1.0 / (k + rank + 1))
            if rank < len(top_bonus):
                scores[fid] += top_bonus[rank]
    return sorted(scores.items(), key=lambda kv: -kv[1])
