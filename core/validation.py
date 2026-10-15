from core.database import connect

def validate_scan(batch_id, barcode, bus_no, spec):
    errors = []

    if not barcode:
        errors.append("Please scan a barcode.")
    if not bus_no:
        errors.append("Please enter the Bus Number.")
    # Master data is helpful for auto-filling the QA Book fields, but it
    # is not required to save a scan. A barcode may be typed manually or
    # may be new to the master file. Those records are saved with blank
    # master-data fields and flagged for review by the calling flow.

    if not errors:
        conn = connect()

        # NOTE: Barcode/BC No is intentionally NOT checked for
        # duplicates here. The same BC No can legitimately
        # correspond to different chassis numbers, so it isn't
        # treated as a uniqueness key — only Bus No is.

        if conn.execute(
            "SELECT 1 FROM batch_records WHERE batch_id=? AND bus_no=?",
            (batch_id, bus_no)
        ).fetchone():
            errors.append("Duplicate Alert: this Bus Number is already assigned in this batch.")
        conn.close()

    return errors