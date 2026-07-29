#!/usr/bin/env python3
"""Classify Film Brain PR risk and auto-merge only low-risk changes."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Any

AUTOMATION_WORKFLOW = "PR merge automation"
LOOKS_GOOD_MARKER = "film-brain-pr-automation:looks-good"
CODEX_REVIEW_MARKER = "film-brain-pr-automation:codex-review:"
HIGH_RISK_DEPENDENCIES = {"torch"}
RISK_LABELS = {
    "risk:low": ("0E8A16", "Low-risk PR eligible for automation"),
    "risk:medium": ("FBCA04", "Medium-risk PR requiring human observation"),
    "risk:high": ("D93F0B", "High-risk PR requiring human review"),
    "risk:manual-only": ("B60205", "PR must not be merged by automation"),
}
CODEX_LABEL = "needs:codex-review"
CODEX_LABEL_COLOR = "5319E7"
DEFAULT_TRUSTED_COMMENT_AUTHORS = ("github-actions[bot]", "jimc1682000")
SAFE_DEPENDENCY_FILE_PATTERNS = (
    "uv.lock",
    "requirements*.txt",
    "site/package-lock.json",
    "site/package.json",
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
)
DOCS_FILE_PATTERNS = (
    "*.md",
    "README*.md",
    ".github/*.md",
    "docs/**/*.md",
)
HIGH_RISK_FILE_PATTERNS = (
    ".github/workflows/*",
    "Dockerfile",
    "**/Dockerfile",
    "docker-compose*.yml",
    "docker-compose*.yaml",
    "backend/config.py",
    "backend/db.py",
    "backend/interfaces.py",
    "backend/llm_client.py",
    "backend/models.py",
    "backend/routers/search.py",
    "backend/services/search/*",
    "docs/adr/*",
)
DEPENDABOT_STRUCTURAL_FILE_PATTERNS = (
    "Dockerfile",
    "**/Dockerfile",
    "docker-compose*.yml",
    "docker-compose*.yaml",
    "backend/config.py",
    "backend/db.py",
    "backend/interfaces.py",
    "backend/llm_client.py",
    "backend/models.py",
    "backend/routers/search.py",
    "backend/services/search/*",
    "docs/adr/*",
)
MANUAL_ONLY_TITLE_KEYWORDS = ("migration", "security", "auth", "rate limit", "stacked")
HIGH_RISK_TITLE_KEYWORDS = ("breaking", "database", "db", "deploy", "refactor")
DRY_RUN = os.getenv("DRY_RUN") == "1"
GH_BIN = shutil.which("gh")
if GH_BIN is None:
    print("gh is required but was not found in PATH", file=sys.stderr)
    raise SystemExit(1)


@dataclass
class Decision:
    risk: str
    automerge: bool
    request_codex_review: bool
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
                "labels",
                "additions",
                "deletions",
                "changedFiles",
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


def dependency_update_text(pr: dict[str, Any]) -> str:
    return "\n".join(
        [
            pr.get("title") or "",
            pr.get("body") or "",
            "\n".join(commit.get("messageBody") or "" for commit in pr.get("commits", [])),
        ]
    )


def parse_version_pairs(text: str) -> list[tuple[tuple[int, ...], tuple[int, ...]]]:
    version = r"[vV]?[><=~^ ]*(\d+(?:\.\d+){0,2})"
    return [
        (
            tuple(int(part) for part in match.group(1).split(".")),
            tuple(int(part) for part in match.group(2).split(".")),
        )
        for match in re.finditer(rf"\bfrom\s+{version}\s+to\s+{version}\b", text)
    ]


def is_major_update(pr: dict[str, Any]) -> bool:
    text = dependency_update_text(pr).lower()
    if "version-update:semver-major" in text:
        return True
    return any(before[0] != after[0] for before, after in parse_version_pairs(text))


def is_unparseable_grouped_update(pr: dict[str, Any]) -> bool:
    title = (pr.get("title") or "").lower()
    return " group " in title and not parse_version_pairs(dependency_update_text(pr))


def files(pr: dict[str, Any]) -> list[str]:
    return [item["path"] for item in pr.get("files", [])]


def changed_lines(pr: dict[str, Any]) -> int:
    return int(pr.get("additions") or 0) + int(pr.get("deletions") or 0)


def all_files_match(paths: list[str], patterns: tuple[str, ...]) -> bool:
    return bool(paths) and all(
        any(fnmatch(path, pattern) for pattern in patterns) for path in paths
    )


def any_file_matches(paths: list[str], patterns: tuple[str, ...]) -> bool:
    return any(any(fnmatch(path, pattern) for pattern in patterns) for path in paths)


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
    query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
      repository(owner: $owner, name: $name) {
        pullRequest(number: $number) {
          reviewThreads(first: 100, after: $cursor) {
            nodes { isResolved }
            pageInfo { hasNextPage endCursor }
          }
        }
      }
    }
    """
    total = 0
    cursor: str | None = None
    while True:
        args = [
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
        ]
        if cursor is not None:
            args.extend(["-F", f"cursor={cursor}"])
        data = gh_json(*args)
        threads = data["data"]["repository"]["pullRequest"]["reviewThreads"]
        total += sum(1 for node in threads["nodes"] if not node["isResolved"])
        page_info = threads["pageInfo"]
        if not page_info["hasNextPage"]:
            return total
        cursor = page_info["endCursor"]


def issue_comments(repo: str, number: int) -> list[dict[str, Any]]:
    return gh_json("api", f"repos/{repo}/issues/{number}/comments", "--paginate")


def trusted_comment_authors() -> set[str]:
    raw_authors = os.getenv("AUTOMATION_COMMENT_AUTHORS")
    if raw_authors:
        return {author.strip() for author in raw_authors.split(",") if author.strip()}
    return set(DEFAULT_TRUSTED_COMMENT_AUTHORS)


def has_exact_marker(comment: dict[str, Any], marker: str) -> bool:
    return f"<!-- {marker} -->" in (comment.get("body") or "")


def find_marker_comment(repo: str, number: int, marker: str) -> dict[str, Any] | None:
    trusted_authors = trusted_comment_authors()
    for comment in issue_comments(repo, number):
        author = comment.get("user", {}).get("login")
        if author in trusted_authors and has_exact_marker(comment, marker):
            return comment
    return None


def comment(repo: str, number: int, body: str) -> None:
    if DRY_RUN:
        first_line = body.splitlines()[0] if body else ""
        print(f"#{number}: dry-run comment - {first_line}")
        return
    run_gh("pr", "comment", str(number), "--repo", repo, "--body", body)


def ensure_labels(repo: str) -> None:
    if DRY_RUN:
        print("dry-run label setup")
        return
    for name, (color, description) in RISK_LABELS.items():
        run_gh(
            "label",
            "create",
            name,
            "--repo",
            repo,
            "--color",
            color,
            "--description",
            description,
            "--force",
        )
    run_gh(
        "label",
        "create",
        CODEX_LABEL,
        "--repo",
        repo,
        "--color",
        CODEX_LABEL_COLOR,
        "--description",
        "Codex review has been requested for this PR",
        "--force",
    )


def current_label_names(pr: dict[str, Any]) -> set[str]:
    return {label["name"] for label in pr.get("labels", [])}


def add_label(repo: str, number: int, label: str) -> None:
    if DRY_RUN:
        print(f"#{number}: dry-run add label - {label}")
        return
    run_gh("pr", "edit", str(number), "--repo", repo, "--add-label", label)


def remove_label(repo: str, number: int, label: str) -> None:
    if DRY_RUN:
        print(f"#{number}: dry-run remove label - {label}")
        return
    run_gh("pr", "edit", str(number), "--repo", repo, "--remove-label", label)


def sync_labels(repo: str, pr: dict[str, Any], decision: Decision) -> None:
    number = pr["number"]
    existing = current_label_names(pr)
    for label in RISK_LABELS:
        if label == decision.risk and label not in existing:
            add_label(repo, number, label)
        elif label != decision.risk and label in existing:
            remove_label(repo, number, label)
    if decision.request_codex_review and CODEX_LABEL not in existing:
        add_label(repo, number, CODEX_LABEL)
    elif not decision.request_codex_review and CODEX_LABEL in existing:
        remove_label(repo, number, CODEX_LABEL)


def is_manual_only(pr: dict[str, Any], default_branch: str) -> bool:
    title = (pr.get("title") or "").lower()
    if pr["baseRefName"] != default_branch:
        return True
    return any(word in title for word in MANUAL_ONLY_TITLE_KEYWORDS)


def is_high_risk(pr: dict[str, Any]) -> bool:
    title = (pr.get("title") or "").lower()
    if any(word in title for word in HIGH_RISK_TITLE_KEYWORDS):
        return True
    return any_file_matches(files(pr), HIGH_RISK_FILE_PATTERNS)


def classify_dependabot(pr: dict[str, Any], pr_files: list[str]) -> Decision:
    names = dependency_names(pr)
    if any_file_matches(pr_files, DEPENDABOT_STRUCTURAL_FILE_PATTERNS):
        return Decision(
            "risk:high",
            False,
            True,
            "Dependabot PR changes structural files",
        )
    if is_unparseable_grouped_update(pr):
        return Decision(
            "risk:high",
            False,
            True,
            "grouped Dependabot PR without parseable version changes",
        )
    if is_major_update(pr) or names & HIGH_RISK_DEPENDENCIES:
        return Decision("risk:high", False, True, "major or high-risk dependency update")
    if all_files_match(pr_files, SAFE_DEPENDENCY_FILE_PATTERNS):
        return Decision(
            "risk:low",
            True,
            False,
            "low-risk Dependabot patch/minor lockfile or workflow update",
        )
    return Decision(
        "risk:medium",
        False,
        True,
        "Dependabot PR changes files outside the low-risk allowlist",
    )


def classify(pr: dict[str, Any], default_branch: str) -> Decision:
    if pr["isDraft"]:
        return Decision("risk:manual-only", False, False, "draft PR")
    if is_manual_only(pr, default_branch):
        return Decision("risk:manual-only", False, True, "stacked or manual-only PR")

    pr_files = files(pr)
    if not is_dependabot(pr):
        if is_high_risk(pr):
            return Decision(
                "risk:high",
                False,
                True,
                "sensitive path or structural keyword; human review required",
            )
        if all_files_match(pr_files, DOCS_FILE_PATTERNS) and changed_lines(pr) <= 100:
            return Decision("risk:low", True, False, "small docs-only PR")
        if len(pr_files) <= 3 and changed_lines(pr) <= 150:
            return Decision("risk:medium", False, True, "small non-dependency PR")
        return Decision("risk:high", False, True, "larger non-dependency PR")

    return classify_dependabot(pr, pr_files)


def request_review_once(repo: str, pr: dict[str, Any], decision: Decision) -> bool:
    marker = f"{CODEX_REVIEW_MARKER}{pr['headRefOid']}"
    if find_marker_comment(repo, pr["number"], marker):
        return False
    comment(
        repo,
        pr["number"],
        (
            "@codex review\n\n"
            f"{decision.risk} PR. Automation will not merge this PR. "
            "Please review the diff and leave findings if anything needs changes.\n\n"
            f"<!-- {marker} -->"
        ),
    )
    return True


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


def merge_if_clean(repo: str, pr: dict[str, Any]) -> None:
    number = pr["number"]
    green, check_reason = has_green_checks(pr)
    if not green:
        print(f"#{number}: skip - {check_reason}")
        return
    if pr["mergeable"] != "MERGEABLE":
        print(f"#{number}: skip - mergeable is {pr['mergeable']}")
        return
    threads = unresolved_threads(repo, number)
    if threads:
        print(f"#{number}: skip - {threads} unresolved review thread(s)")
        return
    merge(repo, pr)
    print(f"#{number}: merged")


def handle_pr(repo: str, default_branch: str, number: int) -> None:
    pr = load_pr(repo, number)
    decision = classify(pr, default_branch)
    print(f"#{number}: {decision.risk} - {decision.reason}")
    sync_labels(repo, pr, decision)

    if decision.automerge:
        merge_if_clean(repo, pr)
    elif decision.request_codex_review and request_review_once(repo, pr, decision):
        print(f"#{number}: requested Codex review")


def main() -> None:
    repo = os.environ["GITHUB_REPOSITORY"]
    default_branch = os.getenv("DEFAULT_BRANCH", "master")
    ensure_labels(repo)
    for number in pr_numbers(repo):
        handle_pr(repo, default_branch, number)


if __name__ == "__main__":
    main()
