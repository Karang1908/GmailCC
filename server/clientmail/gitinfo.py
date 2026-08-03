"""Git evidence for the summary.

The whole point of pinning a baseline is that the email describes what the diff
actually says, not what the conversation remembers. Note that this deliberately
reports *working tree* changes as well as commits -- Claude often finishes a task
without committing, and a commit-only view would silently drop that work from
the client's update.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(Exception):
    pass


def _git(repo: str, *args: str, check: bool = True) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError as exc:
        raise GitError("git is not installed or not on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {' '.join(args)} timed out after 30s") from exc
    if check and proc.returncode != 0:
        raise GitError((proc.stderr or proc.stdout).strip() or f"git {' '.join(args)} failed")
    return proc.stdout.strip()


def repo_root(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        raise GitError(f"Path does not exist: {p}")
    return _git(str(p), "rev-parse", "--show-toplevel")


def head_sha(repo: str) -> str:
    return _git(repo, "rev-parse", "HEAD")


def current_branch(repo: str) -> str:
    return _git(repo, "rev-parse", "--abbrev-ref", "HEAD", check=False) or "(detached)"


def is_clean(repo: str) -> bool:
    return _git(repo, "status", "--porcelain", check=False) == ""


def changes_since(repo: str, baseline: str, max_files: int = 200) -> dict:
    """Commits since baseline, plus every file that differs from it right now
    (committed, staged, unstaged) and every untracked file."""
    log_raw = _git(repo, "log", "--format=%h\t%s", f"{baseline}..HEAD", check=False)
    commits = []
    for line in log_raw.split("\n"):
        if "\t" in line:
            sha, _, subject = line.partition("\t")
            commits.append({"sha": sha, "subject": subject})

    stat = _git(repo, "diff", "--stat", baseline, check=False)
    numstat_raw = _git(repo, "diff", "--numstat", baseline, check=False)
    files = []
    for line in numstat_raw.split("\n"):
        parts = line.split("\t")
        if len(parts) == 3:
            added, removed, path = parts
            files.append({
                "path": path,
                "added": None if added == "-" else int(added),
                "removed": None if removed == "-" else int(removed),
            })

    untracked = [
        line for line in
        _git(repo, "ls-files", "--others", "--exclude-standard", check=False).split("\n")
        if line
    ]

    truncated = len(files) > max_files
    return {
        "baseline": baseline,
        "head": head_sha(repo),
        "branch": current_branch(repo),
        "commits": commits,
        "files_changed": files[:max_files],
        "files_changed_count": len(files),
        "untracked": untracked[:max_files],
        "untracked_count": len(untracked),
        "truncated": truncated,
        "diffstat": stat,
        "working_tree_clean": is_clean(repo),
    }
