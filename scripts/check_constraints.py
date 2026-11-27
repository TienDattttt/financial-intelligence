"""Mechanical Floor Guard & Module Size Ratchet Checker for FinIntel V2.0.

Verified Sources:
- CONSTRAINTS.md (FinIntel V2.0 Mechanical Quality Contract)
- AGENTS.md (FinIntel Root Engineering & AI Coding Rules)
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_MODULE_LOC = 1000
LEGACY_NET_GROWTH_LIMIT = 10

PROTECTED_DATA_PREFIXES: tuple[str, ...] = (
    "data/live_fhsc_universe/",
    "data/universe_600_800/",
)

FORBIDDEN_SERVING_IMPORTS: tuple[str, ...] = (
    "finintel.normalization",
    "finintel.ingestion",
    "streamlit",
    "dashboard",
)

PLATFORM_GROUP_TOPLEVEL_FORBIDDEN: tuple[str, ...] = (
    "fastapi",
    "pydantic",
    "structlog",
    "redis",
    "psycopg",
    "opentelemetry",
    "dagster",
    "granian",
)

# Constructed via concatenation so this file never triggers raw grep scans.
_SUPPRESSION_RE = re.compile(
    r"@" + r"ts-ignore\b"
    r"|@" + r"ts-expect-error\b"
    r"|@" + r"ts-nocheck\b"
    r"|\beslint-" + r"disable\b"
    r"|\bbiome-" + r"ignore\b"
    r"|#\s*" + r"noqa\b"
    r"|#\s*type:\s*" + r"ignore\b"
    r"|\bistanbul\s+ignore\b"
    r"|\bnosemgrep\b"
    r"|\bgitleaks:allow\b"
)

_SKIP_TEST_RE = re.compile(
    r"\.(?:skip|todo)\s*\("
    r"|\bxit\s*\("
    r"|\bxdescribe\s*\("
    r"|@pytest\.mark\.skip\b"
    r"|\bpytest\.skip\s*\("
)

_FORBIDDEN_COMPLIANCE_WORDING_RE = re.compile(
    r"xếp\s+hạng\s+tín\s+nhiệm"
    r"|(?:xếp\s+)?bậc\s+tín\s+nhiệm"
    r"|phân\s+tầng\s+tín\s+nhiệm"
    r"|\btín\s+nhiệm\b"
    r"|khuyến\s+nghị\s+(?:mua|bán)(?:\s*/\s*(?:mua|bán))?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Finding:
    """Single mechanical constraint violation."""

    rule: str
    path: str
    detail: str


def _run_git(
    repo_root: Path, args: list[str], *, allow_diff_exit: bool = False
) -> str | None:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode == 0:
        return proc.stdout
    if allow_diff_exit and proc.returncode == 1:
        return proc.stdout
    return None


def _resolve_merge_base(repo_root: Path, explicit_base: str | None) -> str | None:
    candidates = [explicit_base] if explicit_base else ["main", "origin/main", "HEAD"]
    for candidate in candidates:
        if not candidate:
            continue
        out = _run_git(repo_root, ["merge-base", candidate, "HEAD"])
        if out is not None and out.strip():
            return out.strip()
    return None


def _is_code_or_test_file(rel_path: str) -> bool:
    if rel_path == "scripts/check_constraints.py":
        return False
    if not rel_path.endswith((".py", ".ts", ".tsx", ".js", ".mjs")):
        return False
    return rel_path.startswith(
        ("src/", "scripts/", "tests/", "frontend/src/", "frontend/tests/")
    )


def _is_test_file(rel_path: str) -> bool:
    name = Path(rel_path).name
    if rel_path.startswith("tests/") and (
        name.startswith("test_") or name.endswith("_test.py")
    ):
        return True
    if rel_path.startswith("frontend/tests/") and (
        ".test." in name or ".spec." in name
    ):
        return True
    return False


def _is_sized_source_file(rel_path: str) -> bool:
    if rel_path.endswith(".generated.ts"):
        return False
    if not rel_path.endswith((".py", ".ts", ".tsx")):
        return False
    return rel_path.startswith(("src/", "scripts/", "frontend/src/"))


def _count_lines_text(content: str) -> int:
    return len(content.splitlines())


def _base_file_content(repo_root: Path, merge_base: str, rel_path: str) -> str | None:
    return _run_git(repo_root, ["show", f"{merge_base}:{rel_path}"])


def _parse_unified_diff(
    diff_text: str,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[str]]:
    added: list[tuple[str, str]] = []
    removed: list[tuple[str, str]] = []
    deleted_files: list[str] = []

    cur_file = ""
    old_file = ""
    in_header = False

    def _clean_path(raw: str) -> str:
        cleaned = raw.strip()
        if cleaned.startswith(("a/", "b/")):
            return cleaned[2:]
        return cleaned

    for line in diff_text.splitlines():
        if line.startswith("diff "):
            in_header = True
        elif line.startswith("@@"):
            in_header = False
        elif in_header:
            if line.startswith("--- "):
                old_file = _clean_path(line[4:])
            elif line.startswith("+++ "):
                new_file = _clean_path(line[4:])
                if new_file == "/dev/null":
                    cur_file = old_file
                    if old_file:
                        deleted_files.append(old_file)
                else:
                    cur_file = new_file
        elif line.startswith("+"):
            added.append((cur_file, line[1:]))
        elif line.startswith("-"):
            removed.append((cur_file, line[1:]))

    return added, removed, deleted_files


def check_diff_floor(
    added: list[tuple[str, str]],
    removed: list[tuple[str, str]],
    deleted_files: list[str],
) -> list[Finding]:
    """Check diff-scoped floor invariants (suppressions, test skips, protected paths)."""
    findings: list[Finding] = []

    for rel_path, _ in [*added, *removed]:
        norm_path = rel_path.replace("\\", "/")
        if norm_path.startswith(PROTECTED_DATA_PREFIXES):
            findings.append(
                Finding(
                    "protected-data-mutated",
                    norm_path,
                    "Forbidden diff under protected data/",
                )
            )
            break

    for deleted in deleted_files:
        norm_deleted = deleted.replace("\\", "/")
        if _is_test_file(norm_deleted):
            findings.append(
                Finding(
                    "test-file-deleted",
                    norm_deleted,
                    "Test file deleted without authorization",
                )
            )

    added_suppressions: list[tuple[str, str]] = []
    removed_suppression_count = 0

    for rel_path, text in removed:
        norm_path = rel_path.replace("\\", "/")
        if _is_code_or_test_file(norm_path) and _SUPPRESSION_RE.search(text):
            removed_suppression_count += 1

    for rel_path, text in added:
        norm_path = rel_path.replace("\\", "/")
        if not _is_code_or_test_file(norm_path):
            continue
        if _SUPPRESSION_RE.search(text):
            added_suppressions.append((norm_path, text.strip()[:120]))
        if _is_test_file(norm_path) and _SKIP_TEST_RE.search(text):
            findings.append(Finding("test-skip-added", norm_path, text.strip()[:120]))
        if norm_path.startswith("src/finintel/") and ".fill_null(0" in text:
            findings.append(
                Finding("blind-fill-null-zero", norm_path, text.strip()[:120])
            )
        if norm_path.startswith("src/finintel/serving/") and "iter_rows(" in text:
            findings.append(Finding("serving-iter-rows", norm_path, text.strip()[:120]))

    if len(added_suppressions) > removed_suppression_count:
        excess = len(added_suppressions) - removed_suppression_count
        for norm_path, snippet in added_suppressions[-excess:]:
            findings.append(Finding("silenced-checker", norm_path, snippet))

    return findings


def check_serving_boundaries(repo_root: Path) -> list[Finding]:
    """Verify serving layer AST boundaries, docstring hygiene, and queries/ package rules."""
    findings: list[Finding] = []
    serving_dir = repo_root / "src" / "finintel" / "serving"
    if not serving_dir.exists():
        return findings

    raw_token = "data/" + "raw"
    curated_token = "data/" + "curated"

    for py_file in sorted(serving_dir.rglob("*.py")):
        rel_path = py_file.relative_to(repo_root).as_posix()
        source = py_file.read_text(encoding="utf-8")

        if "Verified Sources:" not in source:
            findings.append(
                Finding(
                    "missing-verified-sources",
                    rel_path,
                    "Missing 'Verified Sources:' docstring",
                )
            )
        if raw_token in source or curated_token in source:
            findings.append(
                Finding(
                    "serving-raw-curated-reference",
                    rel_path,
                    "Forbidden raw/curated path token in serving module",
                )
            )
        if "iter_rows(" in source:
            findings.append(
                Finding(
                    "serving-iter-rows",
                    rel_path,
                    "Forbidden iter_rows() in serving module",
                )
            )
        if ".fill_null(0" in source:
            findings.append(
                Finding(
                    "serving-fill-null-zero",
                    rel_path,
                    "Forbidden .fill_null(0) in serving module",
                )
            )

        try:
            tree = ast.parse(source, filename=rel_path)
        except SyntaxError as exc:
            findings.append(Finding("syntax-error", rel_path, str(exc)))
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(FORBIDDEN_SERVING_IMPORTS):
                        findings.append(
                            Finding("serving-forbidden-import", rel_path, alias.name)
                        )
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith(FORBIDDEN_SERVING_IMPORTS):
                    findings.append(
                        Finding("serving-forbidden-import", rel_path, node.module)
                    )

        if rel_path.startswith("src/finintel/serving/queries/"):
            loc = _count_lines_text(source)
            if loc >= MAX_MODULE_LOC:
                findings.append(
                    Finding(
                        "queries-package-size",
                        rel_path,
                        f"serving/queries module has {loc} LOC (must be < {MAX_MODULE_LOC})",
                    )
                )
            for stmt in tree.body:
                if isinstance(stmt, ast.Import):
                    for alias in stmt.names:
                        root_pkg = alias.name.split(".")[0]
                        if root_pkg in PLATFORM_GROUP_TOPLEVEL_FORBIDDEN:
                            findings.append(
                                Finding(
                                    "queries-toplevel-platform-import",
                                    rel_path,
                                    f"Top-level platform import '{alias.name}' breaks mvp-checks",
                                )
                            )
                elif (
                    isinstance(stmt, ast.ImportFrom) and stmt.module and stmt.level == 0
                ):
                    root_pkg = stmt.module.split(".")[0]
                    if root_pkg in PLATFORM_GROUP_TOPLEVEL_FORBIDDEN:
                        findings.append(
                            Finding(
                                "queries-toplevel-platform-import",
                                rel_path,
                                f"Top-level platform import '{stmt.module}' breaks mvp-checks",
                            )
                        )

    return findings


def check_module_sizes(
    repo_root: Path, merge_base: str, changed_paths: set[str]
) -> list[Finding]:
    """Enforce <= 1000 LOC on new/regular modules and <= +10 net LOC on legacy > 1000 LOC files."""
    findings: list[Finding] = []
    for rel_path in sorted(changed_paths):
        if not _is_sized_source_file(rel_path):
            continue
        full_path = repo_root / rel_path
        if not full_path.exists() or not full_path.is_file():
            continue
        current_loc = _count_lines_text(full_path.read_text(encoding="utf-8"))
        base_text = _base_file_content(repo_root, merge_base, rel_path)

        if base_text is None:
            if current_loc > MAX_MODULE_LOC:
                findings.append(
                    Finding(
                        "new-module-too-large",
                        rel_path,
                        f"New file has {current_loc} LOC (limit <= {MAX_MODULE_LOC})",
                    )
                )
        else:
            base_loc = _count_lines_text(base_text)
            if base_loc <= MAX_MODULE_LOC and current_loc > MAX_MODULE_LOC:
                findings.append(
                    Finding(
                        "module-crossed-1000-loc",
                        rel_path,
                        f"File grew from {base_loc} to {current_loc} LOC (limit <= {MAX_MODULE_LOC})",
                    )
                )
            elif base_loc > MAX_MODULE_LOC:
                net_growth = current_loc - base_loc
                if net_growth > LEGACY_NET_GROWTH_LIMIT:
                    findings.append(
                        Finding(
                            "legacy-module-net-growth",
                            rel_path,
                            f"Legacy file ({base_loc} LOC) grew by +{net_growth} LOC "
                            f"(limit <= +{LEGACY_NET_GROWTH_LIMIT})",
                        )
                    )
    return findings


def check_constraints_tamper(
    repo_root: Path, merge_base: str, changed_paths: set[str]
) -> list[Finding]:
    """Prevent deleting Floor bullets in CONSTRAINTS.md within a feature change."""
    findings: list[Finding] = []
    if "CONSTRAINTS.md" not in changed_paths:
        return findings
    touches_prod = any(p.startswith(("src/", "frontend/src/")) for p in changed_paths)
    if not touches_prod:
        return findings

    base_constraints = _base_file_content(repo_root, merge_base, "CONSTRAINTS.md")
    if base_constraints is None:
        return findings

    current_path = repo_root / "CONSTRAINTS.md"
    if not current_path.exists():
        findings.append(
            Finding(
                "constraints-deleted", "CONSTRAINTS.md", "CONSTRAINTS.md was deleted"
            )
        )
        return findings

    current_constraints = current_path.read_text(encoding="utf-8")

    def _floor_bullets(md_text: str) -> set[str]:
        bullets: set[str] = set()
        in_floor = False
        for line in md_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("## Floor"):
                in_floor = True
                continue
            if in_floor and stripped.startswith("## "):
                break
            if in_floor and stripped.startswith("- "):
                bullets.add(stripped)
        return bullets

    missing_bullets = _floor_bullets(base_constraints) - _floor_bullets(
        current_constraints
    )
    for bullet in sorted(missing_bullets):
        findings.append(
            Finding("constraints-floor-weakened", "CONSTRAINTS.md", bullet[:120])
        )
    return findings


def check_frontend_compliance_text(rel_path: str, content: str) -> list[Finding]:
    """Check one frontend/src file against NĐ 88/2014 & Securities Law 56/2024 wording bans."""
    findings: list[Finding] = []
    for line_no, line in enumerate(content.splitlines(), start=1):
        match = _FORBIDDEN_COMPLIANCE_WORDING_RE.search(line)
        if match:
            findings.append(
                Finding(
                    "forbidden-compliance-wording",
                    f"{rel_path}:{line_no}",
                    f"Forbidden legal wording matched: {match.group(0)!r}",
                )
            )
    return findings


def check_frontend_compliance(repo_root: Path) -> list[Finding]:
    """Scan all frontend/src/**/*.{ts,tsx} files for prohibited credit-rating/advisory phrases."""
    findings: list[Finding] = []
    frontend_src = repo_root / "frontend" / "src"
    if not frontend_src.exists():
        return findings
    for src_file in sorted([*frontend_src.rglob("*.ts"), *frontend_src.rglob("*.tsx")]):
        rel_path = src_file.relative_to(repo_root).as_posix()
        try:
            content = src_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        findings.extend(check_frontend_compliance_text(rel_path, content))
    return findings


def run_checks(
    repo_root: Path, explicit_base: str | None = None
) -> tuple[int, list[Finding]]:
    """Execute all mechanical constraint checks and return ``(exit_code, findings)``."""
    top = _run_git(repo_root, ["rev-parse", "--show-toplevel"])
    if top is None or not top.strip():
        print("check_constraints: error: not inside a git work tree", file=sys.stderr)
        return 2, []

    root = Path(top.strip())
    merge_base = _resolve_merge_base(root, explicit_base)
    if merge_base is None:
        print(
            "check_constraints: error: could not resolve git merge-base",
            file=sys.stderr,
        )
        return 2, []

    tracked_diff = _run_git(root, ["diff", "--unified=0", merge_base, "--"])
    if tracked_diff is None:
        print("check_constraints: error: git diff failed", file=sys.stderr)
        return 2, []

    untracked_out = _run_git(root, ["ls-files", "--others", "--exclude-standard"])
    if untracked_out is None:
        print("check_constraints: error: git ls-files failed", file=sys.stderr)
        return 2, []

    untracked_files = [
        line.strip().replace("\\", "/")
        for line in untracked_out.splitlines()
        if line.strip()
    ]

    added, removed, deleted_files = _parse_unified_diff(tracked_diff)
    changed_paths: set[str] = {p.replace("\\", "/") for p, _ in [*added, *removed] if p}

    for rel_untracked in untracked_files:
        changed_paths.add(rel_untracked)
        untracked_path = root / rel_untracked
        if untracked_path.is_file() and _is_code_or_test_file(rel_untracked):
            try:
                content = untracked_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for line in content.splitlines():
                added.append((rel_untracked, line))

    findings: list[Finding] = []
    findings.extend(check_diff_floor(added, removed, deleted_files))
    findings.extend(check_serving_boundaries(root))
    findings.extend(check_module_sizes(root, merge_base, changed_paths))
    findings.extend(check_constraints_tamper(root, merge_base, changed_paths))
    findings.extend(check_frontend_compliance(root))

    return (1 if findings else 0), findings


def _run_selftest() -> int:
    """Verify the checker catches deliberate violations and passes clean controls."""
    clean_added = [("src/finintel/example.py", "x = 1 + 2")]
    assert check_diff_floor(clean_added, [], []) == [], "Clean control must pass"

    sup_line = "x = 1  #" + " type: " + "ignore[assignment]"
    viol_sup = check_diff_floor([("src/finintel/example.py", sup_line)], [], [])
    assert any(f.rule == "silenced-checker" for f in viol_sup), (
        "Must catch new suppression"
    )

    moved_sup = check_diff_floor(
        [("src/finintel/serving/queries/a.py", sup_line)],
        [("src/finintel/serving/queries.py", sup_line)],
        [],
    )
    assert moved_sup == [], (
        "Behavior-preserving moved suppression must have 0 net increase"
    )

    skip_line = "@pytest.mark." + "skip(reason='temp')"
    viol_skip = check_diff_floor([("tests/unit/test_demo.py", skip_line)], [], [])
    assert any(f.rule == "test-skip-added" for f in viol_skip), "Must catch test skip"

    viol_del = check_diff_floor([], [], ["tests/unit/test_demo.py"])
    assert any(f.rule == "test-file-deleted" for f in viol_del), (
        "Must catch deleted test file"
    )

    clean_fe = check_frontend_compliance_text(
        "frontend/src/lib/compliance.ts",
        "Nhóm cảnh báo rủi ro tài chính định lượng (R1–R5); không phải khuyến nghị đầu tư.",
    )
    assert clean_fe == [], "Clean compliance wording must pass"

    for bad_phrase in (
        "Xếp hạng tín nhiệm nội bộ",
        "Bậc tín nhiệm R4",
        "Khuyến nghị mua/bán cổ phiếu",
    ):
        viol_fe = check_frontend_compliance_text(
            "frontend/src/lib/domain-labels.ts",
            bad_phrase,
        )
        assert any(f.rule == "forbidden-compliance-wording" for f in viol_fe), (
            f"Must catch forbidden compliance wording: {bad_phrase}"
        )

    print(
        "check_constraints --selftest: PASS (all violation & control assertions verified)"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="FinIntel V2.0 mechanical constraints guard."
    )
    parser.add_argument(
        "--base",
        default=None,
        help="Git base ref to diff against (defaults to main / origin/main).",
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="Run built-in violation vs clean-control self-tests.",
    )
    args = parser.parse_args(argv)

    if args.selftest:
        return _run_selftest()

    exit_code, findings = run_checks(Path.cwd(), explicit_base=args.base)
    if exit_code == 2:
        return 2
    if not findings:
        print("check_constraints: PASS (0 floor or ratchet violations)")
        return 0

    print(f"check_constraints: FAIL ({len(findings)} violation(s)):", file=sys.stderr)
    for item in findings:
        print(f"  [{item.rule}] {item.path}: {item.detail}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
