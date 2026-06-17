# Security Policy

## Supported versions

This is a hackathon prototype / portfolio project, not a maintained product.
Only the latest `master` is supported — older commits are not patched.

## Reporting a vulnerability

Please report security issues **privately** via GitHub's
[**Report a vulnerability**](https://github.com/jimc1682000/film-brain/security/advisories/new)
button (repo → **Security** → **Advisories**). Do **not** open a public issue
for a security problem.

This is a best-effort, single-maintainer project: there is **no SLA, no bounty**,
and no guaranteed response time. Valid reports will be acknowledged and fixed on
a best-effort basis.

## Intended use & security posture

This project is a **prototype meant to run locally or in a trusted environment**.
Be aware before deploying it:

- **The API has no authentication or authorization.** Every endpoint is open.
  Do **not** expose the backend (`:8000`) or frontend (`:8080`) directly to the
  public internet — run it locally, behind a VPN, or behind your own
  authenticating proxy.
- **It is not hardened for untrusted input.** It was built to demonstrate
  search/tagging quality, not to resist abuse (no rate limiting, no input-size
  caps beyond the defaults, etc.).
- **Keys live in `.env`** (git-ignored) and are optional — the core runs keyless
  on local models. Never commit a real key; `.env.example` ships placeholders.

Reports of "unauthenticated access" or "open ports" on an instance deployed
against this guidance are expected behavior, not vulnerabilities. Genuine issues
— dependency CVEs, injection, secret leakage in the codebase, a way to read
files outside the intended scope — are in scope and welcome.

## LLM security (OWASP LLM Top 10)

LLMs are used for query expansion (user query → tags/keywords/HyDE) and
auto-tagging (film metadata → tags). Mapping of the applicable risks to the
mitigations actually in the codebase:

| Risk | Mitigation (where) |
| --- | --- |
| **LLM01 Prompt injection** | Defense in depth, both ends. **Input:** a layered gate inspects the user query before it reaches the LLM (`prompt_guard.inspect_deep`, ADR 0024) — always-on regex + heuristics (instruction-override / jailbreak / role-hijack / system-prompt-leak tokens, base64 / unicode / char-ratio obfuscation; zh/en/ja), with optional `llm-guard` ML confirmation on the gray zone. A BLOCK skips the LLM and degrades to BM25; it never hard-fails search. **Output:** tags are checked against the tag registry and non-existent ones dropped (`query_expand._parse_expansion` → `TagRegistry.validate_tag_ids`); a JSON schema constrains the response. A crafted query can at worst degrade result quality — it can't escape into code or SQL. Tests: `test_prompt_guard.py`, `test_query_expand.py::test_valid_expansion_groups_and_drops_hallucinated`. |
| **LLM02 Insecure output handling** | LLM output is treated as data, never executed. Keywords reach BM25 via a parameterized FTS `MATCH ?` (tokens quoted); tags/HyDE feed search only. The dangerous sinks are gated: `eval`/`exec` by ruff `S307`/`S102`, SQL by CodeQL + parameterized queries, outbound fetch by the `.semgrep/httpx-ssrf` rule, logging by `.semgrep/injection` + `_loggable()`. |
| **LLM04 Model DoS** | LLM calls are time-bounded with a circuit breaker (auto-tag) and degrade to BM25 (`query_expand._degraded`). No app-level rate limit — bounded by the local/trusted deployment model above. |
| **LLM06 Sensitive info disclosure** | No secrets/PII in prompts; keys live in `.env` (git-ignored). The query-expansion prompt carries only the query + the public taxonomy. **Auto-tag/re-analyze additionally sends the film's own metadata** (title, description, and any TMDb overview/keywords/cast/director — `AutoTagService._build_film_prompt`) to the configured tagging backend, which may be a cloud model. Operators feeding non-public catalog metadata should keep `tagging_cloud_backend` empty (local-only) or use a backend with a suitable data agreement. |
| **LLM08 Excessive agency** | None — the LLM only *returns* data (tags/keywords/text). It calls no tools, runs no code, and mutates no state. |
| **LLM09 Overreliance** | Honest confidence scoring (ADR 0009) caps displayed scores; tags are validated; LLM failure degrades gracefully rather than fabricating. |

LLM03 (training-data poisoning), LLM05/07/10 (no fine-tuning, no plugins, hosted
/local models) don't apply. Note these are runtime/design defenses verified by
tests — "prompt injection" isn't a statically lintable class, so there's no
dedicated pre-commit hook for it; the sink coverage above is what guards
regressions.
