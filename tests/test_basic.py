# tests/test_basic.py
from pathlib import Path
from datetime import date
import pandas as pd

from beangulp_skandia.importer import SkandiaXlsx


def make_fake_xlsx(path: Path):
    """
    Create a fake Skandia-style 'Kontoutdrag' XLSX for testing.
    No real account numbers included.
    """
    rows = [
        ["Kontonummer", "XXXX-XXX.XXX-X"],
        ["Period", "2025-08-25 - 2025-08-27"],
        ["Bokf. datum", "Beskrivning", "Belopp", "Saldo"],
        ["2025-08-25", "Test Transaction 1", "-100", "1000"],
        ["2025-08-26", "Test Transaction 2", "200", "1200"],
    ]
    df = pd.DataFrame(rows)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Kontoutdrag", header=False, index=False)


def make_temp_config(path: Path):
    """
    Minimal TOML enabling balances so the test is explicit and hermetic.
    """
    path.write_text(
        '\n'.join([
            'default_account = "Assets:SE:Skandia:Test"',
            'currency = "SEK"',
            "",
            "[balances]",
            "enabled = true",
            'granularity = "file_end"',  # only assert final saldo (keeps assertions predictable)
            "",
            # keep rules/transfers off for this test
            "[rules]",
            "enabled = false",
            "",
            "[transfers]",
            "enabled = false",
            "",
        ])
    )


def test_identify_and_extract(tmp_path: Path):
    # Arrange: fake file + temp config that turns on balances
    fakefile = tmp_path / "fake.xlsx"
    make_fake_xlsx(fakefile)

    cfg = tmp_path / "skandia.toml"
    make_temp_config(cfg)

    importer = SkandiaXlsx(
        account_name="Assets:SE:Skandia:Test",
        currency="SEK",
        config_path=cfg,   # <- enable balances via config
    )

    # Act
    assert importer.identify(fakefile)
    entries = importer.extract(fakefile)

    # Assert
    txn_entries = [e for e in entries if e.__class__.__name__ == "Transaction"]
    bal_entries = [e for e in entries if e.__class__.__name__ == "Balance"]

    assert len(txn_entries) == 2
    # file_end granularity -> exactly one balance for the last saldo
    assert len(bal_entries) == 1

    # First transaction checks
    first = txn_entries[0]
    assert first.date == date(2025, 8, 25)
    assert first.payee == "Test Transaction 1"
    assert str(first.postings[0].units) == "-100 SEK"

    # Balance assertion should match the last 'Saldo' value (1200)
    assert str(bal_entries[0].amount) == "1200 SEK"

