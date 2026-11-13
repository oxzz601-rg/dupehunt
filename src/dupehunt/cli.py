"""The ``dupehunt`` command line."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from . import __version__
from .core import human_bytes, link_duplicates, plan, quarantine, render_html, render_markdown, scan


def _parse_size(value):
    text = str(value).strip().lower()
    units = {"b": 1, "k": 1024, "kb": 1024, "m": 1024**2, "mb": 1024**2, "g": 1024**3, "gb": 1024**3}
    for suffix in ("kb", "mb", "gb", "k", "m", "g", "b"):
        if text.endswith(suffix):
            return int(float(text[: -len(suffix)] or 0) * units[suffix])
    return int(float(text))


def _scan(args):
    return plan(
        scan(
            args.path,
            min_size=_parse_size(args.min_size),
            include=tuple(args.include),
            exclude=tuple(args.exclude),
            images=not args.no_images,
            threshold=args.threshold,
        ),
        strategy=args.keep,
    )


def cmd_scan(args):
    result = _scan(args)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(render_markdown(result), end="")
    if args.html:
        pathlib.Path(args.html).write_text(render_html(result), encoding="utf-8")
        print("wrote {}".format(args.html), file=sys.stderr)
    return 0


def cmd_quarantine(args):
    result = _scan(args)
    outcome = quarantine(result, args.to, apply=args.apply)
    if args.json:
        print(json.dumps(outcome, indent=2))
        return 0
    verb = "moved" if args.apply else "would move"
    print("{} {} duplicate file(s), freeing {}".format(verb, outcome["count"], human_bytes(outcome["bytes"])))
    for action in outcome["actions"][: args.show]:
        print("  {} -> {}".format(action["from"], action["to"]))
    if outcome["count"] > args.show:
        print("  ... {} more".format(outcome["count"] - args.show))
    if not args.apply:
        print("\nnothing was changed. Re-run with --apply to move the files.")
    return 0


def cmd_link(args):
    result = _scan(args)
    outcome = link_duplicates(result, apply=args.apply)
    if args.json:
        print(json.dumps(outcome, indent=2))
        return 0
    verb = "linked" if args.apply else "would hard-link"
    print("{} {} duplicate file(s), freeing {}".format(verb, outcome["count"], human_bytes(result.wasted_bytes)))
    for action in outcome["actions"][: args.show]:
        print("  {} -> {}".format(action["duplicate"], action["link_to"]))
    if not args.apply:
        print("\nnothing was changed. Re-run with --apply to replace copies with links.")
    return 0


def _common(parser):
    parser.add_argument("path", nargs="?", default=".", help="folder to scan (default: current directory)")
    parser.add_argument("--min-size", default="1", help="ignore files smaller than this, e.g. 1MB (default: 1 byte)")
    parser.add_argument("--include", action="append", default=[], help="glob to include (repeatable)")
    parser.add_argument("--exclude", action="append", default=[], help="glob to skip (repeatable)")
    parser.add_argument("--no-images", action="store_true", help="skip near-duplicate image detection")
    parser.add_argument("--threshold", type=int, default=6, help="image similarity threshold, 0-16 (default: 6)")
    parser.add_argument("--keep", choices=["oldest", "newest", "shortest", "first"], default="oldest")
    parser.add_argument("--json", action="store_true")


def build_parser():
    parser = argparse.ArgumentParser(prog="dupehunt", description="Find duplicate files without risking your data.")
    parser.add_argument("--version", action="version", version="dupehunt {}".format(__version__))
    sub = parser.add_subparsers(dest="command", required=True)

    scan_parser = sub.add_parser("scan", help="report duplicate clusters")
    _common(scan_parser)
    scan_parser.add_argument("--html", help="also write an HTML report")
    scan_parser.set_defaults(func=cmd_scan)

    quarantine_parser = sub.add_parser("quarantine", help="move duplicates into a folder")
    _common(quarantine_parser)
    quarantine_parser.add_argument("--to", required=True, help="destination folder for duplicate copies")
    quarantine_parser.add_argument("--apply", action="store_true", help="actually move files (default: dry run)")
    quarantine_parser.add_argument("--show", type=int, default=10, help="how many planned moves to print")
    quarantine_parser.set_defaults(func=cmd_quarantine)

    link_parser = sub.add_parser("link", help="replace duplicates with hard links")
    _common(link_parser)
    link_parser.add_argument("--apply", action="store_true", help="actually replace files (default: dry run)")
    link_parser.add_argument("--show", type=int, default=10)
    link_parser.set_defaults(func=cmd_link)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (NotADirectoryError, ValueError, OSError) as exc:
        print("dupehunt: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
