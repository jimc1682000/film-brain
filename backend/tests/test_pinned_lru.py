from backend.services.pinned_lru import PinnedLRU


def test_evicts_oldest_non_pinned():
    c = PinnedLRU(2)
    c["a"] = 1
    c["b"] = 2
    c["c"] = 3  # evicts "a" (oldest non-pinned)
    assert "a" not in c
    assert "b" in c and "c" in c


def test_get_refreshes_recency():
    c = PinnedLRU(2)
    c["a"] = 1
    c["b"] = 2
    assert c["a"] == 1  # touch "a" → now "b" is oldest
    c["c"] = 3  # evicts "b"
    assert "b" not in c
    assert "a" in c and "c" in c


def test_pinned_never_evicted():
    c = PinnedLRU(2)
    c["demo"] = "warm"
    assert c.pin("demo")
    c["x"] = 1
    c["y"] = 2
    c["z"] = 3  # non-pinned population capped at 2 → churns x/y/z, demo stays
    assert "demo" in c
    assert c["demo"] == "warm"


def test_pin_capacity_is_non_pinned_only():
    c = PinnedLRU(2)
    for k in ("p1", "p2", "p3"):
        c[k] = k
        c.pin(k)
    # all pinned — none evicted even past maxsize
    assert all(k in c for k in ("p1", "p2", "p3"))
    # non-pinned still capped on top of pins
    c["a"] = 1
    c["b"] = 2
    c["c"] = 3
    assert "a" not in c
    assert all(k in c for k in ("p1", "p2", "p3", "b", "c"))


def test_pin_missing_key_is_noop():
    c = PinnedLRU(2)
    assert c.pin("nope") is False
