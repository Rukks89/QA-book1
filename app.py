import hashlib
import re

import streamlit as st
from datetime import datetime
import pandas as pd

from config.config import DB_NAME, EXPECTED_BOOKS_DEFAULT

from core.database import (
    init_db,
    get_batches,
    get_batch_records,
    get_batch_summary,
    get_record_history,
    delete_batch,
    get_all_batch_history,
    update_batch_target_books,
    get_bus_numbers_only, 
    find_bus_no_usage,
    find_existing_duplicate_groups,
    get_batch_records_for_edit,
    update_record_fields,
    delete_record,
    save_batch_atomically,
)

from core.report_generator import generate_excel


# ============================================================
# OCR MODULE (OPTIONAL DEPENDENCY)
# ============================================================

try:

    from core.ocr import (
        extract_label_fields,
        estimate_image_quality,
        HEIF_AVAILABLE,
    )

    OCR_AVAILABLE = True
    OCR_IMPORT_ERROR = None

except Exception as _ocr_import_error:

    OCR_AVAILABLE = False
    OCR_IMPORT_ERROR = str(_ocr_import_error)
    HEIF_AVAILABLE = False


# ============================================================
# PAGE CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="TMBSL QA Book System",
    page_icon="🚌",
    layout="wide"
)


# ============================================================
# INITIALIZE DATABASE
# ============================================================

init_db()


# ============================================================
# TATA MOTORS BODY SOLUTIONS - 3D ENTERPRISE THEME (CSS)
# ============================================================
# White background, blue text, raised "3D" cards/buttons/tabs,
# a faint company watermark, and a hover highlight on toggles,
# checkboxes and radio options.

THEME_CSS = """
<style>
:root {
    --ink:        #1e3a8a;   /* main blue text   */
    --ink-soft:   #3b5bb5;   /* secondary text   */
    --brand:      #1d4ed8;   /* primary blue     */
    --brand-dark: #1e40af;
    --brand-soft: #eaf1ff;
    --brand-line: #cfdcf5;
    --teal:       #0d9488;
    --amber:      #d97706;
    --edge:       #d3def4;   /* bottom edge that creates the 3D depth */
}

/* ---------- page: white + faint company watermark ---------- */
.stApp {
    color: var(--ink);
    background-image:
        url("data:image/svg+xml;utf8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='480' height='300'%3E%3Cg transform='rotate(-24 240 150)' fill='%231d4ed8' fill-opacity='0.055' font-family='Arial,Helvetica,sans-serif' font-weight='700'%3E%3Ctext x='30' y='135' font-size='26' letter-spacing='3'%3ETATA MOTORS BODY SOLUTIONS%3C/text%3E%3Ctext x='140' y='172' font-size='15' letter-spacing='6'%3EQA BOOK SYSTEM%3C/text%3E%3C/g%3E%3C/svg%3E"),
        radial-gradient(900px 260px at 0% 0%, rgba(29,78,216,0.07), transparent 70%),
        radial-gradient(700px 220px at 100% 0%, rgba(13,148,136,0.06), transparent 70%);
    background-color: #ffffff;
    background-attachment: fixed, scroll, scroll;
}
header[data-testid="stHeader"] {
    background: transparent;
    border-top: 4px solid var(--brand);
}
.block-container { padding-top: 2.2rem; }

/* corner company tag (faint) */
.stApp::after {
    content: "TATA MOTORS BODY SOLUTIONS LIMITED";
    position: fixed; right: 18px; bottom: 10px;
    letter-spacing: 0.18em; color: var(--brand); opacity: 0.28;
    pointer-events: none; z-index: 1;
}

/* ---------- blue text (scoped, alerts keep their own colours) ---------- */
.stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5 { color: var(--ink); }
.stMarkdown p, .stMarkdown li { color: var(--ink); }
[data-testid="stWidgetLabel"] p,
[data-testid="stCaptionContainer"],
[data-testid="stMetricLabel"] p,
[data-testid="stExpander"] summary p,
[data-testid="stRadio"] label p,
[data-testid="stCheckbox"] label p,
[data-testid="stToggle"] label p { color: var(--ink); }
[data-testid="stAlert"] p, [data-testid="stAlertContainer"] p { color: inherit; }

/* ---------- company ribbon above the page title ---------- */
.brand-ribbon { display: flex; align-items: center; gap: 0.7rem; margin-bottom: 0.4rem; }
.brand-ribbon .pill {
    padding: 0.28rem 0.9rem; border-radius: 999px; color: #ffffff;
    letter-spacing: 0.14em; font-weight: 700;
    background: linear-gradient(180deg, #2f64ec, var(--brand-dark));
    box-shadow: 0 3px 0 #17358f, 0 6px 14px rgba(29,78,216,0.28);
}
.brand-ribbon .sub { color: var(--ink-soft); letter-spacing: 0.12em; }

/* page title: blue with a gradient underline */
.stApp h1 {
    padding-bottom: 0.4rem; position: relative;
    border-bottom: 2px solid var(--brand-soft);
    text-shadow: 0 1px 0 #ffffff, 0 2px 3px rgba(29,78,216,0.18);
}
.stApp h1::after {
    content: ""; position: absolute; left: 0; bottom: -2px; width: 96px; height: 3px;
    border-radius: 3px; background: linear-gradient(90deg, var(--brand), var(--teal));
}

/* ---------- 3D metric cards ---------- */
[data-testid="stMetric"] {
    padding: 0.95rem 1.2rem 0.95rem 1.3rem;
    border-radius: 16px;
    border: 1px solid #e3ebfa;
    border-left: 5px solid var(--brand);
    background: linear-gradient(180deg, #ffffff 0%, #f1f6ff 100%);
    box-shadow:
        inset 0 1px 0 #ffffff,
        0 3px 0 var(--edge),
        0 12px 24px rgba(29,78,216,0.13);
    transition: transform .22s ease, box-shadow .22s ease;
}
[data-testid="stMetric"]:hover {
    transform: translateY(-4px);
    box-shadow:
        inset 0 1px 0 #ffffff,
        0 3px 0 var(--edge),
        0 18px 32px rgba(29,78,216,0.20);
}
[data-testid="stHorizontalBlock"] > div:nth-child(2) [data-testid="stMetric"] { border-left-color: var(--teal); }
[data-testid="stHorizontalBlock"] > div:nth-child(3) [data-testid="stMetric"] { border-left-color: var(--amber); }
[data-testid="stMetricValue"] { color: var(--brand); text-shadow: 0 1px 0 #ffffff, 0 2px 4px rgba(29,78,216,0.22); }

/* ---------- 3D tabs (raised keys) ---------- */
.stTabs [data-baseweb="tab-list"] {
    gap: 0.55rem; padding: 0.45rem 0.5rem 0.6rem;
    border-radius: 16px; background: #eef3fc;
    box-shadow: inset 0 2px 5px rgba(29,78,216,0.10);
}
.stTabs [data-baseweb="tab"] {
    height: auto; padding: 0.55rem 1.2rem; border-radius: 11px;
    background: #ffffff;
    box-shadow: 0 3px 0 var(--edge), 0 6px 10px rgba(29,78,216,0.08);
    transition: transform .15s ease, box-shadow .15s ease, background .15s ease;
}
.stTabs [data-baseweb="tab"] p { color: var(--ink); }
.stTabs [data-baseweb="tab"]:hover {
    background: var(--brand-soft); transform: translateY(-2px);
    box-shadow: 0 5px 0 var(--edge), 0 10px 16px rgba(29,78,216,0.14);
}
.stTabs [aria-selected="true"] {
    background: linear-gradient(180deg, #2f64ec, var(--brand-dark));
    box-shadow: 0 3px 0 #17358f, 0 8px 16px rgba(29,78,216,0.32);
}
.stTabs [aria-selected="true"] p { color: #ffffff; }
.stTabs [data-baseweb="tab-highlight"],
.stTabs [data-baseweb="tab-border"] { display: none; }

/* ---------- 3D buttons (keycap style) ---------- */
.stButton > button,
.stDownloadButton > button,
[data-testid="stFormSubmitButton"] > button {
    border-radius: 11px; border: 1px solid var(--brand-line);
    background: linear-gradient(180deg, #ffffff, #f1f6ff);
    box-shadow: 0 3px 0 var(--edge), 0 6px 12px rgba(29,78,216,0.10);
    transition: transform .12s ease, box-shadow .12s ease, border-color .15s ease;
}
.stButton > button p, .stDownloadButton > button p,
[data-testid="stFormSubmitButton"] > button p { color: var(--ink); }
.stButton > button:hover,
.stDownloadButton > button:hover,
[data-testid="stFormSubmitButton"] > button:hover {
    border-color: var(--brand); transform: translateY(-2px);
    box-shadow: 0 5px 0 var(--edge), 0 12px 18px rgba(29,78,216,0.18);
}
.stButton > button:active,
.stDownloadButton > button:active,
[data-testid="stFormSubmitButton"] > button:active {
    transform: translateY(2px); box-shadow: 0 1px 0 var(--edge), 0 2px 6px rgba(29,78,216,0.12);
}
.stButton > button[kind="primary"],
.stDownloadButton > button[kind="primary"],
[data-testid="stFormSubmitButton"] > button[kind="primaryFormSubmit"] {
    background: linear-gradient(180deg, #2f64ec, var(--brand-dark));
    border-color: var(--brand-dark);
    box-shadow: 0 3px 0 #17358f, 0 8px 16px rgba(29,78,216,0.30);
}
.stButton > button[kind="primary"] p,
.stDownloadButton > button[kind="primary"] p,
[data-testid="stFormSubmitButton"] > button[kind="primaryFormSubmit"] p { color: #ffffff; }
.stButton > button[kind="primary"]:hover,
.stDownloadButton > button[kind="primary"]:hover,
[data-testid="stFormSubmitButton"] > button[kind="primaryFormSubmit"]:hover {
    box-shadow: 0 5px 0 #17358f, 0 14px 22px rgba(29,78,216,0.38);
}
.stButton > button[kind="primary"]:active,
[data-testid="stFormSubmitButton"] > button[kind="primaryFormSubmit"]:active {
    box-shadow: 0 1px 0 #17358f, 0 3px 8px rgba(29,78,216,0.30);
}

/* ---------- TOGGLES / CHECKBOXES / RADIOS: highlight on hover ---------- */
[data-testid="stCheckbox"] label,
[data-testid="stToggle"] label,
[data-testid="stRadio"] label[data-baseweb="radio"] {
    padding: 0.28rem 0.7rem; margin-left: -0.7rem; border-radius: 10px;
    cursor: pointer; transition: background .15s ease, box-shadow .15s ease, transform .15s ease;
}
[data-testid="stCheckbox"] label:hover,
[data-testid="stToggle"] label:hover,
[data-testid="stRadio"] label[data-baseweb="radio"]:hover {
    background: var(--brand-soft);
    box-shadow: 0 0 0 1px #9db8f0, 0 4px 12px rgba(29,78,216,0.18);
    transform: translateY(-1px);
}
/* glow ring on the switch / box / dot itself */
[data-testid="stCheckbox"] label:hover > span:first-child,
[data-testid="stCheckbox"] label:hover > div:first-child,
[data-testid="stToggle"] label:hover > span:first-child,
[data-testid="stToggle"] label:hover > div:first-child,
[data-testid="stRadio"] label[data-baseweb="radio"]:hover > div:first-child {
    box-shadow: 0 0 0 4px rgba(29,78,216,0.22);
}

/* ---------- inputs: inset look + blue focus ring ---------- */
.stTextInput input, .stTextArea textarea, .stNumberInput input, .stDateInput input {
    color: var(--ink);
    box-shadow: inset 0 2px 4px rgba(29,78,216,0.08);
}
.stTextInput input:focus, .stTextArea textarea:focus, .stNumberInput input:focus {
    border-color: var(--brand);
    box-shadow: inset 0 2px 4px rgba(29,78,216,0.06), 0 0 0 3px rgba(29,78,216,0.16);
}
div[data-baseweb="select"] > div { box-shadow: inset 0 2px 4px rgba(29,78,216,0.08); }

/* ---------- raised panels: expanders, forms ---------- */
[data-testid="stExpander"], [data-testid="stForm"] {
    border: 1px solid #e3ebfa; border-radius: 16px;
    background: linear-gradient(180deg, #ffffff, #f8fbff);
    box-shadow: 0 3px 0 var(--edge), 0 12px 24px rgba(29,78,216,0.10);
}
[data-testid="stDataFrame"], [data-testid="stDataEditor"] {
    border-radius: 12px;
    box-shadow: 0 3px 0 var(--edge), 0 10px 20px rgba(29,78,216,0.09);
}
[data-testid="stFileUploader"] section {
    border: 2px dashed var(--brand-line); border-radius: 14px; background: #f5f9ff;
}

/* ---------- sidebar ---------- */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #f1f6fe 0%, #ffffff 100%);
    border-right: 1px solid var(--brand-line);
    box-shadow: 4px 0 18px rgba(29,78,216,0.08);
}
.user-chip {
    display: flex; align-items: center; gap: 0.7rem; padding: 0.7rem 0.8rem;
    margin-bottom: 0.6rem; border-radius: 14px; border: 1px solid #e3ebfa;
    background: linear-gradient(180deg, #ffffff, #f1f6ff);
    box-shadow: 0 3px 0 var(--edge), 0 8px 16px rgba(29,78,216,0.10);
}
.user-chip .avatar {
    width: 38px; height: 38px; border-radius: 50%; display: grid; place-items: center;
    font-weight: 700; color: #ffffff;
    background: linear-gradient(135deg, #2f64ec, var(--teal));
    box-shadow: 0 2px 0 #17358f, 0 6px 12px rgba(29,78,216,0.30);
}
.user-chip .who { line-height: 1.2; }
.user-chip .who b { display: block; color: var(--ink); }
.user-chip .who span { color: var(--ink-soft); letter-spacing: 0.08em; }

/* ---------- login screen ---------- */
.login-brand { text-align: center; margin: 5vh 0 1rem; }
.login-brand .logo {
    width: 72px; height: 72px; margin: 0 auto 0.8rem; border-radius: 20px;
    display: grid; place-items: center; font-size: 2rem;
    background: linear-gradient(180deg, #ffffff, #e6efff);
    box-shadow: 0 4px 0 var(--edge), 0 14px 26px rgba(29,78,216,0.22);
}
.login-brand h1 { margin: 0; border: none; }
.login-brand h1::after { left: 50%; transform: translateX(-50%); }
.login-brand p { color: var(--ink-soft); letter-spacing: 0.16em; margin-top: 0.6rem; }
.login-foot { text-align: center; color: var(--ink-soft); letter-spacing: 0.1em; margin-top: 1rem; }
</style>
"""

st.markdown(THEME_CSS, unsafe_allow_html=True)


# ============================================================
# LOGIN / AUTHENTICATION
# ============================================================
# Default accounts are defined below as SHA-256 hashes (no plain-text
# passwords are stored in the code). To change them, either:
#   1) generate a new hash:  python -c "import hashlib;print(hashlib.sha256(b'NewPassword').hexdigest())"
#      and paste it into DEFAULT_USERS, or
#   2) define [users] in .streamlit/secrets.toml, e.g.
#         [users.admin]
#         password_sha256 = "<hash>"
#         role = "Administrator"
#      Secrets, when present, take priority over DEFAULT_USERS.
#
# DEFAULT LOGINS (change before production!):
#   admin / Tmbsl@2026   (Administrator)
#   qa    / Qa@2026      (QA Inspector)

import hmac
import os
import time

DEFAULT_USERS = {
    "admin": {
        "password_sha256": hashlib.sha256(b"Tmbsl@2026").hexdigest(),
        "role": "Administrator",
        "name": "Admin",
    },
    "qa": {
        "password_sha256": hashlib.sha256(b"Qa@2026").hexdigest(),
        "role": "QA Inspector",
        "name": "QA Inspector",
    },
}

MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 60


def _load_users():
    """Return the user table (secrets override defaults)."""
    try:
        secret_users = st.secrets.get("users", None)
    except Exception:
        secret_users = None
    if secret_users:
        users = {}
        for uname, info in secret_users.items():
            users[str(uname).lower()] = {
                "password_sha256": str(info.get("password_sha256", "")),
                "role": str(info.get("role", "User")),
                "name": str(info.get("name", uname)).strip() or str(uname),
            }
        if users:
            return users
    return DEFAULT_USERS


def _verify_login(username, password):
    users = _load_users()
    user = users.get((username or "").strip().lower())
    supplied = hashlib.sha256((password or "").encode("utf-8")).hexdigest()
    # Compare even when the user is unknown, to keep timing uniform.
    expected = user["password_sha256"] if user else hashlib.sha256(b"-").hexdigest()
    ok = hmac.compare_digest(supplied, expected)
    return (user if (ok and user) else None)


def _logout():
    for key in ("auth_user", "auth_role", "auth_name"):
        st.session_state.pop(key, None)


def _render_login():
    st.markdown(
        """
        <div class="login-brand">
            <div class="logo">🚌</div>
            <h1>  TMBSL QA BOOK SYSTEM</h1>
     
        </div>
        """,
        unsafe_allow_html=True,
    )

    left, mid, right = st.columns([1, 1.15, 1])
    with mid:
        failed = st.session_state.get("_login_failed", 0)
        locked_until = st.session_state.get("_login_locked_until", 0)
        remaining = int(locked_until - time.time())

        with st.form("login_form", clear_on_submit=False):
            st.markdown("""
    
            <div style="text-align: center; margin-bottom: 20px;">
                <h3 style="margin-bottom: 5px;">🔐 Welcome back</h3>
                <p style="margin-top: 0; font-size: 16px;">
                     Please sign in
                </p>
            </div>
        """, unsafe_allow_html=True)

    
            username = st.text_input("Username", placeholder="Enter username")
            password = st.text_input(
                "Password", type="password", placeholder="Enter password"
            )
            submitted = st.form_submit_button(
                "AUTHENTICATE →", type="primary", use_container_width=True
            )

        if submitted:
            if remaining > 0:
                st.error(f"Too many attempts. Try again in {remaining}s.")
            else:
                user = _verify_login(username, password)
                if user:
                    st.session_state["auth_user"] = username.strip().lower()
                    st.session_state["auth_role"] = user["role"]
                    st.session_state["auth_name"] = user["name"]
                    st.session_state["_login_failed"] = 0
                    st.session_state["_login_locked_until"] = 0
                    st.rerun()
                else:
                    failed += 1
                    st.session_state["_login_failed"] = failed
                    if failed >= MAX_LOGIN_ATTEMPTS:
                        st.session_state["_login_locked_until"] = (
                            time.time() + LOCKOUT_SECONDS
                        )
                        st.session_state["_login_failed"] = 0
                        st.error(
                            f"Too many failed attempts. Locked for {LOCKOUT_SECONDS}s."
                        )
                    else:
                        st.error(
                            "Invalid username or password. "
                            f"({MAX_LOGIN_ATTEMPTS - failed} attempts left)"
                        )

        st.markdown(
            '<div class="login-foot">TATA MOTORS BODY SOLUTIONS LIMITED </div>',
            unsafe_allow_html=True,
        )


if not st.session_state.get("auth_user"):
    _render_login()
    st.stop()



# ============================================================
# HELPER FUNCTIONS
# ============================================================

def _normalize_column_name(value):
    """
    Normalize Excel/CSV column names so small differences such as
    'Chassis No', 'Chassis Number', 'chassis_no' are easier to detect.
    """
    return re.sub(
        r"[^a-z0-9]+",
        "",
        str(value).strip().lower()
    )


def _clean_string(value):
    """
    Safely convert a cell/widget value to a clean string.
    """
    if value is None:
        return ""

    if pd.isna(value):
        return ""

    return str(value).strip()


def _normalize_chassis(value):
    """
    Normalize chassis number for lookup.

    Removes spaces and makes comparison case-insensitive.
    """
    value = _clean_string(value)

    return re.sub(
        r"\s+",
        "",
        value
    ).upper()


def _normalize_bus(value, vehicle_type=""):
    """
    Normalize a Bus Number while preserving its text representation.

    Examples:
        100101 -> 100101
        '100101' -> 100101
        MCV 1234 -> 1234
        LCV 123 -> 000123
        100101.0 -> 100101
    """

    value = _clean_string(value)

    if value.endswith(".0"):

        possible_integer = value[:-2]

        if possible_integer.isdigit():
            value = possible_integer

    # Only LCV mappings use six-digit padding when Excel has read a
    # numeric cell without its displayed leading zeros. MCV values are
    # four digits and must never be expanded to six.
    if (
        value.isdigit()
        and str(vehicle_type).strip().upper() == "LCV"
        and len(value) < 6
    ):
        value = value.zfill(6)

    return value


def _find_mapping_column(columns, aliases):
    """
    Find a column using normalized aliases.
    """

    normalized = {
        _normalize_column_name(column): column
        for column in columns
    }

    for alias in aliases:

        alias_norm = _normalize_column_name(alias)

        if alias_norm in normalized:

            return normalized[alias_norm]

    return None


def _load_bus_chassis_mapping(uploaded_file):
    """
    Load Bus-Chassis mapping from ALL worksheets of an Excel file
    or from a CSV file.

    Required logical columns:
        Chassis No
        Bus No

    Excel:
        Reads every worksheet (LCV, MCV, etc.) and combines them.

    CSV:
        Reads the single CSV table.

    Returns:
        dataframe, error_message
    """

    try:

        file_name = uploaded_file.name.lower()

        # ========================================================
        # READ ALL SHEETS FROM EXCEL
        # ========================================================

        if file_name.endswith(".xlsx"):

            all_sheets = pd.read_excel(
                uploaded_file,
                sheet_name=None,
                dtype=str
            )

            sheet_frames = []

            for sheet_name, sheet_df in all_sheets.items():

                if sheet_df is None or sheet_df.empty:
                    continue

                # Clean column names
                sheet_df.columns = [
                    str(column).strip()
                    for column in sheet_df.columns
                ]

                # ------------------------------------------------
                # Sometimes the first row itself contains headers
                # (as appears to happen in some MCV-style sheets).
                # Detect and promote it when necessary.
                # ------------------------------------------------

                normalized_headers = [
                    _normalize_column_name(column)
                    for column in sheet_df.columns
                ]

                has_chassis_header = any(
                    value in normalized_headers
                    for value in [
                        "chassisno",
                        "chassisnumber",
                        "chassis"
                    ]
                )

                has_bus_header = any(
                    value in normalized_headers
                    for value in [
                        "busno",
                        "busnumber",
                        "bus"
                    ]
                )

                if not (
                    has_chassis_header
                    and has_bus_header
                ):

                    # Check whether first data row contains
                    # the real headers.
                    if len(sheet_df) > 0:

                        first_row = [
                            _normalize_column_name(value)
                            for value in sheet_df.iloc[0].tolist()
                        ]

                        first_row_has_chassis = any(
                            value in first_row
                            for value in [
                                "chassisno",
                                "chassisnumber",
                                "chassis"
                            ]
                        )

                        first_row_has_bus = any(
                            value in first_row
                            for value in [
                                "busno",
                                "busnumber",
                                "bus"
                            ]
                        )

                        if (
                            first_row_has_chassis
                            and first_row_has_bus
                        ):

                            sheet_df.columns = [
                                str(value).strip()
                                for value
                                in sheet_df.iloc[0].tolist()
                            ]

                            sheet_df = (
                                sheet_df.iloc[1:]
                                .reset_index(drop=True)
                            )

                # ------------------------------------------------
                # Find logical columns
                # ------------------------------------------------

                chassis_aliases = [
                    "Chassis No",
                    "Chassis Number",
                    "Chassis",
                    "Chassis_No",
                    "ChassisNo",
                    "CHASSIS NO",
                ]

                bus_aliases = [
                    "Bus No",
                    "Bus Number",
                    "BusNo",
                    "Bus_Number",
                    "Bus",
                    "BUS NO",
                ]

                chassis_column = _find_mapping_column(
                    sheet_df.columns,
                    chassis_aliases
                )

                bus_column = _find_mapping_column(
                    sheet_df.columns,
                    bus_aliases
                )

                # This sheet does not contain a usable mapping.
                # Ignore it rather than failing the complete upload.
                if not chassis_column or not bus_column:
                    continue

                temp_df = sheet_df[
                    [chassis_column, bus_column]
                ].copy()

                temp_df.columns = [
                    "Chassis No",
                    "Bus No"
                ]

                # Keep source sheet and inferred vehicle type for traceability.
                temp_df["Source Sheet"] = (
                    str(sheet_name)
                )
                sheet_name_upper = str(sheet_name).upper()
                temp_df["Vehicle Type"] = (
                    "MCV"
                    if "MCV" in sheet_name_upper
                    else "LCV"
                    if "LCV" in sheet_name_upper
                    else ""
                )

                sheet_frames.append(
                    temp_df
                )

            if not sheet_frames:

                return None, (
                    "No worksheet containing both "
                    "'Chassis No' and 'Bus No' could be found "
                    "in the uploaded Excel file."
                )

            mapping_df = pd.concat(
                sheet_frames,
                ignore_index=True
            )

        # ========================================================
        # CSV
        # ========================================================

        elif file_name.endswith(".csv"):

            mapping_df = pd.read_csv(
                uploaded_file,
                dtype=str
            )

            mapping_df.columns = [
                str(column).strip()
                for column in mapping_df.columns
            ]

            chassis_aliases = [
                "Chassis No",
                "Chassis Number",
                "Chassis",
                "Chassis_No",
                "ChassisNo",
                "CHASSIS NO",
            ]

            bus_aliases = [
                "Bus No",
                "Bus Number",
                "BusNo",
                "Bus_Number",
                "Bus",
                "BUS NO",
            ]

            chassis_column = _find_mapping_column(
                mapping_df.columns,
                chassis_aliases
            )

            bus_column = _find_mapping_column(
                mapping_df.columns,
                bus_aliases
            )

            if not chassis_column:

                return None, (
                    "Chassis Number column was not found "
                    "in the CSV file."
                )

            if not bus_column:

                return None, (
                    "Bus Number column was not found "
                    "in the CSV file."
                )

            mapping_df = mapping_df[
                [chassis_column, bus_column]
            ].copy()

            mapping_df.columns = [
                "Chassis No",
                "Bus No"
            ]
            mapping_df["Source Sheet"] = "CSV"
            mapping_df["Vehicle Type"] = ""

        else:

            return None, (
                "Unsupported mapping file. "
                "Please upload .xlsx or .csv."
            )


        # ========================================================
        # CLEAN DATA
        # ========================================================

        mapping_df["Chassis No"] = (
            mapping_df["Chassis No"]
            .map(_normalize_chassis)
        )

        mapping_df["Bus No"] = mapping_df.apply(
            lambda row: _normalize_bus(
                row["Bus No"],
                row.get("Vehicle Type", ""),
            ),
            axis=1,
        )


        # Remove rows where either value is missing

        mapping_df = mapping_df[
            (mapping_df["Chassis No"] != "")
            &
            (mapping_df["Bus No"] != "")
        ].copy()


        if mapping_df.empty:

            return None, (
                "No valid Chassis No / Bus No records "
                "were found in the mapping file."
            )


        # ========================================================
        # REMOVE EXACT DUPLICATE PAIRS
        # ========================================================

        mapping_df = (
            mapping_df
            .drop_duplicates(
                subset=[
                    "Chassis No",
                    "Bus No"
                ],
                keep="last"
            )
            .reset_index(drop=True)
        )


        # ========================================================
        # CHECK DUPLICATE CHASSIS WITH DIFFERENT BUS NUMBERS
        # ========================================================

        chassis_bus_counts = (
            mapping_df
            .groupby("Chassis No")["Bus No"]
            .nunique()
        )

        ambiguous_chassis = (
            chassis_bus_counts[
                chassis_bus_counts > 1
            ]
            .index
            .tolist()
        )

        if ambiguous_chassis:

            # Keep the records so the application can flag
            # ambiguous lookups instead of silently choosing
            # the wrong Bus Number.
            mapping_df.attrs[
                "ambiguous_chassis"
            ] = ambiguous_chassis

        else:

            mapping_df.attrs[
                "ambiguous_chassis"
            ] = []


        return mapping_df, None


    except Exception as error:

        return None, (
            f"Could not read the mapping file: {error}"
        )

def _lookup_bus_from_chassis(
    mapping_df,
    chassis_no
):
    """
    VLOOKUP-style Chassis No -> Bus No.

    Returns:
        (bus_number, status)

    status:
        'matched'
        'not_found'
        'ambiguous'
    """

    if mapping_df is None:

        return "", "not_found"

    normalized_chassis = _normalize_chassis(
        chassis_no
    )

    if not normalized_chassis:

        return "", "not_found"

    matches = mapping_df[
        mapping_df["Chassis No"]
        == normalized_chassis
    ]

    if matches.empty:

        return "", "not_found"

    unique_buses = (
        matches["Bus No"]
        .dropna()
        .astype(str)
        .str.strip()
        .unique()
        .tolist()
    )

    if len(unique_buses) == 1:

        return (
            _normalize_bus(unique_buses[0]),
            "matched"
        )

    return "", "ambiguous"


def _lookup_chassis_from_bus(
    mapping_df,
    bus_no
):
    """
    Reverse lookup:
        Bus No -> Chassis No

    Currently used as a reference/verification helper.
    """

    if mapping_df is None:

        return "", "not_found"

    normalized_bus = _normalize_bus(
        bus_no
    )

    if not normalized_bus:

        return "", "not_found"

    matches = mapping_df[
        mapping_df["Bus No"]
        == normalized_bus
    ]

    if matches.empty:

        return "", "not_found"

    unique_chassis = (
        matches["Chassis No"]
        .dropna()
        .astype(str)
        .str.strip()
        .unique()
        .tolist()
    )

    if len(unique_chassis) == 1:

        return (
            unique_chassis[0],
            "matched"
        )

    return "", "ambiguous"


def _lookup_vehicle_type_from_chassis(mapping_df, chassis_no, bus_no=""):
    """Return MCV/LCV only when the exact chassis mapping identifies one type."""

    if (
        mapping_df is None
        or "Vehicle Type" not in mapping_df.columns
    ):
        return ""

    normalized_chassis = _normalize_chassis(chassis_no)
    matches = mapping_df[
        mapping_df["Chassis No"] == normalized_chassis
    ]
    if bus_no:
        matches = matches[
            matches["Bus No"].astype(str).str.strip() == str(bus_no).strip()
        ]

    types = (
        matches["Vehicle Type"]
        .dropna()
        .astype(str)
        .str.upper()
        .loc[lambda values: values.isin(["MCV", "LCV"])]
        .unique()
        .tolist()
    )
    return types[0] if len(types) == 1 else ""


def _load_mapping_into_session(uploaded_file):
    """
    Load the uploaded mapping once per file hash.
    """

    if uploaded_file is None:

        return

    image_bytes = uploaded_file.getvalue()

    file_hash = hashlib.md5(
        image_bytes
    ).hexdigest()

    previous_hash = st.session_state.get(
        "bus_mapping_hash"
    )

    if previous_hash == file_hash:

        return

    _reset_mapping_suggestions()

    mapping_df, error = _load_bus_chassis_mapping(
        uploaded_file
    )

    if error:

        st.session_state["bus_mapping_df"] = None
        st.session_state["bus_mapping_error"] = error
        st.session_state["bus_mapping_hash"] = file_hash
        st.session_state["bus_mapping_name"] = (
            uploaded_file.name
        )

        return

    st.session_state["bus_mapping_df"] = mapping_df
    st.session_state["bus_mapping_error"] = None
    st.session_state["bus_mapping_hash"] = file_hash
    st.session_state["bus_mapping_name"] = (
        uploaded_file.name
    )

def _clear_bus_mapping():
    """
    Clear the currently loaded mapping file.
    """

    for key in (
        "bus_mapping_df",
        "bus_mapping_error",
        "bus_mapping_hash",
        "bus_mapping_name",
    ):

        st.session_state.pop(
            key,
            None
        )

    _reset_mapping_suggestions()


def _get_mapping_df():
    return st.session_state.get(
        "bus_mapping_df"
    )


def _reset_mapping_suggestions():
    """
    Clear stale auto-filled Bus Numbers when lookup is reset.
    """

    for bus_key, auto_key, mapped_key, chassis_key in (
        (
            "manual_bus_no",
            "manual_bus_auto",
            "manual_mapping_bus",
            "manual_mapping_last_chassis",
        ),
        (
            "ocr_f_bus_no",
            "ocr_bus_auto",
            "ocr_mapping_bus",
            "ocr_mapping_last_chassis",
        ),
    ):
        if (
            st.session_state.get(auto_key, False)
            and st.session_state.get(bus_key, "")
            == st.session_state.get(mapped_key, "")
        ):
            st.session_state[bus_key] = ""

        st.session_state[auto_key] = False
        st.session_state[mapped_key] = ""
        st.session_state[chassis_key] = ""


def _get_duplicate_bus_mappings(mapping_df):
    """
    Return Bus Numbers assigned to more than one distinct chassis.

    Duplicate mappings are reported for review but never prevent
    manual entry or saving a QA Book record.
    """

    if mapping_df is None or mapping_df.empty:
        return pd.DataFrame(
            columns=["Bus No", "Chassis Count", "Chassis Nos"]
        )

    duplicate_rows = []

    for bus_no, group in mapping_df.groupby("Bus No"):
        chassis_numbers = sorted(
            set(
                group["Chassis No"]
                .dropna()
                .astype(str)
                .map(_normalize_chassis)
            )
        )

        if len(chassis_numbers) > 1:
            duplicate_rows.append(
                {
                    "Bus No": str(bus_no),
                    "Chassis Count": len(chassis_numbers),
                    "Chassis Nos": ", ".join(chassis_numbers),
                }
            )

    return pd.DataFrame(
        duplicate_rows,
        columns=["Bus No", "Chassis Count", "Chassis Nos"]
    )


def _validate_bus_number(bus_no):
    """
    Common Bus Number validation.
    """

    errors = []

    bus_no = _clean_string(bus_no)

    if not bus_no:

        errors.append(
            "Bus Number is required."
        )

    elif not bus_no.isdigit():

        errors.append(
            "Bus Number must contain only digits."
        )

    elif len(bus_no) not in (4, 6):

        errors.append(
            "Bus Number must be exactly 4 digits (MCV) or 6 digits (LCV)."
        )

    return errors


def _get_global_bus_conflicts(
    active_batch,
    bus_no
):
    """
    Check whether a Bus Number has already been
    used in another batch.
    """

    if not bus_no:

        return []

    return find_bus_no_usage(
        bus_no,
        exclude_batch_id=active_batch
    )


def _start_batch_draft(
    batch_id,
    batch_date,
    observations,
    target_books,
    is_new
):
    """Initialize session-backed staging; no database writes happen here."""

    draft_generation = st.session_state.get("_draft_generation", 0) + 1
    st.session_state["_draft_generation"] = draft_generation
    st.session_state["draft_batch_meta"] = {
        "batch_id": str(batch_id).strip(),
        "batch_date": str(batch_date),
        "observations": str(observations or ""),
        "target_books": int(target_books),
        "is_new": bool(is_new),
    }
    st.session_state["draft_batch_records"] = []
    st.session_state["draft_batch_active"] = True
    st.session_state["draft_editor_revision"] = 0
    st.session_state["draft_upload_revision"] = 0
    st.session_state["draft_camera_revision"] = 0
    st.session_state["draft_mapping_revision"] = 0


def _stage_draft_record(record):
    """Append a prepared record and preserve its original image in session."""

    records = st.session_state.setdefault("draft_batch_records", [])
    record.setdefault("sequence", len(records) + 1)
    record.setdefault("source_file", "Manual entry")
    record.setdefault("source_hash", "")
    record.setdefault("image_bytes", None)
    record.setdefault("quality", {})
    record.setdefault("barcode", "")
    record.setdefault("chassis_no", "")
    record.setdefault("bus_no", "")
    record.setdefault("vehicle_type", "")
    record.setdefault("mat_no", "")
    record.setdefault("vc_no", "")
    record.setdefault("color", "")
    record.setdefault("order_no", "")
    record.setdefault("seating", "")
    record.setdefault("pdi_clearance", "Pending")
    record.setdefault("qa_book_status", "Not Received")
    record.setdefault("verification_status", "REVIEW")
    record.setdefault(
        "scan_timestamp",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    records.append(record)
    st.session_state["draft_editor_revision"] = (
        st.session_state.get("draft_editor_revision", 0) + 1
    )


def _draft_record_from_image(uploaded_image):
    """Run quality/OCR once and return an editable draft record."""

    image_bytes = uploaded_image.getvalue()
    file_name = getattr(uploaded_image, "name", "camera_capture.jpg")
    image_hash = hashlib.sha256(image_bytes).hexdigest()
    quality = {}
    extracted = {}
    processing_error = ""
    is_heif = str(file_name).lower().endswith((".heic", ".heif"))

    if is_heif and not HEIF_AVAILABLE:
        processing_error = (
            "HEIC/HEIF decoder unavailable; install requirements and restart."
        )
    elif not OCR_AVAILABLE:
        processing_error = (
            "OCR dependencies are unavailable; enter the label fields manually."
        )
    else:
        try:
            quality = estimate_image_quality(image_bytes)
            extracted = extract_label_fields(image_bytes)
        except Exception as error:
            processing_error = f"Image/OCR processing failed: {error}"

    mapping_df = _get_mapping_df()
    chassis_no = str(extracted.get("chassis_no", "")).strip()
    bus_no = ""
    vehicle_type = ""
    if chassis_no and mapping_df is not None:
        bus_no, lookup_status = _lookup_bus_from_chassis(
            mapping_df,
            chassis_no
        )
        if lookup_status != "matched":
            bus_no = ""
        else:
            vehicle_type = _lookup_vehicle_type_from_chassis(
                mapping_df,
                chassis_no,
                bus_no
            )

    record = {
        "source_file": file_name,
        "source_hash": image_hash,
        "image_bytes": image_bytes,
        "quality": quality,
        "barcode": str(extracted.get("barcode", "")).strip(),
        "chassis_no": chassis_no,
        "bus_no": bus_no,
        "vehicle_type": vehicle_type,
        "mat_no": str(extracted.get("mat_no", "")).strip(),
        "vc_no": str(extracted.get("vc_no", "")).strip(),
        "color": str(extracted.get("color", "")).strip(),
        "order_no": str(extracted.get("order_no", "")).strip(),
        "seating": str(extracted.get("seating", "")).strip(),
        "pdi_clearance": "Pending",
        "qa_book_status": "Not Received",
        "verification_status": (
            "REVIEW"
            if (
                processing_error
                or not extracted.get("barcode")
                or quality.get("is_blurry")
                or quality.get("is_low_res")
            )
            else "READY"
        ),
        "processing_error": processing_error,
        "scan_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return record


def _draft_quality_label(record):
    quality = record.get("quality") or {}
    if not quality:
        return "Not measured"
    size = f"{quality.get('width', '?')}×{quality.get('height', '?')}"
    flags = []
    if quality.get("is_blurry"):
        flags.append("blurry")
    if quality.get("is_low_res"):
        flags.append("low resolution")
    suffix = f" — {', '.join(flags)}" if flags else ""
    return f"{size}{suffix}"


def _validate_draft_records(records, batch_meta):
    """Return per-row blockers before the one final database transaction."""

    errors_by_row = {}
    bus_rows = {}
    persisted_bus = set()
    persisted_count = 0

    if not batch_meta.get("is_new"):
        try:
            existing_records = get_batch_records(batch_meta["batch_id"])
            persisted_count = len(existing_records)
            if "Bus No" in existing_records.columns:
                persisted_bus = {
                    str(value).strip()
                    for value in existing_records["Bus No"].dropna()
                }
        except Exception:
            persisted_bus = set()

    for index, record in enumerate(records):
        errors = []
        display_index = index + 1
        barcode = str(record.get("barcode", "")).strip()
        bus_no = str(record.get("bus_no", "")).strip()
        vehicle_type = str(record.get("vehicle_type", "")).strip().upper()

        if not barcode:
            errors.append("Barcode / BC No is required.")

        if not bus_no:
            errors.append("Bus No is required.")
        else:
            if not bus_no.isdigit():
                errors.append("Bus No must contain digits only.")
            elif vehicle_type == "MCV" and len(bus_no) != 4:
                errors.append("MCV Bus No must be exactly 4 digits.")
            elif vehicle_type == "LCV" and len(bus_no) != 6:
                errors.append("LCV Bus No must be exactly 6 digits.")
            elif vehicle_type not in ("MCV", "LCV") and len(bus_no) not in (4, 6):
                errors.append("Unknown Vehicle Type requires a 4- or 6-digit Bus No.")

        if vehicle_type not in ("", "MCV", "LCV"):
            errors.append("Vehicle Type must be MCV, LCV, or Unknown.")

        if bus_no:
            bus_rows.setdefault(bus_no, []).append(display_index)
            if bus_no in persisted_bus:
                errors.append(
                    f"Bus No {bus_no} is already present in this batch."
                )
            conflicts = _get_global_bus_conflicts(
                batch_meta["batch_id"],
                bus_no
            )
            if conflicts:
                errors.append(
                    f"Bus No {bus_no} is already used in batch(es): "
                    f"{', '.join(conflicts)}."
                )

        if record.get("pdi_clearance") not in ("Yes", "No", "Pending"):
            errors.append("Select PDI Yes, No, or Pending.")
        if record.get("qa_book_status") not in ("Received", "Not Received"):
            errors.append("Select Received or Not Received for QA Book Status.")

        if errors:
            errors_by_row[display_index] = errors

    for bus_no, row_numbers in bus_rows.items():
        if len(row_numbers) > 1:
            for row_number in row_numbers:
                errors_by_row.setdefault(row_number, []).append(
                    f"Duplicate draft Bus No {bus_no} also appears in row(s) "
                    f"{', '.join(str(n) for n in row_numbers if n != row_number)}."
                )

    target_books = int(
        batch_meta.get("target_books", EXPECTED_BOOKS_DEFAULT)
    )
    if persisted_count + len(records) > target_books:
        row_number = max(1, len(records))
        errors_by_row.setdefault(row_number, []).append(
            f"Adding these records would exceed the target of {target_books}."
        )

    return errors_by_row


def _clear_batch_draft():
    for key in (
        "draft_batch_meta",
        "draft_batch_records",
        "draft_batch_active",
        "draft_editor_revision",
        "draft_upload_revision",
        "draft_camera_revision",
        "draft_mapping_revision",
    ):
        st.session_state.pop(key, None)


def _select_batch_after_draft(batch_id):
    """Streamlit callback runs before widgets are recreated on rerun."""

    st.session_state["batch_selection"] = batch_id


def _resume_batch_draft():
    """Resume a paused, unsaved draft without replacing its staged rows."""

    if st.session_state.get("draft_batch_meta"):
        st.session_state["draft_batch_active"] = True


def _render_batch_draft():
    """Draft workflow: camera/upload/manual staging, review, atomic commit."""

    meta = st.session_state.get("draft_batch_meta")
    if not meta:
        st.error("Draft batch information is missing.")
        if st.button("Return to batches", key="draft_missing_return"):
            st.session_state["draft_batch_active"] = False
            st.rerun()
        return

    records = st.session_state.setdefault("draft_batch_records", [])
    generation = st.session_state.get("_draft_generation", 1)
    target = int(meta.get("target_books", EXPECTED_BOOKS_DEFAULT))
    saved_count = 0
    if not meta.get("is_new"):
        try:
            saved_count = len(get_batch_records(meta["batch_id"]))
        except Exception:
            saved_count = 0
    total_count = saved_count + len(records)
    remaining = max(0, target - total_count)

    st.title("TMBSL QA Book — Draft Batch")
    if st.button(
        "← Back to Batch Management",
        key=f"draft_back_to_batches_{generation}",
    ):
        # Keep the draft in session so it can be resumed later.
        st.session_state["draft_batch_active"] = False
        st.rerun()
    st.caption(
        "Going back keeps this unsaved draft. Choose another batch in the "
        "sidebar; use the Resume Draft button there to continue this one."
    )
    st.subheader(f"Batch {meta['batch_id']}")
    st.caption(
        f"Target: {target} books · Added: {total_count} "
        f"({saved_count} already saved, {len(records)} staged) · "
        "Only “Confirm & Add Batch” writes staged records to the database."
    )

    target_col, added_col, remaining_col = st.columns(3)
    target_col.metric("Target Books", target)
    added_col.metric("Books Added", f"{total_count} / {target}")
    remaining_col.metric("Remaining to Target", remaining)

    if total_count >= target:
        st.success(
            "Batch target reached. Review the complete draft before final confirmation."
        )
    elif total_count:
        st.info(f"Current batch count: {total_count} of {target} target books.")

    with st.expander("Batch details", expanded=False):
        st.write(f"**Date:** {meta.get('batch_date', '')}")
        st.write(f"**Notes:** {meta.get('observations', '') or '—'}")
        st.write(
            "**Batch type:** "
            + ("New batch (not saved yet)" if meta.get("is_new") else "Existing batch")
        )

    with st.expander("Optional Bus–Chassis mapping lookup", expanded=False):
        st.caption(
            "Mapping only assists Bus No entry. LCV/MCV is inferred from "
            "the worksheet name; an unmapped record can still be entered manually."
        )
        draft_mapping_file = st.file_uploader(
            "Upload mapping workbook or CSV",
            type=["xlsx", "csv"],
            key=(
                f"draft_mapping_upload_{generation}_"
                f"{st.session_state.get('draft_mapping_revision', 0)}"
            ),
        )
        if draft_mapping_file is not None:
            _load_mapping_into_session(draft_mapping_file)
        draft_mapping_error = st.session_state.get("bus_mapping_error")
        draft_mapping_df = _get_mapping_df()
        if draft_mapping_error:
            st.error(draft_mapping_error)
        elif draft_mapping_df is not None:
            st.success(
                f"Loaded {len(draft_mapping_df):,} mapping row(s); "
                "manual Bus No entry remains available."
            )
            if st.button(
                "Clear mapping",
                key=f"draft_clear_mapping_{generation}",
            ):
                _clear_bus_mapping()
                st.session_state["draft_mapping_revision"] = (
                    st.session_state.get("draft_mapping_revision", 0) + 1
                )
                st.rerun()
        else:
            st.info("No mapping uploaded; enter Bus No manually.")

    st.divider()
    st.header("Step 1 — Add QA Books")
    st.caption(
        "Upload several label photos together, capture one photo at a time, "
        "or enter a book manually. Nothing is saved until final confirmation."
    )

    upload_col, camera_col = st.columns(2)
    with upload_col:
        st.subheader("Upload multiple images")
        upload_key = (
            f"draft_multi_upload_{generation}_"
            f"{st.session_state.get('draft_upload_revision', 0)}"
        )
        uploaded_images = st.file_uploader(
            "Select QA Book label images",
            type=[
                "jpg", "jpeg", "png", "heic", "heif",
                "webp", "tif", "tiff",
            ],
            accept_multiple_files=True,
            key=upload_key,
            help="All selected images are added to this same draft batch.",
        )
        if st.button(
            "Process selected images",
            type="primary",
            disabled=not uploaded_images or remaining == 0,
            key=f"draft_process_upload_{generation}_{st.session_state.get('draft_upload_revision', 0)}",
            use_container_width=True,
        ):
            known_hashes = {
                item.get("source_hash")
                for item in records
                if item.get("source_hash")
            }
            images_to_process = [
                image
                for image in uploaded_images
                if hashlib.sha256(image.getvalue()).hexdigest()
                not in known_hashes
            ]
            if not images_to_process:
                st.info("These images have already been added to this draft.")
            else:
                progress = st.progress(0, text="Preparing images…")
                for position, image in enumerate(
                    images_to_process[:remaining],
                    start=1,
                ):
                    progress.progress(
                        int((position - 1) * 100 / len(images_to_process[:remaining])),
                        text=f"Processing {position} / {min(len(images_to_process), remaining)}",
                    )
                    draft_record = _draft_record_from_image(image)
                    if draft_record.get("source_hash") not in known_hashes:
                        _stage_draft_record(draft_record)
                        known_hashes.add(draft_record.get("source_hash"))
                progress.progress(100, text="Image processing complete.")
                if len(images_to_process) > remaining:
                    st.warning(
                        f"Only {remaining} image(s) were added because the "
                        "configured batch target was reached."
                    )
                st.session_state["draft_upload_revision"] = (
                    st.session_state.get("draft_upload_revision", 0) + 1
                )
                st.rerun()

    with camera_col:
        st.subheader("Camera / phone scan")
        camera_key = (
            f"draft_camera_{generation}_"
            f"{st.session_state.get('draft_camera_revision', 0)}"
        )
        captured_image = st.camera_input(
            "Capture one QA Book label",
            key=camera_key,
            resolution="1080p",
        )
        if st.button(
            "Add captured book",
            disabled=captured_image is None or remaining == 0,
            key=f"draft_add_camera_{generation}_{st.session_state.get('draft_camera_revision', 0)}",
            use_container_width=True,
        ):
            captured_hash = hashlib.sha256(
                captured_image.getvalue()
            ).hexdigest()
            if any(
                item.get("source_hash") == captured_hash
                for item in records
            ):
                st.warning("This camera image is already in the draft.")
            else:
                _stage_draft_record(_draft_record_from_image(captured_image))
                st.session_state["draft_camera_revision"] = (
                    st.session_state.get("draft_camera_revision", 0) + 1
                )
                st.rerun()

    with st.expander("Manual entry", expanded=False):
        manual_revision = st.session_state.get("draft_editor_revision", 0)
        with st.form(f"draft_manual_form_{generation}_{manual_revision}"):
            manual_col1, manual_col2, manual_col3 = st.columns(3)
            barcode = manual_col1.text_input("Barcode / BC No")
            chassis_no = manual_col1.text_input("Chassis No")
            bus_no = manual_col1.text_input(
                "Bus No",
                help="Keep leading zeros; MCV uses 4 digits and LCV uses 6.",
            )
            vehicle_type = manual_col2.selectbox(
                "Vehicle Type",
                ["Unknown", "MCV", "LCV"],
            )
            mat_no = manual_col2.text_input("MAT No")
            vc_no = manual_col2.text_input("VC No")
            color = manual_col3.text_input("Color")
            order_no = manual_col3.text_input("Order No")
            seating = manual_col3.text_input("Seating")
            pdi_clearance = manual_col3.selectbox(
                "PDI Clearance",
                ["Pending", "Yes", "No"],
            )
            qa_book_status = manual_col3.selectbox(
                "QA Book Status",
                ["Not Received", "Received"],
            )
            manual_add = st.form_submit_button(
                "Add manual book to draft",
                disabled=remaining == 0,
                use_container_width=True,
            )
        if manual_add:
            normalized_type = (
                ""
                if vehicle_type == "Unknown"
                else vehicle_type
            )
            if not normalized_type and chassis_no:
                normalized_type = _lookup_vehicle_type_from_chassis(
                    _get_mapping_df(),
                    chassis_no,
                    bus_no,
                )
            mapped_bus = bus_no.strip()
            if not mapped_bus and chassis_no:
                mapped_bus, lookup_status = _lookup_bus_from_chassis(
                    _get_mapping_df(),
                    chassis_no,
                )
                if lookup_status != "matched":
                    mapped_bus = ""
            _stage_draft_record(
                {
                    "source_file": "Manual entry",
                    "barcode": barcode.strip(),
                    "chassis_no": chassis_no.strip(),
                    "bus_no": mapped_bus,
                    "vehicle_type": normalized_type,
                    "mat_no": mat_no.strip(),
                    "vc_no": vc_no.strip(),
                    "color": color.strip(),
                    "order_no": order_no.strip(),
                    "seating": seating.strip(),
                    "pdi_clearance": pdi_clearance,
                    "qa_book_status": qa_book_status,
                    "verification_status": "REVIEW",
                }
            )
            st.rerun()

    st.divider()
    st.header("Step 2 — Review and edit the complete draft")
    if not records:
        st.info("No books have been added yet.")
    else:
        table_rows = []
        for record in records:
            quality_flags = []
            quality = record.get("quality") or {}
            if quality.get("is_blurry"):
                quality_flags.append(
                    "Image appears blurry — please retake."
                )
            if quality.get("is_low_res"):
                quality_flags.append(
                    "Low resolution image — please retake."
                )
            review_status = record.get("processing_error") or (
                " ".join(quality_flags)
                or (
                    "Manual review required"
                    if record.get("verification_status") == "REVIEW"
                    else "Ready"
                )
            )
            table_rows.append(
                {
                    "Remove?": False,
                    "#": record.get("sequence", ""),
                    "Source File": record.get("source_file", ""),
                    "Barcode": record.get("barcode", ""),
                    "Chassis No": record.get("chassis_no", ""),
                    "Bus No": record.get("bus_no", ""),
                    "Vehicle Type": record.get("vehicle_type", "") or "Unknown",
                    "MAT No": record.get("mat_no", ""),
                    "VC No": record.get("vc_no", ""),
                    "Color": record.get("color", ""),
                    "Order No": record.get("order_no", ""),
                    "Seating": record.get("seating", ""),
                    "PDI Clearance": record.get("pdi_clearance", "Pending"),
                    "QA Book Status": record.get(
                        "qa_book_status",
                        "Not Received",
                    ),
                    "Image Quality": _draft_quality_label(record),
                    "Review Status": review_status,
                }
            )
        revision = st.session_state.get("draft_editor_revision", 0)
        edited_table = st.data_editor(
            pd.DataFrame(table_rows),
            use_container_width=True,
            hide_index=True,
            num_rows="fixed",
            key=f"draft_records_editor_{generation}_{revision}",
            disabled=[
                "#", "Source File", "Image Quality", "Review Status",
            ],
            column_config={
                "Remove?": st.column_config.CheckboxColumn(default=False),
                "#": st.column_config.NumberColumn(format="%d"),
                "Vehicle Type": st.column_config.SelectboxColumn(
                    options=["Unknown", "MCV", "LCV"],
                    required=True,
                ),
                "PDI Clearance": st.column_config.SelectboxColumn(
                    options=["Yes", "No", "Pending"],
                    required=True,
                ),
                "QA Book Status": st.column_config.SelectboxColumn(
                    options=["Received", "Not Received"],
                    required=True,
                ),
            },
        )
        evidence_records = [
            record
            for record in records
            if record.get("image_bytes")
        ]
        if evidence_records:
            with st.expander("Preview original image evidence", expanded=False):
                for evidence_record in evidence_records:
                    with st.expander(
                        f"#{evidence_record.get('sequence', '')} — "
                        f"{evidence_record.get('source_file', 'Image')}",
                        expanded=False,
                    ):
                        st.image(
                            evidence_record["image_bytes"],
                            caption="Original uploaded/captured image",
                            use_container_width=True,
                        )

        removed_rows = [
            index
            for index, row in edited_table.iterrows()
            if bool(row.get("Remove?", False))
        ]
        if st.button(
            "Remove selected draft rows",
            disabled=not removed_rows,
            key=f"draft_remove_rows_{generation}_{revision}",
        ):
            st.session_state["draft_batch_records"] = [
                record
                for index, record in enumerate(records)
                if index not in removed_rows
            ]
            for sequence, record in enumerate(
                st.session_state["draft_batch_records"],
                start=1,
            ):
                record["sequence"] = sequence
            st.session_state["draft_editor_revision"] = revision + 1
            st.rerun()

        table_to_record = {
            "Barcode": "barcode",
            "Chassis No": "chassis_no",
            "Bus No": "bus_no",
            "Vehicle Type": "vehicle_type",
            "MAT No": "mat_no",
            "VC No": "vc_no",
            "Color": "color",
            "Order No": "order_no",
            "Seating": "seating",
            "PDI Clearance": "pdi_clearance",
            "QA Book Status": "qa_book_status",
        }
        for index, row in edited_table.iterrows():
            for display_name, record_key in table_to_record.items():
                value = row.get(display_name, "")
                if display_name == "Vehicle Type" and value == "Unknown":
                    value = ""
                records[index][record_key] = str(value or "").strip()

    st.divider()
    st.header("Step 3 — Validate and confirm once")
    errors_by_row = _validate_draft_records(records, meta)
    ready_count = len(records) - len(errors_by_row)
    summary_col1, summary_col2, summary_col3 = st.columns(3)
    summary_col1.metric("Books Added", f"{total_count} / {target}")
    summary_col2.metric("Ready", ready_count)
    summary_col3.metric("Needs Review", len(errors_by_row))

    if errors_by_row:
        for row_number, row_errors in errors_by_row.items():
            st.error(
                f"Row {row_number}: " + " ".join(row_errors)
            )
    elif records:
        st.success("All staged records pass the current validation checks.")

    if total_count < target:
        st.warning(
            f"Target is {target}; this batch contains {total_count} actual "
            "books. No placeholder records will be created."
        )

    review_key = (
        f"draft_review_confirm_{generation}_"
        f"{st.session_state.get('draft_editor_revision', 0)}"
    )
    reviewed = st.checkbox(
        "I reviewed and approve the complete batch table above.",
        key=review_key,
    )
    short_batch_confirmed = True
    if records and total_count < target:
        short_batch_confirmed = st.checkbox(
            "Confirm this batch below its target count.",
            key=f"draft_short_confirm_{generation}_{st.session_state.get('draft_editor_revision', 0)}",
        )

    can_commit = bool(
        records
        and not errors_by_row
        and reviewed
        and short_batch_confirmed
    )
    if st.button(
        "CONFIRM & ADD BATCH",
        type="primary",
        disabled=not can_commit,
        use_container_width=True,
        key=f"draft_final_confirm_{generation}",
    ):
        db_records = [
            {
                "scan_timestamp": record.get("scan_timestamp"),
                "barcode": record.get("barcode", ""),
                "bus_no": record.get("bus_no", ""),
                "mat_no": record.get("mat_no", ""),
                "vc_no": record.get("vc_no", ""),
                "color": record.get("color", ""),
                "order_no": record.get("order_no", ""),
                "seating": record.get("seating", ""),
                "chassis_no": record.get("chassis_no", ""),
                "vehicle_type": record.get("vehicle_type", ""),
                "source_file": record.get("source_file", ""),
                "image_evidence": record.get("image_bytes"),
                "pdi_clearance": record.get("pdi_clearance", "Pending"),
                "qa_book_status": record.get(
                    "qa_book_status",
                    "Not Received",
                ),
                "verification_status": "VERIFIED",
            }
            for record in records
        ]
        success, message = save_batch_atomically(meta, db_records)
        if success:
            committed_batch_id = meta["batch_id"]
            _clear_batch_draft()
            st.success(message)
            st.button(
                "View saved batch",
                type="primary",
                key="draft_view_saved_batch",
                on_click=_select_batch_after_draft,
                args=(committed_batch_id,),
            )
            return
        st.error(message)

    st.divider()
    with st.expander("Discard this unsaved draft", expanded=False):
        st.warning(
            "Discard this draft batch and its staged records? Previously saved "
            "database records will not be deleted."
        )
        discard_confirm = st.checkbox(
            "I understand this removes only the unsaved draft.",
            key=f"draft_discard_confirm_{generation}",
        )
        if st.button(
            "Discard Draft",
            disabled=not discard_confirm,
            key=f"draft_discard_button_{generation}",
        ):
            _clear_batch_draft()
            st.rerun()


# ============================================================
# APPLICATION HEADER
# ============================================================

st.markdown(
    """
    <div class="brand-ribbon">
        <span class="pill">TATA MOTORS BODY SOLUTIONS LIMITED</span>
       
    </div>
    """,
    unsafe_allow_html=True,
)

st.title(
    "🚌 TMBSL QA Book Scanning & Batch Tracking System"
)

st.caption(
    "Barcode-based QA Book digitization, Batch tracking and Excel reporting"
)


# ============================================================
# SIDEBAR - BATCH MANAGEMENT
# ============================================================

_auth_name = st.session_state.get("auth_name", "User")
_auth_role = st.session_state.get("auth_role", "")
st.sidebar.markdown(
    f"""
    <div class="user-chip">
        <div class="avatar">{_auth_name[:1].upper()}</div>
        <div class="who"><b>{_auth_name} </b><span>{_auth_role.upper()}</span></div>
    </div>
    """,
    unsafe_allow_html=True,
)
if st.sidebar.button("⏻ Sign out", use_container_width=True, key="logout_btn"):
    _logout()
    st.rerun()
st.sidebar.divider()

st.sidebar.header(
    "Batch Management"
)

batches = get_batches()


# ============================================================
# GENERATE EASY BATCH ID SUGGESTION
# ============================================================

now = datetime.now()

month_code = now.strftime("%b").upper()
year_code = now.strftime("%y")

existing_prefix = (
    f"{month_code}{year_code}-QA"
)

sequence = 1

for batch in batches:

    batch_id = batch["batch_id"]

    if batch_id.startswith(
        existing_prefix
    ):

        try:

            number_part = (
                batch_id.split("QA")[-1]
            )

            number = int(
                number_part
            )

            if number >= sequence:

                sequence = number + 1

        except (
            ValueError,
            IndexError
        ):

            pass


suggested_batch_id = (
    f"{existing_prefix}{sequence:02d}"
)


# ============================================================
# BATCH SELECTION
# ============================================================

all_batch_ids = sorted(
    b["batch_id"]
    for b in batches
)

batch_options = (
    ["Create New Batch"]
    + all_batch_ids
)

selected = st.sidebar.selectbox(
    "Select Batch",
    batch_options,
    key="batch_selection",
)

paused_draft = st.session_state.get("draft_batch_meta")
has_paused_draft = bool(
    paused_draft
    and not st.session_state.get("draft_batch_active")
)
if has_paused_draft:
    st.sidebar.info(
        f"Paused draft: {paused_draft['batch_id']} "
        f"({len(st.session_state.get('draft_batch_records', []))} staged)."
    )
    st.sidebar.button(
        f"Resume Draft {paused_draft['batch_id']}",
        on_click=_resume_batch_draft,
        key=f"resume_batch_draft_{st.session_state.get('_draft_generation', 0)}",
        use_container_width=True,
    )


# ============================================================
# CREATE NEW BATCH
# ============================================================

if (
    selected == "Create New Batch"
    and not st.session_state.get("draft_batch_active")
    and not has_paused_draft
):

    with st.sidebar.form(
        "create_batch"
    ):

        st.subheader(
            "➕ Create New Batch"
        )

        st.caption(
            "Recommended format: SEP26-QA01"
        )

        new_batch = st.text_input(
            "Batch ID",
            value=suggested_batch_id,
            placeholder="Example: SEP26-QA01"
        )

        target_books = st.number_input(
            "Books in this batch",
            min_value=1,
            max_value=1000,
            value=EXPECTED_BOOKS_DEFAULT,
            step=1,
            help=(
                "Starts at 30. Increase or decrease this for "
                "the batch you are creating."
            )
        )

        batch_date = st.date_input(
            "Batch Date"
        )

        observations = st.text_area(
            "Observations",
            placeholder=(
                "Any notes about this batch (optional) — e.g. "
                "shift, inspector name, special conditions..."
            ),
            height=80
        )

        create_submitted = (
        st.form_submit_button(
                "✓ Create Draft Batch",
                type="primary",
                use_container_width=True
            )
        )

        if create_submitted:

            new_batch = new_batch.strip().upper()

            if not new_batch:

                st.error(
                    "Batch ID is required."
                )

            elif len(new_batch) > 20:

                st.error(
                    "Batch ID must not exceed 20 characters."
                )

            else:

                if new_batch in all_batch_ids:
                    st.error("Batch ID already exists.")
                else:
                    _start_batch_draft(
                        new_batch,
                        str(batch_date),
                        observations.strip(),
                        target_books,
                        is_new=True,
                    )
                    st.rerun()


    active_batch = None


elif selected == "Create New Batch":

    active_batch = None
    if has_paused_draft:
        st.sidebar.warning(
            "Resume or discard the paused draft before starting another draft."
        )


else:

    active_batch = selected


# If a different batch is selected while a draft is paused, allow switching
# directly only when the paused draft has no staged records. This avoids
# silently overwriting unsaved QA Book entries.
paused_draft_records_count = len(
    st.session_state.get("draft_batch_records", [])
)

selected_is_different_from_paused_draft = bool(
    has_paused_draft
    and paused_draft
    and active_batch
    and active_batch != paused_draft.get("batch_id")
)

if (
    active_batch
    and selected_is_different_from_paused_draft
    and paused_draft_records_count == 0
):
    selected_batch_info = next(
        (
            item
            for item in batches
            if item["batch_id"] == active_batch
        ),
        {},
    )
    st.sidebar.caption(
        f"Selected batch: {active_batch}. The paused draft "
        f"{paused_draft.get('batch_id')} has no staged records."
    )
    if st.sidebar.button(
        f"Prepare Draft for {active_batch}",
        use_container_width=True,
        key=f"switch_draft_to_{active_batch}",
    ):
        _start_batch_draft(
            active_batch,
            selected_batch_info.get(
                "batch_date",
                str(datetime.now().date()),
            ),
            selected_batch_info.get("observations", ""),
            selected_batch_info.get(
                "target_books",
                EXPECTED_BOOKS_DEFAULT,
            ),
            is_new=False,
        )
        st.rerun()

elif active_batch and has_paused_draft:
    if selected_is_different_from_paused_draft and paused_draft_records_count:
        st.sidebar.warning(
            f"Draft {paused_draft.get('batch_id')} contains "
            f"{paused_draft_records_count} staged record(s). Resume it or "
            "discard it before preparing a different batch, so unsaved data "
            "is not lost."
        )
    else:
        st.sidebar.caption(
            "You can open this batch to review its dashboard. Resume or discard "
            "the paused draft before preparing another draft."
        )

elif active_batch and not st.session_state.get("draft_batch_active"):
    selected_batch_info = next(
        (
            item
            for item in batches
            if item["batch_id"] == active_batch
        ),
        {},
    )
    if st.sidebar.button(
        "Prepare Draft for Selected Batch",
        use_container_width=True,
        key="start_existing_batch_draft",
    ):
        _start_batch_draft(
            active_batch,
            selected_batch_info.get("batch_date", str(datetime.now().date())),
            selected_batch_info.get("observations", ""),
            selected_batch_info.get(
                "target_books",
                EXPECTED_BOOKS_DEFAULT,
            ),
            is_new=False,
        )
        st.rerun()


if st.session_state.get("draft_batch_active"):
    _render_batch_draft()
    st.stop()


# ============================================================
# DELETE BATCH
# ============================================================

if batches:

    st.sidebar.divider()

    st.sidebar.subheader(
        "🗑 Batch Administration"
    )

    delete_batch_id = (
        st.sidebar.selectbox(
            "Select Batch to Delete",
            [
                b["batch_id"]
                for b in batches
            ],
            key="delete_batch_select"
        )
    )

    st.sidebar.warning(
        "Deleting a batch permanently removes its "
        "batch records and scan logs."
    )

    delete_confirm = (
        st.sidebar.checkbox(
            "I understand this cannot be undone",
            key="delete_batch_confirm"
        )
    )

    if st.sidebar.button(
        "🗑 Delete Selected Batch",
        type="secondary",
        use_container_width=True
    ):

        if not delete_confirm:

            st.sidebar.error(
                "Please confirm deletion first."
            )

        else:

            ok, msg = delete_batch(
                delete_batch_id
            )

            if ok:

                st.sidebar.success(msg)

                st.rerun()

            else:

                st.sidebar.error(msg)


# ============================================================
# ACTIVE BATCH
# ============================================================

if active_batch:

    summary = get_batch_summary(
        active_batch
    )

    # ========================================================
    # DASHBOARD METRICS
    # ========================================================

    c1, c2, c3 = st.columns(3)

    c1.metric(
        "Scanned",
        summary["scanned"]
    )

    c2.metric(
        "PDI Cleared",
        summary["pdi_cleared"]
    )

    c3.metric(
        "QA Books Received",
        summary["received"]
    )

    # Show current configured target without changing
    # your three main dashboard metrics.
    active_batch_info = next(
        (
            batch
            for batch in batches
            if batch["batch_id"] == active_batch
        ),
        None
    )

    if active_batch_info:

        configured_target = active_batch_info.get(
            "Books per Batch",
            EXPECTED_BOOKS_DEFAULT
        )

        st.caption(
            f"Batch size configured: "
            f"**{configured_target} books**"
        )


    # ========================================================
    # TABS
    # ========================================================

    tabs = st.tabs(
        [
            "📷 Scan QA Book",
            "📋 Batch Records",
            "📊 Report",
            "📜 History"
        ]
    )


    # ========================================================
    # TAB 1 - SCAN QA BOOK
    # ========================================================

    with tabs[0]:

        if has_paused_draft:
            st.warning(
                f"Draft {paused_draft['batch_id']} is paused. Resume or discard "
                "it before scanning into a batch; this prevents new entries "
                "from being added to the wrong draft."
            )

        st.subheader(
            "📷 QA Book Data Entry"
        )


        # ====================================================
        # BUS-CHASSIS MAPPING FILE
        # ====================================================

        with st.expander(
            "📁 Bus–Chassis Mapping File",
            expanded=True
        ):

            st.caption(
                "Upload an Excel or CSV file containing "
                "Chassis No and Bus No. The application uses "
                "a VLOOKUP-style exact match: Chassis No → Bus No. "
                "The mapping is only used to assist data entry; "
                "it does not block saving."
            )

            mapping_file = st.file_uploader(
                "Upload Bus–Chassis Mapping",
                type=["xlsx", "csv"],
                key="bus_chassis_mapping_upload",
                help=(
                    "Recommended columns: "
                    "'Chassis No' and 'Bus No'."
                )
            )

            bus_lookup_mode = st.radio(
                "Bus Number entry",
                [
                    "Auto lookup from uploaded file",
                    "Enter manually",
                ],
                horizontal=True,
                key="bus_lookup_mode",
                help=(
                    "Auto lookup fills the Bus Number when the "
                    "Chassis No matches. You can edit the result; "
                    "manual entry remains available either way."
                ),
            )
            auto_bus_lookup_enabled = (
                bus_lookup_mode == "Auto lookup from uploaded file"
            )

            previous_lookup_mode = st.session_state.get(
                "_previous_bus_lookup_mode"
            )
            if previous_lookup_mode != bus_lookup_mode:
                if auto_bus_lookup_enabled:
                    # Re-check existing chassis values after enabling lookup.
                    st.session_state["manual_mapping_last_chassis"] = ""
                    st.session_state["ocr_mapping_last_chassis"] = ""
                else:
                    # Do not leave an auto-filled value looking authoritative
                    # after the operator switches to manual entry.
                    for bus_key, auto_key, mapped_key in (
                        (
                            "manual_bus_no",
                            "manual_bus_auto",
                            "manual_mapping_bus",
                        ),
                        (
                            "ocr_f_bus_no",
                            "ocr_bus_auto",
                            "ocr_mapping_bus",
                        ),
                    ):
                        if (
                            st.session_state.get(auto_key, False)
                            and st.session_state.get(bus_key, "")
                            == st.session_state.get(mapped_key, "")
                        ):
                            st.session_state[bus_key] = ""
                        st.session_state[auto_key] = False

                st.session_state["_previous_bus_lookup_mode"] = (
                    bus_lookup_mode
                )

            if mapping_file is not None:

                _load_mapping_into_session(
                    mapping_file
                )

            mapping_df = _get_mapping_df()

            mapping_error = st.session_state.get(
                "bus_mapping_error"
            )

            if mapping_error:

                st.error(
                    mapping_error
                )

            elif mapping_df is not None:

                mapping_name = (
                    st.session_state.get(
                        "bus_mapping_name",
                        "Mapping file"
                    )
                )

                st.success(
                    f"✓ Mapping loaded: "
                    f"{mapping_name} — "
                    f"{len(mapping_df):,} valid records"
                )

                st.caption(
                    "Columns used: Chassis No ↔ Bus No"
                )

                duplicate_bus_mappings = (
                    _get_duplicate_bus_mappings(mapping_df)
                )
                if not duplicate_bus_mappings.empty:
                    st.warning(
                        f"⚠ Duplicate Bus No alert: "
                        f"{len(duplicate_bus_mappings):,} Bus Number(s) "
                        "are assigned to multiple chassis in this file. "
                        "Review these mapping rows; lookup and manual "
                        "record saving remain available."
                    )
                    st.dataframe(
                        duplicate_bus_mappings.head(10),
                        use_container_width=True,
                        hide_index=True,
                    )
                    if len(duplicate_bus_mappings) > 10:
                        st.caption(
                            "Showing the first 10 duplicate Bus Numbers."
                        )

                map_col1, map_col2 = st.columns(2)

                with map_col1:

                    st.metric(
                        "Mapping Records",
                        len(mapping_df)
                    )

                with map_col2:

                    unique_chassis_count = (
                        mapping_df["Chassis No"]
                        .nunique()
                    )

                    st.metric(
                        "Unique Chassis",
                        unique_chassis_count
                    )

                if st.button(
                    "🗑 Clear Mapping File",
                    key="clear_mapping_file"
                ):

                    _clear_bus_mapping()

                    st.rerun()

            else:

                st.info(
                    "No mapping file uploaded. "
                    "You can still enter the Bus Number manually."
                )


        st.divider()


        # ====================================================
        # INPUT METHOD SWITCH
        # ====================================================

        input_mode = st.radio(
            "Choose Input Method",
            [
                "📷 Barcode Scanner / Manual Entry",
                "🖼 OCR — Capture / Upload Label Photo"
            ],
            horizontal=True,
            key="qa_input_mode"
        )

        st.divider()


        # ========================================================
        # MODE 1 — BARCODE SCANNER / MANUAL ENTRY
        # ========================================================

        if (
            input_mode
            == "📷 Barcode Scanner / Manual Entry"
        ):

            st.info(
                "Use a USB barcode scanner or type the barcode "
                "manually. All QA Book fields can be entered "
                "manually. If a Bus–Chassis mapping file is "
                "uploaded and the Chassis No matches, the Bus "
                "Number is filled automatically. If there is no "
                "match, enter the Bus Number manually."
            )


            # ----------------------------------------------------
            # CURRENT INPUT VALUES
            # ----------------------------------------------------

            manual_barcode = st.text_input(
                "Scan / Enter Barcode",
                placeholder=(
                    "Scan barcode here or type barcode manually"
                ),
                key="manual_barcode"
            )


            col1, col2 = st.columns(
                [1, 1]
            )


            # =================================================
            # LEFT COLUMN
            # =================================================

            with col1:

                st.subheader(
                    "1️⃣ QA Book Input"
                )

                manual_mat_no = st.text_input(
                    "MAT No",
                    placeholder="Enter MAT No",
                    key="manual_mat_no"
                )

                manual_vc_no = st.text_input(
                    "VC No",
                    placeholder="Enter VC No",
                    key="manual_vc_no"
                )

                manual_color = st.text_input(
                    "Color",
                    placeholder="Enter color",
                    key="manual_color"
                )

                manual_order_no = st.text_input(
                    "Order",
                    placeholder="Enter order number",
                    key="manual_order_no"
                )

                manual_seating = st.text_input(
                    "Seating",
                    placeholder="Enter seating",
                    key="manual_seating"
                )


            # =================================================
            # RIGHT COLUMN
            # =================================================

            with col2:

                st.subheader(
                    "2️⃣ Chassis & Bus Mapping"
                )

                manual_chassis_no = st.text_input(
                    "Chassis No",
                    placeholder="Enter Chassis No",
                    key="manual_chassis_no"
                )


                # ------------------------------------------------
                # AUTOMATIC CHASSIS -> BUS LOOKUP
                # ------------------------------------------------

                current_chassis = (
                    manual_chassis_no.strip()
                )

                previous_chassis = (
                    st.session_state.get(
                        "manual_mapping_last_chassis",
                        ""
                    )
                )

                if (
                    auto_bus_lookup_enabled
                    and current_chassis != previous_chassis
                ):

                    if mapping_df is not None:

                        found_bus, lookup_status = (
                            _lookup_bus_from_chassis(
                                mapping_df,
                                current_chassis
                            )
                        )

                        if lookup_status == "matched":

                            st.session_state[
                                "manual_bus_no"
                            ] = found_bus

                            st.session_state[
                                "manual_bus_auto"
                            ] = True

                            st.session_state[
                                "manual_mapping_bus"
                            ] = found_bus

                        elif lookup_status == "ambiguous":

                            if st.session_state.get(
                                "manual_bus_auto",
                                False
                            ):

                                st.session_state[
                                    "manual_bus_no"
                                ] = ""

                            st.session_state[
                                "manual_bus_auto"
                            ] = False

                        else:

                            if st.session_state.get(
                                "manual_bus_auto",
                                False
                            ):

                                st.session_state[
                                    "manual_bus_no"
                                ] = ""

                            st.session_state[
                                "manual_bus_auto"
                            ] = False

                    else:

                        if st.session_state.get(
                            "manual_bus_auto",
                            False
                        ):

                            st.session_state[
                                "manual_bus_no"
                            ] = ""

                        st.session_state[
                            "manual_bus_auto"
                        ] = False

                    st.session_state[
                        "manual_mapping_last_chassis"
                    ] = current_chassis


                manual_bus_no = st.text_input(
                    "Bus Number",
                    max_chars=6,
                    placeholder="4 digits for MCV, 6 for LCV",
                    key="manual_bus_no"
                )


                # ------------------------------------------------
                # DETECT MANUAL OVERRIDE
                # ------------------------------------------------

                if (
                    st.session_state.get(
                        "manual_bus_auto",
                        False
                    )
                    and manual_bus_no.strip()
                    != st.session_state.get(
                        "manual_mapping_bus",
                        ""
                    )
                ):

                    st.session_state[
                        "manual_bus_auto"
                    ] = False


                # ------------------------------------------------
                # BUS LOOKUP STATUS
                # ------------------------------------------------

                if not auto_bus_lookup_enabled:

                    st.info(
                        "Manual Bus Number entry selected. "
                        "Mapping lookup is off."
                    )

                elif st.session_state.get(
                    "manual_bus_auto",
                    False
                ):

                    st.success(
                        "✓ Matched from uploaded file. "
                        "Bus Number remains editable."
                    )

                elif current_chassis and mapping_df is None:

                    st.warning(
                        "⚠ Chassis No not found in mapping file. "
                        "Please enter Bus Number manually."
                    )

                elif current_chassis and mapping_df is not None:

                    found_bus, lookup_status = (
                        _lookup_bus_from_chassis(
                            mapping_df,
                            current_chassis
                        )
                    )

                    if lookup_status == "not_found":

                        st.warning(
                            "⚠ Chassis No not found in mapping file. "
                            "Please enter Bus Number manually."
                        )

                    elif lookup_status == "ambiguous":

                        st.error(
                            "⚠ Multiple Bus Numbers were found "
                            "for this Chassis No. Enter the correct "
                            "Bus Number manually."
                        )

                    elif lookup_status == "matched":

                        st.info(
                            f"Mapping match: "
                            f"Chassis {current_chassis} → "
                            f"Bus {found_bus}"
                        )

                else:

                    st.caption(
                        "Enter a Chassis No to look it up, or "
                        "enter the Bus Number manually."
                    )


                manual_pdi = st.selectbox(
                    "PDI Status",
                    [
                        "Yes",
                        "No",
                        "Pending",
                    ],
                    key="manual_pdi"
                )

                manual_book_status = st.selectbox(
                    "QA Book Status",
                    [
                        "Received",
                        "Not Received"
                    ],
                    key="manual_book_status"
                )


            st.divider()


            # ====================================================
            # STAGE BUTTON — records remain in session until final confirmation
            # ====================================================

            manual_submitted = st.button(
                "Add Book to Draft Batch",
                type="primary",
                use_container_width=True,
                key="manual_submit_button",
                disabled=has_paused_draft,
            )


            if manual_submitted:

                manual_barcode_clean = (
                    manual_barcode.strip()
                )

                manual_chassis_clean = (
                    manual_chassis_no.strip()
                )

                manual_bus_clean = (
                    manual_bus_no.strip()
                )

                manual_errors = []


                # ------------------------------------------------
                # BARCODE
                # ------------------------------------------------

                if not manual_barcode_clean:

                    manual_errors.append(
                        "Barcode / BC No is required."
                    )


                # ------------------------------------------------
                # BUS NUMBER
                # ------------------------------------------------

                manual_errors.extend(
                    _validate_bus_number(
                        manual_bus_clean
                    )
                )


                # ------------------------------------------------
                # GLOBAL DUPLICATE BUS NUMBER
                # ------------------------------------------------

                if manual_bus_clean:

                    conflicts = (
                        _get_global_bus_conflicts(
                            active_batch,
                            manual_bus_clean
                        )
                    )

                    if conflicts:

                        manual_errors.append(
                            f"Duplicate Alert: Bus Number "
                            f"'{manual_bus_clean}' has already "
                            f"been used in batch(es): "
                            f"{', '.join(conflicts)}. "
                            "A Bus Number cannot be scanned in "
                            "more than one batch."
                        )


                # ------------------------------------------------
                # SHOW ERRORS
                # ------------------------------------------------

                if manual_errors:

                    for error in manual_errors:

                        st.error(error)


                else:
                    if not st.session_state.get("draft_batch_meta"):
                        selected_batch_info = next(
                            (
                                item
                                for item in batches
                                if item["batch_id"] == active_batch
                            ),
                            {},
                        )
                        _start_batch_draft(
                            active_batch,
                            selected_batch_info.get(
                                "batch_date",
                                str(datetime.now().date()),
                            ),
                            selected_batch_info.get("observations", ""),
                            selected_batch_info.get(
                                "target_books",
                                EXPECTED_BOOKS_DEFAULT,
                            ),
                            is_new=False,
                        )

                    inferred_type = _lookup_vehicle_type_from_chassis(
                        mapping_df,
                        manual_chassis_clean,
                        manual_bus_clean,
                    )
                    _stage_draft_record(
                        {
                            "source_file": "Manual entry",
                            "barcode": manual_barcode_clean,
                            "bus_no": manual_bus_clean,
                            "mat_no": manual_mat_no.strip(),
                            "vc_no": manual_vc_no.strip(),
                            "color": manual_color.strip(),
                            "order_no": manual_order_no.strip(),
                            "seating": manual_seating.strip(),
                            "chassis_no": manual_chassis_clean,
                            "vehicle_type": inferred_type,
                            "pdi_clearance": manual_pdi,
                            "qa_book_status": manual_book_status,
                            "verification_status": "READY",
                        }
                    )
                    st.session_state["draft_batch_active"] = True
                    st.rerun()


        # ========================================================
        # MODE 2 — OCR
        # ========================================================

        else:

            st.info(
                "Capture or upload a photo of the QA Book label. "
                "OCR will read the Barcode, Chassis No, TMBSL VC "
                "No, Color, Order and Seating. Review/correct the "
                "fields before saving. If the uploaded Bus–Chassis "
                "mapping contains the extracted Chassis No, the "
                "Bus Number will be filled automatically. "
                "Otherwise, enter the Bus Number manually."
            )


            # ----------------------------------------------------
            # OCR DEPENDENCIES MISSING
            # ----------------------------------------------------

            if not OCR_AVAILABLE:

                st.error(
                    "OCR dependencies are not installed on this "
                    "server, so label-photo scanning isn't "
                    "available yet."
                )

                st.code(
                    "pip install pytesseract opencv-python-headless "
                    "Pillow pillow-heif numpy\n"
                    "# plus the system package:\n"
                    "sudo apt-get install tesseract-ocr",
                    language="bash"
                )

                st.caption(
                    f"Import error: "
                    f"{OCR_IMPORT_ERROR}"
                )


            # ----------------------------------------------------
            # OCR AVAILABLE
            # ----------------------------------------------------

            else:

                ocr_col1, ocr_col2 = (
                    st.columns([1.5, 1])
                )


                # =================================================
                # LEFT COLUMN — IMAGE
                # =================================================

                with ocr_col1:

                    st.subheader(
                        "1️⃣ Label Photo"
                    )

                    image_source = st.radio(
                        "Image Source",
                        [
                            "Use Camera",
                            "Upload Photo"
                        ],
                        horizontal=True,
                        key="ocr_image_source"
                    )


                    if image_source == "Use Camera":

                        st.caption(
                            "📸 For the best one-shot read: fill "
                            "the frame with the label, wait a "
                            "moment for the camera to focus, and "
                            "avoid glare/shadows across the text. "
                            "A slight camera angle is fine — it's "
                            "corrected automatically."
                        )

                        image_file = st.camera_input(
                            "Capture QA Book Label",
                            key="ocr_camera_input",
                            resolution="1080p",
                        )

                    else:

                        image_file = st.file_uploader(
                            "Upload QA Book Label Photo",
                            type=[
                                "jpg",
                                "jpeg",
                                "png",
                                "heic",
                                "heif",
                                "webp",
                                "tif",
                                "tiff",
                            ],
                            key="ocr_file_uploader"
                        )


                    # ------------------------------------------------
                    # OCR HASH / CACHE
                    # ------------------------------------------------

                    ocr_data = None

                    if image_file is not None:

                        image_bytes = (
                            image_file.getvalue()
                        )

                        image_hash = hashlib.md5(
                            image_bytes
                        ).hexdigest()

                        is_heif_image = getattr(
                            image_file,
                            "name",
                            ""
                        ).lower().endswith((".heic", ".heif"))

                        if is_heif_image and not HEIF_AVAILABLE:

                            st.error(
                                "HEIC/HEIF decoding isn't installed. "
                                "Install the updated requirements with "
                                "`pip install -r requirements.txt` and "
                                "restart the app, or upload a JPG/PNG."
                            )
                            st.session_state.pop(
                                "ocr_image_hash",
                                None
                            )
                            st.session_state["ocr_extracted"] = None
                            is_new_image = False

                        else:

                            is_new_image = (
                                st.session_state.get(
                                    "ocr_image_hash"
                                )
                                != image_hash
                            )


                        # ------------------------------------------------
                        # QUALITY CHECK
                        # ------------------------------------------------

                        if is_new_image:

                            quality = (
                                estimate_image_quality(
                                    image_bytes
                                )
                            )

                            if quality["is_blurry"]:

                                st.warning(
                                    "⚠ This photo looks a bit "
                                    "blurry — hold the camera "
                                    "steady, make sure it is "
                                    "focused on the label, and "
                                    "retake if needed."
                                )

                            if quality["is_low_res"]:

                                st.warning(
                                    "⚠ This photo is fairly low "
                                    "resolution "
                                    f"({quality['width']}×"
                                    f"{quality['height']}px). "
                                    "Move closer to the label, "
                                    "or use a higher camera "
                                    "quality."
                                )


                        # ------------------------------------------------
                        # RUN OCR
                        # ------------------------------------------------

                        if is_new_image:

                            with st.spinner(
                                "Extracting label data... this "
                                "runs several accuracy passes "
                                "and usually takes 5-15 seconds "
                                "(harder photos may take up to 30s)."
                            ):

                                try:

                                    ocr_data = (
                                        extract_label_fields(
                                            image_bytes
                                        )
                                    )

                                    st.session_state[
                                        "ocr_image_hash"
                                    ] = image_hash

                                    st.session_state[
                                        "ocr_extracted"
                                    ] = ocr_data


                                    # ------------------------------------------------
                                    # PUSH OCR RESULTS INTO WIDGET STATE
                                    # ------------------------------------------------

                                    st.session_state[
                                        "ocr_f_barcode"
                                    ] = (
                                        ocr_data.get(
                                            "barcode",
                                            ""
                                        )
                                    )

                                    st.session_state[
                                        "ocr_f_mat_no"
                                    ] = ""

                                    st.session_state[
                                        "ocr_f_vc_no"
                                    ] = (
                                        ocr_data.get(
                                            "vc_no",
                                            ""
                                        )
                                    )

                                    st.session_state[
                                        "ocr_f_color"
                                    ] = (
                                        ocr_data.get(
                                            "color",
                                            ""
                                        )
                                    )

                                    st.session_state[
                                        "ocr_f_order_no"
                                    ] = (
                                        ocr_data.get(
                                            "order_no",
                                            ""
                                        )
                                    )

                                    st.session_state[
                                        "ocr_f_seating"
                                    ] = (
                                        ocr_data.get(
                                            "seating",
                                            ""
                                        )
                                    )

                                    st.session_state[
                                        "ocr_f_chassis_no"
                                    ] = (
                                        ocr_data.get(
                                            "chassis_no",
                                            ""
                                        )
                                    )

                                    st.session_state[
                                        "ocr_mapping_last_chassis"
                                    ] = ""

                                    st.session_state[
                                        "ocr_bus_auto"
                                    ] = False

                                    st.session_state[
                                        "ocr_mapping_bus"
                                    ] = ""

                                    st.session_state[
                                        "ocr_f_bus_no"
                                    ] = ""


                                except Exception as ocr_error:

                                    st.error(
                                        "✗ OCR extraction failed: "
                                        f"{ocr_error}"
                                    )

                                    st.session_state[
                                        "ocr_extracted"
                                    ] = None


                        ocr_data = (
                            st.session_state.get(
                                "ocr_extracted"
                            )
                        )


                        if ocr_data:

                            st.success(
                                "✓ Label scanned. Review the "
                                "extracted fields on the right."
                            )


                            _tilt_corrected = (
                                ocr_data.get(
                                    "tilt_corrected_degrees",
                                    0.0
                                )
                            )

                            if abs(
                                _tilt_corrected
                            ) > 0.01:

                                st.caption(
                                    "📐 Photo was held at an "
                                    "angle — automatically "
                                    "straightened "
                                    f"(~{abs(_tilt_corrected):.0f}°) "
                                    "before reading."
                                )


                            _barcode_source = (
                                ocr_data.get(
                                    "barcode_source",
                                    "none"
                                )
                            )

                            if (
                                _barcode_source
                                == "symbol"
                            ):

                                st.caption(
                                    "🔵 Barcode decoded directly "
                                    "from the barcode symbol."
                                )

                            elif (
                                _barcode_source
                                == "text"
                            ):

                                st.caption(
                                    "🟡 Barcode read from the "
                                    "'BC No' text on the label. "
                                    "Please double-check it."
                                )

                            else:

                                st.caption(
                                    "⚪ No barcode value could "
                                    "be read automatically — "
                                    "please enter it manually."
                                )


                            _summary_rows = [
                                (
                                    "chassis_no",
                                    ocr_data.get(
                                        "chassis_no",
                                        ""
                                    )
                                ),
                                (
                                    "vc_no",
                                    ocr_data.get(
                                        "vc_no",
                                        ""
                                    )
                                ),
                                (
                                    "color",
                                    ocr_data.get(
                                        "color",
                                        ""
                                    )
                                ),
                                (
                                    "order_no",
                                    ocr_data.get(
                                        "order_no",
                                        ""
                                    )
                                ),
                                (
                                    "seating",
                                    ocr_data.get(
                                        "seating",
                                        ""
                                    )
                                ),
                                (
                                    "barcode",
                                    ocr_data.get(
                                        "barcode",
                                        ""
                                    )
                                ),
                            ]

                            _label_width = max(
                                len(label)
                                for label, _ in _summary_rows
                            )

                            _summary_lines = [
                                (
                                    f"{label.ljust(_label_width)} : "
                                    f"{value if value else '(not found)'}"
                                )
                                for label, value
                                in _summary_rows
                            ]

                            st.markdown(
                                "**📋 Extracted Fields Summary**"
                            )

                            st.code(
                                "\n".join(
                                    _summary_lines
                                ),
                                language=None
                            )


                            with st.expander(
                                "🔎 Raw OCR Text (for reference)"
                            ):

                                st.text(
                                    ocr_data.get(
                                        "raw_text",
                                        ""
                                    )
                                    or "(no text detected)"
                                )

                    else:

                        st.session_state.pop(
                            "ocr_image_hash",
                            None
                        )

                        st.session_state.pop(
                            "ocr_extracted",
                            None
                        )

                        st.info(
                            "Capture or upload a label photo "
                            "to begin."
                        )


                # =================================================
                # RIGHT COLUMN — EXTRACTED FIELDS
                # =================================================

                with ocr_col2:

                    st.subheader(
                        "2️⃣ Review & Confirm Extracted Data"
                    )

                    ocr_barcode = st.text_input(
                        "Barcode",
                        key="ocr_f_barcode"
                    )

                    ocr_mat_no = st.text_input(
                        "MAT No",
                        key="ocr_f_mat_no",
                        help=(
                            "Enter MAT No manually if known."
                        )
                    )

                    ocr_vc_no = st.text_input(
                        "TMBSL VC No",
                        key="ocr_f_vc_no"
                    )

                    ocr_color = st.text_input(
                        "Color",
                        key="ocr_f_color"
                    )

                    ocr_order_no = st.text_input(
                        "Order",
                        key="ocr_f_order_no"
                    )

                    ocr_seating = st.text_input(
                        "Seating",
                        key="ocr_f_seating"
                    )

                    ocr_chassis_no = st.text_input(
                        "Chassis No",
                        key="ocr_f_chassis_no"
                    )


                    # ------------------------------------------------
                    # AUTOMATIC CHASSIS -> BUS LOOKUP
                    # ------------------------------------------------

                    current_ocr_chassis = (
                        ocr_chassis_no.strip()
                    )

                    previous_ocr_chassis = (
                        st.session_state.get(
                            "ocr_mapping_last_chassis",
                            ""
                        )
                    )

                    if (
                        auto_bus_lookup_enabled
                        and current_ocr_chassis != previous_ocr_chassis
                    ):

                        if mapping_df is not None:

                            found_bus, lookup_status = (
                                _lookup_bus_from_chassis(
                                    mapping_df,
                                    current_ocr_chassis
                                )
                            )

                            if lookup_status == "matched":

                                st.session_state[
                                    "ocr_f_bus_no"
                                ] = found_bus

                                st.session_state[
                                    "ocr_bus_auto"
                                ] = True

                                st.session_state[
                                    "ocr_mapping_bus"
                                ] = found_bus

                            else:

                                if st.session_state.get(
                                    "ocr_bus_auto",
                                    False
                                ):

                                    st.session_state[
                                        "ocr_f_bus_no"
                                    ] = ""

                                st.session_state[
                                    "ocr_bus_auto"
                                ] = False

                        else:

                            if st.session_state.get(
                                "ocr_bus_auto",
                                False
                            ):

                                st.session_state[
                                    "ocr_f_bus_no"
                                ] = ""

                            st.session_state[
                                "ocr_bus_auto"
                            ] = False

                        st.session_state[
                            "ocr_mapping_last_chassis"
                        ] = current_ocr_chassis


                st.divider()


                # =================================================
                # BUS NUMBER + STATUS
                # =================================================

                st.subheader(
                    "3️⃣ Bus Number & Status"
                )

                ocr_bus_col1, ocr_bus_col2, ocr_bus_col3 = (
                    st.columns([1, 1, 1])
                )

                with ocr_bus_col1:

                    ocr_bus_no = st.text_input(
                        "Bus Number",
                        max_chars=6,
                    placeholder="4 digits for MCV, 6 for LCV",
                        key="ocr_f_bus_no"
                    )

                    if not auto_bus_lookup_enabled:

                        st.info(
                            "Manual Bus Number entry selected. "
                            "Mapping lookup is off."
                        )

                    elif st.session_state.get(
                        "ocr_bus_auto",
                        False
                    ):

                        st.success(
                            "✓ Matched from uploaded file. "
                            "Bus Number remains editable."
                        )

                    elif (
                        current_ocr_chassis
                        and mapping_df is None
                    ):

                        st.warning(
                            "⚠ Chassis No not found in mapping file. "
                            "Please enter Bus Number manually."
                        )

                    elif (
                        current_ocr_chassis
                        and mapping_df is not None
                    ):

                        found_bus, lookup_status = (
                            _lookup_bus_from_chassis(
                                mapping_df,
                                current_ocr_chassis
                            )
                        )

                        if lookup_status == "not_found":

                            st.warning(
                                "⚠ Chassis No not found in mapping file. "
                                "Please enter Bus Number manually."
                            )

                        elif lookup_status == "ambiguous":

                            st.error(
                                "⚠ Multiple Bus Numbers were "
                                "found for this Chassis No. "
                                "Enter the correct Bus Number "
                                "manually."
                            )

                with ocr_bus_col2:

                    ocr_pdi = st.selectbox(
                        "PDI Status",
                        [
                            "Yes",
                            "No",
                            "Pending",
                        ],
                        key="ocr_f_pdi"
                    )

                with ocr_bus_col3:

                    ocr_book_status = st.selectbox(
                        "QA Book Status",
                        [
                            "Received",
                            "Not Received"
                        ],
                        key="ocr_f_book_status"
                    )


                # ------------------------------------------------
                # DETECT MANUAL OVERRIDE
                # ------------------------------------------------

                if (
                    st.session_state.get(
                        "ocr_bus_auto",
                        False
                    )
                    and ocr_bus_no.strip()
                    != st.session_state.get(
                        "ocr_mapping_bus",
                        ""
                    )
                ):

                    st.session_state[
                        "ocr_bus_auto"
                    ] = False


                # =================================================
                # SAVE OCR
                # =================================================

                ocr_submitted = st.button(
                    "Add Book to Draft Batch",
                    type="primary",
                    use_container_width=True,
                    key="ocr_submit_button",
                    disabled=has_paused_draft,
                )


                if ocr_submitted:

                    ocr_barcode_clean = (
                        ocr_barcode.strip()
                    )

                    ocr_bus_no_clean = (
                        ocr_bus_no.strip()
                    )

                    ocr_errors = []


                    # ------------------------------------------------
                    # BARCODE
                    # ------------------------------------------------

                    if not ocr_barcode_clean:

                        ocr_errors.append(
                            "Barcode could not be read — "
                            "please type it in manually."
                        )


                    # ------------------------------------------------
                    # BUS
                    # ------------------------------------------------

                    ocr_errors.extend(
                        _validate_bus_number(
                            ocr_bus_no_clean
                        )
                    )


                    # ------------------------------------------------
                    # GLOBAL DUPLICATE
                    # ------------------------------------------------

                    if ocr_bus_no_clean:

                        conflicts = (
                            _get_global_bus_conflicts(
                                active_batch,
                                ocr_bus_no_clean
                            )
                        )

                        if conflicts:

                            ocr_errors.append(
                                "Duplicate Alert: Bus Number "
                                f"'{ocr_bus_no_clean}' has "
                                "already been used in batch(es): "
                                f"{', '.join(conflicts)}. "
                                "A Bus Number cannot be scanned "
                                "in more than one batch."
                            )


                    # ------------------------------------------------
                    # SHOW ERRORS
                    # ------------------------------------------------

                    if ocr_errors:

                        for error in ocr_errors:

                            st.error(error)


                    else:
                        if not st.session_state.get("draft_batch_meta"):
                            selected_batch_info = next(
                                (
                                    item
                                    for item in batches
                                    if item["batch_id"] == active_batch
                                ),
                                {},
                            )
                            _start_batch_draft(
                                active_batch,
                                selected_batch_info.get(
                                    "batch_date",
                                    str(datetime.now().date()),
                                ),
                                selected_batch_info.get("observations", ""),
                                selected_batch_info.get(
                                    "target_books",
                                    EXPECTED_BOOKS_DEFAULT,
                                ),
                                is_new=False,
                            )

                        source_bytes = (
                            image_file.getvalue()
                            if image_file is not None
                            else None
                        )
                        source_name = (
                            getattr(image_file, "name", "camera_capture.jpg")
                            if image_file is not None
                            else "OCR input"
                        )
                        source_hash = (
                            hashlib.sha256(source_bytes).hexdigest()
                            if source_bytes
                            else ""
                        )
                        inferred_type = _lookup_vehicle_type_from_chassis(
                            mapping_df,
                            ocr_chassis_no.strip(),
                            ocr_bus_no_clean,
                        )
                        _stage_draft_record(
                            {
                                "source_file": source_name,
                                "source_hash": source_hash,
                                "image_bytes": source_bytes,
                                "barcode": ocr_barcode_clean,
                                "bus_no": ocr_bus_no_clean,
                                "mat_no": ocr_mat_no.strip(),
                                "vc_no": ocr_vc_no.strip(),
                                "color": ocr_color.strip(),
                                "order_no": ocr_order_no.strip(),
                                "seating": ocr_seating.strip(),
                                "chassis_no": ocr_chassis_no.strip(),
                                "vehicle_type": inferred_type,
                                "pdi_clearance": ocr_pdi,
                                "qa_book_status": ocr_book_status,
                                "verification_status": "READY",
                            }
                        )
                        st.session_state["draft_batch_active"] = True
                        st.rerun()


    # ============================================================
    # TAB 2 - BATCH RECORDS
    # ============================================================

    with tabs[1]:

        st.subheader(
            f"📋 Batch Records — {active_batch}"
        )

        edit_df = get_batch_records_for_edit(
            active_batch
        )


        if edit_df.empty:

            st.info(
                "No QA Books scanned in this batch yet."
            )

        else:

            st.caption(
                "Edit **PDI Status** or **QA Book Status** "
                "directly in the table below, or tick "
                "**🗑 Delete?** to remove a record scanned "
                "in error. Click **💾 Save Changes** when done."
            )


            display_df = edit_df.copy()

            display_df["🗑 Delete?"] = False

            edited_df = st.data_editor(
                display_df,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                key="batch_records_editor",
                column_order=[
                    "🗑 Delete?",
                    "Scan Time",
                    "Source File",
                    "Barcode",
                    "Bus No",
                    "Vehicle Type",
                    "VC No",
                    "Color",
                    "Order",
                    "Seating",
                    "Chassis No",
                    "PDI Status",
                    "QA Book Status"
                ],
                disabled=[
                    "Scan Time",
                    "Source File",
                    "Barcode",
                    "Bus No",
                    "Vehicle Type",
                    "VC No",
                    "Color",
                    "Order",
                    "Seating",
                    "Chassis No"
                ],
                column_config={
                    "🗑 Delete?":
                        st.column_config.CheckboxColumn(
                            default=False
                        ),
                    "PDI Status":
                        st.column_config.SelectboxColumn(
                            options=[
                                "Yes",
                                "No",
                                "Pending",
                            ],
                            required=True
                        ),
                    "QA Book Status":
                        st.column_config.SelectboxColumn(
                            options=[
                                "Received",
                                "Not Received"
                            ],
                            required=True
                        )
                }
            )


            save_col, _spacer_col = (
                st.columns([1, 3])
            )

            with save_col:

                save_clicked = st.button(
                    "💾 Save Changes",
                    type="primary",
                    use_container_width=True
                )


            if save_clicked:

                updates_made = 0
                deletes_made = 0
                save_errors = []

                for row_index in range(
                    len(edit_df)
                ):

                    original_row = (
                        edit_df.iloc[row_index]
                    )

                    edited_row = (
                        edited_df.iloc[row_index]
                    )

                    record_id = int(
                        original_row["_id"]
                    )


                    # ------------------------------------------------
                    # DELETE
                    # ------------------------------------------------

                    if bool(
                        edited_row["🗑 Delete?"]
                    ):

                        ok, msg = delete_record(
                            record_id
                        )

                        if ok:

                            deletes_made += 1

                        else:

                            save_errors.append(
                                msg
                            )

                        continue


                    # ------------------------------------------------
                    # UPDATE PDI / QA BOOK STATUS
                    # ------------------------------------------------

                    pdi_changed = (
                        edited_row["PDI Status"]
                        != original_row["PDI Status"]
                    )

                    qa_status_changed = (
                        edited_row["QA Book Status"]
                        != original_row["QA Book Status"]
                    )

                    if (
                        pdi_changed
                        or qa_status_changed
                    ):

                        ok, msg = (
                            update_record_fields(
                                record_id,
                                pdi_clearance=(
                                    edited_row["PDI Status"]
                                    if pdi_changed
                                    else None
                                ),
                                qa_book_status=(
                                    edited_row["QA Book Status"]
                                    if qa_status_changed
                                    else None
                                )
                            )
                        )

                        if ok:

                            updates_made += 1

                        else:

                            save_errors.append(
                                msg
                            )


                if (
                    updates_made
                    or deletes_made
                ):

                    summary_parts = []

                    if updates_made:

                        summary_parts.append(
                            f"{updates_made} record(s) updated"
                        )

                    if deletes_made:

                        summary_parts.append(
                            f"{deletes_made} record(s) deleted"
                        )

                    st.success(
                        "✓ Saved: "
                        + ", ".join(summary_parts)
                        + "."
                    )


                for error_msg in save_errors:

                    st.error(
                        error_msg
                    )


                if (
                    (updates_made or deletes_made)
                    and not save_errors
                ):

                    st.rerun()

                elif not (
                    updates_made
                    or deletes_made
                    or save_errors
                ):

                    st.info(
                        "No changes to save."
                    )


            st.write(
                f"Total QA Books: **{len(edit_df)}**"
            )


    # ============================================================
    # TAB 3 - REPORT
    # ============================================================

    with tabs[2]:

        st.subheader(
            "📊 Batch Excel Report"
        )

        df = get_batch_records(
            active_batch
        )


        if df.empty:

            st.info(
                "Scan or manually enter at least one "
                "QA Book before generating the report."
            )

        else:

            st.write(
                "Generate a formatted Excel workbook "
                "containing the QA register, detailed "
                "records and batch summary."
            )

            excel_bytes = generate_excel(
                df,
                active_batch,
                summary
            )

            st.download_button(
                label="⬇ Download Excel Report",
                data=excel_bytes,
                file_name=(
                    f"{active_batch}_QA_Report.xlsx"
                ),
                mime=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                type="primary",
                use_container_width=True
            )


    # ============================================================
    # TAB 4 - HISTORY
    # ============================================================

    with tabs[3]:

        st.subheader(
            "📜 Batch History"
        )

        st.caption(
            "Every batch ever created, grouped by Month - Year. "
            "Type any part of a Batch ID to search."
        )


        # --------------------------------------------------------
        # SEARCH HISTORY
        # --------------------------------------------------------

        history_search = (
            st.text_input(
                "🔍 Search History (Batch ID)",
                placeholder=(
                    "Type any part of a Batch ID "
                    "e.g. 'a', 'au', '26'"
                ),
                key="history_search"
            )
            .strip()
            .upper()
        )


        # --------------------------------------------------------
        # ALL BATCHES CREATED
        # --------------------------------------------------------

        st.markdown(
            "#### All Batches Created"
        )

        history_df = get_all_batch_history()


        if not history_df.empty:

            history_df = (
                history_df
                .drop_duplicates(
                    subset=["Batch ID"],
                    keep="last"
                )
            )


        if (
            history_search
            and not history_df.empty
        ):

            history_df = history_df[
                history_df["Batch ID"]
                .astype(str)
                .str.upper()
                .str.contains(
                    history_search,
                    na=False
                )
            ]


        if history_df.empty:

            st.info(
                "No batches match your search."
                if history_search
                else
                "No batches have been created yet."
            )

        else:

            parsed_dates = pd.to_datetime(
                history_df["Batch Date"],
                errors="coerce"
            )

            history_df = history_df.assign(
                _sort_date=parsed_dates,
                **{
                    "Month - Year":
                        parsed_dates.dt.strftime(
                            "%B %Y"
                        )
                }
            )

            history_df["Month - Year"] = (
                history_df[
                    "Month - Year"
                ]
                .fillna("Unknown")
            )

            history_df["_sort_date"] = (
                history_df["_sort_date"]
                .fillna(
                    pd.Timestamp.min
                )
            )


            st.metric(
                "Overall Total Batches",
                len(history_df)
            )


            month_summary = (
                history_df
                .groupby(
                    "Month - Year",
                    as_index=False
                )
                .agg(
                    **{
                        "No. of Batches":
                            ("Batch ID", "count"),
                        "_sort_date":
                            ("_sort_date", "max")
                    }
                )
                .sort_values(
                    "_sort_date",
                    ascending=False
                )
                .drop(
                    columns="_sort_date"
                )
            )


            st.markdown(
                "###### 📅 Batch Count by Month - Year"
            )

            st.dataframe(
                month_summary,
                use_container_width=True,
                hide_index=True
            )


            st.divider()


            st.markdown(
                "###### 📋 Batch History"
            )

            st.caption(
                "Change Books per Batch with the arrows "
                "or by typing a value, then save your changes."
            )


            editable_history_df = (
                history_df
                .sort_values(
                    [
                        "_sort_date",
                        "Batch ID"
                    ],
                    ascending=[
                        False,
                        True
                    ]
                )
                .drop(
                    columns="_sort_date"
                )
                .reset_index(
                    drop=True
                )
            )


            editable_history_df[
                "Books per Batch"
            ] = (
                pd.to_numeric(
                    editable_history_df[
                        "Books per Batch"
                    ],
                    errors="coerce"
                )
                .fillna(
                    EXPECTED_BOOKS_DEFAULT
                )
                .astype(int)
            )


            history_editor_key = (
                f"history_batch_editor_"
                f"{history_search or 'all'}"
            )


            edited_history_df = (
                st.data_editor(
                    editable_history_df,
                    column_config={
                        "Books per Batch":
                            st.column_config.NumberColumn(
                                "Books per Batch",
                                min_value=1,
                                max_value=1000,
                                step=1,
                                help=(
                                    "Set the book count "
                                    "for this batch."
                                )
                            )
                    },
                    disabled=[
                        column
                        for column
                        in editable_history_df.columns
                        if column
                        != "Books per Batch"
                    ],
                    num_rows="fixed",
                    use_container_width=True,
                    hide_index=True,
                    key=history_editor_key
                )
            )


            if st.button(
                "💾 Save book-count changes",
                type="primary",
                key=f"save_{history_editor_key}"
            ):

                original_counts = (
                    editable_history_df
                    .set_index(
                        "Batch ID"
                    )[
                        "Books per Batch"
                    ]
                )

                changes = []


                for _, edited_row in (
                    edited_history_df.iterrows()
                ):

                    batch_id = str(
                        edited_row["Batch ID"]
                    )

                    try:

                        new_count = int(
                            edited_row[
                                "Books per Batch"
                            ]
                        )

                    except (
                        TypeError,
                        ValueError
                    ):

                        st.error(
                            f"Enter a whole-number book count "
                            f"for batch {batch_id}."
                        )

                        st.stop()


                    if not (
                        1
                        <= new_count
                        <= 1000
                    ):

                        st.error(
                            "Books per batch must be "
                            "between 1 and 1,000."
                        )

                        st.stop()


                    if (
                        new_count
                        != int(
                            original_counts.loc[
                                batch_id
                            ]
                        )
                    ):

                        changes.append(
                            (
                                batch_id,
                                new_count
                            )
                        )


                if not changes:

                    st.info(
                        "No book-count changes to save."
                    )

                else:

                    update_errors = []

                    for (
                        batch_id,
                        new_count
                    ) in changes:

                        ok, message = (
                            update_batch_target_books(
                                batch_id,
                                new_count
                            )
                        )

                        if not ok:

                            update_errors.append(
                                message
                            )


                    if update_errors:

                        for message in (
                            update_errors
                        ):

                            st.error(
                                message
                            )

                    else:

                        st.success(
                            f"Updated book counts for "
                            f"{len(changes)} batch"
                            f"{'es' if len(changes) != 1 else ''}."
                        )

                        st.rerun()


        st.divider()


        # ========================================================
        # BUS NO ONLY SHEET
        # ========================================================

        st.markdown(
            "#### 🚌 Bus No Only Sheet"
        )

        bus_search = (
            st.text_input(
                "🔍 Search Bus No Sheet "
                "(Batch ID or Bus No)",
                placeholder=(
                    "Type a Batch ID or Bus Number to search"
                ),
                key="bus_search"
            )
            .strip()
            .upper()
        )

        effective_bus_search = (
            bus_search
            or history_search
        )


        if not effective_bus_search:

            st.info(
                "Type in the search bar above "
                "(or the 'Search History' bar) "
                "to view Bus No records."
            )

        else:

            bus_only_df = (
                get_bus_numbers_only()
            )

            bus_only_df = bus_only_df[
                bus_only_df["Batch ID"]
                .astype(str)
                .str.upper()
                .str.contains(
                    effective_bus_search,
                    na=False
                )
                |
                bus_only_df["Bus No"]
                .astype(str)
                .str.upper()
                .str.contains(
                    effective_bus_search,
                    na=False
                )
            ]


            if bus_only_df.empty:

                st.info(
                    "No bus numbers match your search."
                )

            else:

                st.dataframe(
                    bus_only_df,
                    use_container_width=True,
                    hide_index=True
                )

                st.write(
                    f"Total Bus Numbers: "
                    f"**{len(bus_only_df)}**"
                )


        st.divider()


        # ========================================================
        # HISTORICAL CROSS-BATCH DUPLICATES
        # ========================================================

        st.markdown(
            "#### 🔁 Cross-Batch Duplicate Bus Numbers"
        )

        st.caption(
            "New scans are blocked from reusing a Bus Number "
            "already used in another batch. This section shows "
            "historical duplicates that may have existed before "
            "that check was introduced."
        )

        duplicate_groups = (
            find_existing_duplicate_groups()
        )

        has_bus_no_dupes = bool(
            duplicate_groups["bus_no"]
        )


        if not has_bus_no_dupes:

            st.success(
                "✓ No cross-batch duplicate Bus Numbers found."
            )

        else:

            bus_no_dupe_df = pd.DataFrame(
                [
                    {
                        "Bus No":
                            group["value"],
                        "Batches":
                            ", ".join(
                                group["batches"]
                            ),
                        "# Batches":
                            len(
                                group["batches"]
                            )
                    }
                    for group
                    in duplicate_groups[
                        "bus_no"
                    ]
                ]
            )

            st.dataframe(
                bus_no_dupe_df,
                use_container_width=True,
                hide_index=True
            )


# ================================================================
# NO ACTIVE BATCH
# ================================================================

else:

    st.header(
        "Getting Started"
    )


    st.markdown(
        """
        ### TMBSL QA Book Digitization Workflow

        **1. Create a batch**

        Create a batch containing the required number of QA Books.
        The default batch size is normally 30.

        **2. Choose an input method**

        Use any one of:

        - USB Barcode Scanner
        - Manual Entry
        - OCR Camera Capture
        - OCR Photo Upload

        **3. QA Book Data**

        Enter or capture:

        - Barcode
        - MAT No
        - VC No
        - Color
        - Order
        - Seating
        - Chassis No

        **4. Bus Number**

        Upload a Bus–Chassis mapping Excel/CSV file.

        When the Chassis No matches the mapping file, the
        Bus Number is automatically filled.

        If there is no mapping file or no Chassis match,
        the Bus Number can be entered manually.

        **5. Select Status**

        Select:

        - PDI Status
        - QA Book Status

        **6. Confirm & Save**

        The system saves the entered/captured values directly
        into the batch. Master Data is not required for saving.

        **7. Monitor Batch**

        Track:

        - Scanned QA Books
        - PDI Cleared
        - QA Books Received

        **8. Download Excel**

        Generate and download the batch-wise Excel report.

        **9. Review History**

        Search and browse every batch and Bus Number history.
        """
    )


    st.warning(
        "Please create or select a batch from the sidebar "
        "to begin. Click the 'Select Batch' box and type "
        "any part of a Batch ID to quickly find it."
    )