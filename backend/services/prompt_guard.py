"""Two-layer prompt-injection input gate (OWASP LLM01).

Cheap, dependency-free pre-filter run on a user query BEFORE it reaches the
expansion LLM — complements the output-side defense (tags validated against the
registry). Layer 1 = regex patterns (instruction override / jailbreak / system-
prompt-leak / role-hijack tokens, zh+en+ja); Layer 2 = heuristics (base64
payloads, unicode obfuscation, special-char ratio). Scores → SAFE / SUSPICIOUS
/ BLOCK. ~0.2ms, no model call.

This is defense-in-depth, not a perfect filter (semantic / multi-turn / indirect
injection slip past — see SECURITY.md). For a search app a BLOCK just denies the
LLM the crafted text and degrades to BM25; it never hard-fails search.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from enum import Enum

from backend.services.search_config import get_config


class RiskLevel(Enum):
    SAFE = "safe"
    SUSPICIOUS = "suspicious"
    BLOCK = "block"


@dataclass
class Detection:
    level: RiskLevel
    score: int
    matched: list[str]


# (pattern, name) — weight 35. zh/ja allow filler between the two halves so
# "忽略以上所有指令" / "指示を全て無視" word orders still match.
_HIGH_RISK = [
    (
        r"(ignore|disregard|forget)\s+(all|the|your|any)?\s*(previous|above|prior|earlier)\s+(instructions|prompts|rules|context)",
        "instruction_override",
    ),
    (
        r"(忽略|無視|忽视|忘记|忘記)[^,。.，\n]{0,15}?(指令|指示|規則|规则|提示|上下文)",
        "instruction_override_zh",
    ),
    (
        r"(指示|指令|命令|ルール|プロンプト|制約)[^,。.、\n]{0,20}?(無視|忘れ|破棄|消去|解除)",
        "instruction_override_ja",
    ),
    (
        r"(無視|忘れ|忘却|破棄)[^,。.、\n]{0,20}?(指示|指令|命令|ルール|プロンプト)",
        "instruction_override_ja2",
    ),
    (
        r"\b(DAN|do anything now|jailbreak|developer mode|god mode|unrestricted mode|no filter)\b",
        "jailbreak_keyword",
    ),
    (
        r"(reveal|show|print|display|output|tell me|expose)\s+(your|the)?\s*(system prompt|hidden instructions|initial prompt|system message)",
        "system_prompt_leak",
    ),
    (
        r"(告訴|告诉|顯示|显示|透露|洩漏|泄漏|输出|輸出)[^,。.，\n]{0,10}?(系統提示|系统提示|系統指令|系统指令)",
        "system_prompt_leak_zh",
    ),
    (
        r"(システムプロンプト|システム指示|隠された指示|初期プロンプト)[^,。.、\n]{0,15}?(教え|見せ|表示|出力|公開)",
        "system_prompt_leak_ja",
    ),
    (
        r"(教え|見せ|表示|出力|公開)[^,。.、\n]{0,15}?(システムプロンプト|システム指示|隠された指示)",
        "system_prompt_leak_ja2",
    ),
    (r"<\|(im_start|im_end|endoftext|system|user|assistant)\|>", "role_hijack_token"),
    # Match BOTH a real newline and a literal backslash-n (escaped in the raw
    # query text) before a role token — either can smuggle a fake turn.
    (r"(?:\\n|[\r\n])\s*(system|assistant|user)\s*:", "role_hijack_newline"),
]

# weight 20
_MEDIUM_RISK = [
    (
        r"(bypass|circumvent|override|disable)\s+(safety|filter|guardrail|policy|restriction)",
        "safety_bypass",
    ),
    (r"you\s+are\s+now\s+(a|an)?\s*(unrestricted|evil|malicious|hacker)", "persona_hijack"),
    (r"(pretend|act\s+as\s+if|imagine)\s+you\s+(are|have)", "persona_injection"),
]


def _guard_cfg() -> dict:
    """Thresholds + weights from search-config (data/search-config.json,
    hot-reloaded; _DEFAULTS fallback) — tunable without a deploy. Wide
    SUSPICIOUS net by design ('prefer over-catch'): it only logs/escalates,
    never hard-denies; BLOCK stays conservative since it degrades search."""
    return get_config()["prompt_guard"]


def _layer1_regex(text: str, weights: dict) -> tuple[int, list[str]]:
    score, matched = 0, []
    for pattern, name in _HIGH_RISK:
        if re.search(pattern, text, re.IGNORECASE | re.DOTALL):
            score += weights["high"]
            matched.append(name)
    for pattern, name in _MEDIUM_RISK:
        if re.search(pattern, text, re.IGNORECASE | re.DOTALL):
            score += weights["medium"]
            matched.append(name)
    return score, matched


def _has_base64_payload(text: str, weights: dict) -> bool:
    for c in re.findall(r"[A-Za-z0-9+/]{20,}={0,2}", text):
        try:
            decoded = base64.b64decode(c, validate=True).decode("utf-8", errors="ignore")
        except Exception:
            continue
        if _layer1_regex(decoded.lower(), weights)[0] > 0:
            return True
    return False


def _unicode_tricks(text: str) -> list[str]:
    tricks = []
    if re.search(r"[​‌‍﻿]", text):
        tricks.append("zero_width_char")
    if re.search(r"[Ａ-Ｚａ-ｚ]", text):
        tricks.append("fullwidth_ascii")
    if re.search(r"[‮‭]", text):
        tricks.append("bidi_override")
    return tricks


def _layer2_heuristic(text: str, cfg: dict) -> tuple[int, list[str]]:
    w = cfg["weights"]
    score, matched = 0, []
    if _has_base64_payload(text, w):
        score += w["base64"]
        matched.append("base64_injection")
    tricks = _unicode_tricks(text)
    if tricks:
        score += w["unicode"]
        matched.extend(tricks)
    # CJK queries are mostly alnum, so a high punctuation/symbol ratio flags
    # obfuscation. min_len / threshold are config (loosened to widen the net).
    if len(text) >= cfg["char_ratio_min_len"]:
        non_alnum = sum(1 for ch in text if not ch.isalnum() and not ch.isspace())
        if non_alnum / len(text) > cfg["char_ratio_threshold"]:
            score += w["char_ratio"]
            matched.append("suspicious_char_ratio")
    return score, matched


def inspect(text: str) -> Detection:
    """Layers 1+2 only — cheap (~0.2ms), dependency-free, always on (incl. the
    slim image). SAFE passes, BLOCK denies; SUSPICIOUS is the gray zone that
    inspect_deep escalates to llm-guard when available."""
    cfg = _guard_cfg()
    s1, m1 = _layer1_regex((text or "").lower(), cfg["weights"])
    s2, m2 = _layer2_heuristic(text or "", cfg)
    total = s1 + s2
    if total >= cfg["block"]:
        level = RiskLevel.BLOCK
    elif total >= cfg["suspicious"]:
        level = RiskLevel.SUSPICIOUS
    else:
        level = RiskLevel.SAFE
    return Detection(level=level, score=total, matched=m1 + m2)


_llm_guard_scanner = None  # lazy-built PromptInjection scanner (None = unavailable)
_llm_guard_tried = False


def _llm_guard_verdict(text: str) -> bool | None:
    """ML confirmation via llm-guard (OPTIONAL — requirements-st.txt). Returns
    True (injection) / False (clean) / None (llm-guard not installed). Catches
    the semantic attacks the regex/heuristic layers miss. Heavy (transformers +
    a ~400MB model) so it's only the ST layer, invoked on SUSPICIOUS, not slim."""
    global _llm_guard_scanner, _llm_guard_tried
    if not _llm_guard_tried:
        _llm_guard_tried = True
        try:
            from llm_guard.input_scanners import PromptInjection  # type: ignore[import-untyped]

            _llm_guard_scanner = PromptInjection()
        except Exception:
            _llm_guard_scanner = None  # not installed (slim) or failed to load
    if _llm_guard_scanner is None:
        return None
    try:
        _sanitized, is_valid, _score = _llm_guard_scanner.scan(text)
        return not is_valid
    except Exception:
        return None


def inspect_deep(text: str) -> Detection:
    """inspect() + escalate the SUSPICIOUS gray zone to llm-guard when it's
    installed (the ST layer): confirmed injection → BLOCK, cleared → SAFE. With
    llm-guard absent (slim), SUSPICIOUS is left as-is (caller logs + proceeds)."""
    d = inspect(text)
    if d.level is not RiskLevel.SUSPICIOUS:
        return d
    verdict = _llm_guard_verdict(text or "")
    if verdict is True:
        return Detection(RiskLevel.BLOCK, d.score, [*d.matched, "llm_guard:injection"])
    if verdict is False:
        return Detection(RiskLevel.SAFE, d.score, [*d.matched, "llm_guard:cleared"])
    return d  # llm-guard unavailable → keep SUSPICIOUS
