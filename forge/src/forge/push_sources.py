"""Deliver accepted loop work to the central product-sources repository.

Jan's standing rule (2026-10-29): every Forge project keeps its sources in the
central repository under repos/<project>/. After each DONE handoff, this module
delivers the whole accepted tree of the project as ONE squashed commit to the
shared delivery branch configured in the operator TOML ([loop] sources_remote,
sources_branch, sources_script). The engine itself only reads the remote; the
operator-owned script named by sources_script performs the single publishing
step, solely on Jan's explicit order. Nothing here touches product commits or
creates remotes on product repos.
Branch conflict policy: read the branch head first; a moved head means someone
else delivered in the meantime — the machine FAILS CLOSED with a durable BLOCK
instead of rewriting shared history. One script call, one commit, one readback.
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


class PushBlocked(RuntimeError):
    """Durable local-block or fail-closed refusal; nothing partial was pushed."""


@dataclass(frozen=True)
class Target:
    """Delivery target taken from config.loop (operator TOML), never from module constants."""
    remote: str
    branch: str
    script: Path

    @classmethod
    def of(cls, config) -> "Target":
        policy = config.loop or {}
        values = [policy.get(k) for k in ("sources_remote", "sources_branch", "sources_script")]
        if not all(values):
            raise PushBlocked("product-sources delivery is not configured: set loop.sources_remote, "
                              "loop.sources_branch and loop.sources_script in the operator TOML")
        return cls(values[0], values[1], Path(values[2]))


@dataclass(frozen=True)
class PushResult:
    project: str
    target_tree: str          # tree hash of the accepted product commit
    parent_commit: str        # previous head of the shared branch before our commit
    commit: str               # the squashed commit we pushed
    pushed_at: str


# ---- delivered-script contract ------------------------------------------------
# <loop.sources_script> <remote> <branch> <project-repo> <project-ref> <tree> <old-branch-head-or-EMPTY> <commit-message-file>
# stdout (last line): JSON  {"parent": "<sha>", "commit": "<sha>"}
# exit 0 = pushed and readback matches; non-zero = refused, nothing landed or
# the readback mismatched (the caller must treat that as durable local-block).

def deliver(config, project_ref: str, *, clock=time.time) -> PushResult | None:
    """Push the accepted tree of config.target_repo@project_ref as one squash.

    Returns PushResult, None when the branch was already at this exact tree
    (idempotent re-delivery), or raises PushBlocked.
    """
    target = Path(config.target_repo)
    project = target.name
    tree = _git(target, "rev-parse", project_ref + "^{tree}")
    if not tree:
        raise PushBlocked(f"project ref {project_ref} does not resolve in {target}")
    dest = Target.of(config)
    _guard_tree(target, tree, dest)

    scratch = Path(config.state_dir) / "push-sources" / "readback"
    old = _shared_head(dest)
    if old and _shared_subtree(dest, scratch, old, project) == tree:
        return None  # repos/<project> already holds this exact tree, no-op

    message_file = _message(config, project, project_ref, tree)
    answer = _call_script(dest.script, dest.remote, dest.branch, target, project_ref, tree, old, message_file)
    try:
        row = json.loads(answer.strip().splitlines()[-1])
        commit, parent = row["commit"], row["parent"]
    except (ValueError, KeyError) as exc:
        raise PushBlocked(f"push script returned unparsable result: {exc}") from exc
    if parent != (old or ""):
        raise PushBlocked(f"branch moved during delivery ({old} -> {parent}); fail closed, retry with fresh readback")
    if _shared_subtree(dest, scratch, commit, project) != tree:
        raise PushBlocked(f"delivered repos/{project} tree does not match the accepted product tree; fail closed")
    pushed_at = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(clock()))
    return PushResult(project=project, target_tree=tree, parent_commit=old or "",
                      commit=commit, pushed_at=pushed_at)


def _shared_head(dest: Target) -> str:
    out = subprocess.run(["git", "ls-remote", dest.remote, dest.branch],
                         capture_output=True, text=True, timeout=60)
    if out.returncode:
        raise PushBlocked("cannot read the shared branch: " + out.stderr.strip()[-200:])
    text = out.stdout.strip()
    if not text:
        return ""
    oid = text.split()[0]
    if not all(c in "0123456789abcdef" for c in oid):
        raise PushBlocked("shared branch head is not a plain SHA")
    return oid


def _shared_subtree(dest: Target, scratch: Path, commit: str, project: str) -> str:
    """Tree at repos/<project> of a shared-branch commit (fresh fetch + readback).

    The branch root holds every project, so only the per-project subtree can be
    compared with the accepted product tree.
    """
    scratch.mkdir(parents=True, exist_ok=True)
    if not (scratch / ".git").exists() and not (scratch / "HEAD").exists():
        subprocess.run(["git", "clone", "--no-checkout", "--quiet", dest.remote, str(scratch)],
                       capture_output=True, text=True, timeout=300)
    r = subprocess.run(["git", "-C", str(scratch), "fetch", "--no-tags", "--quiet", dest.remote, dest.branch],
                       capture_output=True, text=True, timeout=300)
    if r.returncode:
        raise PushBlocked("readback fetch failed: " + r.stderr.strip()[-200:])
    r = subprocess.run(["git", "-C", str(scratch), "rev-parse", "--verify", "--quiet",
                        f"{commit}:repos/{project}"],
                       capture_output=True, text=True, timeout=60)
    return r.stdout.strip() if r.returncode == 0 else ""


def _guard_tree(target: Path, tree: str, dest: Target) -> None:
    """Refuse to publish paths that leak mission state or credentials.

    The operator state directory is the top-level home directory that hosts the
    configured delivery script; a product tree must never carry a copy of it.
    """
    out = subprocess.run(["git", "-C", target, "ls-tree", "-r", "--name-only", tree],
                         capture_output=True, text=True, timeout=60)
    if out.returncode:
        raise PushBlocked("cannot list the accepted tree: " + out.stderr.strip()[-200:])
    forbidden = ("forge.db", ".env", "id_rsa", "id_ed25519", "credentials")
    state = _operator_state_dir(dest.script)
    offenders = [p for p in out.stdout.splitlines()
                 if any(f in Path(p).name for f in forbidden) or (state and p.startswith(state + "/"))]
    if offenders:
        raise PushBlocked("accepted tree contains mission-state or secret-like paths: " + ", ".join(offenders[:5]))


def _operator_state_dir(script: Path) -> str:
    try:
        top = script.expanduser().relative_to(Path.home()).parts[0]
    except (ValueError, IndexError):
        return ""
    return top if top.startswith(".") else ""


def _message(config, project: str, project_ref: str, tree: str) -> Path:
    path = Path(config.state_dir) / "push-sources" / f"{int(time.time())}-{tree[:12]}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"{project}: squashed accepted tree {tree[:12]} ({project_ref})\n\n"
        f"Delivered by Forge from local project {config.target_repo}."[
            :4000])
    return path


def _git(target: Path, *args) -> str:
    out = subprocess.run(["git", "-C", target, *args], capture_output=True, text=True, timeout=60)
    return out.stdout.strip() if out.returncode == 0 else ""


def _call_script(script: Path, *args) -> str:
    out = subprocess.run(["bash", str(script), *[str(a) for a in args]],
                         capture_output=True, text=True, timeout=600)
    if out.returncode:
        raise PushBlocked("deliver script refused: " + out.stderr.strip()[-300:])
    return out.stdout
