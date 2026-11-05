"""
Accuracy tests for the v2 OCR pipeline.

Pure-helper tests always run.  The real-photo tests run only when the
OCR packages (rapidocr / onnxruntime / opencv) are installed:

    python -m unittest tests.test_ocr_accuracy -v
"""

import importlib
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FIXTURES = PROJECT_ROOT / "tests" / "fixtures"

EXPECTED = {
    "label_1145.jpg": dict(
        chassis_no="MAT789073TMJ02485", barcode="1145",
        vc_no="49912416001R", color="OTHERS", order_no="MTS", seating="56",
    ),
    "label_874.jpg": dict(
        chassis_no="MAT382042T8R21782", barcode="874",
        vc_no="39900309012R", color="WHITE", order_no="EVR", seating="40",
    ),
}


def _real_ocr_available():
    try:
        import importlib.util
        return (
            importlib.util.find_spec("rapidocr") is not None
            and importlib.util.find_spec("onnxruntime") is not None
        )
    except Exception:
        return False


class NormalizerTests(unittest.TestCase):
    def setUp(self):
        self.ocr = importlib.import_module("core.ocr")

    def test_chassis_digit_zones_fixed(self):
        value, valid = self.ocr.normalize_chassis("MAT789O73TMJ0248S")
        self.assertEqual(value, "MAT789073TMJ02485")
        self.assertTrue(valid)

    def test_chassis_letters_zone_kept(self):
        value, valid = self.ocr.normalize_chassis("MAT382042T8R21782")
        self.assertEqual(value, "MAT382042T8R21782")
        self.assertTrue(valid)

    def test_short_chassis_is_not_valid(self):
        self.assertFalse(self.ocr.normalize_chassis("MAT7890737")[1])

    def test_vc_shape(self):
        self.assertEqual(self.ocr.normalize_vc("4991241600IR"), ("49912416001R", True))
        self.assertFalse(self.ocr.normalize_vc("399003090")[1])

    def test_color_snaps_to_vocabulary(self):
        self.assertEqual(self.ocr.normalize_color("OTHEAS"), ("OTHERS", True))
        self.assertEqual(self.ocr.normalize_color("WHlTE".upper()), ("WHITE", True))

    def test_order_and_seating(self):
        self.assertEqual(self.ocr.normalize_order("EvR"), ("EVR", True))
        self.assertEqual(self.ocr.normalize_seating("S6"), ("56", True))

    def test_parse_label_lines(self):
        lines = [
            ("Chassis:MAT789073TMJ02485 BC No:1145", 0.99),
            ("TMBSL VC No:49912416001R", 0.98),
            ("Color:OTHERS Order:MTS SeatIng:56", 0.99),
        ]
        found = self.ocr.parse_fields_from_lines(lines)
        best = {k: v[0][0] for k, v in found.items()}
        self.assertEqual(best["chassis_no"], "MAT789073TMJ02485")
        self.assertEqual(best["barcode_text"], "1145")
        self.assertEqual(best["vc_no"], "49912416001R")
        self.assertEqual(best["color"], "OTHERS")
        self.assertEqual(best["order_no"], "MTS")
        self.assertEqual(best["seating"], "56")

    def test_snap_to_known_unique_close_match(self):
        known = ["MAT789073TMJ02485", "MAT382042T8R21782"]
        self.assertEqual(
            self.ocr._snap_to_known("MAT789073TMJ02486", known),
            ("MAT789073TMJ02485", 1),
        )
        self.assertEqual(self.ocr._snap_to_known("MAT111111AAA11111", known), (None, None))


@unittest.skipUnless(_real_ocr_available(), "OCR packages not installed")
class RealPhotoTests(unittest.TestCase):
    """Runs in a clean subprocess so other tests' import stubs can't interfere."""

    def test_real_labels_read_exactly(self):
        import json
        import subprocess

        script = (
            "import json, sys; sys.path.insert(0, %r)\n"
            "from core import ocr\n"
            "out = {}\n"
            "for name in %r:\n"
            "    data = open(%r + '/' + name, 'rb').read()\n"
            "    r = ocr.extract_label_fields(data)\n"
            "    out[name] = {k: r[k] for k in ('chassis_no','barcode','vc_no','color','order_no','seating')}\n"
            "print('RESULT' + json.dumps(out))\n"
        ) % (str(PROJECT_ROOT), list(EXPECTED), str(FIXTURES))
        proc = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=300
        )
        line = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT")]
        self.assertTrue(line, proc.stderr[-2000:])
        results = json.loads(line[-1][len("RESULT"):])
        for name, expected in EXPECTED.items():
            for field, value in expected.items():
                with self.subTest(photo=name, field=field):
                    self.assertEqual(results[name][field], value)


if __name__ == "__main__":
    unittest.main()
