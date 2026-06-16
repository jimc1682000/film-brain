"""Cloud tagging health gate — circuit breaker + backend selection."""

import backend.llm_client as L
from backend.config import settings


def _reset(monkeypatch, *, cloud="gemini", local="ollama", key="k"):
    monkeypatch.setattr(settings, "tagging_cloud_backend", cloud)
    monkeypatch.setattr(settings, "llm_backend", local)
    monkeypatch.setattr(settings, "gemini_api_key", key)
    L._cloud_circuit.record_success()  # start closed


def test_selects_cloud_when_keyed_and_closed(monkeypatch):
    _reset(monkeypatch)
    assert L.cloud_tagging_available() is True
    assert L.select_tagging_backend() == "gemini"


def test_falls_to_local_without_key(monkeypatch):
    _reset(monkeypatch, key="")
    assert L.cloud_tagging_available() is False
    assert L.select_tagging_backend() == "ollama"


def test_circuit_opens_on_cloud_fallback_then_skips_cloud(monkeypatch):
    _reset(monkeypatch)
    # a cloud call that fell back to local = cloud failure → open circuit
    L.note_tagging_outcome("gemini", fell_back=True)
    assert L._cloud_circuit.is_open() is True
    assert L.select_tagging_backend() == "ollama"  # skip cloud, no retry wait
    # a later clean cloud call closes it again
    L.note_tagging_outcome("gemini", fell_back=False)
    assert L._cloud_circuit.is_open() is False
    assert L.select_tagging_backend() == "gemini"


def test_local_primary_outcome_ignored(monkeypatch):
    """When tagging ran locally (cloud==local or cloud disabled), the outcome
    says nothing about cloud health and must not toggle the circuit."""
    _reset(monkeypatch, cloud="ollama", local="ollama")
    L.note_tagging_outcome("ollama", fell_back=True)
    assert L._cloud_circuit.is_open() is False


def test_cooldown_expiry_half_opens(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(settings, "tagging_cloud_cooldown_s", 300)
    L.note_tagging_outcome("gemini", fell_back=True)
    assert L._cloud_circuit.is_open() is True
    # simulate the cooldown elapsing → circuit half-opens, cloud retried
    L._cloud_circuit._failed_at -= 301
    assert L._cloud_circuit.is_open() is False
    assert L.select_tagging_backend() == "gemini"
