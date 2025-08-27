from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import re
import pandas as pd
from beancount.core.amount import Amount
from beancount.core.data import Posting, Transaction, Balance, EMPTY_SET
from beancount.core.number import D
from beancount.core.flags import FLAG_OKAY
from beancount.core import data as data_core

# beangulp runtime interfaces
try:
    from beangulp.importer import Importer  # type: ignore
except Exception:  # pragma: no cover
    from beangulp import Importer  # type: ignore

# Try stdlib tomllib (Py 3.11+), fall back to a tiny best-effort parser
try:
    import tomllib  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    tomllib = None

SWEDISH_HEADERS = ["Bokf. datum", "Beskrivning", "Belopp", "Saldo"]


@dataclass
class SkandiaXlsx(Importer):
    """
    Beangulp importer for Skandia 'Kontoutdrag' XLSX exports.

    Config file (TOML) support via config_path (or env SKANDIA_CONFIG in your runner):

      default_account = "Assets:SE:Skandia:Default"
      currency = "SEK"

      [accounts]  # Kontonummer -> Account; keys can be raw (XXXX-XXX.XXX-X) or digits-only (XXXXXXXXXXXX)
      "XXXX-XXX.XXX-X" = "Assets:SE:Skandia:Checking"
      "XXXXXXXXXXXX"   = "Assets:SE:Skandia:Savings"

      [balances]
      enabled = true
      granularity = "daily"      # or "file_end"

      [rules]
      enabled = true
      default_counter = "Equity:Unknown"

      [rules.map]      # substring, case-insensitive
      "MALKARS GYM" = "Expenses:Health:Gym"
      "TROSSÖFASTIGHETER" = "Expenses:Rent"
      "UNIONEN" = "Expenses:Professional:Dues"
      "MOBIL" = "Expenses:Utilities:Mobile"
      "PRENUMERATION" = "Expenses:Subscriptions"

      [transfers]
      enabled = true
      classify_account = "Expenses:Transfers:Internal"
      parse_destination_in_description = true
      keywords = ["överföring", "överforing", "overforing"]
    """
    account_name: str
    currency: str = "SEK"
    encoding: str = "utf-8"
    infer_payee_from_description: bool = True
    skip_zero_amounts: bool = True
    positive_is_increase: bool = False  # Skandia exports show outflows as negative
    config_path: Optional[Path] = None  # optional TOML file path

    # (Runtime fields loaded from config)
    _config_loaded: bool = False
    _account_map: Optional[Dict[str, str]] = None
    _balances_enabled: bool = False
    _balances_granularity: str = "daily"  # "daily" | "file_end"
    _rules_enabled: bool = False
    _rules_default_counter: Optional[str] = None
    _rules_map: Dict[str, str] = None  # lowercased substr -> account
    _transfers_enabled: bool = True
    _transfers_classify_account: str = "Expenses:Transfers:Internal"
    _transfers_parse_dest_in_desc: bool = True
    _transfers_keywords: List[str] = None  # lowercased keywords

    # ---- Beangulp interface ----
    def name(self) -> str:
        return "Skandia XLSX Kontoutdrag"

    # Beangulp 0.2.0 calls account(file), newer may call account()
    def account(self, *args, **kwargs) -> str:
        self._ensure_config_loaded(kwargs.get("file") if "file" in kwargs else (args[0] if args else None))
        file: Optional[Path] = None
        if args and isinstance(args[0], Path):
            file = args[0]
        elif "file" in kwargs and isinstance(kwargs["file"], Path):
            file = kwargs["file"]
        if file is not None:
            mapped = self._account_from_kontonummer(file)
            if mapped:
                return mapped
        return self.account_name

    # Older/alternate API
    def file_account(self, file: Path) -> str:
        self._ensure_config_loaded(file)
        mapped = self._account_from_kontonummer(file)
        return mapped or self.account_name

    # --- Config loading ---
    def _ensure_config_loaded(self, file: Optional[Path]) -> None:
        if self._config_loaded:
            return
        cfg = self.config_path
        if cfg and Path(cfg).exists():
            data = self._load_toml(Path(cfg))
            if data:
                # Optional overrides
                if isinstance(data.get("default_account"), str):
                    self.account_name = data["default_account"]
                if isinstance(data.get("currency"), str):
                    self.currency = data["currency"]

                # Account map (raw + normalized digits-only)
                accounts = data.get("accounts") or {}
                if isinstance(accounts, dict):
                    normalized = {}
                    for k, v in accounts.items():
                        if isinstance(k, str) and isinstance(v, str):
                            normalized["".join(ch for ch in k if ch.isdigit())] = v
                    self._account_map = {**accounts, **normalized}

                # Balances
                balances = data.get("balances") or {}
                if isinstance(balances, dict):
                    self._balances_enabled = bool(balances.get("enabled", False))
                    gran = str(balances.get("granularity", "daily")).lower()
                    if gran in ("daily", "file_end"):
                        self._balances_granularity = gran

                # Rules (keyword guessing)
                rules = data.get("rules") or {}
                if isinstance(rules, dict):
                    self._rules_enabled = bool(rules.get("enabled", False))
                    dc = rules.get("default_counter")
                    self._rules_default_counter = str(dc) if isinstance(dc, str) else None
                    rmap = rules.get("map") or {}
                    if isinstance(rmap, dict):
                        self._rules_map = {str(k).lower(): str(v) for k, v in rmap.items()
                                           if isinstance(k, str) and isinstance(v, str)}
                    else:
                        self._rules_map = {}
                else:
                    self._rules_map = {}

                # Transfers
                transfers = data.get("transfers") or {}
                if isinstance(transfers, dict):
                    self._transfers_enabled = bool(transfers.get("enabled", True))
                    ca = transfers.get("classify_account")
                    if isinstance(ca, str) and ca.strip():
                        self._transfers_classify_account = ca.strip()
                    self._transfers_parse_dest_in_desc = bool(
                        transfers.get("parse_destination_in_description", True)
                    )
                    kws = transfers.get("keywords") or ["överföring", "överforing", "overforing"]
                    if isinstance(kws, list):
                        self._transfers_keywords = [str(x).lower() for x in kws if isinstance(x, str)]
                    else:
                        self._transfers_keywords = ["överföring", "överforing", "overforing"]
                else:
                    self._transfers_keywords = ["överföring", "överforing", "overforing"]
        else:
            # No config file: initialize defaults for rules/transfers
            self._rules_map = {}
            self._transfers_keywords = ["överföring", "överforing", "overforing"]

        self._config_loaded = True

    def _load_toml(self, path: Path) -> Optional[dict]:
        try:
            if tomllib is not None:
                with open(path, "rb") as f:
                    return tomllib.load(f)
            # Minimal best-effort fallback if tomllib unavailable.
            with open(path, "rb") as f:
                raw = f.read().decode("utf-8")
            data: dict = {"accounts": {}, "balances": {}, "rules": {"map": {}}, "transfers": {}}
            section = None
            for line in raw.splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if s.startswith("[") and s.endswith("]"):
                    section = s.strip("[]").strip()
                    continue
                if "=" in s:
                    key, val = s.split("=", 1)
                    key = key.strip().strip('"').strip("'")
                    val = val.strip().strip('"').strip("'")
                    if section == "accounts":
                        data.setdefault("accounts", {})[key] = val
                    elif section == "balances":
                        data.setdefault("balances", {})[key] = val
                    elif section == "rules":
                        data.setdefault("rules", {})[key] = val
                    elif section == "rules.map":
                        data.setdefault("rules", {}).setdefault("map", {})[key] = val
                    elif section == "transfers":
                        data.setdefault("transfers", {})[key] = val
                    elif section is None:
                        if key in ("default_account", "currency"):
                            data[key] = val
            return data
        except Exception:
            return None

    # --- Parsing helpers ---
    def _read_raw(self, file: Path) -> pd.DataFrame:
        return pd.read_excel(file, sheet_name="Kontoutdrag", header=None, dtype=str)

    def _normalize_kontonummer(self, s: str) -> str:
        # Keep only digits — handles "XXXX-XXX.XXX-X", spaces, NBSP, etc.
        return "".join(ch for ch in s if ch.isdigit())

    def _extract_kontonummer(self, raw: pd.DataFrame) -> Optional[str]:
        """
        Find a cell with 'Kontonummer' and return the cell to its right,
        or split a single-cell 'Kontonummer <num>'.
        """
        max_rows, max_cols = min(8, len(raw.index)), min(8, raw.shape[1])
        for r in range(max_rows):
            for c in range(max_cols):
                val = str(raw.iat[r, c]) if c < raw.shape[1] else ""
                if val and val.strip().lower() == "kontonummer":
                    if c + 1 < raw.shape[1]:
                        num = raw.iat[r, c + 1]
                        if pd.notna(num) and str(num).strip():
                            return str(num).strip()
        # One-cell variant: "Kontonummer XXXX-XXX.XXX-X"
        for r in range(max_rows):
            for c in range(max_cols):
                val = str(raw.iat[r, c]) if c < raw.shape[1] else ""
                if val and "kontonummer" in val.strip().lower():
                    parts = val.split()
                    if len(parts) >= 2:
                        return parts[-1].strip()
        return None

    def _account_from_kontonummer(self, file: Path) -> Optional[str]:
        if not self._account_map:
            return None
        try:
            raw = self._read_raw(file)
            konto = self._extract_kontonummer(raw)
            if not konto:
                return None
            normalized = self._normalize_kontonummer(konto)
            if konto in self._account_map:
                return self._account_map[konto]
            if normalized in self._account_map:
                return self._account_map[normalized]
            compact = konto.replace(" ", "")
            if compact in self._account_map:
                return self._account_map[compact]
            return None
        except Exception:
            return None

    def _find_header_row(self, raw: pd.DataFrame) -> Optional[int]:
        for i, row in raw.iterrows():
            if str(row.iloc[0]).strip() == "Bokf. datum":
                labels = [str(row.get(j, "")).strip() for j in range(4)]
                if labels[:4] == SWEDISH_HEADERS:
                    return i
        return None

    def identify(self, file: Path) -> bool:
        try:
            self._ensure_config_loaded(file)
            raw = self._read_raw(file)
            hdr = self._find_header_row(raw)
            return hdr is not None
        except Exception:
            return False

    def date(self, file: Path):
        try:
            raw = self._read_raw(file)
            period_str = str(raw.iloc[0, 1])
            if " - " in period_str:
                end = period_str.split(" - ")[1].strip()
                return pd.to_datetime(end, errors="coerce").date()
        except Exception:
            pass
        try:
            df = self._data_frame(file)
            if not df.empty:
                return df["date"].max().date()
        except Exception:
            pass
        return None

    def filename(self, file: Path) -> Optional[str]:
        self._ensure_config_loaded(file)
        acc = (self._account_from_kontonummer(file) or self.account_name).replace(":", "-")
        d = self.date(file)
        if d:
            return f"skandia-{acc}-{d.isoformat()}.xlsx"
        return None

    def _data_frame(self, file: Path) -> pd.DataFrame:
        raw = self._read_raw(file)
        header_row_idx = self._find_header_row(raw)
        if header_row_idx is None:
            raise ValueError("Could not find header row with 'Bokf. datum'")

        data = raw.iloc[header_row_idx + 1 :, :4].copy()
        data.columns = SWEDISH_HEADERS

        data = data[data["Bokf. datum"].notna()]
        data = data[data["Bokf. datum"].astype(str).str.strip() != ""]

        df = data.rename(
            columns={
                "Bokf. datum": "date",
                "Beskrivning": "desc",
                "Belopp": "amount",
                "Saldo": "balance",
            }
        ).copy()

        df["date"] = pd.to_datetime(df["date"], errors="coerce")

        def to_decimal(x) -> Decimal:
            if x is None or (isinstance(x, float) and pd.isna(x)):
                return Decimal("0")
            s = str(x).replace("\u00A0", "").replace(" ", "")
            s = s.replace(".", "").replace(",", ".")
            try:
                return Decimal(s)
            except Exception:
                s2 = re.sub(r"[^0-9\.-]", "", s)
                return Decimal(s2 or "0")

        df["amount"] = df["amount"].map(to_decimal)

        df = df[df["date"].notna()].reset_index(drop=True)
        return df

    # ---- Balance assertions ----
    def _append_balance_assertions(self, entries: List[data_core.Directive],
                                   df: pd.DataFrame, file: Path, acct_for_file: str) -> None:
        def parse_balance(x) -> Optional[Decimal]:
            if x is None or (isinstance(x, float) and pd.isna(x)):
                return None
            s = str(x).replace("\u00A0", "").replace(" ", "")
            s = s.replace(".", "").replace(",", ".")
            try:
                return Decimal(s)
            except Exception:
                s2 = re.sub(r"[^0-9\.-]", "", s)
                try:
                    return Decimal(s2)
                except Exception:
                    return None

        with_bal = df.copy()
        with_bal["balval"] = with_bal["balance"].map(parse_balance)

        if self._balances_granularity == "file_end":
            last = with_bal.dropna(subset=["balval"]).tail(1)
            iter_rows = list(last.iterrows())
        else:
            # "daily": take last txn with a saldo for each date
            g = with_bal.dropna(subset=["balval"]).groupby(with_bal["date"].dt.date, as_index=False, sort=True).tail(1)
            iter_rows = list(g.iterrows())

        for _, row in iter_rows:
            bal = row["balval"]
            if bal is None:
                continue
            meta = data_core.new_metadata(str(file), 0)
            entries.append(
                Balance(
                    meta=meta,
                    date=row["date"].date(),
                    account=acct_for_file,
                    amount=Amount(D(str(bal)), self.currency),
                    tolerance=None,
                    diff_amount=None,
                )
            )

    # ---- Transfers detection / classification ----
    def _looks_like_transfer(self, desc: str) -> bool:
        if not self._transfers_enabled or not desc:
            return False
        s = desc.lower()
        return any(k in s for k in (self._transfers_keywords or []))

    def _resolve_transfer_counter(self, desc: str) -> str:
        """
        If description contains an account number:
          - try to map it via self._account_map (digits-only & sliding windows)
          - if found, use that account
          - else, fall back to self._transfers_classify_account
        """
        if not desc:
            return self._transfers_classify_account
        # Only attempt mapping if config told us to parse destination
        if self._transfers_parse_dest_in_desc and self._account_map:
            digits = "".join(ch for ch in desc if ch.isdigit())
            if len(digits) >= 8:
                # Exact digits-only match
                acct = self._account_map.get(digits)
                if acct:
                    return acct
                # Sliding windows (12..8) to catch embedded numbers like "XXXX XXXXXXX"
                for L in (12, 11, 10, 9, 8):
                    for i in range(0, len(digits) - L + 1):
                        cand = digits[i:i + L]
                        acct = self._account_map.get(cand)
                        if acct:
                            return acct
        return self._transfers_classify_account

    # ---- Rules / counter-account guessing ----
    def _guess_counter_account(self, desc: str) -> Optional[str]:
        if not self._rules_enabled or not self._rules_map:
            return None
        s = (desc or "").lower()
        for substr, acct in self._rules_map.items():
            if substr in s:
                return acct
        return self._rules_default_counter

    def extract(
        self,
        file: Path,
        existing_entries: Optional[Iterable[data_core.Directive]] = None,
    ) -> List[data_core.Directive]:  # type: ignore[override]
        self._ensure_config_loaded(file)
        df = self._data_frame(file)

        # Use mapped account (if any) for postings
        acct_for_file = self._account_from_kontonummer(file) or self.account_name

        entries: List[data_core.Directive] = []
        for _, row in df.iterrows():
            amt = row["amount"]
            if self.skip_zero_amounts and amt == 0:
                continue

            number = D(str(amt if self.positive_is_increase else amt))

            meta = data_core.new_metadata(str(file), 0)
            payee = row["desc"] if self.infer_payee_from_description else None
            narration = "" if self.infer_payee_from_description else row["desc"]

            postings: List[Posting] = [
                Posting(
                    account=acct_for_file,
                    units=Amount(number, self.currency),
                    cost=None,
                    price=None,
                    flag=None,
                    meta={},
                )
            ]

            # 1) Transfers classification (runs before generic rules)
            if self._looks_like_transfer(row["desc"]):
                counter = self._resolve_transfer_counter(row["desc"])
                postings.append(
                    Posting(
                        account=counter,
                        units=Amount(D(str(-Decimal(number))), self.currency),
                        cost=None,
                        price=None,
                        flag=None,
                        meta={},
                    )
                )
            else:
                # 2) Generic keyword rules
                guessed = self._guess_counter_account(row["desc"])
                if guessed:
                    postings.append(
                        Posting(
                            account=guessed,
                            units=Amount(D(str(-Decimal(number))), self.currency),
                            cost=None,
                            price=None,
                            flag=None,
                            meta={},
                        )
                    )

            txn = Transaction(
                meta=meta,
                date=row["date"].date(),
                flag=FLAG_OKAY,
                payee=payee,
                narration=narration,
                tags=EMPTY_SET,
                links=EMPTY_SET,
                postings=postings,
            )
            entries.append(txn)

        # Balance assertions (optional)
        if self._balances_enabled:
            self._append_balance_assertions(entries, df, file, acct_for_file)

        return entries
