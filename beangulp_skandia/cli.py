# beangulp_skandia/cli.py
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from importlib.metadata import version, PackageNotFoundError

from beangulp import Ingest
from beangulp_skandia.importer import SkandiaXlsx


def build_ingest(config_path: Path | None, account_name: str, currency: str):
    if config_path and not config_path.exists():
        raise SystemExit(f"Config not found: {config_path}")
    importer = SkandiaXlsx(
        account_name=account_name,
        currency=currency,
        config_path=config_path,
    )
    return Ingest([importer])


def main():
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

    # Parse our wrapper args; leave beangulp subcommands (identify/extract/archive) in 'remaining'
    args, remaining = parser.parse_known_args()

    cfg_path = Path(args.config).expanduser() if args.config else None
    ingest = build_ingest(cfg_path, args.account, args.currency)

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

    # Otherwise forward subcommand & args to beangulp
    sys.argv = ["beangulp-skandia"] + remaining
    ingest()


if __name__ == "__main__":
    main()

