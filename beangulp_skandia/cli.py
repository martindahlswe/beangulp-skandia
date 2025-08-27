# beangulp_skandia/cli.py
from __future__ import annotations

import argparse
import os
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from shutil import copy2, move


from beangulp import Ingest
from beangulp_skandia.importer import SkandiaXlsx


def build_ingest(config_path: Path | None, account_name: str, currency: str) -> Ingest:
    if config_path and not config_path.exists():
        raise SystemExit(f"Config not found: {config_path}")
    importer = SkandiaXlsx(
        account_name=account_name,
        currency=currency,
        config_path=config_path,
    )
    return Ingest([importer])


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _with_unique_suffix(p: Path) -> Path:
    """Return a non-clobbering path by appending -1, -2, ... before the suffix."""
    if not p.exists():
        return p
    stem, suffix = p.stem, p.suffix
    i = 1
    while True:
        candidate = p.with_name(f"{stem}-{i}{suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def main() -> None:
    try:
        pkg_version = version("beangulp-skandia")
    except PackageNotFoundError:
        pkg_version = "0.0.0+local"

    parser = argparse.ArgumentParser(
        prog="beangulp-skandia",
        description="Importer for Skandia XLSX (Kontoutdrag) via beangulp",
        add_help=True,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {pkg_version}",
        help="Show version and exit",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=os.environ.get("SKANDIA_CONFIG"),
        help="Path to skandia.toml (default: $SKANDIA_CONFIG or none)",
    )
    parser.add_argument(
        "--account",
        metavar="ACCOUNT",
        default="Assets:SE:Skandia:Default",
        help="Default Beancount account if Kontonummer not mapped",
    )
    parser.add_argument(
        "--currency",
        metavar="CURRENCY",
        default="SEK",
        help="Currency code (default: SEK)",
    )

    # Archive options (handled by this wrapper; identify/extract/other go to beangulp)
    parser.add_argument(
        "--archive-dir",
        metavar="DIR",
        default="./archive",
        help="Directory where files should be archived (default: ./archive in CWD)",
    )
    parser.add_argument(
        "--archive-mode",
        choices=("move", "copy"),
        default="move",
        help="Archive by moving (default) or copying the source file",
    )

    # Parse our wrapper args; leave beangulp subcommands (identify/extract/...) in 'remaining'
    args, remaining = parser.parse_known_args()

    cfg_path = Path(args.config).expanduser() if args.config else None
    ingest = build_ingest(cfg_path, args.account, args.currency)
    importer = ingest.importers[0]

    # If no subcommand or explicit -h/--help left, show wrapper help + beangulp help
    if not remaining or remaining[0] in ("-h", "--help"):
        parser.print_help()
        print("\n--- beangulp commands ---\n")
        # Delegate to beangulp's Click CLI for its own help output
        sys.argv = ["beangulp-skandia", "--help"]
        try:
            ingest()
        except SystemExit:
            pass
        return

    # Our custom archive handling (explicit destination + printed path)
    if remaining[0] == "archive":
        if len(remaining) == 1:
            print("No input files provided to 'archive'.", file=sys.stderr)
            sys.exit(2)

        archive_base = Path(args.archive_dir).expanduser().resolve()
        archive_base.mkdir(parents=True, exist_ok=True)

        status = 0
        for f in remaining[1:]:
            src = Path(f).expanduser().resolve()
            if not src.exists():
                print(f"* {src} ... SKIP (not found)", file=sys.stderr)
                status = 1
                continue

            # Prefer importer's canonical filename; fall back to original name
            dest_name = importer.filename(src) or src.name
            dest = _with_unique_suffix(archive_base / dest_name)
            _ensure_parent(dest)

            if args.archive_mode == "copy":
                copy2(src, dest)
            else:
                move(str(src), str(dest))

            print(f"* {src} ... Archived to: {dest}")

        sys.exit(status)

    # Otherwise forward subcommand & args to beangulp (identify/extract/etc.)
    sys.argv = ["beangulp-skandia"] + remaining
    ingest()


if __name__ == "__main__":
    main()
