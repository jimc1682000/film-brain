"""LRU dict with a pinned set that is never evicted.

Both the query-expansion cache and the heavy search cache are shared, global,
and now writable by arbitrary user queries — the confirm-direction gate lets a
user generate an unbounded stream of distinct refined queries (原句 + 修正),
each one cold (it won't be re-typed by anyone else). Left unbounded the caches
grow forever; a plain cap that stops writing when full would let cold junk lock
out genuinely hot entries.

PinnedLRU caps the NON-pinned population: the oldest non-pinned entry is evicted
once that population exceeds ``maxsize``. Demo-chip queries are pinned at warmup
so they are never evicted — the stage demo always hits a warm cache no matter
how much the audience reloops.
"""

from collections import OrderedDict
from typing import Any


class PinnedLRU:
    def __init__(self, maxsize: int) -> None:
        self._maxsize = maxsize
        self._data: OrderedDict[Any, Any] = OrderedDict()
        self._pinned: set[Any] = set()

    def __contains__(self, key: Any) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def clear(self) -> None:
        self._data.clear()
        self._pinned.clear()

    def get(self, key: Any, default: Any = None) -> Any:
        if key not in self._data:
            return default
        if key not in self._pinned:
            self._data.move_to_end(key)
        return self._data[key]

    def __getitem__(self, key: Any) -> Any:
        value = self._data[key]
        if key not in self._pinned:
            self._data.move_to_end(key)
        return value

    def pin(self, key: Any) -> bool:
        """Mark an already-present key as pinned (never evicted). No-op if the
        key isn't there (e.g. its write was skipped). Returns whether it pinned."""
        if key in self._data:
            self._pinned.add(key)
            return True
        return False

    def set(self, key: Any, value: Any, *, pin: bool = False) -> None:
        self._data[key] = value
        if pin:
            self._pinned.add(key)
        else:
            self._data.move_to_end(key)
        self._evict()

    def __setitem__(self, key: Any, value: Any) -> None:
        self.set(key, value)

    def _evict(self) -> None:
        # Evict oldest non-pinned entries until the non-pinned population fits.
        while len(self._data) - len(self._pinned) > self._maxsize:
            for k in self._data:
                if k not in self._pinned:
                    del self._data[k]
                    break
            else:
                break  # everything left is pinned — nothing to evict
