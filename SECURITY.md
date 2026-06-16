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
