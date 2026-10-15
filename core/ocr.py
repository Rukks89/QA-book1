"""
core/ocr.py

Reads a photographed QA Book label and extracts the printed
fields (Barcode / BC No, Chassis No, TMBSL VC No, Color, Order,
Seating) using OCR (Tesseract) — with an optional, much more
reliable barcode-symbol decode via pyzbar if it's installed.

Requires (system + python):
    - tesseract-ocr   (system package on Linux, or the Windows
                        installer from UB Mannheim on Windows)
    - pytesseract
    - opencv-python-headless
    - numpy
    - Pillow
    - pillow-heif     (for .heic/.heif photos)

Optional (for reading an actual printed barcode SYMBOL instead
of the "BC No" text next to it — much more accurate, and is
what a real USB barcode scanner would read):
    - libzbar0        (system package, Linux only — the Windows
                        pyzbar wheel bundles its own zbar DLLs)
    - pyzbar
"""

import io
import os
import re
import shutil
import platform
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image, ImageOps
import pytesseract
import cv2

OCR_UPSCALE_FACTOR = 1.5
OCR_MAX_LONG_EDGE = 3000

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_AVAILABLE = True

except Exception:
    # Keep JPEG/PNG OCR available when the optional HEIF decoder
    # is missing or cannot be initialized.
    HEIF_AVAILABLE = False


try:
    from pyzbar.pyzbar import decode as _zbar_decode
    PYZBAR_AVAILABLE = True

except ImportError:

    PYZBAR_AVAILABLE = False


# ============================================================
# LOCATE THE TESSERACT EXECUTABLE
# ============================================================

def _configure_tesseract():
    """
    Configure pytesseract on Windows and Linux.

    TESSERACT_CMD can override the location on any operating system.
    On Windows, the standard Program Files locations are checked.
    On Linux/Replit, the installed `tesseract` command is used.
    """

    _candidate_paths = [
        os.environ.get("TESSERACT_CMD"),
        shutil.which("tesseract"),
    ]

    if platform.system() == "Windows":

        _candidate_paths.extend([
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ])

    for _candidate in _candidate_paths:

        if _candidate and (
            os.path.isfile(_candidate)
            or shutil.which(_candidate)
        ):

            pytesseract.pytesseract.tesseract_cmd = _candidate

            return _candidate

    return None


TESSERACT_PATH = _configure_tesseract()


# ============================================================
# PARALLELISM
# ============================================================
#
# Each OCR pass shells out to a separate `tesseract` process via
# pytesseract, so running several passes concurrently gives a
# real wall-clock speedup on multi-core machines (the Python
# threads spend their time waiting on those external processes,
# not competing for the GIL). This does NOT change what gets
# computed or how fields are voted on — only how fast the exact
# same set of passes finishes.
# ============================================================

# Each worker starts a separate Tesseract process. Keep this small so
# Streamlit Community Cloud does not spawn a CPU-sized process burst.
_MAX_WORKERS = min(2, max(1, os.cpu_count() or 1))


# ============================================================
# FIELD EXTRACTION PATTERNS
# ============================================================

FIELD_PATTERNS = {

    "chassis_no":
        r"chass?is\s*:?\s*-?\s*([A-Z0-9]{6,25})",

    # Real TMBSL labels print the barcode's human-readable
    # number as "BC No:xxx" right next to the barcode symbol —
    # there's no separate "Barcode:" text on the label.
    "barcode_text":
        r"\bbc\s*no\.?\s*:?\s*([A-Za-z0-9]+)",

    "vc_no":
        r"(?:tmbsl\s*)?vc\s*no\.?\s*:?\s*([A-Za-z0-9]+)",

    "color":
        r"col[o0e]u?r\s*:?\s*([A-Za-z]+)",

    "order_no":
        r"order\s*:?\s*([A-Za-z0-9]+)",

    "seating":
        r"seat(?:ing)?\s*:?\s*(\d+)"

}


# ============================================================
# BARCODE-BAND GEOMETRY DETECTION
# ============================================================

def _find_barcode_geometry(gray):

    sobel_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)

    row_energy = np.mean(np.abs(sobel_x), axis=1)

    row_threshold = row_energy.mean() + 1.2 * row_energy.std()

    band_rows = np.where(row_energy > row_threshold)[0]

    if len(band_rows) == 0:

        return None

    gaps = np.where(np.diff(band_rows) > 20)[0]

    runs = np.split(band_rows, gaps + 1)

    best_run = max(runs, key=len)

    y1, y2 = best_run[0], best_run[-1]

    band_height = y2 - y1

    if band_height < 20:

        return None

    col_strip = np.abs(sobel_x[y1:y2 + 1, :])

    col_energy = np.mean(col_strip, axis=0)

    col_threshold = col_energy.mean() + 0.5 * col_energy.std()

    cols = np.where(col_energy > col_threshold)[0]

    if len(cols) == 0:

        return None

    x1, x2 = cols.min(), cols.max()

    return {
        "x1": int(x1),
        "x2": int(x2),
        "y1": int(y1),
        "y2": int(y2),
        "height": int(band_height)
    }


def _measure_skew_angle(gray, geometry):
    """
    Measures how tilted the photo is, using the barcode's own
    bars as a reference (they're always perfectly vertical when
    printed — any deviation reflects how the camera was angled).
    Uses a Hough line transform on the barcode region rather than
    its bounding box, since the bounding box angle just reflects
    the barcode's overall shape, not the fine tilt of individual
    bars.

    Returns (angle_degrees, num_supporting_lines). A low line
    count means the measurement isn't trustworthy (e.g. barcode
    region too small or too noisy) and should be ignored by the
    caller.
    """

    y1, y2 = geometry["y1"], geometry["y2"]
    x1, x2 = geometry["x1"], geometry["x2"]

    region = gray[y1:y2, x1:x2]

    if region.size == 0:

        return 0.0, 0

    _, binary = cv2.threshold(
        region, 0, 255,
        cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )

    lines = cv2.HoughLines(
        binary, 1, np.pi / 720,
        threshold=max(10, int(region.shape[0] * 0.5))
    )

    if lines is None:

        return 0.0, 0

    angles = []

    for line in lines:

        _rho, theta = line[0]

        deg = np.degrees(theta)

        # bars are printed near-vertical, so deviation from 0
        # (mod 180, normalized) is the tilt we care about
        deviation = deg if deg <= 90 else deg - 180

        if abs(deviation) < 30:

            angles.append(deviation)

    if not angles:

        return 0.0, 0

    return float(np.median(angles)), len(angles)


def _rotate_image(gray, angle_degrees):
    """
    Rotates the image around its center by angle_degrees,
    padding any exposed corners with white (matching the label's
    background) rather than black.
    """

    h, w = gray.shape[:2]

    center = (w // 2, h // 2)

    rotation_matrix = cv2.getRotationMatrix2D(
        center, angle_degrees, 1.0
    )

    return cv2.warpAffine(
        gray, rotation_matrix, (w, h),
        borderValue=255,
        flags=cv2.INTER_CUBIC
    )


# A real hand-held tilt reads well above this; smaller readings
# are just measurement noise from photo-to-photo variation and
# "correcting" them actually makes already-good photos worse, so
# only genuine tilts past this threshold get straightened.
SKEW_CORRECTION_THRESHOLD_DEGREES = 6.0


def _auto_straighten(gray, geometry):
    """
    If the photo is tilted enough to matter (see threshold
    above), rotates it to straighten the label and re-locates
    the barcode on the corrected image (its position shifts once
    rotated). Returns (possibly-rotated gray, possibly-updated
    geometry, angle_applied). angle_applied is 0.0 if no
    correction was needed/possible.
    """

    skew_angle, num_lines = _measure_skew_angle(gray, geometry)

    if num_lines < 20 or abs(skew_angle) <= SKEW_CORRECTION_THRESHOLD_DEGREES:

        return gray, geometry, 0.0

    straightened = _rotate_image(gray, skew_angle)

    new_geometry = _find_barcode_geometry(straightened)

    if new_geometry is None:

        # rotation made the barcode undetectable (rare) — safer
        # to fall back to the original, uncorrected image
        return gray, geometry, 0.0

    return straightened, new_geometry, skew_angle


def _crop_label_region(gray, geometry):

    h, w = gray.shape[:2]

    x1, x2 = geometry["x1"], geometry["x2"]
    y1, y2 = geometry["y1"], geometry["y2"]
    bh = geometry["height"]

    top = max(0, int(y1 - 1.3 * bh))
    bottom = min(h, int(y2 + 3.2 * bh))

    label_width = x2 - x1

    left = max(0, int(x1 - 0.15 * label_width))
    right = min(w, int(x2 + 0.15 * label_width))

    cropped = gray[top:bottom, left:right]

    if cropped.shape[1] < 2400:

        # Upscaling helps small print, but extreme enlargement of a
        # distant label only magnifies blur. Bound the interpolation.
        scale = min(3.0, 2400 / cropped.shape[1])

        cropped = cv2.resize(
            cropped, None, fx=scale, fy=scale,
            interpolation=cv2.INTER_CUBIC
        )

    return cropped


def _crop_chassis_strip(gray, geometry):

    h, w = gray.shape[:2]

    x1, x2 = geometry["x1"], geometry["x2"]
    y1 = geometry["y1"]
    bh = geometry["height"]

    top = max(0, int(y1 - 1.3 * bh))
    bottom = y1

    label_width = x2 - x1

    left = max(0, int(x1 - 0.05 * label_width))
    right = min(w, int(x2 + 0.05 * label_width))

    strip = gray[top:bottom, left:right]

    if strip.shape[0] < 10 or strip.shape[1] < 10:

        return None

    scale = min(4.0, 2400 / max(strip.shape[:2]))

    return cv2.resize(
        strip, None, fx=scale, fy=scale,
        interpolation=cv2.INTER_CUBIC
    )


# ============================================================
# BARCODE SYMBOL DECODE (OPTIONAL, MOST RELIABLE)
# ============================================================

def _decode_barcode_symbol(pil_image):

    if not PYZBAR_AVAILABLE:

        return None

    try:

        results = _zbar_decode(pil_image)

    except Exception:

        return None

    if not results:

        return None

    return results[0].data.decode(
        "utf-8", errors="ignore"
    ).strip()


# ============================================================
# FIELD EXTRACTION FROM A SINGLE OCR PASS
# ============================================================

def _extract_field(lines, pattern):

    compiled = re.compile(pattern, re.IGNORECASE)

    for line in lines:

        match = compiled.search(line)

        if match:

            return match.group(1).strip()

    return ""


def _extract_all_fields(raw_text):

    lines = raw_text.splitlines()

    return {
        field: _extract_field(lines, pattern)
        for field, pattern in FIELD_PATTERNS.items()
    }


# ============================================================
# PREPROCESSING VARIANTS — BUILD TASK LIST (NO OCR YET)
# ============================================================
#
# These just prepare images; the actual (slow) OCR calls are
# collected into a flat task list and executed together, in
# parallel, by _run_tasks() below.
# ============================================================

def _build_full_tasks(gray, tag):

    denoised = cv2.bilateralFilter(gray, 9, 75, 75)

    adaptive = cv2.adaptiveThreshold(
        denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 31, 11
    )

    _, otsu = cv2.threshold(
        gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )

    blurred = cv2.GaussianBlur(gray, (0, 0), 3)

    sharpened = cv2.addWeighted(gray, 1.8, blurred, -0.8, 0)

    # CLAHE (local contrast enhancement) recovers text that a
    # global threshold washes out under uneven lighting or a
    # steep viewing angle — cheap to compute, and only ever adds
    # extra voting evidence rather than replacing anything.
    clahe_img = cv2.createCLAHE(
        clipLimit=3.0, tileGridSize=(8, 8)
    ).apply(gray)

    _, clahe_otsu = cv2.threshold(
        clahe_img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )

    return [
        (f"{tag}_plain", gray, "--oem 3 --psm 6"),
        (f"{tag}_adaptive", adaptive, "--oem 3 --psm 6"),
        (f"{tag}_otsu6", otsu, "--oem 3 --psm 6"),
        (f"{tag}_otsu11", otsu, "--oem 3 --psm 11"),
        (f"{tag}_sharpen4", sharpened, "--oem 3 --psm 4"),
        (f"{tag}_clahe", clahe_img, "--oem 3 --psm 6"),
        (f"{tag}_clahe_otsu", clahe_otsu, "--oem 3 --psm 6"),
    ]


def _build_strip_tasks(strip, tag):

    if strip is None:

        return []

    _, otsu = cv2.threshold(
        strip, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )

    blurred = cv2.GaussianBlur(strip, (0, 0), 3)

    sharpened = cv2.addWeighted(strip, 1.8, blurred, -0.8, 0)

    _, sharpened_otsu = cv2.threshold(
        sharpened, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )

    clahe_strip = cv2.createCLAHE(
        clipLimit=3.0, tileGridSize=(8, 8)
    ).apply(strip)

    return [
        (f"{tag}_plain", strip, "--oem 3 --psm 6"),
        (f"{tag}_otsu", otsu, "--oem 3 --psm 6"),
        (f"{tag}_sharpen", sharpened, "--oem 3 --psm 6"),
        (f"{tag}_sharpen_otsu", sharpened_otsu, "--oem 3 --psm 6"),
        (f"{tag}_clahe", clahe_strip, "--oem 3 --psm 6"),
    ]


def _run_tasks(tasks):
    """
    Runs every (name, image, config) task's OCR concurrently
    across a thread pool. Each task hands off to a separate
    `tesseract` process via pytesseract, so this gives a real
    wall-clock speedup on multi-core machines without changing
    what's computed — same images, same configs, same results.
    """

    if not tasks:

        return []

    def _run_one(task):

        name, image_array, config = task

        text = pytesseract.image_to_string(
            Image.fromarray(image_array),
            config=config
        )

        return name, text

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:

        return list(pool.map(_run_one, tasks))


# ============================================================
# VOTE-BASED MERGE ACROSS ALL OCR PASSES
# ============================================================

# ============================================================
# CHARACTER-CONFUSION SAFETY NET
# ============================================================
#
# Certain OCR letter/digit mixups are common enough (O/0, I/l/1,
# S/5, B/8...) that when a field is KNOWN to be purely numeric
# (or numeric within a specific zone of a known template), it's
# safe to correct them — there's no ambiguity about what the
# character "should" be, only about which glyph the scanner
# picked. This never invents missing characters (e.g. a
# truncated read stays truncated) — it only fixes wrong-glyph
# substitutions in positions we're already confident are digits.
# ============================================================

_DIGIT_LOOKALIKES = str.maketrans({
    "O": "0", "o": "0",
    "I": "1", "l": "1", "i": "1",
    "S": "5", "s": "5",
    "B": "8", "b": "8",
    "Z": "2", "z": "2",
    "G": "6", "g": "6",
})


def _numeric_lookalike_correct(value):
    """
    If a value that should be purely numeric contains stray
    letters that are common OCR digit-lookalikes, corrects them.
    Leaves the value untouched if it's already all-digit, empty,
    or still isn't all-digit after the swap (too different to
    safely guess).
    """

    if not value or value.isdigit():

        return value

    corrected = value.translate(_DIGIT_LOOKALIKES)

    if corrected.isdigit():

        return corrected

    return value


# TMBSL chassis numbers consistently follow this exact template:
# "MAT" + 6 digits + "TFH" + 5 digits (17 characters total). When
# a read is the right LENGTH and has the right letters in the
# right places, any stray digit-lookalike letters in the digit
# zones can be safely corrected the same way.
def _vote_merge(all_field_sets, field):

    candidates = [
        fields[field]
        for fields in all_field_sets
        if fields.get(field)
    ]

    if not candidates:

        return ""

    counts = Counter(candidates)

    top_count = max(counts.values())

    tied = [
        value for value, count in counts.items()
        if count == top_count
    ]

    if len(tied) == 1:

        return tied[0]

    return max(tied, key=len)


def _rank_field_candidates(all_field_sets, field):
    """Return distinct OCR readings with their independent pass counts."""

    counts = Counter(
        str(fields.get(field, "")).strip()
        for fields in all_field_sets
        if str(fields.get(field, "")).strip()
    )
    return [
        {"value": value, "votes": votes}
        for value, votes in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]


def _candidate_is_strong(candidates, min_votes=3, ratio=2):
    """Require repeated agreement; a plurality or longest-string tie is not enough."""

    if not candidates or candidates[0]["votes"] < min_votes:
        return False
    runner_up_votes = (
        candidates[1]["votes"] if len(candidates) > 1 else 0
    )
    return candidates[0]["votes"] >= ratio * max(1, runner_up_votes)


def _build_targeted_tasks(gray, geometry):
    """A small, bounded crop pass for chassis reads lacking consensus."""

    if geometry is None:
        return []

    label = _crop_label_region(gray, geometry)
    strip = _crop_chassis_strip(gray, geometry)
    tasks = [
        ("target_label_plain", label, "--oem 3 --psm 6"),
        (
            "target_label_clahe",
            cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(label),
            "--oem 3 --psm 6",
        ),
    ]
    if strip is not None:
        _, strip_otsu = cv2.threshold(
            strip, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )
        tasks.extend([
            ("target_chassis_strip", strip, "--oem 3 --psm 7"),
            ("target_chassis_otsu", strip_otsu, "--oem 3 --psm 7"),
        ])
    return tasks


def _build_rotation_fallback_tasks(gray):
    """Try quarter-turn orientations only when the initial chassis read fails."""

    rotations = (
        ("90cw", cv2.ROTATE_90_CLOCKWISE),
        ("180", cv2.ROTATE_180),
        ("90ccw", cv2.ROTATE_90_COUNTERCLOCKWISE),
    )
    tasks = []
    for tag, direction in rotations:
        rotated = cv2.rotate(gray, direction)
        tasks.extend([
            (f"rotate_{tag}_plain", rotated, "--oem 3 --psm 6"),
            (f"rotate_{tag}_sparse", rotated, "--oem 3 --psm 11"),
        ])
    return tasks


# ============================================================
# MAIN ENTRY POINT
# ============================================================

def estimate_image_quality(image_bytes):
    """
    Fast (milliseconds), lightweight check run BEFORE the full
    OCR pipeline — gives near-instant feedback on whether a
    photo is likely too blurry or too low-resolution to read
    well, so the user can retake it right away instead of
    waiting 5-30 seconds for a doomed extraction.

    Returns:
        {
            "sharpness": float,     # Laplacian variance; higher = sharper
            "width": int,
            "height": int,
            "is_blurry": bool,
            "is_low_res": bool
        }
    """

    pil_image = Image.open(io.BytesIO(image_bytes))

    pil_image = ImageOps.exif_transpose(pil_image)

    width, height = pil_image.size

    gray = cv2.cvtColor(
        np.array(pil_image.convert("RGB")),
        cv2.COLOR_RGB2GRAY
    )

    # normalize to a consistent size before scoring sharpness,
    # so the score isn't just a proxy for resolution
    scoring_gray = gray

    if max(height, width) > 1200:

        scale = 1200 / max(height, width)

        scoring_gray = cv2.resize(
            gray, None, fx=scale, fy=scale,
            interpolation=cv2.INTER_AREA
        )

    sharpness = float(
        cv2.Laplacian(scoring_gray, cv2.CV_64F).var()
    )

    return {
        "sharpness": sharpness,
        "width": width,
        "height": height,
        "is_blurry": sharpness < 450,
        "is_low_res": max(width, height) < 1600
    }


def extract_label_fields(image_bytes):
    """
    Takes raw image bytes (from st.camera_input or
    st.file_uploader) and returns a dict:

        {
            "barcode":        str,  # best available barcode value
            "barcode_source": str,  # "symbol" | "text" | "none"
            "chassis_no":     str,
            "vc_no":          str,
            "color":          str,
            "order_no":       str,
            "seating":        str,
            "raw_text":       str   (every OCR pass, for audit)
        }

    The first pass uses bounded parallelism. Only an uncertain or
    missing chassis read triggers up to four targeted crop passes.
    Chassis candidates are preserved; OCR never rewrites lookalike
    characters without an exact master-data match.
    """

    pil_image = Image.open(io.BytesIO(image_bytes))

    pil_image = ImageOps.exif_transpose(pil_image)

    # Resize the working copy once: modestly enlarge small phone photos,
    # and cap very large uploads to bound OCR memory and processing time.
    # The original bytes remain untouched for the caller's evidence copy.
    width, height = pil_image.size
    scale = min(
        OCR_UPSCALE_FACTOR,
        OCR_MAX_LONG_EDGE / max(width, height),
    )
    if abs(scale - 1.0) > 0.001:
        pil_image = pil_image.resize(
            (
                max(1, round(width * scale)),
                max(1, round(height * scale)),
            ),
            Image.Resampling.LANCZOS,
        )

    gray = cv2.cvtColor(
        np.array(pil_image.convert("RGB")),
        cv2.COLOR_RGB2GRAY
    )


    # --------------------------------------------------------
    # AUTO-STRAIGHTEN (ANGLE / TILT CORRECTION)
    #
    # Detects the barcode once here, measures how tilted the
    # photo is against it, and — only for a genuine tilt, not
    # just photo-to-photo noise — rotates the whole photo so
    # the label reads as if it were held flat. This is what
    # makes the app tolerant of the camera being held at an
    # angle, rather than needing it perfectly square-on. The
    # (possibly-updated) geometry is reused below so it isn't
    # detected twice.
    # --------------------------------------------------------

    geometry = _find_barcode_geometry(gray)

    tilt_corrected_degrees = 0.0

    if geometry is not None:

        gray, geometry, tilt_corrected_degrees = _auto_straighten(
            gray, geometry
        )


    # --------------------------------------------------------
    # STAGE 1 — WHOLE-PHOTO PASSES (ALWAYS RUN, IN PARALLEL)
    # --------------------------------------------------------

    full_tasks = _build_full_tasks(gray, "full")

    full_results = _run_tasks(full_tasks)

    all_field_sets = [
        _extract_all_fields(text) for _name, text in full_results
    ]

    pass_texts = [text for _name, text in full_results]


    # --------------------------------------------------------
    # STAGE 2 — ONLY WHEN THE CRITICAL CHASSIS READ IS UNCERTAIN
    # --------------------------------------------------------

    initial_chassis = _rank_field_candidates(
        all_field_sets, "chassis_no"
    )
    if not _candidate_is_strong(initial_chassis):
        extra_tasks = (
            _build_targeted_tasks(gray, geometry)
            if geometry is not None
            else _build_rotation_fallback_tasks(gray)
        )
        extra_results = _run_tasks(extra_tasks)
        all_field_sets += [
            _extract_all_fields(text) for _name, text in extra_results
        ]
        pass_texts += [text for _name, text in extra_results]


    # --------------------------------------------------------
    # VOTE-MERGE EVERY FIELD ACROSS ALL PASSES THAT RAN
    # --------------------------------------------------------

    chassis_candidates = _rank_field_candidates(
        all_field_sets, "chassis_no"
    )
    barcode_candidates = _rank_field_candidates(
        all_field_sets, "barcode_text"
    )
    extracted = {
        field: _vote_merge(all_field_sets, field)
        for field in FIELD_PATTERNS
    }

    barcode_text = extracted.pop("barcode_text", "")

    # Numeric-only fields have a known shape; chassis identifiers
    # do not have a verified universal template in the supplied
    # master data, so preserve their OCR characters exactly.
    barcode_text = _numeric_lookalike_correct(barcode_text)
    extracted["seating"] = _numeric_lookalike_correct(
        extracted.get("seating", "")
    )
    extracted["chassis_no"] = (
        chassis_candidates[0]["value"] if chassis_candidates else ""
    )
    extracted["chassis_original_candidate"] = extracted["chassis_no"]
    extracted["chassis_candidates"] = chassis_candidates
    extracted["chassis_status"] = (
        "consensus"
        if _candidate_is_strong(chassis_candidates)
        else "manual_verification_required"
    )
    extracted["barcode_candidates"] = barcode_candidates


    # --------------------------------------------------------
    # BARCODE — prefer a real decoded symbol over label text
    # --------------------------------------------------------

    symbol_barcode = _decode_barcode_symbol(pil_image)

    if symbol_barcode:

        extracted["barcode"] = symbol_barcode
        extracted["barcode_source"] = "symbol"

    elif barcode_text:

        extracted["barcode"] = barcode_text
        extracted["barcode_source"] = "text"

    else:

        extracted["barcode"] = ""
        extracted["barcode_source"] = "none"

    extracted["raw_text"] = "\n\n".join(
        f"--- pass {i + 1} ---\n{text.strip()}"
        for i, text in enumerate(pass_texts)
        if text.strip()
    )

    extracted["tilt_corrected_degrees"] = tilt_corrected_degrees

    return extracted