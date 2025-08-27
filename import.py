from pathlib import Path
import os
from beangulp import Ingest
from beangulp_skandia.importer import SkandiaXlsx

# Let env var override path; fallback to local skandia.toml if it exists.
cfg_env = os.environ.get("SKANDIA_CONFIG")
cfg_path = Path(cfg_env) if cfg_env else Path("skandia.toml")
if not cfg_path.exists():
    cfg_path = None  # importer will fallback to account_name/currency and no extra features

IMPORTERS = [
    SkandiaXlsx(
        account_name="Assets:SE:Skandia:Default",
        currency="SEK",
        config_path=cfg_path,  # pass None if not found
    ),
]

if __name__ == "__main__":
    Ingest(IMPORTERS)()

