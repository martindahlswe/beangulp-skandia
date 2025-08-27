# beangulp-skandia

Importer plugin for [beangulp](https://github.com/redstreet/beangulp) that reads **XLSX exports** (“Kontoutdrag”) from the Swedish bank **Skandia** and converts them into [Beancount](https://beancount.github.io/) transactions.

## Quickstart

```bash
# Install in editable mode
git clone https://github.com/martindahlswe/beangulp-skandia.git
cd beangulp-skandia
pip install -e .

# Copy the example config and adjust it
cp skandia.example.toml skandia.toml

# Run identify or extract
beangulp-skandia --config skandia.toml identify ~/Downloads/skandia.xlsx
beangulp-skandia --config skandia.toml extract  ~/Downloads/skandia.xlsx > out.bean
```

## Features

- Parses Skandia’s `Kontoutdrag` XLSX format.
- Detects account number (`Kontonummer`) and maps it to your Beancount account.
- Optional balance assertions from the `Saldo` column.
- Keyword-based counter-account rules (e.g. rent, gym, subscriptions).
- Internal transfers detection and classification (e.g. “Överföring …”).
- Configurable via a simple `skandia.toml`.
- Provides a CLI entrypoint: `beangulp-skandia`.

## Installation

Clone the repository and install in editable mode:

```bash
git clone https://github.com/martindahlswe/beangulp-skandia.git
cd beangulp-skandia
pip install -e .
```

Requires:
- Python 3.9+
- [beangulp](https://github.com/redstreet/beangulp)
- [beancount](https://beancount.github.io/)
- [pandas](https://pandas.pydata.org/)
- [openpyxl](https://openpyxl.readthedocs.io/)

## Usage

You can now run the importer via the CLI entrypoint:

```bash
beangulp-skandia --config skandia.toml identify ~/Downloads/skandia.xlsx
beangulp-skandia --config skandia.toml extract  ~/Downloads/skandia.xlsx > out.bean
```

The old developer wrapper is still available:

```bash
python3 import.py identify ~/Downloads/skandia.xlsx
python3 import.py extract   ~/Downloads/skandia.xlsx
```

## Configuration

Copy `skandia.example.toml` to `skandia.toml` and adjust to your own setup.  
(Your personal `skandia.toml` is ignored by git — see `.gitignore`.)

```toml
default_account = "Assets:SE:Skandia:Default"
currency = "SEK"

[accounts]
"XXXX-XXX.XXX-X" = "Assets:SE:Skandia:Checking"
"XXXXXXXXXXXX"   = "Assets:SE:Skandia:Savings"

[balances]
enabled = true
granularity = "daily"   # or "file_end"

[rules]
enabled = true
default_counter = "Equity:Unknown"

[rules.map]
"SATS"                  = "Expenses:Health:Gym"
"Lundbergs Fastigheter" = "Expenses:Rent"
"UNIONEN"               = "Expenses:Unionen"
"TELENOR"               = "Expenses:Subscription:Mobile"
"PRENUMERATION"         = "Expenses:Subscriptions"

[transfers]
enabled = true
classify_account = "Expenses:Transfers:Internal"
parse_destination_in_description = true
keywords = ["överföring", "overforing"]
```

## Example output

```
2025-08-25 * "Överföring XXXXX XXXXXXX"
  Assets:SE:Skandia:Checking   -1000 SEK
  Assets:SE:Skandia:Savings     1000 SEK

2025-08-27 * "Autogiro SATS"
  Assets:SE:Skandia:Checking    -449 SEK
  Expenses:Health:Gym            449 SEK
```

## Development

- `beangulp_skandia/importer.py` contains the importer class.
- `beangulp_skandia/cli.py` provides the CLI entrypoint (`beangulp-skandia`).
- `import.py` is a thin wrapper around [beangulp](https://github.com/redstreet/beangulp).
- `skandia.example.toml` documents configuration.
- Tests are included in `tests/` (they generate fake XLSX, no real data).
- Contributions welcome.

## License

MIT — see [LICENSE](LICENSE).
