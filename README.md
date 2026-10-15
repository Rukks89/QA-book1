# TMBSL QA Book Scanning & Batch Tracking System

## Purpose
Barcode-based digitization of QA Book records. No OCR is used.

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
