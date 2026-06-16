"""Unit tests for RRF fusion."""

from backend.services.fusion import rrf_fuse


def test_doc_in_both_lists_outranks_single_list():
    vec = ["a", "b", "c"]
    bm = ["b", "d", "e"]
    fused = dict(rrf_fuse([vec, bm]))
    # b appears in both → highest fused score
    assert max(fused, key=fused.get) == "b"


def test_weights_bias_toward_heavier_list():
    vec = ["x"]
    bm = ["y"]
    fused = dict(rrf_fuse([vec, bm], weights=[2.0, 1.0]))
    assert fused["x"] > fused["y"]


def test_top_bonus_lifts_leading_positions():
    a = ["p", "q"]
    b = ["q", "p"]
    # Without bonus p and q tie; bonus on rank 0 breaks toward each list head.
    fused = dict(rrf_fuse([a, b], top_bonus=(0.5,)))
    # p is rank0 in a, q is rank0 in b → both get one bonus; still symmetric.
    assert round(fused["p"], 6) == round(fused["q"], 6)


def test_empty_lists():
    assert rrf_fuse([[], []]) == []
