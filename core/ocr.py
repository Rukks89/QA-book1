"""
core/ocr.py  —  QA Book label reader (v2, high-accuracy)

Reads a photographed TMBSL QA Book label and extracts:

    Chassis No, BC No (barcode number), TMBSL VC No, Color, Order, Seating

Label layout (fixed, printed):

    Chassis:MAT789073TMJ02485 BC No:1145
    ||||||||||||| barcode (Code 39 = chassis number) |||||||||||||
    TMBSL VC No:49912416001R
    DL_MCV_56HB_LP01618_57NAC_STD_MSRTC_SD
    Color:OTHERS Order:MTS Seating:56
    TML VCBC:  Bus No:

How accuracy is achieved
------------------------
1.  Deep-learning OCR (RapidOCR / PP-OCR, ONNX, CPU-only) is the
    primary engine.  It is far more accurate than Tesseract on phone
    photos (glare, blur, tilt, perspective).  Tesseract is kept as an
    automatic fallback / extra voter.
2.  The printed barcode symbol is decoded with zxing-cpp.  On TMBSL
    labels the barcode encodes the CHASSIS number, so a decoded symbol
    is an independent, near-certain confirmation of the chassis.
3.  Every field has a known shape (e.g. chassis = MAT + 6 digits +
    3 chars + 5 digits, VC No = 11 digits + R).  Readings are checked
    against those templates and common OCR glyph mix-ups (O/0, I/1,
    S/5, B/8 ...) are fixed ONLY in positions that must be digits.
4.  Several image variants are read and the results are voted on.
    Extra passes only run when a field is missing or doubtful, so
    good photos stay fast (~1-3 s).
5.  Optional "known values" (chassis numbers from master data / the
    uploaded Bus–Chassis mapping) let a reading that is 1-2 characters
    off snap to the exact real chassis number.
6.  Each field gets a confidence flag ("high" / "check") so the
    operator knows exactly which boxes to double-check.

The public API is backwards compatible with v1:
    estimate_image_quality(image_bytes) -> dict
    extract_label_fields(image_bytes, known_chassis=None) -> dict
"""

from __future__ import annotations

import difflib
import io
import os
import platform
import re
import shutil
import threading
from collections import defaultdict

import numpy as np
from PIL import Image, ImageOps
import cv2

# ------------------------------------------------------------------
# OPTIONAL BACKENDS
# ------------------------------------------------------------------

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_AVAILABLE = True
except Exception:
    HEIF_AVAILABLE = False

try:
    import pytesseract

    TESSERACT_IMPORTED = True
except Exception:
    pytesseract = None
    TESSERACT_IMPORTED = False

try:
    import zxingcpp

    ZXING_AVAILABLE = True
except Exception:
    zxingcpp = None
    ZXING_AVAILABLE = False

try:
    from pyzbar.pyzbar import decode as _zbar_decode

    PYZBAR_AVAILABLE = True
except Exception:
    _zbar_decode = None
    PYZBAR_AVAILABLE = False

try:
    from rapidocr import RapidOCR as _RapidOCR

    RAPIDOCR_IMPORTED = True
    RAPIDOCR_IMPORT_ERROR = ""
except Exception as _rapid_err:  # pragma: no cover - depends on install
    _RapidOCR = None
    RAPIDOCR_IMPORTED = False
    RAPIDOCR_IMPORT_ERROR = str(_rapid_err)


# ------------------------------------------------------------------
# TESSERACT LOCATION (fallback engine)
# ------------------------------------------------------------------

def _configure_tesseract():
    if not TESSERACT_IMPORTED:
        return None
    candidates = [os.environ.get("TESSERACT_CMD"), shutil.which("tesseract")]
    if platform.system() == "Windows":
        candidates += [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ]
    for cand in candidates:
        if cand and (os.path.isfile(cand) or shutil.which(cand)):
            try:
                pytesseract.pytesseract.tesseract_cmd = cand
            except Exception:
                return None
            return cand
    return None


TESSERACT_PATH = _configure_tesseract()
TESSERACT_AVAILABLE = bool(TESSERACT_PATH)


# ------------------------------------------------------------------
# RAPIDOCR ENGINE (lazy singleton, thread-safe)
# ------------------------------------------------------------------

_ENGINE = None
_ENGINE_LOCK = threading.Lock()
_ENGINE_ERROR = ""


def _get_engine():
    """Create the RapidOCR engine once per process (models load ~1 s)."""
    global _ENGINE, _ENGINE_ERROR
    if not RAPIDOCR_IMPORTED:
        return None
    if _ENGINE is not None:
        return _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None and not _ENGINE_ERROR:
            try:
                _ENGINE = _RapidOCR(
                    params={
                        "Global.log_level": "error",
                        "Global.text_score": 0.3,
                        "Global.max_side_len": 2400,
                        "EngineConfig.onnxruntime.intra_op_num_threads": 2,
                    }
                )
            except Exception as err:  # pragma: no cover
                _ENGINE_ERROR = str(err)
                _ENGINE = None
    return _ENGINE


# onnxruntime sessions are not guaranteed re-entrant across Streamlit
# sessions; serialising calls also keeps memory flat on small servers.
_RUN_LOCK = threading.Lock()


def ocr_engine_name():
    if _get_engine() is not None:
        return "RapidOCR (deep learning)" + (" + Tesseract" if TESSERACT_AVAILABLE else "")
    if TESSERACT_AVAILABLE:
        return "Tesseract"
    return "none"


def warm_up():
    """Load models ahead of the first scan (call from app start-up)."""
    engine = _get_engine()
    if engine is None:
        return False
    try:
        with _RUN_LOCK:
            engine(np.full((64, 256, 3), 255, dtype=np.uint8), use_det=True, use_cls=True, use_rec=True)
    except Exception:
        pass
    return True


# ------------------------------------------------------------------
# CONSTANTS / TEMPLATES
# ------------------------------------------------------------------

WORK_MAX_LONG_EDGE = 2000      # resize target for the OCR working copy
FIELDS = ("chassis_no", "barcode_text", "vc_no", "color", "order_no", "seating")

# characters OCR commonly confuses with digits (only applied to
# positions that MUST be digits)
_TO_DIGIT = {
    "O": "0", "Q": "0", "D": "0", "U": "0", "C": "0",
    "I": "1", "L": "1", "J": "1", "|": "1", "!": "1", "T": "1",
    "Z": "2",
    "S": "5", "$": "5",
    "G": "6", "b": "6",
    "B": "8",
    "A": "4",
    "g": "9", "q": "9",
}
_TO_LETTER = {"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z", "6": "G", "4": "A"}

DIGITISH = r"[0-9OQDUILJTZSGBA|!$]"

# Chassis (VIN-like) template: MAT + 6 digits + 3 alnum + 5 digits
CHASSIS_RE = re.compile(r"^MAT\d{6}[A-Z0-9]{3}\d{5}$")
VC_RE = re.compile(r"^\d{11}R$")
VC_LOOSE_RE = re.compile(r"^\d{8,14}[A-Z]?$")

KNOWN_COLORS = [
    "WHITE", "OTHERS", "RED", "BLUE", "YELLOW", "GREEN", "SILVER", "GREY",
    "GRAY", "BLACK", "ORANGE", "MAROON", "CREAM", "BROWN", "IVORY", "BEIGE",
    "PURPLE", "PINK", "GOLD", "MULTI", "MULTICOLOR", "SKYBLUE", "NAVYBLUE",
]
KNOWN_ORDERS = ["MTS", "EVR", "MTO"]

_CONF_HIGH = "high"
_CONF_CHECK = "check"
_CONF_MISSING = "missing"


# ------------------------------------------------------------------
# SMALL HELPERS
# ------------------------------------------------------------------

def _digits_fix(value):
    """Map look-alike glyphs to digits. Returns corrected string."""
    return "".join(ch if ch.isdigit() else _TO_DIGIT.get(ch, ch) for ch in value)


def _numeric_lookalike_correct(value):
    """Backwards-compatible helper: fix a purely numeric value if possible."""
    if not value or value.isdigit():
        return value
    fixed = _digits_fix(value.upper())
    return fixed if fixed.isdigit() else value


def _levenshtein(a, b):
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _clean_token(text):
    return re.sub(r"[^A-Z0-9]", "", str(text).upper())


# ------------------------------------------------------------------
# FIELD NORMALISERS  (raw OCR value -> (clean value, is_valid))
# ------------------------------------------------------------------

def normalize_chassis(raw):
    s = _clean_token(raw)
    if not s:
        return "", False
    # Leading "MAT": fix 4->A / M misreads like "MA7", "NAT", "M4T"
    if len(s) >= 17 and not s.startswith("MAT"):
        head = s[:3]
        if _levenshtein(head, "MAT") <= 1 and head[0] in "MNH" :
            s = "MAT" + s[3:]
    if s.startswith("MAT") and len(s) >= 17:
        s = s[:17]
        body = list(s)
        # positions 3..8 and 12..16 must be digits
        for idx in list(range(3, 9)) + list(range(12, 17)):
            ch = body[idx]
            if not ch.isdigit():
                body[idx] = _TO_DIGIT.get(ch, ch)
        # VIN rule: letters I, O, Q never appear in a VIN
        for idx in range(9, 12):
            ch = body[idx]
            if ch in "OQ":
                body[idx] = "0"
            elif ch == "I":
                body[idx] = "1"
        s = "".join(body)
    return s, bool(CHASSIS_RE.match(s))


def normalize_bc(raw):
    s = _clean_token(raw)
    s = _digits_fix(s)
    m = re.match(r"^(\d{1,7})", s)
    if not m:
        return "", False
    return m.group(1), True


def normalize_vc(raw):
    s = _clean_token(raw)
    if not s:
        return "", False
    if len(s) >= 12:
        head, tail = s[:11], s[11]
        head = _digits_fix(head)
        if tail in "RK" or tail == "P" or tail == "A" or tail == "8":
            tail = "R" if tail in "RKPA8" else tail
        cand = head + tail
        if VC_RE.match(cand):
            return cand, True
    fixed = _digits_fix(s[:-1]) + s[-1] if len(s) > 1 else s
    # Only the exact printed shape (11 digits + R) counts as valid; a
    # shorter/longer read is kept as a candidate but flagged for review.
    return fixed, bool(VC_RE.match(fixed))


def normalize_color(raw):
    s = re.sub(r"[^A-Z]", "", str(raw).upper())
    if not s:
        return "", False
    # strip anything glued on from the next key ("WHITEORDER")
    s = re.split(r"[O0]RDER|ORDE|RDER", s)[0] or s
    if s in KNOWN_COLORS:
        return s, True
    match = difflib.get_close_matches(s, KNOWN_COLORS, n=1, cutoff=0.7)
    if match:
        return match[0], True
    return s, len(s) >= 3


def normalize_order(raw):
    s = re.sub(r"[^A-Z0-9\-]", "", str(raw).upper())
    s = re.split(r"SEAT", s)[0]
    if not s:
        return "", False
    if s in KNOWN_ORDERS:
        return s, True
    if len(s) == 3:
        match = difflib.get_close_matches(s, KNOWN_ORDERS, n=1, cutoff=0.66)
        if match:
            return match[0], True
    return s, 2 <= len(s) <= 12


def normalize_seating(raw):
    s = _digits_fix(_clean_token(raw))
    m = re.match(r"^(\d{1,3})", s)
    if not m:
        return "", False
    val = m.group(1).lstrip("0") or "0"
    return val, 1 <= int(val) <= 120


NORMALIZERS = {
    "chassis_no": normalize_chassis,
    "barcode_text": normalize_bc,
    "vc_no": normalize_vc,
    "color": normalize_color,
    "order_no": normalize_order,
    "seating": normalize_seating,
}


# ------------------------------------------------------------------
# PARSING OCR TEXT LINES INTO FIELDS
# ------------------------------------------------------------------
#
# Patterns are tolerant of the usual OCR damage: missing colon,
# extra spaces, 0 for O, l for I, merged words.

_KEY = {
    "chassis": r"C\s*H\s*A?\s*S{1,2}\s*[I1L|]?\s*S",
    "bc": r"\bB\s*[CG]\s*N\s*[O0Q]\b|B[CG]N[O0Q]",
    "vc": r"V\s*[CG]\s*N\s*[O0Q]",
    "color": r"C\s*[O0Q]\s*L\s*[O0Q]\s*U?\s*R",
    "order": r"[O0Q]\s*R\s*D\s*E\s*R",
    "seating": r"S\s*E\s*A\s*T\s*[I1L|]\s*N\s*G|SEAT",
}

_SEP = r"\s*[:;.,\-=]?\s*"

_FIELD_PATTERNS = {
    # chassis value: grab the next long alnum run; spaces inside tolerated
    "chassis_no": [
        re.compile(_KEY["chassis"] + _SEP + r"([A-Z0-9][A-Z0-9 ]{8,24}[A-Z0-9])"),
        re.compile(r"(M\s*[A4]\s*[T7]\s*(?:" + DIGITISH + r"\s*){6}[A-Z0-9]\s*[A-Z0-9]\s*[A-Z0-9]\s*(?:" + DIGITISH + r"\s*){5})"),
    ],
    "barcode_text": [
        re.compile(r"(?:" + _KEY["bc"] + r")" + _SEP + r"(" + DIGITISH + r"{1,7})"),
    ],
    "vc_no": [
        re.compile(_KEY["vc"] + r"\.?" + _SEP + r"([0-9A-Z][0-9A-Z ]{6,16})"),
        re.compile(r"\b(\d{11}\s?R)\b"),
    ],
    "color": [
        re.compile(_KEY["color"] + _SEP + r"([A-Z][A-Z]{1,14})"),
    ],
    "order_no": [
        re.compile(_KEY["order"] + _SEP + r"([A-Z0-9][A-Z0-9\-]{1,11})"),
    ],
    "seating": [
        re.compile(r"(?:" + _KEY["seating"] + r")" + _SEP + r"(" + DIGITISH + r"{1,3})"),
    ],
}


def _cut_at_next_key(value):
    """Stop a captured value where the next printed key begins."""
    upper = value.upper()
    cut = len(upper)
    for key in (r"B\s*C\s*N\s*O", r"ORDER", r"SEAT", r"COLOR", r"TMBSL", r"BUS"):
        m = re.search(key, upper)
        if m and 0 < m.start() < cut:
            cut = m.start()
    return value[:cut]


def parse_fields_from_lines(lines):
    """
    lines: list of (text, score) in reading order.
    Returns {field: [(value, valid, score), ...]}  (all candidates found)
    """
    found = defaultdict(list)
    texts = [(str(t).upper().replace("\n", " "), float(s)) for t, s in lines if str(t).strip()]
    # also try each line joined with the next one (OCR sometimes splits
    # "Chassis:" and the value into two boxes)
    joined = [
        (texts[i][0] + " " + texts[i + 1][0], min(texts[i][1], texts[i + 1][1]) * 0.9)
        for i in range(len(texts) - 1)
    ]
    for text, score in texts + joined:
        for field, patterns in _FIELD_PATTERNS.items():
            for pat in patterns:
                for m in pat.finditer(text):
                    raw = m.group(1)
                    if field in ("chassis_no", "vc_no", "color", "order_no"):
                        raw = _cut_at_next_key(raw)
                    if field == "chassis_no":
                        raw = raw.replace(" ", "")
                    if field == "vc_no":
                        raw = raw.split(" ")[0] if not re.match(r"^\d{11}\s?R", raw) else raw.replace(" ", "")
                    value, valid = NORMALIZERS[field](raw)
                    if value:
                        found[field].append((value, valid, score))
    return found


# ------------------------------------------------------------------
# IMAGE LOADING / QUALITY
# ------------------------------------------------------------------

def _load_image(image_bytes):
    pil = Image.open(io.BytesIO(image_bytes))
    pil = ImageOps.exif_transpose(pil)
    return pil.convert("RGB")


def _to_working_bgr(pil):
    w, h = pil.size
    scale = min(1.0, WORK_MAX_LONG_EDGE / max(w, h))
    if max(w, h) < 1100:
        scale = 1100 / max(w, h)  # small camera frames: enlarge a bit
    if abs(scale - 1.0) > 0.01:
        pil = pil.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.LANCZOS)
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def estimate_image_quality(image_bytes):
    """Fast (ms) blur / resolution check, run before OCR."""
    pil = _load_image(image_bytes)
    width, height = pil.size
    gray = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2GRAY)
    if max(height, width) > 1200:
        s = 1200 / max(height, width)
        gray = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(gray.mean())
    return {
        "sharpness": sharpness,
        "brightness": brightness,
        "width": width,
        "height": height,
        # calibrated on real TMBSL label photos: the AI OCR still reads
        # every field down to ~2.0; below 3.0 a retake is advised
        "is_blurry": sharpness < 3.0,
        "is_low_res": max(width, height) < 1000,
        "is_dark": brightness < 60,
    }


# ------------------------------------------------------------------
# IMAGE VARIANTS
# ------------------------------------------------------------------

def _clahe_bgr(bgr):
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(l)
    return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)


def _sharpen(bgr):
    blur = cv2.GaussianBlur(bgr, (0, 0), 2.0)
    return cv2.addWeighted(bgr, 1.7, blur, -0.7, 0)


def _deglare_gray(bgr):
    """Flatten uneven lighting / glare (divide by background estimate)."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    k = max(15, (min(gray.shape[:2]) // 20) | 1)
    bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    norm = cv2.divide(gray, bg, scale=255)
    return cv2.cvtColor(norm, cv2.COLOR_GRAY2BGR)


# ------------------------------------------------------------------
# OCR PASSES
# ------------------------------------------------------------------

def _rapid_lines(bgr):
    """Run RapidOCR. Returns (lines[(text, score)], boxes[np.array 4x2])."""
    engine = _get_engine()
    if engine is None:
        return [], []
    with _RUN_LOCK:
        # always pass the mode explicitly: RapidOCR remembers the flags
        # from the previous call (e.g. a recognition-only line re-read)
        result = engine(bgr, use_det=True, use_cls=True, use_rec=True)
    txts = getattr(result, "txts", None) or ()
    scores = getattr(result, "scores", None) or ()
    boxes = getattr(result, "boxes", None)
    boxes = list(boxes) if boxes is not None else []
    lines = list(zip(txts, scores))
    return lines, boxes


def _tesseract_lines(bgr, psm=6):
    if not TESSERACT_AVAILABLE:
        return []
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    try:
        text = pytesseract.image_to_string(gray, config=f"--oem 3 --psm {psm}")
    except Exception:
        return []
    return [(ln, 0.6) for ln in text.splitlines() if ln.strip()]


def _label_crop(bgr, boxes, lines):
    """Crop tightly around the detected label text, with margin, upscaled."""
    keyish = re.compile(r"CHASS|BC\s*N|VC\s*N|COLO|ORDER|SEAT|MAT\d", re.I)
    pts = [np.asarray(b) for b, (t, _s) in zip(boxes, lines) if keyish.search(str(t))]
    if len(pts) < 1:
        return None
    pts = np.concatenate(pts).reshape(-1, 2)
    x1, y1 = pts.min(axis=0)
    x2, y2 = pts.max(axis=0)
    h, w = bgr.shape[:2]
    mx, my = 0.06 * (x2 - x1) + 10, 0.25 * (y2 - y1) + 10
    x1, y1 = int(max(0, x1 - mx)), int(max(0, y1 - my))
    x2, y2 = int(min(w, x2 + mx)), int(min(h, y2 + my))
    crop = bgr[y1:y2, x1:x2]
    if crop.size == 0 or crop.shape[0] < 20 or crop.shape[1] < 40:
        return None
    target = 1800
    scale = min(2.5, target / crop.shape[1])
    if scale > 1.05:
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    return crop


def _has_label_keywords(lines):
    joined = " ".join(str(t).upper() for t, _ in lines)
    hits = sum(bool(re.search(p, joined)) for p in (r"CHASS", r"VC\s*N", r"SEAT", r"COLO", r"ORDER", r"MAT\d"))
    return hits


# ------------------------------------------------------------------
# BARCODE SYMBOL
# ------------------------------------------------------------------

def _decode_barcode_symbols(bgr_list):
    """Try zxing-cpp (then pyzbar) on several variants; return list of texts."""
    texts = []
    for img in bgr_list:
        if img is None:
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        if ZXING_AVAILABLE:
            for variant in (gray, cv2.createCLAHE(3.0, (8, 8)).apply(gray)):
                try:
                    for res in zxingcpp.read_barcodes(variant):
                        if res.text:
                            texts.append(res.text.strip())
                except Exception:
                    pass
                if texts:
                    return texts
        if PYZBAR_AVAILABLE:
            try:
                for res in _zbar_decode(Image.fromarray(gray)):
                    texts.append(res.data.decode("utf-8", errors="ignore").strip())
            except Exception:
                pass
            if texts:
                return texts
    return texts


# ---------------- custom Code 39 decoder ----------------
#
# The TMBSL label barcode is Code 39 and encodes the chassis number.
# Phone photos of it are often too soft for generic decoders, so this
# decoder averages many scan-lines into a clean 1-D profile, measures
# bar/space widths with sub-pixel precision, classifies bars and spaces
# separately (ink spread makes printed bars thinner than spaces) and
# requires the SAME result from many independent scan-lines/angles.

_C39 = {
    "0": "000110100", "1": "100100001", "2": "001100001", "3": "101100000",
    "4": "000110001", "5": "100110000", "6": "001110000", "7": "000100101",
    "8": "100100100", "9": "001100100", "A": "100001001", "B": "001001001",
    "C": "101001000", "D": "000011001", "E": "100011000", "F": "001011000",
    "G": "000001101", "H": "100001100", "I": "001001100", "J": "000011100",
    "K": "100000011", "L": "001000011", "M": "101000010", "N": "000010011",
    "O": "100010010", "P": "001010010", "Q": "000000111", "R": "100000110",
    "S": "001000110", "T": "000010110", "U": "110000001", "V": "011000001",
    "W": "111000000", "X": "010010001", "Y": "110010000", "Z": "011010000",
    "-": "010000101", ".": "110000100", " ": "011000100", "*": "010010100",
}
_C39_REV = {v: k for k, v in _C39.items()}


def _c39_widths(profile):
    p = profile.astype(np.float32)
    row = p.reshape(1, -1)
    lo = cv2.GaussianBlur(cv2.erode(row, np.ones((1, 31), np.uint8)), (0, 0), 8).ravel()
    hi = cv2.GaussianBlur(cv2.dilate(row, np.ones((1, 31), np.uint8)), (0, 0), 8).ravel()
    t = (lo + hi) / 2
    d = p - t
    sign = d < 0
    idx = np.nonzero(sign[:-1] != sign[1:])[0]
    if len(idx) < 40:
        return None, None
    a, b = d[idx], d[idx + 1]
    xs = idx + a / (a - b)
    first_after_cross_is_dark = bool(sign[idx[0] + 1])
    return np.diff(xs), first_after_cross_is_dark


def _c39_decode_runs(w, first_dark):
    best = None
    start = 0 if first_dark else 1
    n = len(w)
    for s0 in range(start, n - 9, 2):
        g = w[s0:s0 + 9]
        if _c39_char(g) != "*":
            continue
        out = []
        i = s0 + 10
        while i + 9 <= n:
            ch = _c39_char(w[i:i + 9])
            if ch is None:
                break
            if ch == "*":
                txt = "".join(out)
                if len(txt) >= 4 and (best is None or len(txt) > len(best)):
                    best = txt
                break
            out.append(ch)
            i += 10
    return best


def _c39_char(g):
    bars = sorted((0, 2, 4, 6, 8), key=lambda k: -g[k])[:2]
    sp = max((1, 3, 5, 7), key=lambda k: g[k])
    wide = set(bars) | {sp}
    return _C39_REV.get("".join("1" if k in wide else "0" for k in range(9)))


def _decode_code39_band(band):
    """Return {text: votes} from many scan-lines/angles of a barcode band."""
    if band is None or band.size == 0 or band.shape[0] < 8 or band.shape[1] < 100:
        return {}
    g = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY) if band.ndim == 3 else band
    h, w = g.shape
    found = {}
    for ang in (0, -1, 1, -2, 2, -3, 3):
        if ang:
            m = cv2.getRotationMatrix2D((w / 2, h / 2), ang, 1)
            r = cv2.warpAffine(g, m, (w, h), borderMode=cv2.BORDER_REPLICATE)
        else:
            r = g
        r = r.astype(np.float32)
        for a, b in ((0.3, 0.7), (0.15, 0.45), (0.55, 0.85)):
            prof = r[int(h * a):max(int(h * a) + 1, int(h * b))].mean(axis=0)
            prof = cv2.resize(prof.reshape(1, -1), (w * 3, 1), interpolation=cv2.INTER_CUBIC).ravel()
            widths, first_dark = _c39_widths(prof)
            if widths is None:
                continue
            for ww, fd in ((widths, first_dark), (widths[::-1], (len(widths) % 2 == 1) == first_dark)):
                txt = _c39_decode_runs(ww, fd)
                if txt:
                    found[txt] = found.get(txt, 0) + 1
    return found


def _barcode_band(bgr, boxes, lines):
    """Image strip between the 'Chassis:' text line and the 'VC No' line."""
    ch = [np.asarray(b) for b, (t, _s) in zip(boxes, lines) if re.search(r"CHASS|MAT\d{3}", str(t).upper())]
    if not ch:
        return None
    c = ch[0]
    lh = float(c[:, 1].max() - c[:, 1].min())
    top = float(c[:, 1].max())
    vc = [
        np.asarray(b) for b, (t, _s) in zip(boxes, lines)
        if re.search(r"VC\s*N|TMBSL", str(t).upper()) and np.asarray(b)[:, 1].min() > top
    ]
    bottom = float(vc[0][:, 1].min()) if vc else top + 4 * lh
    h = bgr.shape[0]
    y1, y2 = int(max(0, top)), int(min(h, bottom))
    if y2 - y1 < 6:
        return None
    return bgr[y1:y2, :]


def _barcode_chassis(bgr, boxes, lines, pil):
    """
    Decode the chassis from the barcode. Returns (chassis, votes, method).
    zxing-cpp first (fast); then the custom multi-scan-line Code 39 decoder.
    """
    band = _barcode_band(bgr, boxes, lines) if boxes else None
    for t in _decode_barcode_symbols([band, bgr, cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)]):
        val, valid = normalize_chassis(t)
        if valid and val == _clean_token(t):
            return val, 99, "zxing"
    if band is not None:
        votes = _decode_code39_band(band)
        valid = {}
        for txt, n in votes.items():
            val, ok = normalize_chassis(txt)
            if ok and val == _clean_token(txt):
                valid[val] = valid.get(val, 0) + n
        if valid:
            best, n = max(valid.items(), key=lambda kv: kv[1])
            total = sum(valid.values())
            if n >= 3 and n >= 0.7 * total:
                return best, n, "code39"
    return "", 0, ""


# ---------------- per-line re-reads + per-character voting ----------------

def _line_crop(bgr, box, pad_ratio=0.15):
    """Perspective-correct crop of one detected text line (tight, no bars)."""
    q = np.asarray(box, dtype=np.float32).reshape(4, 2)
    # order: tl, tr, br, bl (RapidOCR returns this order)
    w = int(max(np.linalg.norm(q[0] - q[1]), np.linalg.norm(q[3] - q[2])))
    h = int(max(np.linalg.norm(q[0] - q[3]), np.linalg.norm(q[1] - q[2])))
    if w < 10 or h < 5:
        return None
    pad = pad_ratio * h
    ux = (q[1] - q[0]) / max(1.0, np.linalg.norm(q[1] - q[0]))
    uy = (q[3] - q[0]) / max(1.0, np.linalg.norm(q[3] - q[0]))
    src = np.float32([
        q[0] - ux * pad - uy * pad, q[1] + ux * pad - uy * pad,
        q[2] + ux * pad + uy * pad, q[3] - ux * pad + uy * pad,
    ])
    W, H = int(w + 2 * pad), int(h + 2 * pad)
    dst = np.float32([[0, 0], [W, 0], [W, H], [0, H]])
    m = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(bgr, m, (W, H), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def _line_variants(crop):
    out = []
    if crop is None or crop.size == 0:
        return out
    base_h = max(1, crop.shape[0])
    min_fx = 48.0 / base_h                  # rec model works at >= ~48 px height
    for f in (1.0, 1.5, 2.2):
        fx = max(f, min_fx * f)
        big = cv2.resize(crop, None, fx=fx, fy=fx, interpolation=cv2.INTER_CUBIC)
        out.append(big)
        out.append(_clahe_bgr(big))
        out.append(_sharpen(big))
        out.append(_deglare_gray(big))
        g = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
        _t, otsu = cv2.threshold(cv2.GaussianBlur(g, (3, 3), 0), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        out.append(cv2.cvtColor(otsu, cv2.COLOR_GRAY2BGR))
    return out


def _rec_only(img):
    engine = _get_engine()
    if engine is None:
        return []
    try:
        with _RUN_LOCK:
            r = engine(img, use_det=False, use_cls=False, use_rec=True)
    except Exception:
        return []
    txts = getattr(r, "txts", None) or ()
    scores = getattr(r, "scores", None) or ()
    return list(zip(txts, scores))


def _reread_key_lines(bgr, boxes, lines):
    """
    Re-read the Chassis/BC line and the VC line from 15 enhanced,
    enlarged crops each (recognition only, ~30 ms per read).
    Returns {field: [(value, valid, score), ...]}.
    """
    found = defaultdict(list)
    targets = []
    for box, (t, _s) in zip(boxes, lines):
        up = str(t).upper()
        if re.search(r"CHASS|MAT\d{3}|VC\s*N|TMBSL|COLO|ORDER|SEAT", up):
            targets.append(box)
    for box in targets[:4]:
        for img in _line_variants(_line_crop(bgr, box)):
            res = _rec_only(img)
            for field, items in parse_fields_from_lines(res).items():
                found[field].extend(items)
    return found


def _char_vote(cands, length):
    """
    Per-position weighted vote across same-length readings.
    cands: [(value, score)]. Returns (string, [(pos, top, runner_up)]).
    """
    rows = [(v, sc) for v, sc in cands if len(v) == length]
    if not rows:
        return "", []
    out, doubtful = [], []
    for i in range(length):
        tally = defaultdict(float)
        for v, sc in rows:
            tally[v[i]] += max(0.05, sc)
        ranked = sorted(tally.items(), key=lambda kv: -kv[1])
        out.append(ranked[0][0])
        if len(ranked) > 1 and ranked[1][1] >= 0.35 * ranked[0][1]:
            doubtful.append((i + 1, ranked[0][0], ranked[1][0]))
    return "".join(out), doubtful


def _decode_barcode_symbol(pil_image):
    """v1-compatible single decode helper."""
    bgr = cv2.cvtColor(np.array(pil_image.convert("RGB")), cv2.COLOR_RGB2BGR)
    texts = _decode_barcode_symbols([bgr])
    return texts[0] if texts else None


# ------------------------------------------------------------------
# VOTING
# ------------------------------------------------------------------

def _rank_field_candidates(all_field_sets, field):
    """v1-compatible: distinct readings with pass counts (list of dicts)."""
    from collections import Counter

    counts = Counter(
        str(fs.get(field, "")).strip() for fs in all_field_sets if str(fs.get(field, "")).strip()
    )
    return [
        {"value": v, "votes": c}
        for v, c in sorted(counts.items(), key=lambda it: (-it[1], it[0]))
    ]


def _candidate_is_strong(candidates, min_votes=3, ratio=2):
    """v1-compatible consensus check."""
    if not candidates or candidates[0]["votes"] < min_votes:
        return False
    runner = candidates[1]["votes"] if len(candidates) > 1 else 0
    return candidates[0]["votes"] >= ratio * max(1, runner)


def _vote(candidates):
    """
    candidates: list of (value, valid, score, weight)
    Returns (best_value, best_is_valid, support, total, ranking list)
    """
    agg = defaultdict(lambda: {"w": 0.0, "n": 0, "valid": False, "best": 0.0})
    for value, valid, score, weight in candidates:
        a = agg[value]
        a["w"] += max(0.05, score) * weight * (1.0 if valid else 0.35)
        a["n"] += 1
        a["valid"] = a["valid"] or valid
        a["best"] = max(a["best"], score)
    if not agg:
        return "", False, 0.0, 0.0, []
    ranking = sorted(agg.items(), key=lambda kv: (-kv[1]["valid"], -kv[1]["w"], -kv[1]["n"]))
    total = sum(v["w"] for v in agg.values())
    best_value, info = ranking[0]
    return best_value, info["valid"], info["w"], total, ranking


def _snap_to_known(value, known, max_dist=2):
    """
    Snap an OCR chassis to a known chassis when it is unambiguously
    close (unique best match, clearly better than the runner-up).
    """
    if not value or not known:
        return None, None
    norm_known = {}
    for k in known:
        nk = _clean_token(k)
        if nk:
            norm_known[nk] = k
    if value in norm_known:
        return norm_known[value], 0
    scored = sorted((_levenshtein(value, nk), nk) for nk in norm_known if abs(len(nk) - len(value)) <= max_dist)
    if not scored:
        return None, None
    best_d, best_k = scored[0]
    runner_d = scored[1][0] if len(scored) > 1 else 99
    if best_d <= max_dist and runner_d >= best_d + 2:
        return norm_known[best_k], best_d
    return None, None


# ------------------------------------------------------------------
# MAIN ENTRY POINT
# ------------------------------------------------------------------

def extract_label_fields(image_bytes, known_chassis=None):
    """
    Read a QA Book label photo.

    Returns a dict (all v1 keys kept):
        barcode, barcode_source ("symbol" | "text" | "none"),
        chassis_no, vc_no, color, order_no, seating,
        raw_text, tilt_corrected_degrees,
        chassis_candidates, chassis_status, chassis_original_candidate,
        barcode_candidates,
      plus (v2):
        confidence      {field: "high" | "check" | "missing"}
        notes           [str]  human-readable remarks for the operator
        engine          str
        chassis_source  "barcode" | "ocr" | "ocr+barcode" | "master" | ""
        needs_review    bool
    """
    pil = _load_image(image_bytes)
    bgr = _to_working_bgr(pil)

    candidates = defaultdict(list)   # field -> [(value, valid, score, weight)]
    raw_blocks = []
    notes = []
    engine_used = []

    def add(found, weight, tag, lines):
        for field, items in found.items():
            for value, valid, score in items:
                candidates[field].append((value, valid, score, weight))
        if lines:
            raw_blocks.append(f"--- {tag} ---\n" + "\n".join(str(t) for t, _ in lines))

    def missing_or_weak():
        weak = []
        for f in FIELDS:
            val, valid, w, total, _r = _vote(candidates[f])
            if not val or not valid or (total and w / total < 0.6):
                weak.append(f)
        return weak

    rotated_to = 0
    boxes, lines = [], []

    # ---------- PASS 1: deep-learning OCR on the photo ----------
    if _get_engine() is not None:
        engine_used.append("rapidocr")
        lines, boxes = _rapid_lines(bgr)
        add(parse_fields_from_lines(lines), 1.0, "rapid_original", lines)

        # Sideways / upside-down photo: try the other orientations
        orig_hits = _has_label_keywords(lines)
        if orig_hits < 2:
            for code, deg in ((cv2.ROTATE_90_CLOCKWISE, 90), (cv2.ROTATE_90_COUNTERCLOCKWISE, -90), (cv2.ROTATE_180, 180)):
                rb = cv2.rotate(bgr, code)
                rl, rbx = _rapid_lines(rb)
                if _has_label_keywords(rl) >= max(3, orig_hits + 2):
                    bgr, lines, boxes, rotated_to = rb, rl, rbx, deg
                    add(parse_fields_from_lines(rl), 1.0, f"rapid_rot{deg}", rl)
                    notes.append(f"Photo was rotated {abs(deg)}° — corrected automatically.")
                    break

        # ---------- PASS 2: tight, upscaled crop of the label ----------
        weak = missing_or_weak()
        crop = _label_crop(bgr, boxes, lines) if boxes else None
        if weak and crop is not None:
            cl, _cb = _rapid_lines(crop)
            add(parse_fields_from_lines(cl), 1.0, "rapid_label_crop", cl)

        # ---------- PASS 3: contrast / glare-corrected variants ----------
        weak = missing_or_weak()
        if weak:
            base = crop if crop is not None else bgr
            for tag, variant in (("rapid_clahe", _clahe_bgr(base)), ("rapid_deglare", _deglare_gray(base)), ("rapid_sharpen", _sharpen(base))):
                vl, _vb = _rapid_lines(variant)
                add(parse_fields_from_lines(vl), 0.9, tag, vl)
                if not missing_or_weak():
                    break

    # ---------- Tesseract: fallback engine / extra voter ----------
    weak = missing_or_weak()
    if TESSERACT_AVAILABLE and (not engine_used or weak):
        engine_used.append("tesseract")
        base = None
        if boxes:
            base = _label_crop(bgr, boxes, lines)
        if base is None:
            base = bgr
        gray_bgr = _deglare_gray(base)
        tess_weight = 0.6 if "rapidocr" in engine_used else 1.0
        tess_passes = [("tess_deglare", gray_bgr, 6), ("tess_plain", base, 6), ("tess_sparse", gray_bgr, 11)]
        if "rapidocr" in engine_used:
            tess_passes = tess_passes[:1]   # just one extra opinion
        for tag, img, psm in tess_passes:
            tl = _tesseract_lines(img, psm)
            add(parse_fields_from_lines(tl), tess_weight, tag, tl)
            if "rapidocr" in engine_used and not missing_or_weak():
                break

    # ---------- Re-read the key lines (chassis / VC) many ways ----------
    if boxes and _get_engine() is not None:
        add(_reread_key_lines(bgr, boxes, lines), 1.0, "line_rereads", [])

    # ---------- Barcode symbol (encodes chassis on TMBSL labels) ----------
    symbol_chassis, symbol_votes, symbol_method = _barcode_chassis(bgr, boxes, lines, pil)
    symbol_other = ""
    if not symbol_chassis:
        for t in _decode_barcode_symbols([bgr]):
            if t and not normalize_chassis(t)[1]:
                symbol_other = t
                break

    # ---------- VOTE ----------
    result = {}
    confidence = {}
    for field in FIELDS:
        val, valid, w, total, ranking = _vote(candidates[field])
        result[field] = val
        share = (w / total) if total else 0.0
        n_support = ranking[0][1]["n"] if ranking else 0
        best_score = ranking[0][1]["best"] if ranking else 0.0
        if not val:
            confidence[field] = _CONF_MISSING
        elif valid and share >= 0.6 and n_support >= 3 and best_score >= 0.8:
            confidence[field] = _CONF_HIGH
        else:
            confidence[field] = _CONF_CHECK
        result[f"__rank_{field}"] = ranking

    # ---------- Chassis: char-level vote, barcode, master data ----------
    chassis_doubtful = []
    valid_reads = [(v, sc) for v, ok, sc, _w in candidates["chassis_no"] if ok]
    voted, chassis_doubtful = _char_vote(valid_reads, 17)
    ocr_best = voted or result["chassis_no"]
    chassis = ocr_best
    chassis_source = "ocr" if chassis else ""
    if voted:
        if chassis_doubtful:
            confidence["chassis_no"] = _CONF_CHECK
        elif len(valid_reads) >= 3:
            confidence["chassis_no"] = _CONF_HIGH

    if symbol_chassis:
        # Barcode agreed on by many independent scan-lines: authoritative.
        chassis = symbol_chassis
        confidence["chassis_no"] = _CONF_HIGH
        chassis_doubtful = []
        if ocr_best and ocr_best != symbol_chassis:
            chassis_source = "barcode"
            notes.append(
                f"Chassis taken from the barcode ({symbol_chassis}); the printed text read as {ocr_best}."
            )
        else:
            chassis_source = "ocr+barcode"
    elif known_chassis and chassis:
        snapped, dist = _snap_to_known(chassis, known_chassis)
        if snapped is not None:
            snapped_clean = _clean_token(snapped)
            if dist and dist > 0:
                notes.append(
                    f"Chassis matched to master/mapping record {snapped_clean} (OCR read {chassis}, {dist} char difference)."
                )
            chassis_source = "master"
            chassis = snapped_clean
            confidence["chassis_no"] = _CONF_HIGH
            chassis_doubtful = []
        elif len(known_chassis) >= 20 and confidence["chassis_no"] == _CONF_HIGH:
            notes.append("Chassis not found in master data / mapping — please verify.")
            confidence["chassis_no"] = _CONF_CHECK

    if chassis_doubtful:
        parts = ", ".join(f"character {pos}: '{a}' or '{b}'" for pos, a, b in chassis_doubtful)
        notes.append(f"Chassis unclear at {parts} — please check against the label.")
    result["chassis_no"] = chassis

    # ---------- Barcode (BC No) ----------
    barcode_text = result["barcode_text"]
    if barcode_text:
        barcode, barcode_source = barcode_text, "text"
    elif symbol_other and re.fullmatch(r"[A-Za-z0-9\-]{1,20}", symbol_other):
        barcode, barcode_source = symbol_other, "symbol"
    else:
        barcode, barcode_source = "", "none"

    # ---------- Assemble ----------
    chassis_ranking = result.pop("__rank_chassis_no", [])
    bc_ranking = result.pop("__rank_barcode_text", [])
    for f in FIELDS:
        result.pop(f"__rank_{f}", None)

    out = {
        "barcode": barcode,
        "barcode_source": barcode_source,
        "chassis_no": chassis,
        "vc_no": result["vc_no"],
        "color": result["color"],
        "order_no": result["order_no"],
        "seating": result["seating"],
        "raw_text": "\n\n".join(raw_blocks),
        "tilt_corrected_degrees": float(rotated_to),
        "chassis_original_candidate": ocr_best or chassis,
        "chassis_candidates": [
            {"value": v, "votes": info["n"]} for v, info in chassis_ranking[:5]
        ],
        "barcode_candidates": [
            {"value": v, "votes": info["n"]} for v, info in bc_ranking[:5]
        ],
        "chassis_symbol": symbol_chassis,
        "chassis_symbol_method": symbol_method,
        "chassis_doubtful_positions": [pos for pos, _a, _b in chassis_doubtful],
        "chassis_source": chassis_source,
        "engine": "+".join(engine_used) or "none",
        "notes": notes,
    }
    confidence["barcode"] = confidence.pop("barcode_text")
    out["confidence"] = confidence
    out["chassis_status"] = (
        "consensus" if confidence.get("chassis_no") == _CONF_HIGH else "manual_verification_required"
    )
    out["needs_review"] = any(c != _CONF_HIGH for c in confidence.values())
    if not engine_used:
        out["notes"].append(
            "No OCR engine is installed on the server (RapidOCR / Tesseract). Enter fields manually."
        )
    return out
