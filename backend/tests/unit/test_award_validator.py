"""Unit tests for backend.validators.award_validator.

Wikidata SPARQL is not hit live; httpx is mocked to keep tests deterministic.
"""

from unittest.mock import patch

import pytest

from backend.validators import award_validator as v


@pytest.fixture
def fake_sparql():
    """Patch the httpx client used inside query_film_awards."""

    def _make(payload: dict, status_code: int = 200):
        class FakeResp:
            def __init__(self):
                self.status_code = status_code

            def json(self):
                return payload

        class FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, *a, **kw):
                return FakeResp()

        return patch.object(v.httpx, "Client", return_value=FakeClient())

    return _make


def _sparql_binding(qid: str, label_en: str, statement: str, label_zh: str | None = None) -> dict:
    binding = {
        "award": {"value": f"http://www.wikidata.org/entity/{qid}"},
        "awardLabel": {"value": label_en},
        "statement": {"value": statement},
    }
    if label_zh:
        binding["awardLabelZh"] = {"value": label_zh}
    return binding


def test_query_film_awards_parses_bindings(fake_sparql):
    payload = {
        "results": {
            "bindings": [
                _sparql_binding("Q19020", "Academy Award for Best Picture", "nominated"),
                _sparql_binding("Q103360", "Golden Globe", "won"),
            ]
        }
    }
    with fake_sparql(payload):
        result = v.query_film_awards("tt0000001")
    assert result is not None
    assert len(result) == 2
    assert result[0]["award_qid"] == "Q19020"
    assert result[0]["is_nomination"] is True
    assert result[1]["is_nomination"] is False


def test_query_film_awards_returns_none_on_http_error(fake_sparql):
    with fake_sparql({}, status_code=500):
        result = v.query_film_awards("tt0000001")
    assert result is None


def test_query_film_awards_rejects_bad_imdb_id():
    assert v.query_film_awards("") is None
    assert v.query_film_awards("not-an-imdb-id") is None


def test_match_org_recognises_oscar_label():
    assert (
        v.match_org(
            {
                "award_qid": "Q19020",
                "award_label_en": "Academy Award for Best Picture",
                "award_label_zh": None,
                "is_nomination": True,
            }
        )
        == "oscars"
    )


def test_match_org_uses_zh_label_for_golden_horse():
    assert (
        v.match_org(
            {
                "award_qid": "Q999",
                "award_label_en": "",
                "award_label_zh": "金馬獎最佳劇情片",
                "is_nomination": False,
            }
        )
        == "golden-horse"
    )


def test_verify_returns_unknown_when_lookup_failed():
    row = {"id": 1, "matched_film_id": "f", "org_id": "cannes", "year": 2025, "tag_id": "x"}
    verdict = v.verify_nominee_row(row, None)
    assert verdict["verdict"] == "unknown"


def test_verify_returns_unknown_when_wikidata_empty():
    row = {"id": 1, "matched_film_id": "f", "org_id": "cannes", "year": 2025, "tag_id": "x"}
    verdict = v.verify_nominee_row(row, [])
    assert verdict["verdict"] == "unknown"


def test_verify_returns_verified_when_org_matches():
    row = {"id": 1, "matched_film_id": "f", "org_id": "oscars", "year": 2025, "tag_id": "x"}
    wd_awards = [
        {
            "award_qid": "Q19020",
            "award_label_en": "Academy Award for Best Picture",
            "award_label_zh": None,
            "is_nomination": True,
        }
    ]
    verdict = v.verify_nominee_row(row, wd_awards)
    assert verdict["verdict"] == "verified"


def test_verify_returns_suspicious_when_org_mismatch():
    """DB claims Cannes, Wikidata only lists Berlin → flag for human review."""
    row = {"id": 1, "matched_film_id": "f", "org_id": "cannes", "year": 2025, "tag_id": "x"}
    wd_awards = [
        {
            "award_qid": "Q12345",
            "award_label_en": "Berlin International Film Festival",
            "award_label_zh": None,
            "is_nomination": False,
        }
    ]
    verdict = v.verify_nominee_row(row, wd_awards)
    assert verdict["verdict"] == "suspicious"
    assert "berlin" in verdict["wikidata_orgs"]
