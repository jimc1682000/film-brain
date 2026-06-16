"""Pre-commit gate: no hardcoded CJK string literals in frontend Python.

All user-facing copy must live in frontend/locales/*.json and be looked up
via frontend.i18n.t(). This checker walks the AST of each frontend .py file
and fails on any string literal containing CJK characters, so jargon fixes
and future locales only ever touch the locale table.

Skips:
- comments (never reach the AST)
- docstrings (module / class / function)
- lines carrying an explicit `# i18n: allow` marker (last-resort escape hatch)

Usage: python scripts/check_i18n_hardcode.py [files...]
Without args, scans every .py under frontend/.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]")
ALLOW_MARKER = "# i18n: allow"


def _docstring_lines(tree: ast.AST) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                doc = body[0].value
                lines.update(range(doc.lineno, (doc.end_lineno or doc.lineno) + 1))
    return lines


def check_file(path: pathlib.Path) -> list[str]:
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:  # let other hooks report syntax problems
        return [f"{path}:{e.lineno}: unparseable ({e.msg})"]
    src_lines = src.splitlines()
    skip = _docstring_lines(tree)
    errors = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if not CJK.search(node.value):
            continue
        if node.lineno in skip:
            continue
        line = src_lines[node.lineno - 1] if node.lineno <= len(src_lines) else ""
        if ALLOW_MARKER in line:
            continue
        snippet = node.value.replace("\n", "\\n")[:40]
        errors.append(
            f"{path}:{node.lineno}: hardcoded CJK string {snippet!r} — "
            f"move it to frontend/locales/*.json and use t()"
        )
    return errors


def main(argv: list[str]) -> int:
    if argv:
        files = [pathlib.Path(a) for a in argv if a.endswith(".py")]
    else:
        files = sorted(pathlib.Path("frontend").rglob("*.py"))
    all_errors: list[str] = []
    for f in files:
        if f.exists():
            all_errors.extend(check_file(f))
    for e in all_errors:
        print(e)
    return 1 if all_errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
