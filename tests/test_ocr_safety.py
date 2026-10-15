import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _install_optional_import_stubs():
    """ Allow pure safety-helper tests without image/OCR packages installed."""

    sys.modules.setdefault("numpy", types.ModuleType("numpy"))
    sys.modules.setdefault("cv2", types.ModuleType("cv2"))
    sys.modules.setdefault("pytesseract", types.ModuleType("pytesseract"))

    if "PIL" not in sys.modules:
        pil = types.ModuleType("PIL")
        pil.Image = types.ModuleType("PIL.Image")
        pil.ImageOps = types.ModuleType("PIL.ImageOps")
        sys.modules["PIL"] = pil
        sys.modules["PIL.Image"] = pil.Image
        sys.modules["PIL.ImageOps"] = pil.ImageOps

    sys.modules.setdefault("pandas", types.ModuleType("pandas"))


_install_optional_import_stubs()
ocr = importlib.import_module("core.ocr")
database = importlib.import_module("core.database")


class ChassisCandidateTests(unittest.TestCase):
    def test_candidates_keep_lookalikes_unchanged(self):
        reads = [
            {"chassis_no": "MAT784063TFH09898"},
            {"chassis_no": "MAT784063TFH09898"},
            {"chassis_no": "MAT784063TFH0989B"},
        ]

        candidates = ocr._rank_field_candidates(reads, "chassis_no")

        self.assertEqual(
            candidates,
            [
                {"value": "MAT784063TFH09898", "votes": 2},
                {"value": "MAT784063TFH0989B", "votes": 1},
            ],
        )

    def test_tie_and_plurality_are_not_strong_consensus(self):
        self.assertFalse(
            ocr._candidate_is_strong(
                [
                    {"value": "MAT001", "votes": 2},
                    {"value": "MAT00I", "votes": 2},
                ]
            )
        )
        self.assertFalse(
            ocr._candidate_is_strong(
                [
                    {"value": "MAT001", "votes": 2},
                    {"value": "MAT00I", "votes": 1},
                ]
            )
        )

    def test_repeated_candidate_with_clear_margin_is_strong(self):
        self.assertTrue(
            ocr._candidate_is_strong(
                [
                    {"value": "CHS99004", "votes": 4},
                    {"value": "CHS9900A", "votes": 1},
                ]
            )
        )


class MasterLookupTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.sqlite"
        with patch.object(database, "DB_NAME", str(self.db_path)):
            conn = database.connect()
        conn.execute(
            """
            CREATE TABLE master_specs (
                barcode TEXT PRIMARY KEY,
                mat_no TEXT,
                vc_no TEXT,
                color TEXT,
                order_no TEXT,
                seating TEXT,
                chassis_no TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO master_specs VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("BC001", "MAT-1", "VC-1", "Red", "O-1", "40", "CHS99004"),
                ("BC002", "MAT-2", "VC-2", "Blue", "O-2", "30", "CHS99005"),
            ],
        )
        conn.commit()
        conn.close()
        self.db_patch = patch.object(
            database, "DB_NAME", str(self.db_path)
        )
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_exact_barcode_returns_master_fields(self):
        result = database.lookup_master_data(barcode=" bc001 ")
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["record"]["mat_no"], "MAT-1")

    def test_exact_chassis_requires_repeated_ocr_support(self):
        result = database.lookup_master_data(
            chassis_candidates=[
                {"value": "chs99004", "votes": 2}
            ]
        )
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["record"]["barcode"], "BC001")

    def test_distinct_records_are_ambiguous(self):
        result = database.lookup_master_data(
            chassis_candidates=[
                {"value": "CHS99004", "votes": 2},
                {"value": "CHS99005", "votes": 2},
            ]
        )
        self.assertEqual(result["status"], "ambiguous")
        self.assertIsNone(result["record"])

    def test_casefold_does_not_merge_distinct_master_rows(self):
        conn = database.connect()
        conn.execute(
            "INSERT INTO master_specs VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("bc001", "MAT-3", "VC-3", "Green", "O-3", "20", "CHS99006"),
        )
        conn.commit()
        conn.close()
        result = database.lookup_master_data(barcode="Bc001")
        self.assertEqual(result["status"], "ambiguous")
        self.assertIsNone(result["record"])

    def test_unknown_or_weak_candidate_is_not_corrected(self):
        weak = database.lookup_master_data(
            chassis_candidates=[
                {"value": "CHS99004", "votes": 1}
            ]
        )
        unknown = database.lookup_master_data(
            chassis_candidates=[
                {"value": "CHS9900A", "votes": 2}
            ]
        )
        self.assertEqual(weak["status"], "manual_verification_required")
        self.assertEqual(unknown["status"], "not_found")

    def test_internal_spaces_are_not_removed_as_a_correction(self):
        result = database.lookup_master_data(
            chassis_candidates=[
                {"value": "CHS 99004", "votes": 2}
            ]
        )
        self.assertEqual(result["status"], "not_found")

    def test_proposed_chassis_correction_is_kept_in_existing_scan_log(self):
        conn = database.connect()
        conn.executescript(
            """
            CREATE TABLE batches (
                batch_id TEXT PRIMARY KEY,
                batch_date TEXT,
                observations TEXT,
                target_books INTEGER,
                created_at TEXT
            );
            CREATE TABLE batch_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT,
                scan_timestamp TEXT,
                barcode TEXT,
                bus_no TEXT,
                mat_no TEXT,
                vc_no TEXT,
                color TEXT,
                order_no TEXT,
                seating TEXT,
                chassis_no TEXT,
                vehicle_type TEXT,
                source_file TEXT,
                image_evidence BLOB,
                pdi_clearance TEXT,
                qa_book_status TEXT,
                verification_status TEXT,
                UNIQUE(batch_id, bus_no)
            );
            CREATE TABLE scan_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT,
                barcode TEXT,
                scan_timestamp TEXT,
                action TEXT,
                message TEXT
            );
            """
        )
        conn.close()

        success, _message = database.save_batch_atomically(
            {
                "batch_id": "TEST",
                "batch_date": "2026-10-09",
                "target_books": 30,
                "is_new": True,
            },
            [
                {
                    "barcode": "BC001",
                    "bus_no": "1001",
                    "pdi_clearance": "Pending",
                    "qa_book_status": "Not Received",
                    "chassis_original_candidate": "CHS9900A",
                    "chassis_proposed_correction": "CHS99004",
                }
            ],
        )

        self.assertTrue(success)
        conn = database.connect()
        logged = conn.execute(
            "SELECT message FROM scan_logs WHERE batch_id = 'TEST'"
        ).fetchone()[0]
        conn.close()
        self.assertIn("CHS9900A", logged)
        self.assertIn("CHS99004", logged)


if __name__ == "__main__":
    unittest.main()
