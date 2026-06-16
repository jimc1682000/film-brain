"""Wikidata-backed cross-check for award_nominees rows.

Why: even after fixing fuzzy-match (see backend/award_manager.py + tmdb_lookup),
DB rows can still claim "Film X was nominated for Award Y" without an
authoritative source. Wikidata exposes structured P166 (award received) and
P1411 (nominated for) statements that we can query by IMDb id (P345) without
authentication. For each (film, award) pair in our DB we ask Wikidata for
all award statements about the film, then bucket the row as:

  - verified=true   Wikidata explicitly carries that award org/name
  - verified=false  Wikidata has data for the film but does not list this award
  - verified=null   Wikidata has no entity for the film or query failed —
                    insufficient evidence either way; humans should review

Coverage: international ceremonies (Oscar, Cannes, Berlin, Venice, Golden Globe,
Annie, Independent Spirit, BIFF, Locarno…) sit well in Wikidata. Asian regional
awards (金馬, 香港金像獎, 台北電影獎) are partially populated; treat misses there
as `null`, not `false`.
"""

from __future__ import annotations

from typing import TypedDict

import httpx

WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
USER_AGENT = "ai-film-library-validator/0.1 (https://github.com/jimc1682000/ai-film-library)"


class WikidataAward(TypedDict):
    award_qid: str
    award_label_en: str
    award_label_zh: str | None
    is_nomination: bool  # True for P1411, False for P166 (won)


def query_film_awards(imdb_id: str, timeout: float = 20.0) -> list[WikidataAward] | None:
    """Return award statements Wikidata knows about for this film.

    Returns None on transport / parse failure so callers can mark `verified=null`
    rather than confusing absence-of-evidence with evidence-of-absence.
    """
    if not imdb_id or not imdb_id.startswith("tt"):
        return None

    safe_imdb = imdb_id.replace('"', "")
    sparql = (
        "SELECT ?award ?awardLabel ?awardLabelZh ?statement WHERE { "
        f'?film wdt:P345 "{safe_imdb}" . '
        '{ ?film wdt:P166 ?award . BIND("won" AS ?statement) } UNION '
        '{ ?film wdt:P1411 ?award . BIND("nominated" AS ?statement) } '
        'SERVICE wikibase:label { bd:serviceParam wikibase:language "en" . '
        "?award rdfs:label ?awardLabel . } "
        "OPTIONAL { ?award rdfs:label ?awardLabelZh . "
        'FILTER(LANG(?awardLabelZh) IN ("zh", "zh-tw", "zh-hant")) } '
        "}"
    )

    headers = {
        "Accept": "application/sparql-results+json",
        "User-Agent": USER_AGENT,
    }
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.get(WIKIDATA_SPARQL, params={"query": sparql}, headers=headers)
        if r.status_code != 200:
            return None
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return None

    out: list[WikidataAward] = []
    for binding in data.get("results", {}).get("bindings", []):
        qid = binding.get("award", {}).get("value", "")
        qid = qid.rsplit("/", 1)[-1] if qid else ""
        if not qid:
            continue
        out.append(
            {
                "award_qid": qid,
                "award_label_en": binding.get("awardLabel", {}).get("value", ""),
                "award_label_zh": (binding.get("awardLabelZh") or {}).get("value") or None,
                "is_nomination": binding.get("statement", {}).get("value") == "nominated",
            }
        )
    return out


def _normalise(s: str) -> str:
    return "".join(s.lower().split())


# Mapping from Wikidata award labels (any language) to CATCHPLAY+ awards-registry
# org_id values. Keys MUST match awards-registry.json `org_id` exactly so the
# verifier can compare against `award_nominees.org_id` directly.
ORG_LABEL_KEYWORDS: dict[str, list[tuple[str, ...]]] = {
    "oscars": [("academy award", "oscar", "奧斯卡")],
    "golden-globes": [("golden globe", "金球")],
    "cannes": [("cannes", "坎城", "戛纳")],
    "berlin": [("berlin", "berlinale", "柏林")],
    "venice": [("venice", "venezia", "威尼斯")],
    "biff": [("busan international film festival", "釜山")],
    "annie": [("annie award", "安妮獎")],
    "indie-spirit": [("independent spirit", "獨立精神獎")],
    "golden-horse": [("golden horse", "金馬")],
    "hk-film-award": [("hong kong film awards", "香港電影金像獎", "香港金像獎")],
    "taipei-film-award": [("taipei film festival", "台北電影節", "台北電影獎")],
    "emmy": [("primetime emmy", "艾美")],
    "golden-bell": [("golden bell", "金鐘")],
    "japan-academy": [("japan academy", "日本電影學院")],
    "sundance": [("sundance",)],
    "bafta": [("british academy film", "bafta", "英國電影學院")],
    "razzies": [("golden raspberry", "razzie", "金酸梅")],
    "critics-choice": [("critics' choice", "critics choice", "評論家選擇")],
}


def match_org(award: WikidataAward) -> str | None:
    """Best-effort: which CATCHPLAY+ org_id does this Wikidata award correspond to?"""
    haystack = " ".join(filter(None, [award["award_label_en"], award["award_label_zh"]]))
    haystack_l = haystack.lower()
    for org_id, keyword_groups in ORG_LABEL_KEYWORDS.items():
        for keywords in keyword_groups:
            if any(k.lower() in haystack_l for k in keywords):
                return org_id
    return None


class VerificationResult(TypedDict):
    nominee_id: int
    matched_film_id: str
    org_id: str
    year: int
    tag_id: str
    verdict: str  # verified | suspicious | unknown
    wikidata_orgs: list[str]
    reason: str


def _verification_result(
    nominee_row: dict,
    *,
    org_id: str,
    verdict: str,
    reason: str,
    wikidata_orgs: list[str] | None = None,
) -> VerificationResult:
    """Assemble a VerificationResult: the common nominee fields (from
    nominee_row) plus the verdict-specific org_id / verdict / reason /
    wikidata_orgs. Single source for the result shape."""
    return {
        "nominee_id": nominee_row["id"],
        "matched_film_id": nominee_row.get("matched_film_id") or "",
        "org_id": org_id,
        "year": nominee_row["year"],
        "tag_id": nominee_row["tag_id"],
        "verdict": verdict,
        "wikidata_orgs": wikidata_orgs or [],
        "reason": reason,
    }


def verify_nominee_row(
    nominee_row: dict, wikidata_awards: list[WikidataAward] | None
) -> VerificationResult:
    """Compare one award_nominees row against the film's Wikidata awards list."""
    if wikidata_awards is None:
        return _verification_result(
            nominee_row,
            org_id=nominee_row["org_id"],
            verdict="unknown",
            reason="wikidata lookup failed or film not on wikidata",
        )
    if not wikidata_awards:
        return _verification_result(
            nominee_row,
            org_id=nominee_row["org_id"],
            verdict="unknown",
            reason="wikidata returned no award statements (coverage gap)",
        )

    matched_orgs = sorted({o for a in wikidata_awards if (o := match_org(a))})
    org_id = nominee_row["org_id"]

    if org_id in matched_orgs:
        return _verification_result(
            nominee_row,
            org_id=org_id,
            verdict="verified",
            wikidata_orgs=matched_orgs,
            reason="wikidata carries an award statement matching this org",
        )

    if not matched_orgs:
        # Wikidata had awards but none mapped to a known org — coverage gap.
        return _verification_result(
            nominee_row,
            org_id=org_id,
            verdict="unknown",
            reason="wikidata awards exist but none mapped to our org registry",
        )

    return _verification_result(
        nominee_row,
        org_id=org_id,
        verdict="suspicious",
        wikidata_orgs=matched_orgs,
        reason=f"wikidata lists {matched_orgs} for this film, but DB claims {org_id}",
    )
