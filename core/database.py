import sqlite3
from pathlib import Path
from datetime import datetime
import pandas as pd

from config.config import DB_NAME, MASTER_CSV, EXPECTED_BOOKS_DEFAULT


# ============================================================
# DATABASE CONNECTION
# ============================================================

def connect():
    """
    Create SQLite database connection.
    """

    Path(DB_NAME).parent.mkdir(
        parents=True,
        exist_ok=True
    )

    return sqlite3.connect(DB_NAME)


# ============================================================
# INITIALIZE DATABASE
# ============================================================

def init_db():

    conn = connect()
    c = conn.cursor()

    # ========================================================
    # 1. BATCH TABLE
    # ========================================================

    c.execute("""
    CREATE TABLE IF NOT EXISTS batches (
        batch_id TEXT PRIMARY KEY,
        batch_date TEXT NOT NULL,
        observations TEXT,
        created_at TEXT NOT NULL,
        status TEXT DEFAULT 'OPEN',
        target_books INTEGER NOT NULL DEFAULT 30
    )
    """)


    # ========================================================
    # 2. MASTER SPECIFICATIONS TABLE
    # ========================================================

    c.execute("""
    CREATE TABLE IF NOT EXISTS master_specs (
        barcode TEXT PRIMARY KEY,
        mat_no TEXT,
        vc_no TEXT,
        color TEXT,
        order_no TEXT,
        seating TEXT,
        chassis_no TEXT
    )
    """)


    # ========================================================
    # 3. BATCH RECORDS TABLE
    # ========================================================

    c.execute("""
    CREATE TABLE IF NOT EXISTS batch_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id TEXT NOT NULL,
        scan_timestamp TEXT NOT NULL,
        barcode TEXT NOT NULL,
        bus_no TEXT NOT NULL,
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
    )
    """)


    # ========================================================
    # 4. SCAN LOG TABLE
    # ========================================================

    c.execute("""
    CREATE TABLE IF NOT EXISTS scan_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id TEXT,
        barcode TEXT,
        scan_timestamp TEXT,
        action TEXT,
        message TEXT
    )
    """)


    conn.commit()


    # ========================================================
    # MIGRATION: DROP THE OLD UNIQUE(batch_id, barcode)
    # CONSTRAINT ON EXISTING DATABASES
    #
    # Earlier versions of this app blocked the same barcode from
    # being scanned twice within a batch. In practice, the same
    # BC No can legitimately correspond to different chassis
    # numbers, so that constraint is no longer wanted — only Bus
    # No stays unique. CREATE TABLE IF NOT EXISTS above only
    # applies to brand-new databases, so this rebuilds the table
    # (preserving all existing data) if the old constraint is
    # still present. Safe to run on every startup — it's a no-op
    # once already migrated.
    # ========================================================

    _migrate_drop_barcode_unique_constraint(
        conn
    )

    _ensure_record_vehicle_type_column(conn)


    # Add batch configuration to existing databases without
    # rebuilding the table or removing existing batch data.
    _ensure_batch_configuration_columns(conn)


    # ========================================================
    # IMPORTANT:
    # SYNCHRONIZE MASTER CSV WITH DATABASE
    # ========================================================

    sync_master_data(
        conn
    )


    conn.close()


# ============================================================
# MIGRATION HELPER — SEE init_db() ABOVE
# ============================================================

def _ensure_batch_configuration_columns(conn):
    """Ensure existing databases can store a per-batch book count."""

    columns = {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(batches)"
        ).fetchall()
    }

    if "observations" not in columns:
        conn.execute(
            "ALTER TABLE batches ADD COLUMN observations TEXT"
        )

    if "target_books" not in columns:
        conn.execute(
            "ALTER TABLE batches "
            "ADD COLUMN target_books INTEGER NOT NULL DEFAULT 30"
        )

        # Preserve values from older databases that stored this as
        # expected_books, while retaining that original column/data.
        if "expected_books" in columns:
            conn.execute(
                """
                UPDATE batches
                SET target_books = COALESCE(expected_books, 30)
                """
            )

    conn.execute(
        """
        UPDATE batches
        SET target_books = ?
        WHERE target_books IS NULL OR target_books < 1
        """,
        (EXPECTED_BOOKS_DEFAULT,)
    )

    conn.commit()


# ============================================================
# MIGRATION HELPER — SEE init_db() ABOVE
# ============================================================

def _migrate_drop_barcode_unique_constraint(conn):

    row = conn.execute(
        """
        SELECT sql FROM sqlite_master
        WHERE type = 'table' AND name = 'batch_records'
        """
    ).fetchone()

    if not row or not row[0]:

        return

    existing_sql = row[0].replace(
        " ", ""
    )

    # Already on the new schema (or a fresh install) — nothing
    # to migrate.
    if "UNIQUE(batch_id,barcode)" not in existing_sql:

        return

    print(
        "Migrating batch_records: removing the old "
        "UNIQUE(batch_id, barcode) constraint (barcodes no "
        "longer need to be unique)..."
    )

    conn.execute(
        "ALTER TABLE batch_records "
        "RENAME TO batch_records_pre_migration"
    )

    old_columns = {
        column[1]
        for column in conn.execute(
            "PRAGMA table_info(batch_records)"
        ).fetchall()
    }
    vehicle_type_select = (
        "vehicle_type"
        if "vehicle_type" in old_columns
        else "NULL"
    )
    image_evidence_select = (
        "image_evidence"
        if "image_evidence" in old_columns
        else "NULL"
    )
    source_file_select = (
        "source_file"
        if "source_file" in old_columns
        else "NULL"
    )

    conn.execute(
        """
        CREATE TABLE batch_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id TEXT NOT NULL,
            scan_timestamp TEXT NOT NULL,
            barcode TEXT NOT NULL,
            bus_no TEXT NOT NULL,
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
        )
        """
    )

    conn.execute(
        """
        INSERT INTO batch_records (
            id, batch_id, scan_timestamp, barcode, bus_no,
            mat_no, vc_no, color, order_no, seating, chassis_no,
            vehicle_type, source_file, image_evidence,
            pdi_clearance, qa_book_status, verification_status
        )
        SELECT
            id, batch_id, scan_timestamp, barcode, bus_no,
            mat_no, vc_no, color, order_no, seating, chassis_no,
            {vehicle_type_select}, {source_file_select},
            {image_evidence_select},
            pdi_clearance, qa_book_status, verification_status
        FROM batch_records_pre_migration
        """.format(
            vehicle_type_select=vehicle_type_select,
            source_file_select=source_file_select,
            image_evidence_select=image_evidence_select,
        )
    )

    conn.execute(
        "DROP TABLE batch_records_pre_migration"
    )

    conn.commit()

    print(
        "Migration complete: barcodes can now repeat across "
        "batches; Bus No remains unique."
    )


def _ensure_record_vehicle_type_column(conn):
    """Add draft metadata/evidence columns without rebuilding existing rows."""

    columns = {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(batch_records)"
        ).fetchall()
    }

    if "vehicle_type" not in columns:
        conn.execute(
            "ALTER TABLE batch_records ADD COLUMN vehicle_type TEXT"
        )
        conn.commit()
    if "image_evidence" not in columns:
        conn.execute(
            "ALTER TABLE batch_records ADD COLUMN image_evidence BLOB"
        )
        conn.commit()
    if "source_file" not in columns:
        conn.execute(
            "ALTER TABLE batch_records ADD COLUMN source_file TEXT"
        )
        conn.commit()


# ============================================================
# MASTER DATA SYNCHRONIZATION
# ============================================================

def sync_master_data(conn):
    """
    Synchronize master_specs.csv with SQLite.

    The CSV is treated as the latest master data source.

    Existing barcode:
        UPDATE existing information

    New barcode:
        INSERT new information

    This means changes to:
        - MAT No
        - VC No
        - Color
        - Order
        - Seating
        - Chassis No

    will automatically be reflected in the application.
    """

    # --------------------------------------------------------
    # CHECK CSV
    # --------------------------------------------------------

    csv_path = Path(
        MASTER_CSV
    )

    if not csv_path.exists():

        print(
            f"WARNING: Master CSV not found: {csv_path}"
        )

        return


    # --------------------------------------------------------
    # READ CSV
    # --------------------------------------------------------

    try:

        df = pd.read_csv(
            csv_path,
            dtype=str
        ).fillna("")

    except Exception as e:

        print(
            f"ERROR reading master CSV: {e}"
        )

        return


    # --------------------------------------------------------
    # REQUIRED COLUMNS
    # --------------------------------------------------------

    required_columns = [
        "barcode",
        "mat_no",
        "vc_no",
        "color",
        "order_no",
        "seating",
        "chassis_no"
    ]


    missing_columns = [
        column
        for column in required_columns
        if column not in df.columns
    ]


    if missing_columns:

        raise ValueError(
            "Master CSV is missing columns: "
            + ", ".join(missing_columns)
        )


    # --------------------------------------------------------
    # CLEAN DATA
    # --------------------------------------------------------

    for column in required_columns:

        df[column] = (
            df[column]
            .astype(str)
            .str.strip()
        )


    # --------------------------------------------------------
    # REMOVE EMPTY BARCODE
    # --------------------------------------------------------

    df = df[
        df["barcode"] != ""
    ]


    # --------------------------------------------------------
    # REMOVE DUPLICATE BARCODES
    #
    # If same barcode appears more than once,
    # last row will be considered the latest value.
    # --------------------------------------------------------

    df = df.drop_duplicates(
        subset=["barcode"],
        keep="last"
    )


    # ========================================================
    # UPSERT DATA
    # ========================================================

    cursor = conn.cursor()


    for _, row in df.iterrows():

        cursor.execute(
            """
            INSERT INTO master_specs (
                barcode,
                mat_no,
                vc_no,
                color,
                order_no,
                seating,
                chassis_no
            )

            VALUES (?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(barcode)

            DO UPDATE SET

                mat_no = excluded.mat_no,

                vc_no = excluded.vc_no,

                color = excluded.color,

                order_no = excluded.order_no,

                seating = excluded.seating,

                chassis_no = excluded.chassis_no
            """,

            (
                row["barcode"],
                row["mat_no"],
                row["vc_no"],
                row["color"],
                row["order_no"],
                row["seating"],
                row["chassis_no"]
            )
        )


    conn.commit()


# ============================================================
# GET MASTER DATA
# ============================================================

def get_master_data(barcode):

    conn = connect()

    df = pd.read_sql_query(
        """
        SELECT
            barcode,
            mat_no,
            vc_no,
            color,
            order_no,
            seating,
            chassis_no

        FROM master_specs

        WHERE barcode = ?
        """,

        conn,

        params=(
            barcode.strip(),
        )
    )

    conn.close()


    if df.empty:

        return None


    return df.iloc[0].to_dict()


def lookup_master_data(
    barcode="",
    barcode_candidates=None,
    chassis_candidates=None,
):
    """
    Match OCR evidence to existing master rows without guessing characters.

    A directly decoded barcode is trusted as an exact key. OCR text candidates
    must have at least two pass votes. Chassis candidates are compared exactly
    after trimming surrounding whitespace and ignoring case; internal
    characters are never substituted or removed.
    """

    barcode_candidates = barcode_candidates or []
    chassis_candidates = chassis_candidates or []

    def _trusted_candidates(candidates):
        trusted = []
        for candidate in candidates:
            if isinstance(candidate, dict):
                value = str(candidate.get("value", "")).strip()
                try:
                    votes = int(candidate.get("votes", 0))
                except (TypeError, ValueError):
                    votes = 0
            else:
                value = str(candidate).strip()
                votes = 0
            if value and votes >= 2:
                trusted.append((value, votes))
        return trusted

    exact_barcode = str(barcode or "").strip()
    trusted_barcodes = _trusted_candidates(barcode_candidates)
    trusted_chassis = _trusted_candidates(chassis_candidates)

    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT barcode, mat_no, vc_no, color, order_no, seating, chassis_no
            FROM master_specs
            """
        ).fetchall()
    finally:
        conn.close()

    columns = (
        "barcode", "mat_no", "vc_no", "color",
        "order_no", "seating", "chassis_no",
    )
    records = [dict(zip(columns, row)) for row in rows]

    evidence = []
    if exact_barcode:
        evidence.append(("barcode", exact_barcode, None))
    evidence.extend(
        ("barcode", value, votes)
        for value, votes in trusted_barcodes
    )
    evidence.extend(
        ("chassis_no", value, votes)
        for value, votes in trusted_chassis
    )

    if not evidence:
        return {
            "status": "manual_verification_required",
            "reason": "No sufficiently supported barcode or chassis candidate.",
            "record": None,
            "matched_by": "",
            "matched_value": "",
        }

    def _normalized(value):
        return str(value or "").strip().casefold()

    matched = {}
    for field, value, votes in evidence:
        normalized_value = _normalized(value)
        if not normalized_value:
            continue
        for record in records:
            if _normalized(record.get(field)) == normalized_value:
                # Keep differently-cased stored keys distinct: case-insensitive
                # matching must not collapse two separate master records.
                key = str(record.get("barcode") or "").strip()
                if key:
                    matched.setdefault(key, []).append(
                        (record, field, value, votes)
                    )

    if not matched:
        return {
            "status": "not_found",
            "reason": "Exact barcode/chassis match not found.",
            "record": None,
            "matched_by": "",
            "matched_value": "",
        }

    if len(matched) != 1:
        return {
            "status": "ambiguous",
            "reason": "Multiple possible master-data matches.",
            "record": None,
            "matched_by": "",
            "matched_value": "",
        }

    hits = next(iter(matched.values()))
    # Prefer direct barcode evidence, then the most-supported exact candidate.
    hits.sort(
        key=lambda hit: (
            hit[1] != "barcode",
            -(hit[3] or 0),
            hit[2],
        )
    )
    record, matched_by, matched_value, _votes = hits[0]
    return {
        "status": "matched",
        "reason": "Unique exact master-data match.",
        "record": record,
        "matched_by": matched_by,
        "matched_value": matched_value,
    }


# ============================================================
# CREATE BATCH
# ============================================================

def create_batch(
    batch_id,
    batch_date,
    observations="",
    target_books=EXPECTED_BOOKS_DEFAULT
):

    if not batch_id:

        return (
            False,
            "Batch ID is required."
        )

    try:
        target_books = int(target_books)
    except (TypeError, ValueError):
        return (
            False,
            "Number of books in the batch must be a whole number."
        )

    if target_books < 1:
        return (
            False,
            "A batch must contain at least 1 book."
        )

    conn = connect()


    try:

        conn.execute(
            """
            INSERT INTO batches (
                batch_id,
                batch_date,
                observations,
                target_books,
                created_at
            )

            VALUES (
                ?,
                ?,
                ?,
                ?,
                datetime('now')
            )
            """,

            (
                batch_id,
                batch_date,
                observations or "",
                target_books
            )
        )


        conn.commit()


        return (
            True,
            f"Batch {batch_id} created successfully."
        )


    except sqlite3.IntegrityError:

        return (
            False,
            "Batch ID already exists."
        )


    finally:

        conn.close()


# ============================================================
# UPDATE BATCH BOOK COUNT
# ============================================================

def update_batch_target_books(batch_id, target_books):
    """Update the configured number of books for an existing batch."""

    try:
        target_books = int(target_books)
    except (TypeError, ValueError):
        return False, "Books per batch must be a whole number."

    if not 1 <= target_books <= 1000:
        return False, "Books per batch must be between 1 and 1,000."

    conn = connect()

    try:
        cursor = conn.execute(
            """
            UPDATE batches
            SET target_books = ?
            WHERE batch_id = ?
            """,
            (target_books, batch_id)
        )
        conn.commit()

        if cursor.rowcount == 0:
            return False, f"Batch {batch_id} was not found."

        return True, f"Books per batch updated for {batch_id}."

    finally:
        conn.close()


# ============================================================
# GET BATCHES
# ============================================================

def get_batches():

    conn = connect()


    rows = conn.execute(
        """
        SELECT
            batch_id,
            batch_date,
            observations,
            created_at,
            status,
            target_books

        FROM batches

        ORDER BY created_at DESC
        """
    ).fetchall()


    conn.close()


    return [

        {
            "batch_id": row[0],
            "batch_date": row[1],
            "observations": row[2],
            "created_at": row[3],
            "status": row[4],
            "target_books": row[5]
        }

        for row in rows
    ]


# ============================================================
# SAVE RECORD
# ============================================================

def save_record(record):

    conn = connect()


    try:

        conn.execute(
            """
            INSERT INTO batch_records (

                batch_id,
                scan_timestamp,
                barcode,
                bus_no,
                mat_no,
                vc_no,
                color,
                order_no,
                seating,
                chassis_no,
                pdi_clearance,
                qa_book_status,
                verification_status

            )

            VALUES (
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?
            )
            """,

            record
        )


        # ----------------------------------------------------
        # SAVE SCAN LOG
        # ----------------------------------------------------

        conn.execute(
            """
            INSERT INTO scan_logs (
                batch_id,
                barcode,
                scan_timestamp,
                action,
                message
            )

            VALUES (
                ?,
                ?,
                ?,
                ?,
                ?
            )
            """,

            (
                record[0],
                record[2],
                record[1],
                "SAVE",
                "Record saved successfully"
            )
        )


        conn.commit()


        return (
            True,
            "QA Book record saved successfully."
        )


    except sqlite3.IntegrityError as e:

        msg = str(e)


        # ----------------------------------------------------
        # DUPLICATE BUS NUMBER
        #
        # Barcode is intentionally NOT unique-constrained here
        # anymore — the same BC No can legitimately correspond
        # to different chassis numbers, so only Bus No is
        # enforced as unique within a batch (global uniqueness
        # across batches is checked separately, at the app
        # level, before this is even called).
        # ----------------------------------------------------

        if (
            "batch_records.batch_id, "
            "batch_records.bus_no"
            in msg
        ):

            return (
                False,
                "Duplicate Alert: this Bus Number is "
                "already assigned in this batch."
            )


        return (
            False,
            f"Database error: {msg}"
        )


    finally:

        conn.close()


def save_batch_atomically(batch_metadata, records):
    """
    Persist a new batch (when requested) and all staged records in one
    SQLite transaction. Existing batches receive their staged records in
    one transaction. Any failed row rolls the entire operation back.

    batch_metadata keys: batch_id, batch_date, observations, target_books,
    is_new. Each record is a mapping of the batch_records column names.
    """

    conn = connect()
    current_record_number = None

    try:
        conn.execute("BEGIN IMMEDIATE")

        batch_id = str(batch_metadata.get("batch_id", "")).strip()
        if not batch_id:
            raise ValueError("Batch ID is required.")

        is_new = bool(batch_metadata.get("is_new"))
        if is_new:
            conn.execute(
                """
                INSERT INTO batches (
                    batch_id, batch_date, observations, target_books,
                    created_at
                )
                VALUES (?, ?, ?, ?, datetime('now'))
                """,
                (
                    batch_id,
                    str(batch_metadata.get("batch_date", "")),
                    str(batch_metadata.get("observations", "")),
                    int(batch_metadata.get("target_books", EXPECTED_BOOKS_DEFAULT)),
                ),
            )
        elif not conn.execute(
            "SELECT 1 FROM batches WHERE batch_id = ?",
            (batch_id,),
        ).fetchone():
            raise ValueError(f"Batch {batch_id} was not found.")

        if not records:
            raise ValueError("Add at least one QA Book before confirming.")
        target_books = int(
            batch_metadata.get("target_books", EXPECTED_BOOKS_DEFAULT)
        )
        if not 1 <= target_books <= 1000:
            raise ValueError("Books per batch must be between 1 and 1,000.")
        if len(records) > target_books:
            raise ValueError(
                f"Draft contains {len(records)} books, above the target of "
                f"{target_books}."
            )
        saved_count = 0
        if not is_new:
            saved_count = conn.execute(
                "SELECT COUNT(*) FROM batch_records WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()[0]
        if saved_count + len(records) > target_books:
            raise ValueError(
                f"This would bring the batch to {saved_count + len(records)} "
                f"books, above the target of {target_books}."
            )

        for current_record_number, record in enumerate(records, start=1):
            barcode = str(record.get("barcode", "")).strip()
            bus_no = str(record.get("bus_no", "")).strip()
            vehicle_type = str(record.get("vehicle_type", "")).strip().upper()

            if not barcode:
                raise ValueError(
                    f"Record {current_record_number}: Barcode / BC No is required."
                )
            if not bus_no.isdigit() or len(bus_no) not in (4, 6):
                raise ValueError(
                    f"Record {current_record_number}: Bus No must be exactly "
                    "4 or 6 digits."
                )
            if vehicle_type == "MCV" and len(bus_no) != 4:
                raise ValueError(
                    f"Record {current_record_number}: MCV Bus No must be "
                    "exactly 4 digits."
                )
            if vehicle_type == "LCV" and len(bus_no) != 6:
                raise ValueError(
                    f"Record {current_record_number}: LCV Bus No must be "
                    "exactly 6 digits."
                )
            if vehicle_type not in ("", "MCV", "LCV"):
                raise ValueError(
                    f"Record {current_record_number}: Vehicle Type must be "
                    "MCV, LCV, or blank."
                )
            if record.get("pdi_clearance") not in ("Yes", "No", "Pending"):
                raise ValueError(
                    f"Record {current_record_number}: Select a valid PDI status."
                )
            if record.get("qa_book_status") not in (
                "Received",
                "Not Received",
            ):
                raise ValueError(
                    f"Record {current_record_number}: Select a valid QA Book status."
                )

            scan_message = (
                f"Record {current_record_number} saved in batch confirmation"
            )
            chassis_candidate = str(
                record.get("chassis_original_candidate", "")
            ).strip()
            chassis_correction = str(
                record.get("chassis_proposed_correction", "")
            ).strip()
            if (
                chassis_candidate
                and chassis_correction
                and chassis_candidate != chassis_correction
            ):
                scan_message += (
                    f"; chassis OCR candidate {chassis_candidate!r} "
                    f"matched uniquely to master value "
                    f"{chassis_correction!r}"
                )

            # A Bus No remains globally unique across batches, while
            # records within this same batch are protected by the index.
            conflicting_batch = conn.execute(
                """
                SELECT batch_id
                FROM batch_records
                WHERE bus_no = ? AND batch_id <> ?
                LIMIT 1
                """,
                (bus_no, batch_id),
            ).fetchone()
            if conflicting_batch:
                raise ValueError(
                    f"Record {current_record_number}: Bus No {bus_no} is "
                    f"already used in batch {conflicting_batch[0]}."
                )

            timestamp = str(
                record.get("scan_timestamp", "")
                or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )
            conn.execute(
                """
                INSERT INTO batch_records (
                    batch_id, scan_timestamp, barcode, bus_no, mat_no,
                    vc_no, color, order_no, seating, chassis_no,
                    vehicle_type, source_file, image_evidence,
                    pdi_clearance, qa_book_status,
                    verification_status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    timestamp,
                    barcode,
                    bus_no,
                    str(record.get("mat_no", "")).strip(),
                    str(record.get("vc_no", "")).strip(),
                    str(record.get("color", "")).strip(),
                    str(record.get("order_no", "")).strip(),
                    str(record.get("seating", "")).strip(),
                    str(record.get("chassis_no", "")).strip(),
                    vehicle_type or None,
                    str(record.get("source_file", "")).strip(),
                    record.get("image_evidence"),
                    record["pdi_clearance"],
                    record["qa_book_status"],
                    str(record.get("verification_status", "VERIFIED")),
                ),
            )
            conn.execute(
                """
                INSERT INTO scan_logs (
                    batch_id, barcode, scan_timestamp, action, message
                )
                VALUES (?, ?, ?, 'SAVE', ?)
                """,
                (
                    batch_id,
                    barcode,
                    timestamp,
                    scan_message,
                ),
            )

        conn.commit()
        return True, f"Batch {batch_id} and {len(records)} record(s) saved successfully."

    except (sqlite3.Error, ValueError, TypeError) as error:
        conn.rollback()
        message = str(error)
        if isinstance(error, sqlite3.IntegrityError):
            if "batches.batch_id" in message or "UNIQUE constraint failed: batches" in message:
                message = "Batch ID already exists. Choose a different ID."
            elif "batch_records.batch_id, batch_records.bus_no" in message:
                message = (
                    f"Record {current_record_number}: duplicate Bus No "
                    "within this batch."
                )
            else:
                message = f"Record {current_record_number or '?'}: {message}"
        return False, message

    finally:
        conn.close()


# ============================================================
# GET BATCH RECORDS
# ============================================================

def get_batch_records(batch_id):

    conn = connect()


    df = pd.read_sql_query(
        """
        SELECT

            scan_timestamp AS "Scan Time",

            source_file AS "Source File",

            barcode AS "Barcode",

            bus_no AS "Bus No",

            vc_no AS "VC No",

            color AS "Color",

            order_no AS "Order",

            seating AS "Seating",

            chassis_no AS "Chassis No",

            vehicle_type AS "Vehicle Type",

            pdi_clearance AS "PDI Status",

            qa_book_status AS "QA Book Status"

        FROM batch_records

        WHERE batch_id = ?

        ORDER BY id
        """,

        conn,

        params=(
            batch_id,
        )
    )


    conn.close()


    return df


# ============================================================
# GET BATCH SUMMARY
# ============================================================

def get_batch_summary(batch_id):

    conn = connect()


    row = conn.execute(
        """
        SELECT

            b.observations,

            COUNT(r.id),

            SUM(
                CASE
                    WHEN r.pdi_clearance = 'Yes'
                    THEN 1
                    ELSE 0
                END
            ),

            SUM(
                CASE
                    WHEN r.qa_book_status = 'Received'
                    THEN 1
                    ELSE 0
                END
            )

        FROM batches b

        LEFT JOIN batch_records r

            ON b.batch_id = r.batch_id

        WHERE b.batch_id = ?

        GROUP BY b.batch_id
        """,

        (
            batch_id,
        )
    ).fetchone()


    conn.close()


    observations = row[0] if row else ""
    scanned = row[1] if row else 0
    pdi = row[2] if row else 0
    received = row[3] if row else 0


    observations = observations or ""
    scanned = scanned or 0
    pdi = pdi or 0
    received = received or 0


    return {

        "observations": observations,

        "scanned": scanned,

        "pdi_cleared": pdi,

        "received": received
    }


# ============================================================
# GET EXCEPTION RECORDS
# ============================================================

def get_exception_records(batch_id):

    conn = connect()


    df = pd.read_sql_query(
        """
        SELECT *

        FROM batch_records

        WHERE batch_id = ?

        AND verification_status <> 'VERIFIED'
        """,

        conn,

        params=(
            batch_id,
        )
    )


    conn.close()


    return df


# ============================================================
# GET RECORD HISTORY
# ============================================================

def get_record_history(barcode):

    conn = connect()


    df = pd.read_sql_query(
        """
        SELECT *

        FROM scan_logs

        WHERE barcode = ?

        ORDER BY id DESC
        """,

        conn,

        params=(
            barcode.strip(),
        )
    )


    conn.close()


    return df


# ============================================================
# GET ALL BATCH HISTORY (SUMMARY OF EVERY BATCH EVER CREATED)
# ============================================================

def get_all_batch_history():
    """
    Returns a DataFrame containing every batch ever created,
    along with how many QA Books have been scanned, PDI
    cleared and received for each of them.

    Used to power the "History" tab's full historical view
    of all batches.
    """

    conn = connect()


    df = pd.read_sql_query(
        """
        SELECT

            b.batch_id AS "Batch ID",

            b.batch_date AS "Batch Date",

            b.observations AS "Observations",

            b.created_at AS "Created At",

            b.status AS "Status",

            b.target_books AS "Books per Batch",

            COUNT(r.id) AS "Scanned",

            SUM(
                CASE
                    WHEN r.pdi_clearance = 'Yes'
                    THEN 1
                    ELSE 0
                END
            ) AS "PDI Cleared",

            SUM(
                CASE
                    WHEN r.qa_book_status = 'Received'
                    THEN 1
                    ELSE 0
                END
            ) AS "Received"

        FROM batches b

        LEFT JOIN batch_records r

            ON b.batch_id = r.batch_id

        GROUP BY b.batch_id

        ORDER BY b.batch_id ASC
        """,

        conn
    )


    conn.close()


    return df


# ============================================================
# GET BUS NUMBER ONLY SHEET (ACROSS ALL BATCHES OR ONE BATCH)
# ============================================================

def get_bus_numbers_only(batch_id=None):
    """
    Returns a DataFrame containing ONLY the Batch ID and
    Bus No columns.

    If batch_id is provided, only that batch's bus numbers
    are returned. Otherwise, bus numbers for every batch
    (i.e. the full "30 Bus No only" sheet history) are
    returned.
    """

    conn = connect()


    if batch_id:

        df = pd.read_sql_query(
            """
            SELECT

                batch_id AS "Batch ID",

                bus_no AS "Bus No"

            FROM batch_records

            WHERE batch_id = ?

            ORDER BY bus_no ASC
            """,

            conn,

            params=(
                batch_id,
            )
        )

    else:

        df = pd.read_sql_query(
            """
            SELECT

                batch_id AS "Batch ID",

                bus_no AS "Bus No"

            FROM batch_records

            ORDER BY batch_id ASC, bus_no ASC
            """,

            conn
        )


    conn.close()


    return df


# ============================================================
# GLOBAL DUPLICATE CHECKS (ACROSS ALL BATCHES)
# ============================================================
#
# The batch_records table's UNIQUE(batch_id, barcode) and
# UNIQUE(batch_id, bus_no) constraints only stop a duplicate
# from being scanned TWICE WITHIN THE SAME BATCH — they say
# nothing about a barcode or bus number that was already used
# in a DIFFERENT batch. These two functions answer that
# specific question, so the app can block the entry and tell
# the user exactly which other batch(es) already used it.
# ============================================================

def find_barcode_usage(barcode, exclude_batch_id=None):
    """
    Returns a list of batch_ids (excluding exclude_batch_id, if
    given) that already contain this exact barcode, across every
    batch in the system.
    """

    if not barcode:

        return []

    conn = connect()


    query = (
        "SELECT DISTINCT batch_id "
        "FROM batch_records "
        "WHERE barcode = ?"
    )

    params = [barcode.strip()]

    if exclude_batch_id:

        query += " AND batch_id <> ?"

        params.append(exclude_batch_id)

    query += " ORDER BY batch_id"


    rows = conn.execute(
        query,
        params
    ).fetchall()

    conn.close()


    return [row[0] for row in rows]


def find_bus_no_usage(bus_no, exclude_batch_id=None):
    """
    Returns a list of batch_ids (excluding exclude_batch_id, if
    given) that already contain this exact Bus No, across every
    batch in the system.
    """

    if not bus_no:

        return []

    conn = connect()


    query = (
        "SELECT DISTINCT batch_id "
        "FROM batch_records "
        "WHERE bus_no = ?"
    )

    params = [bus_no.strip()]

    if exclude_batch_id:

        query += " AND batch_id <> ?"

        params.append(exclude_batch_id)

    query += " ORDER BY batch_id"


    rows = conn.execute(
        query,
        params
    ).fetchall()

    conn.close()


    return [row[0] for row in rows]


def find_existing_duplicate_groups():
    """
    Diagnostic helper: scans the WHOLE batch_records table and
    returns any barcode or bus_no values that already appear in
    more than one batch (i.e. duplicates that got in before the
    global check existed). Returns:

        {
            "barcode": [{"value": "885", "batches": ["AUG26-QA01", "SEP26-QA01", ...]}, ...],
            "bus_no":  [{"value": "111222", "batches": [...]}, ...]
        }

    Historical duplicates already in the database are NOT
    touched or deleted automatically — this is read-only, so you
    can review and decide what to do with each one.
    """

    conn = connect()


    def _dupes_for(column):

        rows = conn.execute(
            f"""
            SELECT {column}, GROUP_CONCAT(DISTINCT batch_id)
            FROM batch_records
            GROUP BY {column}
            HAVING COUNT(DISTINCT batch_id) > 1
            ORDER BY {column}
            """
        ).fetchall()

        return [
            {
                "value": value,
                "batches": sorted(batch_ids.split(","))
            }
            for value, batch_ids in rows
        ]

    result = {
        "barcode": _dupes_for("barcode"),
        "bus_no": _dupes_for("bus_no")
    }

    conn.close()

    return result


# ============================================================
# GET BATCH RECORDS (EDITABLE VIEW — INCLUDES ROW ID)
# ============================================================

def get_batch_records_for_edit(batch_id):
    """
    Same data as get_batch_records(), but includes the row's
    internal database id (as "_id") so the app can target a
    specific record for editing or deletion. The app hides the
    "_id" column from what the user actually sees.
    """

    conn = connect()


    df = pd.read_sql_query(
        """
        SELECT

            id AS "_id",

            scan_timestamp AS "Scan Time",

            source_file AS "Source File",

            barcode AS "Barcode",

            bus_no AS "Bus No",

            vc_no AS "VC No",

            color AS "Color",

            order_no AS "Order",

            seating AS "Seating",

            chassis_no AS "Chassis No",

            vehicle_type AS "Vehicle Type",

            pdi_clearance AS "PDI Status",

            qa_book_status AS "QA Book Status"

        FROM batch_records

        WHERE batch_id = ?

        ORDER BY id
        """,

        conn,

        params=(
            batch_id,
        )
    )


    conn.close()


    return df


# ============================================================
# UPDATE RECORD FIELDS (PDI CLEARANCE / QA BOOK STATUS)
# ============================================================

def update_record_fields(
    record_id,
    pdi_clearance=None,
    qa_book_status=None
):
    """
    Updates PDI Clearance and/or QA Book Status for a single
    existing record, identified by its internal id. Pass only
    the field(s) you want changed — leave the other as None to
    leave it untouched.
    """

    updates = []
    params = []

    if pdi_clearance is not None:

        updates.append(
            "pdi_clearance = ?"
        )

        params.append(
            pdi_clearance
        )

    if qa_book_status is not None:

        updates.append(
            "qa_book_status = ?"
        )

        params.append(
            qa_book_status
        )

    if not updates:

        return (
            False,
            "Nothing to update."
        )

    params.append(
        record_id
    )

    conn = connect()

    try:

        cursor = conn.execute(
            f"""
            UPDATE batch_records
            SET {', '.join(updates)}
            WHERE id = ?
            """,

            params
        )

        conn.commit()

        if cursor.rowcount == 0:

            return (
                False,
                f"Record {record_id} not found."
            )

        return (
            True,
            "Record updated successfully."
        )

    except Exception as e:

        conn.rollback()

        return (
            False,
            f"Unable to update record: {e}"
        )

    finally:

        conn.close()


# ============================================================
# DELETE A SINGLE RECORD
# ============================================================

def delete_record(record_id):
    """
    Permanently deletes a single scanned QA Book record by its
    internal id (NOT the whole batch — see delete_batch() for
    that). Use when a record was scanned in error.
    """

    conn = connect()

    try:

        row = conn.execute(
            "SELECT id FROM batch_records WHERE id = ?",
            (record_id,)
        ).fetchone()

        if not row:

            return (
                False,
                f"Record {record_id} not found."
            )

        conn.execute(
            "DELETE FROM batch_records WHERE id = ?",
            (record_id,)
        )

        conn.commit()

        return (
            True,
            "Record deleted successfully."
        )

    except Exception as e:

        conn.rollback()

        return (
            False,
            f"Unable to delete record: {e}"
        )

    finally:

        conn.close()


def delete_batch(batch_id):
    """
    Permanently delete a batch and all records/logs associated
    with that batch.
    """

    if not batch_id:
        return False, "Batch ID is required."

    conn = connect()

    try:
        # Check whether batch exists
        row = conn.execute(
            "SELECT batch_id FROM batches WHERE batch_id = ?",
            (batch_id,)
        ).fetchone()

        if not row:
            return False, f"Batch '{batch_id}' not found."

        # Delete batch records
        conn.execute(
            "DELETE FROM batch_records WHERE batch_id = ?",
            (batch_id,)
        )

        # Delete scan logs
        conn.execute(
            "DELETE FROM scan_logs WHERE batch_id = ?",
            (batch_id,)
        )

        # Delete batch itself
        conn.execute(
            "DELETE FROM batches WHERE batch_id = ?",
            (batch_id,)
        )

        conn.commit()

        return True, f"Batch '{batch_id}' deleted successfully."

    except Exception as e:

        conn.rollback()

        return False, f"Unable to delete batch: {e}"

    finally:

        conn.close()