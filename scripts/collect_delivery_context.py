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


def select_task(root: Path, changed: str, requested_task: str | None) -> Path | None:
    if requested_task:
        task_path = Path(requested_task)
        return task_path if task_path.is_absolute() else root / task_path

    changed_tasks = []
    for line in changed.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        candidate = Path(parts[-1])
        if (
            candidate.parent == Path("tasks")
            and candidate.suffix in {".yaml", ".yml"}
            and candidate.name != "TASK_TEMPLATE.yaml"
        ):
            changed_tasks.append(root / candidate)

    changed_tasks = sorted(set(changed_tasks))
    if len(changed_tasks) == 1:
        return changed_tasks[0]
    if len(changed_tasks) > 1:
        names = ", ".join(str(path.relative_to(root)) for path in changed_tasks)
        raise ValueError(f"multiple changed task files found; pass --task explicitly: {names}")

    candidates = sorted(
        path
        for pattern in ("*.yaml", "*.yml")
        for path in (root / "tasks").glob(pattern)
        if path.name != "TASK_TEMPLATE.yaml"
    )
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        names = ", ".join(str(path.relative_to(root)) for path in candidates)
        raise ValueError(f"multiple task files found; pass --task explicitly: {names}")
    return None


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

    task_path = select_task(root, changed, args.task)
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
