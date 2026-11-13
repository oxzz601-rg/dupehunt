"""dupehunt: find duplicates, then decide what to do about them."""

from .core import (
    Cluster,
    FileEntry,
    ScanResult,
    human_bytes,
    link_duplicates,
    plan,
    quarantine,
    render_html,
    render_markdown,
    scan,
)

__version__ = "0.1.0"

__all__ = [
    "Cluster",
    "FileEntry",
    "ScanResult",
    "human_bytes",
    "link_duplicates",
    "plan",
    "quarantine",
    "render_html",
    "render_markdown",
    "scan",
]
