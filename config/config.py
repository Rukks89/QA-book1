from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_NAME = str(BASE_DIR / "database" / "qa_batch_system.db")
MASTER_CSV = BASE_DIR / "data" / "master_specs.csv"
REPORT_DIR = BASE_DIR / "reports"
LOG_DIR = BASE_DIR / "logs"

EXPECTED_BOOKS_DEFAULT = 30
