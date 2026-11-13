"""Scan for duplicate files, cluster near-duplicate images, and plan cleanup.

Nothing is deleted anywhere in this module. ``quarantine`` moves copies into a
folder you choose, and ``link_duplicates`` replaces a copy with a hard link to
one kept file, in both cases only when ``apply=True``.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import pathlib
import shutil
import stat
from dataclasses import dataclass, replace

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", ".cache",
    ".Trash", ".Trashes", "System Volume Information", "$RECYCLE.BIN", "lost+found",
}

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
CHUNK = 1024 * 1024
DEFAULT_THRESHOLD = 6


def human_bytes(value):
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return "{:.0f} {}".format(size, unit)
            return "{:.1f} {}".format(size, unit)
        size /= 1024
    return "{:.1f} TB".format(size)


@dataclass
class FileEntry:
    path: pathlib.Path
    size: int
    digest: str = ""
    image_hash: int = None
    mtime: float = 0.0

    @property
    def suffix(self):
        return self.path.suffix.lower()


@dataclass
class Cluster:
    kind: str
    files: tuple = ()
    keep: pathlib.Path = None

    @property
    def wasted_bytes(self):
        if not self.files:
            return 0
        return sum(entry.size for entry in self.files) - max(entry.size for entry in self.files)

    @property
    def duplicates(self):
        return tuple(entry for entry in self.files if entry.path != self.keep)


@dataclass
class ScanResult:
    root: pathlib.Path
    files: tuple = ()
    clusters: tuple = ()
    errors: tuple = ()

    @property
    def scanned(self):
        return len(self.files)

    @property
    def wasted_bytes(self):
        return sum(cluster.wasted_bytes for cluster in self.clusters)

    @property
    def duplicate_count(self):
        return sum(len(cluster.duplicates) for cluster in self.clusters)

    def to_dict(self):
        return {
            "root": str(self.root),
            "scanned": self.scanned,
            "clusters": [
                {
                    "kind": cluster.kind,
                    "keep": str(cluster.keep) if cluster.keep else None,
                    "files": [str(entry.path) for entry in cluster.files],
                    "wasted_bytes": cluster.wasted_bytes,
                }
                for cluster in self.clusters
            ],
            "duplicate_count": self.duplicate_count,
            "wasted_bytes": self.wasted_bytes,
            "errors": list(self.errors),
        }


def _digest(path):
    hasher = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _dhash(path, size=8):
    """Difference hash: 64 bits describing the image's rough structure."""
    from PIL import Image

    with Image.open(path) as image:
        small = image.convert("L").resize((size + 1, size), Image.LANCZOS)
        pixels = list(small.getdata())
    bits = 0
    width = size + 1
    for row in range(size):
        for column in range(size):
            left = pixels[row * width + column]
            right = pixels[row * width + column + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def _hamming(first, second):
    return bin(first ^ second).count("1")


def scan(root, min_size=1, include=(), exclude=(), images=True, threshold=DEFAULT_THRESHOLD):
    """Walk ``root`` and return exact duplicate clusters plus similar images."""
    root = pathlib.Path(root)
    if not root.is_dir():
        raise NotADirectoryError("{} is not a directory".format(root))
    files, errors = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRS and not name.startswith("."))
        for name in sorted(filenames):
            path = pathlib.Path(dirpath) / name
            relative = path.relative_to(root).as_posix()
            if include and not any(fnmatch.fnmatch(relative, pattern) for pattern in include):
                continue
            if any(fnmatch.fnmatch(relative, pattern) for pattern in exclude):
                continue
            try:
                details = path.lstat()
            except OSError as exc:
                errors.append("{}: {}".format(relative, exc))
                continue
            if not stat.S_ISREG(details.st_mode) or details.st_size < min_size:
                continue
            files.append(FileEntry(path=path, size=details.st_size, mtime=details.st_mtime))

    by_size = {}
    for entry in files:
        by_size.setdefault(entry.size, []).append(entry)

    exact_clusters = []
    for size in sorted(by_size):
        group = by_size[size]
        if len(group) < 2:
            continue
        buckets = {}
        for entry in group:
            try:
                entry.digest = _digest(entry.path)
            except OSError as exc:
                errors.append("{}: {}".format(entry.path, exc))
                continue
            buckets.setdefault(entry.digest, []).append(entry)
        for digest in sorted(buckets):
            same = buckets[digest]
            if len(same) > 1:
                exact_clusters.append(Cluster(kind="exact", files=tuple(same)))

    similar_clusters = []
    if images:
        candidates = [entry for entry in files if entry.suffix in IMAGE_SUFFIXES]
        hashed = []
        for entry in candidates:
            try:
                entry.image_hash = _dhash(entry.path)
            except Exception as exc:  # Pillow missing or file unreadable
                errors.append("{}: {}".format(entry.path, exc))
                continue
            hashed.append(entry)
        similar_clusters = _cluster_images(hashed, threshold, exact_clusters)

    return ScanResult(
        root=root,
        files=tuple(files),
        clusters=tuple(exact_clusters + similar_clusters),
        errors=tuple(errors),
    )


def _cluster_images(entries, threshold, exact_clusters):
    if len(entries) < 2 or len(entries) > 5000:
        return []
    exact_members = {entry.path for cluster in exact_clusters for entry in cluster.files}
    parents = list(range(len(entries)))

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left, right):
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[max(left_root, right_root)] = min(left_root, right_root)

    for first in range(len(entries)):
        for second in range(first + 1, len(entries)):
            if entries[first].size == entries[second].size and entries[first].digest == entries[second].digest:
                continue
            if _hamming(entries[first].image_hash, entries[second].image_hash) <= threshold:
                union(first, second)

    groups = {}
    for index in range(len(entries)):
        groups.setdefault(find(index), []).append(entries[index])
    clusters = []
    for root_index in sorted(groups):
        group = groups[root_index]
        if len(group) < 2:
            continue
        if all(entry.path in exact_members for entry in group):
            continue
        clusters.append(Cluster(kind="similar", files=tuple(sorted(group, key=lambda entry: str(entry.path)))))
    return clusters


def choose_keep(cluster, strategy="oldest"):
    """Pick the file to keep: ``oldest``, ``newest``, ``shortest`` or ``first``."""
    files = list(cluster.files)
    if strategy == "newest":
        return max(files, key=lambda entry: (entry.mtime, str(entry.path)))
    if strategy == "shortest":
        return min(files, key=lambda entry: (len(str(entry.path)), str(entry.path)))
    if strategy == "first":
        return files[0]
    return min(files, key=lambda entry: (entry.mtime, str(entry.path)))


def plan(result, strategy="oldest"):
    """Decide which file to keep in every cluster."""
    clusters = tuple(replace(cluster, keep=choose_keep(cluster, strategy).path) for cluster in result.clusters)
    return replace(result, clusters=clusters)


def quarantine(result, destination, apply=False):
    """Move duplicates into ``destination``, keeping their relative paths."""
    destination = pathlib.Path(destination)
    actions = []
    for cluster in result.clusters:
        for entry in cluster.duplicates:
            try:
                relative = entry.path.relative_to(result.root)
            except ValueError:
                relative = pathlib.Path(entry.path.name)
            target = destination / relative
            actions.append({"from": str(entry.path), "to": str(target), "bytes": entry.size})
            if apply:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(entry.path), str(target))
    return {
        "apply": bool(apply),
        "count": len(actions),
        "bytes": sum(action["bytes"] for action in actions),
        "actions": actions,
    }


def link_duplicates(result, apply=False):
    """Replace duplicates with hard links to the kept file (same filesystem only)."""
    actions = []
    for cluster in result.clusters:
        if cluster.keep is None:
            raise ValueError("plan() the result before linking")
        for entry in cluster.duplicates:
            actions.append({"duplicate": str(entry.path), "link_to": str(cluster.keep), "bytes": entry.size})
            if replace_with_link(entry.path, cluster.keep, apply):
                continue
    return {"apply": bool(apply), "count": len(actions), "actions": actions}


def replace_with_link(path, target, apply=False):
    """Swap ``path`` for a hard link to ``target`` without a window for data loss."""
    import tempfile

    if not apply:
        return True
    handle, temporary = tempfile.mkstemp(prefix=".dupehunt-", dir=str(pathlib.Path(path).parent))
    os.close(handle)
    os.unlink(temporary)
    try:
        os.link(target, temporary)
        os.replace(temporary, path)
    except OSError:
        if os.path.exists(temporary):
            os.unlink(temporary)
        return False
    return True


def render_markdown(result):
    lines = [
        "# dupehunt report",
        "",
        "Scanned **{}** files under `{}`.".format(result.scanned, result.root),
        "",
        "- duplicate copies: **{}**".format(result.duplicate_count),
        "- reclaimable space: **{}**".format(human_bytes(result.wasted_bytes)),
        "- clusters: {}".format(len(result.clusters)),
        "",
    ]
    for index, cluster in enumerate(result.clusters, start=1):
        lines.append("## {}. {} duplicate ({} files, {})".format(
            index, cluster.kind, len(cluster.files), human_bytes(cluster.wasted_bytes)))
        lines.append("")
        if cluster.keep:
            lines.append("- keep: `{}`".format(cluster.keep))
        for entry in cluster.files:
            marker = "keep" if cluster.keep == entry.path else "copy"
            lines.append("- {}: `{}` ({}) ".format(marker, entry.path, human_bytes(entry.size)))
        lines.append("")
    if result.errors:
        lines.append("## Skipped")
        lines.append("")
        for error in result.errors[:50]:
            lines.append("- {}".format(error))
    return "\n".join(lines).rstrip() + "\n"


def render_html(result):
    import html

    rows = []
    for cluster in result.clusters:
        for entry in cluster.files:
            rows.append(
                "<tr><td>{}</td><td>{}</td><td><code>{}</code></td><td>{}</td></tr>".format(
                    html.escape(cluster.kind),
                    "keep" if cluster.keep == entry.path else "copy",
                    html.escape(str(entry.path)),
                    human_bytes(entry.size),
                )
            )
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>dupehunt report</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:70rem;margin:2rem auto;padding:0 1rem}}
table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #e4e6eb;padding:.4rem .6rem;text-align:left}}
code{{background:#f2f3f5;padding:.1rem .3rem}}
</style></head><body>
<h1>dupehunt report</h1>
<p>{files} files scanned, {count} duplicate copies, {wasted} reclaimable.</p>
<table><thead><tr><th>kind</th><th>role</th><th>path</th><th>size</th></tr></thead><tbody>
{rows}
</tbody></table></body></html>
""".format(
        files=result.scanned,
        count=result.duplicate_count,
        wasted=human_bytes(result.wasted_bytes),
        rows="\n".join(rows),
    )
