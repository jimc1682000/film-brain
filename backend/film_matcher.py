"""Fuzzy match arbitrary film titles against the CATCHPLAY+ library.

Pulled out of `award_manager` because matching is a generic concern —
nothing about it is award-specific. Callers feed in any title (and an
optional alternate spelling) and get back the best `(film_id, title,
score)` triple in our films table.

Heuristics (in order):

1. Exact normalised match → score 1.0.
2. Short titles (<5 normalised chars) — exact only; avoids "Ari" ≈
   "Maria" or "Drive" ≈ "F1" false positives.
3. One is a normalised substring of the other AND the length ratio is
   ≥ 0.75 → score 0.95 (handles 進行曲 vs 進行曲：序章 style suffixes).
4. Else fall back to SequenceMatcher ratio.

Callers compare the returned score against `MATCH_THRESHOLD` themselves
to decide whether to actually link the row.
"""

from __future__ import annotations

import re
import sqlite3
from difflib import SequenceMatcher

MATCH_THRESHOLD = 0.93


def _normalise_title(t: str) -> str:
    if not t:
        return ""
    s = t.lower()
    return re.sub(r"[\s\-\:\.\,\'\"’！!?？（）()【】「」]", "", s)


def _title_pair_score(ft_norm: str, cand_norm: str) -> float:
    """Similarity in [0,1] for two normalised titles (heuristics 1-4 in the
    module docstring). Returns 0.0 to skip a pair — empty, or a short
    (<5-char) non-exact pair that would false-match (Ari ≈ Maria / Drive ≈ F1).
    A 0.0 never beats the 0.0-initialised best, so skip == no-update.
    """
    if not ft_norm or not cand_norm:
        return 0.0
    if ft_norm == cand_norm:
        return 1.0
    if min(len(ft_norm), len(cand_norm)) < 5:
        return 0.0  # short titles: exact only
    if (ft_norm in cand_norm or cand_norm in ft_norm) and (
        min(len(ft_norm), len(cand_norm)) / max(len(ft_norm), len(cand_norm)) >= 0.75
    ):
        return 0.95
    return SequenceMatcher(None, ft_norm, cand_norm).ratio()


def find_film_match(
    conn: sqlite3.Connection, primary_title: str, alt_title: str | None = None
) -> tuple[str | None, str | None, float]:
    """Return (film_id, matched_title, score).

    Score in [0.0, 1.0]; threshold check is upstream's responsibility so
    callers can express looser policies (e.g. relink scripts that accept
    0.9 with manual review).
    """
    candidates = [c for c in (primary_title, alt_title) if c]
    if not candidates:
        return None, None, 0.0

    rows = conn.execute("SELECT film_id, title_zh, title_en FROM films").fetchall()

    best_id: str | None = None
    best_title: str | None = None
    best_score: float = 0.0
    norm_cands = [_normalise_title(c) for c in candidates]

    for row in rows:
        for film_title in (row["title_zh"], row["title_en"]):
            if not film_title:
                continue
            ft_norm = _normalise_title(film_title)
            for cand_norm in norm_cands:
                score = _title_pair_score(ft_norm, cand_norm)
                if score > best_score:
                    best_score = score
                    best_id = row["film_id"]
                    best_title = film_title

    return best_id, best_title, best_score
