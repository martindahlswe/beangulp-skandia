# beangulp_skandia/cli.py
from __future__ import annotations

import argparse
import os
import sys
from importlib.metadata import PackageNotFoundError, version
from importlib import import_module
from pathlib import Path
from shutil import copy2, move
import pkgutil
from typing import Optional, Tuple, Any, Iterable, List
from beancount.core.amount import Amount
from beancount.core.data import Transaction, Balance, Posting

from beangulp import Ingest
from beangulp_skandia.importer import SkandiaXlsx

# Beancount loader/printer for --smart path
try:
    from beancount.loader import load_file as beancount_load_file
    from beancount.parser import printer as beancount_printer  # noqa: F401 (kept for availability checks)
except Exception:
    beancount_load_file = None
    beancount_printer = None


def _load_smart_importer_classes() -> Tuple[Optional[Any], Optional[Any], Optional[str]]:
    """
    Try to import (PredictPostings, PredictPayees) from smart_importer across layouts.
    Returns (PredictPostings, PredictPayees, error_str).
    """
    tried = []
    candidates = [
        "smart_importer",             # documented API
        "smart_importer.hooks",       # historical
        "smart_importer.predict",
        "smart_importer.core",
        "smart_importer.api",
    ]
    for modname in candidates:
        try:
            m = import_module(modname)
            pp = getattr(m, "PredictPostings", None)
            py = getattr(m, "PredictPayees", None)
            if pp and py:
                return pp, py, None
            tried.append(f"{modname} (missing PredictPostings/PredictPayees)")
        except Exception as e:
            tried.append(f"{modname} ({e})")

    try:
        base = import_module("smart_importer")
        if hasattr(base, "__path__"):
            for _, name, _ in pkgutil.iter_modules(base.__path__, base.__name__ + "."):
                try:
                    m = import_module(name)
                    pp = getattr(m, "PredictPostings", None)
                    py = getattr(m, "PredictPayees", None)
                    if pp and py:
                        return pp, py, None
                    tried.append(f"{name} (missing PredictPostings/PredictPayees)")
                except Exception as e:
                    tried.append(f"{name} ({e})")
        else:
            tried.append("smart_importer (no __path__ to scan)")
    except Exception as e:
        tried.append(f"smart_importer base import failed: {e}")

    return None, None, " ; ".join(tried)


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


def _format_entry(entry: Any) -> str:
    """
    Format a Beancount entry to text. Prefer beancount's format_entry if available,
    otherwise use a minimal fallback printer that handles Transaction and Balance.
    """
    # Try beancount's printer if present
    try:
        from beancount.parser.printer import format_entry as _beancount_format_entry  # type: ignore
        return _beancount_format_entry(entry)
    except Exception:
        pass

    # --- Fallback formatter ---
    if isinstance(entry, Transaction):
        payee = f'"{entry.payee}"' if entry.payee else '""'
        narration = f' "{entry.narration}"' if entry.narration else ' ""'
        flag = entry.flag or "*"
        header = f"{entry.date} {flag} {payee}{narration}"
        lines = [header]
        for p in entry.postings:
            # Posting: "  <Account><pad><amount currency>"
            if p.units and isinstance(p.units, Amount):
                amt = f"{p.units.number} {p.units.currency}"
                lines.append(f"  {p.account}  {amt}")
            else:
                lines.append(f"  {p.account}")
        return "\n".join(lines)

    if isinstance(entry, Balance):
        amt = (
            f"{entry.amount.number} {entry.amount.currency}"
            if isinstance(entry.amount, Amount) else str(entry.amount)
        )
        return f"{entry.date} balance {entry.account} {amt}"

    # Generic fallback
    return str(entry)


def _apply_predictor(pred: Any, entries: Iterable[Any], existing_entries: Iterable[Any]) -> List[Any]:
    """
    Apply a smart_importer predictor instance to entries using whatever API it exposes.
    Tries, in order:
      1) __call__(entries, existing_entries)
      2) __call__(entries)
      3) .apply/.predict/.transform with (entries, existing_entries)
      4) same methods with keyword args: existing_entries=..., existing=..., ledger=...
      5) train/predict split: .train(existing_entries) then .predict(entries)
    Returns a list of entries; if nothing matches, returns entries unchanged.
    """
    def _as_list(x):
        # Some implementations yield; make sure we return a list.
        return list(x) if x is not None else list(entries)

    # 1) Callable instance
    if callable(pred):
        for args in ((entries, existing_entries), (entries,)):
            try:
                return _as_list(pred(*args))
            except TypeError:
                pass

    # 2) Common method names
    for meth in ("apply", "predict", "transform"):
        fn = getattr(pred, meth, None)
        if not callable(fn):
            continue
        # Positional (entries, existing_entries)
        try:
            return _as_list(fn(entries, existing_entries))
        except TypeError:
            pass
        # Keyword variants
        for kw in ("existing_entries", "existing", "ledger", "history"):
            try:
                return _as_list(fn(entries, **{kw: existing_entries}))
            except TypeError:
                continue

    # 3) Train + predict split
    train = getattr(pred, "train", None)
    predict = getattr(pred, "predict", None)
    if callable(train) and callable(predict):
        try:
            train(existing_entries)
            return _as_list(predict(entries))
        except TypeError:
            pass

    # 4) Nothing worked; return original entries unchanged
    return list(entries)


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

    # Archive options
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

    # Smart importer options
    parser.add_argument(
        "--smart",
        action="store_true",
        help="Enable smart_importer (PredictPostings + PredictPayees) for extract",
    )
    parser.add_argument(
        "--ledger",
        metavar="LEDGER_FILE",
        help="Path to your Beancount ledger used as training data (required with --smart)",
    )

    # Parse our wrapper args; leave beangulp subcommands in 'remaining'
    args, remaining = parser.parse_known_args()

    cfg_path = Path(args.config).expanduser() if args.config else None
    ingest = build_ingest(cfg_path, args.account, args.currency)
    importer = ingest.importers[0]

    # If no subcommand or explicit -h/--help left, show wrapper help + beangulp help
    if not remaining or remaining[0] in ("-h", "--help"):
        parser.print_help()
        print("\n--- beangulp commands ---\n")
        sys.argv = ["beangulp-skandia", "--help"]
        try:
            ingest()
        except SystemExit:
            pass
        return

    # --- Custom archive handling ---
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

            dest_name = importer.filename(src) or src.name
            dest = _with_unique_suffix(archive_base / dest_name)
            _ensure_parent(dest)

            if args.archive_mode == "copy":
                copy2(src, dest)
            else:
                move(str(src), str(dest))

            print(f"* {src} ... Archived to: {dest}")

        sys.exit(status)

    # --- Smart extract path (predictions using your ledger) ---
    if remaining[0] == "extract" and args.smart:
        if beancount_load_file is None or beancount_printer is None:
            raise SystemExit("beancount extras not available for --smart path.")
        if not args.ledger:
            raise SystemExit("--smart requires --ledger PATH to your Beancount file")

        # Load smart_importer classes from any supported layout
        PredictPostings, PredictPayees, err = _load_smart_importer_classes()
        if not PredictPostings:
            raise SystemExit(
                "smart_importer is installed but required classes were not found.\n"
                f"Tried: {err}\n\n"
                "Workarounds:\n"
                "  - pip install -U smart_importer scikit-learn\n"
                "  - or use a different build/branch of smart_importer\n"
                "  - or install via conda-forge if wheels are inconsistent\n"
            )

        # 1) Load your ledger -> existing_entries
        ledger_path = Path(args.ledger).expanduser()
        existing_entries, errors, _ = beancount_load_file(ledger_path.as_posix())
        if errors:
            for e in errors:
                sys.stderr.write(str(e) + "\n")

        # 2) Plain extract via your importer
        status = 0
        predictor_payees = PredictPayees()
        predictor_postings = PredictPostings()

        for f in remaining[1:]:
            src = Path(f).expanduser().resolve()
            if not src.exists():
                print(f"* {src} ... SKIP (not found)", file=sys.stderr)
                status = 1
                continue

            print(f"* {src} ... OK", file=sys.stderr)
            entries = list(importer.extract(src))

            # 3) Apply predictors (support multiple API shapes)
            entries = _apply_predictor(predictor_payees, entries, existing_entries)
            entries = _apply_predictor(predictor_postings, entries, existing_entries)

            # 4) Print the resulting entries
            for entry in entries:
                print(_format_entry(entry))
                print()

        sys.exit(status)

    # Otherwise forward subcommand & args to beangulp (identify/extract/etc.)
    sys.argv = ["beangulp-skandia"] + remaining
    ingest()


if __name__ == "__main__":
    main()