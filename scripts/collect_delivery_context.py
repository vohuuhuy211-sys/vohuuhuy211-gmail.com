#!/usr/bin/env python3
"""Collect reproducible delivery evidence for CI and reviewers."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=False
    )
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", help="Task YAML path; otherwise auto-detect changed tasks")
    parser.add_argument("--test-log", default="artifacts/test-results.txt")
    parser.add_argument("--output", default="delivery-context")
    parser.add_argument("--base", help="Base git ref, e.g. origin/main")
    args = parser.parse_args()

    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
    output = (root / args.output).resolve()
    if root not in output.parents:
        raise ValueError("output must be inside the repository")
    output.mkdir(parents=True, exist_ok=True)

    base = args.base
    if not base:
        base = git(root, "merge-base", "HEAD", "origin/main", check=False).strip()
    diff_range = f"{base}...HEAD" if base else "HEAD"

    changed = git(root, "diff", "--name-status", diff_range, check=False)
    if not changed.strip():
        changed = git(root, "status", "--short", "--untracked-files=all")
    (output / "changed-files.txt").write_text(changed, encoding="utf-8")
    (output / "diff.patch").write_text(
        git(root, "diff", "--binary", diff_range, check=False), encoding="utf-8"
    )

    task_path = Path(args.task) if args.task else None
    if task_path is None:
        candidates = sorted((root / "tasks").glob("*.yaml"))
        task_path = candidates[0] if candidates else None
    elif not task_path.is_absolute():
        task_path = root / task_path
    if task_path and task_path.is_file():
        shutil.copyfile(task_path, output / "task.yaml")
        task_source = str(task_path.relative_to(root))
    else:
        (output / "task.yaml").write_text(
            "# No task file was found. Delivery is incomplete until one is supplied.\n",
            encoding="utf-8",
        )
        task_source = None

    test_log = Path(args.test_log)
    if not test_log.is_absolute():
        test_log = root / test_log
    if test_log.is_file():
        shutil.copyfile(test_log, output / "test-results.txt")
        test_status = "collected"
    else:
        (output / "test-results.txt").write_text(
            f"Test log not found: {test_log.relative_to(root)}\n", encoding="utf-8"
        )
        test_status = "missing"

    generated_dirs = ("dist", "build", "coverage", "reports", "artifacts", "outputs")
    generated = []
    for dirname in generated_dirs:
        directory = root / dirname
        if directory.is_dir() and directory.resolve() != output:
            generated.extend(
                str(path.relative_to(root)) for path in directory.rglob("*") if path.is_file()
            )
    generated = sorted(set(generated))
    (output / "generated-files.txt").write_text(
        "\n".join(generated) + ("\n" if generated else ""), encoding="utf-8"
    )

    summary = {
        "schema_version": "1.0",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "commit": git(root, "rev-parse", "--verify", "HEAD", check=False).strip() or None,
        "base": base or None,
        "task_source": task_source,
        "test_log_status": test_status,
        "changed_file_entries": len([line for line in changed.splitlines() if line]),
        "generated_file_count": len(generated),
    }
    (output / "delivery-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
