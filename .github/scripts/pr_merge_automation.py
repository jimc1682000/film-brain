#!/usr/bin/env python3
"""Classify Film Brain PRs and apply the narrow auto-merge policy."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatch
from typing import Any

AUTOMATION_WORKFLOW = "PR merge automation"
LOOKS_GOOD_MARKER = "film-brain-pr-automation:looks-good"
CODEX_REVIEW_MARKER = "film-brain-pr-automation:codex-review:"
MANUAL_REVIEW_MARKER = "film-brain-pr-automation:manual-review:"
HIGH_RISK_DEPENDENCIES = {"torch"}
SAFE_FILE_PATTERNS = (
    "uv.lock",
    "requirements*.txt",
    "site/package-lock.json",
    "site/package.json",
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
)
STRUCTURAL_FILE_PATTERNS = (
    "backend/db.py",
    "backend/services/search/*",
    "backend/routers/search.py",
    "backend/models.py",
    "backend/interfaces.py",
    "docs/adr/*",
)
DRY_RUN = os.getenv("DRY_RUN") == "1"
GH_BIN = shutil.which("gh")
if GH_BIN is None:
    print("gh is required but was not found in PATH", file=sys.stderr)
    raise SystemExit(1)


@dataclass
class Decision:
    action: str
    reason: str


def run_gh(*args: str, input_text: str | None = None) -> str:
    result = subprocess.run(  # noqa: S603
        [GH_BIN, *args],
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        print(result.stderr.strip(), file=sys.stderr)
        raise SystemExit(result.returncode)
    return result.stdout


def gh_json(*args: str) -> Any:
    output = run_gh(*args)
    return json.loads(output) if output.strip() else None


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def pr_numbers(repo: str) -> list[int]:
    event_pr = os.getenv("PR_NUMBER")
    if event_pr:
        return [int(event_pr)]
    prs = gh_json(
        "pr",
        "list",
        "--repo",
        repo,
        "--state",
        "open",
        "--json",
        "number",
    )
    return [int(pr["number"]) for pr in prs]


def load_pr(repo: str, number: int) -> dict[str, Any]:
    return gh_json(
        "pr",
        "view",
        str(number),
        "--repo",
        repo,
        "--json",
        ",".join(
            [
                "number",
                "title",
                "body",
                "author",
                "baseRefName",
                "headRefName",
                "headRefOid",
                "isDraft",
                "mergeable",
                "files",
                "commits",
                "statusCheckRollup",
                "url",
            ]
        ),
    )


def is_dependabot(pr: dict[str, Any]) -> bool:
    return pr["author"]["login"] in {"app/dependabot", "dependabot[bot]"}


def dependency_names(pr: dict[str, Any]) -> set[str]:
    text = "\n".join(
        [
            pr.get("title") or "",
            pr.get("body") or "",
            "\n".join(commit.get("messageBody") or "" for commit in pr.get("commits", [])),
        ]
    )
    names = set(re.findall(r"dependency-name:\s*([A-Za-z0-9_.@/-]+)", text))
    title_match = re.search(r"\bbump\s+([A-Za-z0-9_.@/-]+)\s+from\b", pr.get("title") or "")
    if title_match:
        names.add(title_match.group(1))
    title_match = re.search(
        r"\bupdate\s+([A-Za-z0-9_.@/-]+)\s+requirement\b",
        pr.get("title") or "",
    )
    if title_match:
        names.add(title_match.group(1))
    return {name.lower() for name in names}


def parse_versions(title: str) -> tuple[tuple[int, ...], tuple[int, ...]] | None:
    version = r"[vV]?[><=~^ ]*(\d+(?:\.\d+){0,2})"
    match = re.search(rf"\bfrom\s+{version}\s+to\s+{version}\b", title)
    if not match:
        return None
    before = tuple(int(part) for part in match.group(1).split("."))
    after = tuple(int(part) for part in match.group(2).split("."))
    return before, after


def is_major_update(pr: dict[str, Any]) -> bool:
    text = "\n".join(
        [
            pr.get("title") or "",
            pr.get("body") or "",
            "\n".join(commit.get("messageBody") or "" for commit in pr.get("commits", [])),
        ]
    ).lower()
    if "version-update:semver-major" in text:
        return True
    versions = parse_versions(pr.get("title") or "")
    return bool(versions and versions[0][0] != versions[1][0])


def files(pr: dict[str, Any]) -> list[str]:
    return [item["path"] for item in pr.get("files", [])]


def all_files_match(paths: list[str], patterns: tuple[str, ...]) -> bool:
    return bool(paths) and all(
        any(fnmatch(path, pattern) for pattern in patterns) for path in paths
    )


def has_green_checks(pr: dict[str, Any]) -> tuple[bool, str]:
    checks = [
        check
        for check in pr.get("statusCheckRollup", [])
        if check.get("workflowName") != AUTOMATION_WORKFLOW and check.get("name") != "evaluate"
    ]
    if not checks:
        return False, "no completed checks found"
    failures: list[str] = []
    for check in checks:
        if check["__typename"] == "CheckRun":
            if check.get("status") != "COMPLETED" or check.get("conclusion") != "SUCCESS":
                failures.append(
                    f"{check.get('name')}={check.get('status')}/{check.get('conclusion')}"
                )
        elif check["__typename"] == "StatusContext":
            if check.get("state") != "SUCCESS":
                failures.append(f"{check.get('context')}={check.get('state')}")
        else:
            failures.append(f"{check.get('name', check['__typename'])}=unknown")
    if failures:
        return False, ", ".join(failures)
    return True, "all checks green"


def unresolved_threads(repo: str, number: int) -> int:
    owner, name = repo.split("/", 1)
    query = """
    query($owner: String!, $name: String!, $number: Int!) {
      repository(owner: $owner, name: $name) {
        pullRequest(number: $number) {
          reviewThreads(first: 100) {
            nodes { isResolved }
          }
        }
      }
    }
    """
    data = gh_json(
        "api",
        "graphql",
        "-f",
        f"query={query}",
        "-F",
        f"owner={owner}",
        "-F",
        f"name={name}",
        "-F",
        f"number={number}",
    )
    nodes = data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    return sum(1 for node in nodes if not node["isResolved"])


def issue_comments(repo: str, number: int) -> list[dict[str, Any]]:
    return gh_json("api", f"repos/{repo}/issues/{number}/comments", "--paginate")


def review_comments(repo: str, number: int) -> list[dict[str, Any]]:
    return gh_json("api", f"repos/{repo}/pulls/{number}/comments", "--paginate")


def reviews(repo: str, number: int) -> list[dict[str, Any]]:
    return gh_json("api", f"repos/{repo}/pulls/{number}/reviews", "--paginate")


def find_marker_comment(repo: str, number: int, marker: str) -> dict[str, Any] | None:
    for comment in issue_comments(repo, number):
        if marker in (comment.get("body") or ""):
            return comment
    return None


def comments_after_review_marker(repo: str, number: int, marker_time: datetime) -> int:
    count = 0
    for comment in issue_comments(repo, number):
        if parse_time(comment["created_at"]) > marker_time and "film-brain-pr-automation:" not in (
            comment.get("body") or ""
        ):
            count += 1
    for comment in review_comments(repo, number):
        if parse_time(comment["created_at"]) > marker_time:
            count += 1
    for review in reviews(repo, number):
        submitted_at = review.get("submitted_at")
        if submitted_at and parse_time(submitted_at) > marker_time:
            count += 1
    return count


def comment(repo: str, number: int, body: str) -> None:
    if DRY_RUN:
        first_line = body.splitlines()[0] if body else ""
        print(f"#{number}: dry-run comment - {first_line}")
        return
    run_gh("pr", "comment", str(number), "--repo", repo, "--body", body)


def merge(repo: str, pr: dict[str, Any]) -> None:
    number = str(pr["number"])
    if not find_marker_comment(repo, pr["number"], LOOKS_GOOD_MARKER):
        comment(repo, pr["number"], f"Looks Good\n\n<!-- {LOOKS_GOOD_MARKER} -->")
    if DRY_RUN:
        print(f"#{number}: dry-run merge - {pr['headRefOid']}")
        return
    run_gh(
        "pr",
        "merge",
        number,
        "--repo",
        repo,
        "--squash",
        "--match-head-commit",
        pr["headRefOid"],
    )


def is_manual_structural(pr: dict[str, Any], default_branch: str) -> bool:
    title = (pr.get("title") or "").lower()
    if pr["baseRefName"] != default_branch:
        return True
    if any(word in title for word in ["refactor", "migration", "db", "database"]):
        return True
    return any(
        any(fnmatch(path, pattern) for pattern in STRUCTURAL_FILE_PATTERNS) for path in files(pr)
    )


def classify(pr: dict[str, Any], default_branch: str) -> Decision:
    if pr["isDraft"]:
        return Decision("skip", "draft PR")
    green, check_reason = has_green_checks(pr)
    if not green:
        return Decision("skip", check_reason)
    if not is_dependabot(pr):
        if is_manual_structural(pr, default_branch):
            return Decision(
                "manual_review_only",
                "structural or stacked PR; request Codex review but never auto-merge",
            )
        return Decision("skip", "non-Dependabot PR outside the auto-merge policy")
    names = dependency_names(pr)
    if is_major_update(pr) or names & HIGH_RISK_DEPENDENCIES:
        return Decision("codex_review_then_merge", "major or high-risk dependency update")
    if all_files_match(files(pr), SAFE_FILE_PATTERNS):
        return Decision("merge", "low-risk Dependabot patch/minor lockfile or workflow update")
    return Decision("skip", "Dependabot PR changes files outside the low-risk allowlist")


def request_review_once(repo: str, pr: dict[str, Any], marker_prefix: str, body: str) -> bool:
    marker = f"{marker_prefix}{pr['headRefOid']}"
    if find_marker_comment(repo, pr["number"], marker):
        return False
    comment(repo, pr["number"], f"@codex review\n\n{body}\n\n<!-- {marker} -->")
    return True


def merge_if_clean(repo: str, pr: dict[str, Any], success_message: str) -> None:
    number = pr["number"]
    if pr["mergeable"] != "MERGEABLE":
        print(f"#{number}: skip - mergeable is {pr['mergeable']}")
        return
    threads = unresolved_threads(repo, number)
    if threads:
        print(f"#{number}: skip - {threads} unresolved review thread(s)")
        return
    merge(repo, pr)
    print(f"#{number}: {success_message}")


def handle_high_risk_dependabot(repo: str, pr: dict[str, Any], wait_minutes: int) -> None:
    requested = request_review_once(
        repo,
        pr,
        CODEX_REVIEW_MARKER,
        (
            "High-risk Dependabot update. Automation will merge only after "
            "green checks, no unresolved review threads, no new comments "
            "after this review request, and the review wait window has elapsed."
        ),
    )
    if requested:
        print(f"#{pr['number']}: requested Codex review")
        return

    number = pr["number"]
    marker = find_marker_comment(repo, number, f"{CODEX_REVIEW_MARKER}{pr['headRefOid']}")
    if marker is None:
        print(f"#{number}: skip - Codex review marker missing")
        return
    marker_time = parse_time(marker["created_at"])
    wait_until = marker_time + timedelta(minutes=wait_minutes)
    if datetime.now(UTC) < wait_until:
        print(f"#{number}: skip - waiting until {wait_until.isoformat()}")
        return
    if comments_after_review_marker(repo, number, marker_time):
        print(f"#{number}: skip - comments appeared after Codex review request")
        return
    merge_if_clean(repo, pr, "merged after Codex review wait")


def handle_manual_review(repo: str, pr: dict[str, Any]) -> None:
    requested = request_review_once(
        repo,
        pr,
        MANUAL_REVIEW_MARKER,
        (
            "Manual-only structural or stacked PR. Per the dotfiles "
            "CONTRIBUTING policy, this PR needs review and the 30-minute "
            "comment window, but automation will not merge it."
        ),
    )
    if requested:
        print(f"#{pr['number']}: requested Codex review; manual merge required")


def handle_pr(repo: str, default_branch: str, wait_minutes: int, number: int) -> None:
    pr = load_pr(repo, number)
    decision = classify(pr, default_branch)
    print(f"#{number}: {decision.action} - {decision.reason}")

    if decision.action == "merge":
        merge_if_clean(repo, pr, "merged")
    elif decision.action == "codex_review_then_merge":
        handle_high_risk_dependabot(repo, pr, wait_minutes)
    elif decision.action == "manual_review_only":
        handle_manual_review(repo, pr)


def main() -> None:
    repo = os.environ["GITHUB_REPOSITORY"]
    default_branch = os.getenv("DEFAULT_BRANCH", "master")
    wait_minutes = int(os.getenv("REVIEW_WAIT_MINUTES", "30"))
    for number in pr_numbers(repo):
        handle_pr(repo, default_branch, wait_minutes, number)


if __name__ == "__main__":
    main()
