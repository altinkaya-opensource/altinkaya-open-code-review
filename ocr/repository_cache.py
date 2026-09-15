"""Persist Git objects without exposing a writable shared checkout to reviews."""

import errno
import os
import re
import shutil


def copy_objects(source, destination):
    """Link immutable Git objects locally; copy when temporary storage is another device."""
    destination.mkdir(parents=True, exist_ok=True)
    for directory in source.iterdir():
        if directory.is_symlink() or not directory.is_dir():
            continue
        if directory.name != "pack" and not re.fullmatch(r"[0-9a-f]{2}", directory.name):
            continue  # Never propagate alternates or other external object-store paths.
        target = destination / directory.name
        target.mkdir(exist_ok=True)
        for item in directory.iterdir():
            if item.is_symlink() or not item.is_file():
                continue
            if directory.name == "pack":
                if not re.fullmatch(r"pack-[0-9a-f]+\.(pack|idx|rev|promisor)", item.name):
                    continue
            elif not re.fullmatch(r"[0-9a-f]{38}", item.name):
                continue
            output = target / item.name
            if output.exists():
                continue
            try:
                os.link(item, output)
            except OSError as error:
                if error.errno != errno.EXDEV:
                    raise
                pending = output.with_name(output.name + ".pending")
                try:
                    shutil.copy2(item, pending)
                    pending.replace(output)
                finally:
                    pending.unlink(missing_ok=True)


def copy_shallow(source, destination):
    """Keep the shallow boundary consistent with the cached commit graph."""
    original = source / "shallow"
    target = destination / "shallow"
    if original.is_file():
        pending = destination / "shallow.pending"
        shutil.copyfile(original, pending)
        pending.replace(target)
    else:
        target.unlink(missing_ok=True)


def cached_refs(cache, git):
    """Return only the two refs owned by this cache implementation."""
    refs = {}
    for name in ("base", "head"):
        try:
            refs[name] = git(cache, "rev-parse", "--verify", f"refs/heads/review-{name}").strip()
        except RuntimeError:
            continue
        if not re.fullmatch(r"[0-9a-f]{40}", refs[name]):
            raise ValueError("Invalid cached commit")
    return refs


def restore(cache, repo, repository, git):
    """Seed a fresh repository with local objects, never a cache config or worktree."""
    if not (cache / "HEAD").is_file():
        git(cache, "init", "--bare", "--quiet")
        git(cache, "remote", "add", "origin", f"https://github.com/{repository}.git")
    copy_objects(cache / "objects", repo / ".git" / "objects")
    copy_shallow(cache, repo / ".git")
    refs = cached_refs(cache, git)
    for name, sha in refs.items():
        git(repo, "update-ref", f"refs/heads/review-{name}", sha)
    # Cached packs can omit blobs; Git may fetch those through the trusted origin.
    git(repo, "config", "remote.origin.promisor", "true")
    git(repo, "config", "remote.origin.partialclonefilter", "blob:none")
    return refs


def save(cache, repo, base, head, git):
    """Retain fetched and hydrated objects after a successful preparation."""
    copy_objects(repo / ".git" / "objects", cache / "objects")
    copy_shallow(repo / ".git", cache)
    for name, sha in (("base", base), ("head", head)):
        git(cache, "update-ref", f"refs/heads/review-{name}", sha)
    git(cache, "config", "remote.origin.promisor", "true")
    git(cache, "config", "remote.origin.partialclonefilter", "blob:none")
    # Synchronous auto-GC stays inside the caller's lock; linked review objects survive it.
    git(cache, "-c", "gc.autoDetach=false", "gc", "--auto")
    (cache / "last-used").touch()
