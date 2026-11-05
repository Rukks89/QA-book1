# TMBSL QA Book Scanning & Batch Tracking System

## Purpose
Digitization of QA Book records by USB barcode scanner, manual entry,
or **AI OCR of a label photo** (phone camera or upload).

## Workflow
1. Create a batch (default 30 books).
2. Connect a USB barcode scanner.
3. Scan a QA Book barcode.
4. Retrieve MAT No, VC No, Color, Order, Seating and Chassis No from master data when the barcode exists.
5. Enter Bus Number manually.
6. Select PDI/VQA status and QA Book status.
7. Confirm and save.
8. Duplicate Bus Number checks are performed. A barcode may be scanned or typed even when it is not in master data; those lookup fields remain blank and the record is flagged for review.
9. Monitor batch progress.
10. Download formatted Excel report.

## Optional Bus–Chassis mapping
In the **Scan QA Book** tab, upload an `.xlsx` or `.csv` file with
`Chassis No` and `Bus No` columns. Choose **Auto lookup from uploaded file**
to fill Bus No when a normalized chassis number matches, or **Enter manually**
to turn lookup off. The Bus Number field remains editable in either mode.

Chassis matching ignores letter case and whitespace. Bus Numbers are handled
as text, including leading zeros. Missing, unreadable, or ambiguous mappings
show a warning and leave manual Bus Number entry available. Duplicate Bus No
values assigned to different chassis in the uploaded mapping are flagged for
review; they do not block entry or saving. Existing duplicate Bus Number checks
against QA Book records continue to apply.

## Run

Create a virtual environment:

Windows:
    python -m venv venv
    venv\Scripts\activate

Install:
    pip install -r requirements.txt

Run:
    streamlit run app.py

## Master data
Replace `data/master_specs.csv` with the approved TMBSL master data. Keep these columns:

barcode,mat_no,vc_no,color,order_no,seating,chassis_no

The application loads the CSV into SQLite when the master table is empty.

Master data is used for lookup and auto-fill, not as a requirement for
accepting a scan. A USB barcode scanner normally works as a keyboard: place
the cursor in the barcode field and scan. The scanner types the barcode and
often sends Enter as a suffix; no special scanner driver or camera permission
is needed for a USB scanner.

## Important
This is a prototype. Before production deployment, confirm the exact TMBSL business definitions for:
- PDI/VQA status
- QA Book status
- required Excel columns
- batch identification
- master-data source
- barcode format


## OCR (label photo) — v2, high accuracy

The OCR reads **Chassis No, BC No, TMBSL VC No, Color, Order, Seating**
from a photo of the QA Book label.

How it gets high accuracy:

| Layer | What it does |
|---|---|
| RapidOCR (PP-OCR deep-learning models, CPU/ONNX) | Main OCR engine — far better than Tesseract on phone photos |
| Barcode decode (zxing-cpp + built-in Code 39 decoder) | The label barcode encodes the chassis number; it is read from many scan-lines and, when they agree, the chassis is taken from the barcode (no T/1, O/0, S/5 mix-ups possible) |
| Character-by-character vote | The Chassis/VC line is re-read from ~15 enhanced crops; each of the 17 chassis characters is voted separately and any unclear character is reported ("character 11: 'M' or 'N'") |
| Field templates | Chassis `MAT`+6 digits+3 chars+5 digits, VC No 11 digits + `R`; fixes O/0, I/1, S/5, B/8 only where a digit must be |
| Vocabulary snap | Color (WHITE, OTHERS, …) and Order (MTS, EVR) fixed for small misreads (e.g. OTHEAS → OTHERS) |
| Master / mapping snap | A chassis 1–2 characters off a known chassis (master data or uploaded Bus–Chassis file) is matched to it |
| Auto-orientation | Sideways / upside-down / tilted photos are handled automatically |
| Confidence flags | Each field shows ✅ confident, ⚠️ please check, or ❌ not found |

Benchmark on real TMBSL label photos + 30 degraded variants (blur, tilt,
90°/180° rotation, glare, darkness, noise, JPEG artefacts, perspective,
distance): **old engine 53% of fields correct → new engine 98%**, about
1–2 s per photo. The few misses were physically unreadable (text cut off
or washed out by glare) and were all flagged ⚠️/❌ rather than silently wrong.

Tips for best results:
- On a phone, use **Upload Photo → Camera** (full resolution + autofocus).
- Fill the frame with the label; avoid strong glare across the text.

Run the OCR tests:

    python -m unittest tests.test_ocr_accuracy -v

## Deploying on Streamlit Community Cloud

1. Push this folder to the GitHub repo used by the app (`app.py` at the repo root,
   with `requirements.txt` and `packages.txt` next to it).
2. Streamlit Cloud reinstalls dependencies automatically (first boot after
   the change takes ~3–5 min). If it doesn't, use **Manage app → Reboot app**.
3. Python 3.11–3.13 is recommended (Advanced settings).
4. The app header shows the OCR engine in use under the extracted fields
   ("RapidOCR (deep learning) + Tesseract").
