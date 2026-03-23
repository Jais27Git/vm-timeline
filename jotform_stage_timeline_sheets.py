# ########################## jotform_stage_timeline.py #################################
# Purpose:   Fetch New VM Installation stage timeline + TAT for ALL matching submissions
#            and save to Google Sheets. Sheet is cleared and rewritten on each run.
# Approach:  Reads directly from form answer fields for accurate timestamps.
# Filter:    Field 3 (Select Operation) == "New VM Installation"
# API used:  GET https://api.jotform.com/form/{formId}/submissions
# TAT logic: Calculated between nearest two stages that both have dates (gaps skipped).
# Output:    Google Sheet — TAT tab, overwritten each run.
# Read-only on Jotform — no writes to Jotform whatsoever.
# Requires:  pip install gspread google-auth
########################################################################################

import requests
import time
from datetime import datetime
import gspread
import os
import json

API_KEY         = os.environ["JOTFORM_API_KEY"]
FORM_ID         = "251590768630059"
BASE_URL        = "https://api.jotform.com"
HEADERS         = {"APIKEY": API_KEY}
OPERATION_FIELD = "3"
OPERATION_VALUE = "New VM Installation"

# ── Google Sheets config ──────────────────────────────────────────────────────
SPREADSHEET_ID = "1IMbQ8TYZCZVTpZs0tlMcuvLitx5oaq5Kug7F5EmLqRY"
SHEET_NAME     = "TAT"

SERVICE_ACCOUNT_INFO = json.loads(os.environ["GSHEET_SERVICE_ACCOUNT"])

# ── Colors — RGB 0-255 for Google Sheets API ──────────────────────────────────
def hex_to_rgb(hex_str):
    """Convert hex color string to Google Sheets API RGB dict (0-1 scale)."""
    h = hex_str.lstrip("#")
    return {
        "red":   int(h[0:2], 16) / 255,
        "green": int(h[2:4], 16) / 255,
        "blue":  int(h[4:6], 16) / 255
    }

C = {
    "LEAD":   {"bg": "#1E3A5F", "fg": "#FFFFFF"},  # dark blue header
    "COL":    {"bg": "#D9E8F5", "fg": "#1E3A5F"},  # light blue column labels
    "DONE":   {"bg": "#E6F4EA", "fg": "#1E7E34"},  # light green — completed
    "ACTIVE": {"bg": "#FFF3CD", "fg": "#856404"},  # light yellow — active
    "SKIP":   {"bg": "#F8F9FA", "fg": "#6C757D"},  # light grey — skipped
    "FUTURE": {"bg": "#F8F9FA", "fg": "#ADB5BD"},  # muted grey — not picked
    "PEND":   {"bg": "#FFFFFF", "fg": "#495057"},  # white — pending
    "SEP":    {"bg": "#FFFFFF", "fg": "#FFFFFF"},  # white separator
}

# ── Pending task name → Stage label mapping ───────────────────────────────────
PENDING_TASK_MAP = {
    "Fill Fresh Form":                   "Stage - Fill Fresh Form",
    "Set Recce date: Fresh Form Filled": "Stage - Set Recce Date",
    "Update Recce status & Date":        "Stage - Update Recce Status",
    "Franchise Found?":                  "Stage - Found Franchise?",
    "Upload PO/PI":                      "Stage - Upload PO/PI",
    "Check PO/PI":                       "Stage - Upload PO/PI",
    "Sign Document":                     "Stage - Upload PO/PI",
    "Tech-ops Installation Initite":     "Stage - Set Installation Date",
    "Installation Complete":             "Stage - Update Installation Status",
    "Align Refill Manager":              "Stage - Align Refill Manager",
    "align refilling mgr":               "Stage - Align Refill Manager",
    "Set Refilling Date":                "Stage - Set Refilling Date",
    "Set Refilling Date nupoor":         "Stage - Set Refilling Date",
    "Update Refilling Status":           "Stage - Update Refilling Status",
    "Ads cohort update growth":          "Stage - Ads-Cohort Update",
}

# ── Stage definitions ─────────────────────────────────────────────────────────
STAGES = [
    {"label": "Stage - Fill Fresh Form",            "date_field": "104"},
    {"label": "Stage - Set Recce Date",             "date_field": "230"},
    {"label": "Stage - Update Recce Status",        "date_field": "231"},
    {"label": "Stage - Found Franchise?",           "date_field": "267", "value_field": "131"},
    {"label": "Stage - Upload PO/PI",               "date_field": "306"},
    {"label": "Stage - Set Installation Date",      "date_field": "115"},
    {"label": "Stage - Update Installation Status", "date_field": "118"},
    {"label": "Stage - Align Refill Manager",       "date_field": "256"},
    {"label": "Stage - Set Refilling Date",         "date_field": "244"},
    {"label": "Stage - Update Refilling Status",    "date_field": "129"},
    {"label": "Stage - Ads-Cohort Update",          "date_field": None, "value_field": "317"},
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_date(answer):
    if not answer:
        return None
    if isinstance(answer, dict):
        dt = answer.get("datetime")
        if dt:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    return datetime.strptime(dt, fmt)
                except ValueError:
                    continue
        try:
            y, m, d = answer.get("year"), answer.get("month"), answer.get("day")
            if y and m and d:
                return datetime.strptime(f"{y}-{m}-{d}", "%Y-%m-%d")
        except Exception:
            pass
    if isinstance(answer, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(answer, fmt)
            except ValueError:
                continue
    return None

def fmt_date(dt):
    return dt.strftime("%d/%m/%Y") if dt else "—"

def tat_str(days):
    if days is None:
        return "—"
    if days <= 1:
        return "Same day"
    return f"{days} days"

def get_matching_submissions():
    matched, offset, batch_size = [], 0, 300
    while True:
        r = requests.get(
            f"{BASE_URL}/form/{FORM_ID}/submissions",
            headers=HEADERS,
            params={"limit": batch_size, "offset": offset,
                    "orderby": "created_at", "direction": "ASC"}
        )
        r.raise_for_status()
        batch = r.json().get("content", [])
        if not batch:
            break
        for sub in batch:
            if sub.get("answers", {}).get(OPERATION_FIELD, {}).get("answer") == OPERATION_VALUE:
                matched.append(sub)
        print(f"  Fetched offset={offset} | matched: {len(matched)}")
        offset += batch_size
        if len(batch) < batch_size:
            break
        time.sleep(0.3)

    matched.sort(key=lambda s: (
        int(s.get("answers", {}).get("251", {}).get("answer", "DC-LEAD-0")
            .replace("DC-LEAD-", "") or 0)
    ), reverse=True)
    return matched

def extract_stages(answers, pending_task=""):
    recce_needed = answers.get("235", {}).get("answer", "With Recce")
    rows = []

    for stage in STAGES:
        label       = stage["label"]
        date_field  = stage.get("date_field")
        value_field = stage.get("value_field")
        end_date, display_end = None, "—"
        raw_value = answers.get(value_field, {}).get("answer") if value_field else None

        if label == "Stage - Found Franchise?":
            if raw_value == "Yes":
                end_date    = parse_date(answers.get("267", {}).get("answer"))
                display_end = fmt_date(end_date) if end_date else "Yes (no date)"
            elif raw_value == "No":
                display_end = "No (skipped)"
        elif label == "Stage - Ads-Cohort Update":
            display_end = raw_value if raw_value else "—"
        else:
            if label in ("Stage - Set Recce Date", "Stage - Update Recce Status") and recce_needed == "Without Recce":
                display_end = "Not Required"
            elif date_field:
                end_date    = parse_date(answers.get(date_field, {}).get("answer"))
                display_end = fmt_date(end_date) if end_date else "—"

        rows.append({"label": label, "end_date": end_date, "display_end": display_end,
                     "start_date": None, "display_start": "—", "tat": "—", "active": False})

    for i, row in enumerate(rows):
        if i == 0:
            row["start_date"]    = row["end_date"]
            row["display_start"] = row["display_end"]
            row["tat"]           = "—"
        else:
            prev_end = None
            for j in range(i - 1, -1, -1):
                if rows[j]["end_date"] is not None:
                    prev_end = rows[j]["end_date"]
                    break
            row["start_date"]    = prev_end
            row["display_start"] = fmt_date(prev_end) if prev_end else "—"
            if row["display_end"] == "Not Required":
                row["tat"] = "N/A"
            elif row["end_date"] and row["start_date"]:
                end_day   = row["end_date"].replace(hour=0, minute=0, second=0, microsecond=0)
                start_day = row["start_date"].replace(hour=0, minute=0, second=0, microsecond=0)
                days      = (end_day - start_day).days + 1
                row["tat"] = tat_str(days)
            else:
                row["tat"] = "—"

    active_label = PENDING_TASK_MAP.get(pending_task, "")
    if pending_task and pending_task != "—" and not active_label:
        print(f"  [WARN] Pending task not in map: '{pending_task}'")
    active_found = False
    for row in rows:
        row["active"] = (row["label"] == active_label)
        if row["active"]:
            active_found = True
            continue
        if active_found:
            row["start_date"]    = None
            row["display_start"] = "Not Picked"
            row["display_end"]   = "Not Picked"
            row["tat"]           = "—"

    return rows

# ── Google Sheets helpers ─────────────────────────────────────────────────────

def make_cell_format(bg_hex, fg_hex, bold=False):
    """Build a Google Sheets API cellFormat dict."""
    return {
        "backgroundColor": hex_to_rgb(bg_hex),
        "textFormat": {
            "foregroundColor": hex_to_rgb(fg_hex),
            "bold": bold,
            "fontSize": 10,
            "fontFamily": "Arial"
        },
        "verticalAlignment": "MIDDLE"
    }

def make_format_request(sheet_id, row_idx, col_start, col_end, bg_hex, fg_hex, bold=False):
    """Build a repeatCell request for the Sheets API batchUpdate."""
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": row_idx,
                "endRowIndex": row_idx + 1,
                "startColumnIndex": col_start,
                "endColumnIndex": col_end
            },
            "cell": {"userEnteredFormat": make_cell_format(bg_hex, fg_hex, bold)},
            "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment)"
        }
    }

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Fetching ALL '{OPERATION_VALUE}' submissions...\n")
    submissions = get_matching_submissions()
    print(f"\nFound: {len(submissions)} matching submissions\n")

    # ── Auth + connect to sheet ───────────────────────────────────────────────
    print("Connecting to Google Sheets...")
    gc = gspread.service_account_from_dict(SERVICE_ACCOUNT_INFO)
    sh = gc.open_by_key(SPREADSHEET_ID)
    ws     = sh.worksheet(SHEET_NAME)
    sheet_id = ws.id

    print("Clearing sheet...")
    ws.clear()

    # ── Build all values in memory ────────────────────────────────────────────
    print("Building data...")
    all_values      = []  # 2D array for batch write
    format_requests = []  # formatting batch
    row_idx         = 0   # 0-based for Sheets API

    for sub in submissions:
        answers      = sub.get("answers", {})
        lead_id      = answers.get("251", {}).get("answer", "N/A")
        sub_id       = sub["id"]
        client       = answers.get("15",  {}).get("answer", "—")
        pending_task = answers.get("317", {}).get("answer", "—")
        pending_on   = answers.get("318", {}).get("answer", "—")
        rows         = extract_stages(answers, pending_task=pending_task)

        # Lead header row
        all_values.append([f"Lead ID: {lead_id}  |  Sub: {sub_id}", client,
                           f"Pending: {pending_task}", f"On: {pending_on}"])
        format_requests.append(make_format_request(sheet_id, row_idx, 0, 4,
                                                   C["LEAD"]["bg"], C["LEAD"]["fg"], bold=True))
        row_idx += 1

        # Column label row
        all_values.append(["Stage", "Start Date", "End Date", "TAT"])
        format_requests.append(make_format_request(sheet_id, row_idx, 0, 4,
                                                   C["COL"]["bg"], C["COL"]["fg"], bold=True))
        row_idx += 1

        # Stage rows
        for row in rows:
            is_ads  = row["label"] == "Stage - Ads-Cohort Update"
            marker  = "► " if row["active"] else "   "
            dstart  = "—" if is_ads else row["display_start"]
            dend    = "—" if is_ads else row["display_end"]
            tat     = "—" if is_ads else row["tat"]

            if row["active"]:
                style = C["ACTIVE"]
            elif dstart == "Not Picked":
                style = C["FUTURE"]
            elif dend in ("Not Required", "No (skipped)"):
                style = C["SKIP"]
            elif dend != "—":
                style = C["DONE"]
            else:
                style = C["PEND"]

            all_values.append([marker + row["label"], dstart, dend, tat])
            format_requests.append(make_format_request(sheet_id, row_idx, 0, 4,
                                                       style["bg"], style["fg"],
                                                       bold=row["active"]))
            row_idx += 1

        # Separator row
        all_values.append(["", "", "", ""])
        format_requests.append(make_format_request(sheet_id, row_idx, 0, 4,
                                                   C["SEP"]["bg"], C["SEP"]["fg"]))
        row_idx += 1

    # ── Write values in one batch call ────────────────────────────────────────
    print(f"Writing {len(all_values)} rows to sheet...")
    ws.update(all_values, f"A1:D{len(all_values)}")
    print("Values written.")

    # ── Apply formatting in one batch call ────────────────────────────────────
    print("Applying formatting...")
    # Split into chunks of 500 to stay within API limits
    chunk_size = 500
    for i in range(0, len(format_requests), chunk_size):
        sh.batch_update({"requests": format_requests[i:i+chunk_size]})
        print(f"  Formatted rows {i}–{min(i+chunk_size, len(format_requests))}")

    # ── Column widths ─────────────────────────────────────────────────────────
    sh.batch_update({"requests": [
        {"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": 320}, "fields": "pixelSize"
        }},
        {"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 2},
            "properties": {"pixelSize": 110}, "fields": "pixelSize"
        }},
        {"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 2, "endIndex": 3},
            "properties": {"pixelSize": 110}, "fields": "pixelSize"
        }},
        {"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 3, "endIndex": 4},
            "properties": {"pixelSize": 90}, "fields": "pixelSize"
        }},
    ]})

    print(f"\nDone. {len(submissions)} leads | {len(all_values)} rows written to '{SHEET_NAME}' tab.")

if __name__ == "__main__":
    main()
