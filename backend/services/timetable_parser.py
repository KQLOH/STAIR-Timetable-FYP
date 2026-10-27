from bs4 import BeautifulSoup
from datetime import datetime
from pathlib import Path
from hashlib import sha256

import json
import re


# ---------------------------------------------------------
# TAR UMT timetable colours
# ---------------------------------------------------------

STATUS_COLOURS = {
    "#46854d": "normal",
    "#aea434": "replacement",
    "#a63134": "clashed_or_public_holiday",
}


TYPE_SHORT = {
    "L": "Lecture",
    "P": "Practical",
    "T": "Tutorial",
}

# Shared with the M1 evidence adapter; preserve the existing parser grammar.
COURSE_PATTERN = r"\b([A-Z]{2,}\d{3,})\s*\(([LPT])\)"
TIME_PATTERN = r"(\d{1,2}:\d{2}\s*[AP]M)\s*-\s*(\d{1,2}:\d{2}\s*[AP]M)"
TITLE_PATTERN = r"^(.*?)\s+\((Lecture|Practical|Tutorial)\s*\)\s+(.*?)\s+\((.*?)\)\s*$"


# ---------------------------------------------------------
# Helper functions
# ---------------------------------------------------------

def clean_space(value: str | None) -> str:
    """
    Remove repeated whitespace/newlines from HTML text.
    """

    return re.sub(r"\s+", " ", value or "").strip()


def time_to_24h(value: str) -> str:
    """
    Convert:
        11:00 AM -> 11:00
        1:00 PM  -> 13:00
    """

    value = clean_space(value).upper()

    return datetime.strptime(
        value,
        "%I:%M %p"
    ).strftime("%H:%M")


# ---------------------------------------------------------
# Page metadata
# ---------------------------------------------------------

def parse_page_metadata(soup: BeautifulSoup, raw_html: str) -> dict:
    """
    Extract:
        semester
        selected week
        week start
        week end
    """

    semester = None
    week = None
    week_start = None
    week_end = None

    # Example:
    # <strong>202605 Semester</strong>

    semester_match = re.search(
        r"<strong>\s*(\d{6})\s+Semester\s*</strong>",
        raw_html,
        re.IGNORECASE,
    )

    if semester_match:
        semester = semester_match.group(1)

    # Find currently selected week
    #
    # Example:
    # Week 10 : 2026-08-17 ~ 2026-08-23

    selected_option = soup.select_one(
        "#fweek option[selected]"
    )

    if selected_option:

        option_text = clean_space(
            selected_option.get_text(
                " ",
                strip=True
            )
        )

        week_match = re.search(
            r"Week\s+(\d+)\s*:\s*"
            r"(\d{4}-\d{2}-\d{2})\s*~\s*"
            r"(\d{4}-\d{2}-\d{2})",
            option_text,
            re.IGNORECASE,
        )

        if week_match:

            week = int(
                week_match.group(1)
            )

            week_start = week_match.group(2)
            week_end = week_match.group(3)

    return {
        "semester": semester,
        "week": week,
        "week_start": week_start,
        "week_end": week_end,
    }


# ---------------------------------------------------------
# Find weekly timetable
# ---------------------------------------------------------

def find_week_timetable(soup: BeautifulSoup):
    """
    TAR UMT response contains more than one table using
    id="simple-table".

    Therefore we cannot simply use:

        soup.find(id="simple-table")

    We specifically find the timetable containing Day/Time.
    """

    tables = soup.find_all(
        "table",
        id="simple-table"
    )

    for table in tables:

        table_text = clean_space(
            table.get_text(
                " ",
                strip=True
            )
        )

        if "Day/Time" in table_text:
            return table

    return None


# ---------------------------------------------------------
# Parse event cell
# ---------------------------------------------------------

def parse_event_cell(
    cell,
    day: str,
    date: str,
) -> dict | None:

    style = (
        cell.get("style") or ""
    ).lower().replace(" ", "")

    # Example:
    # background-color:#46854d

    colour_match = re.search(
        r"background-color:(#[0-9a-f]{6})",
        style
    )

    if not colour_match:
        return None

    colour = colour_match.group(1)

    # Event information is stored inside tooltip div

    event_div = cell.find(
        "div",
        attrs={
            "data-rel": "tooltip"
        }
    )

    if not event_div:
        return None

    visible_text = clean_space(
        event_div.get_text(
            " ",
            strip=True
        )
    )

    tooltip_title = clean_space(
        event_div.get("title", "")
    )

    # -----------------------------------------------------
    # Course code
    #
    # Example:
    #
    # BMSE3004 (P)
    # BMDS2133 (L)
    # BMSE3153 (T)
    # -----------------------------------------------------

    course_match = re.search(
        COURSE_PATTERN,
        visible_text,
        re.IGNORECASE,
    )

    if not course_match:
        return None

    subject_code = (
        course_match.group(1).upper()
    )

    type_short = (
        course_match.group(2).upper()
    )

    # -----------------------------------------------------
    # Time
    #
    # Example:
    #
    # 11:00 AM - 1:00 PM
    # -----------------------------------------------------

    time_match = re.search(
        TIME_PATTERN,
        visible_text,
        re.IGNORECASE,
    )

    if not time_match:
        return None

    start_time = time_to_24h(
        time_match.group(1)
    )

    end_time = time_to_24h(
        time_match.group(2)
    )

    # -----------------------------------------------------
    # Tooltip
    #
    # Example:
    #
    # COLLABORATIVE DEVELOPMENT
    # (Practical)
    # Mohd Ridzuan Bin Ahmad
    # (B001J, Block B)
    # -----------------------------------------------------

    title_match = re.match(
        TITLE_PATTERN,
        tooltip_title,
        re.IGNORECASE,
    )

    subject_name = None
    class_type = TYPE_SHORT.get(
        type_short,
        type_short
    )

    lecturer = None
    venue_full = None

    if title_match:

        subject_name = clean_space(
            title_match.group(1)
        )

        class_type = (
            title_match.group(2).title()
        )

        lecturer = clean_space(
            title_match.group(3)
        )

        venue_full = clean_space(
            title_match.group(4)
        )

    venue = None

    if venue_full:

        venue = venue_full.split(
            ",",
            1
        )[0].strip()

    class_status = STATUS_COLOURS.get(
        colour,
        "unknown"
    )

    return {
        "subject_code": subject_code,
        "subject_name": subject_name,

        "day": day,
        "date": date,

        "start_time": start_time,
        "end_time": end_time,

        "class_type": class_type,

        "venue": venue,
        "venue_full": venue_full,

        "lecturer": lecturer,

        "class_status": class_status,

        "source": "tarumt_intranet",
    }


# ---------------------------------------------------------
# Main parser
# ---------------------------------------------------------

def parse_intranet_response(
    html_content: str
) -> dict:

    soup = BeautifulSoup(
        html_content,
        "lxml"
    )

    metadata = parse_page_metadata(
        soup,
        html_content
    )

    timetable = find_week_timetable(
        soup
    )

    if timetable is None:

        raise ValueError(
            "TAR UMT weekly timetable table was not found."
        )

    events = []
    source_records = []

    rows = timetable.find_all("tr")

    for row_index, row in enumerate(rows):

        cells = row.find_all(
            "td",
            recursive=False
        )

        if not cells:
            continue

        # First column contains:
        #
        # Mon
        # 2026-08-17

        first_cell_text = clean_space(
            cells[0].get_text(
                " ",
                strip=True
            )
        )

        date_match = re.search(
            r"\b"
            r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun)"
            r"\b.*?"
            r"(\d{4}-\d{2}-\d{2})",
            first_cell_text,
            re.IGNORECASE,
        )

        # A recognized header is not a session. Other malformed rows are retained.
        if "Day/Time" in first_cell_text:
            continue

        day = date_match.group(1).title() if date_match else None
        date = date_match.group(2) if date_match else None

        # Parse every timetable cell
        for cell_index, cell in enumerate(cells[1:], start=1):
            event_div = cell.find("div", attrs={"data-rel": "tooltip"})
            raw_text = cell.get_text(" ", strip=False)
            title = event_div.get("title", "") if event_div else ""
            if not raw_text.strip() and not title and event_div is None:
                continue
            event = None
            issue_code = None
            if date_match is None:
                issue_code = "UNPARSED_ROW_HEADER"
            else:
                try:
                    event = parse_event_cell(cell, day, date)
                    if event is None:
                        issue_code = "UNPARSED_SESSION_CELL"
                except ValueError:
                    issue_code = "INVALID_SESSION_TIME"
            source_record = {
                "row_index": row_index,
                "cell_index": cell_index,
                "raw_header": cells[0].get_text(" ", strip=False),
                "raw_text": raw_text,
                "raw_html": str(cell),
                "visible_text": event_div.get_text(" ", strip=False) if event_div else raw_text,
                "tooltip_title": title,
                "style": cell.get("style", ""),
                "day": day,
                "date": date,
                "parsed_event": event,
                "issue_code": issue_code,
            }
            tooltip_divs = cell.find_all("div", attrs={"data-rel": "tooltip"})
            if len(tooltip_divs) > 1:
                # The legacy parser reads the first tooltip only. Preserve every
                # source session candidate and disclose that limitation.
                for tooltip_index, tooltip in enumerate(tooltip_divs):
                    source_records.append({
                        **source_record,
                        "visible_text": tooltip.get_text(" ", strip=False),
                        "tooltip_title": tooltip.get("title", ""),
                        "tooltip_index": tooltip_index,
                        "issue_code": issue_code or "MULTIPLE_SESSION_CELL",
                    })
            else:
                source_records.append(source_record)

            if event:
                events.append(event)

    # Sort events by date + starting time

    events.sort(
        key=lambda event: (
            event["date"],
            event["start_time"],
            event["subject_code"],
        )
    )

    return {
        **metadata,

        "event_count": len(events),

        "events": events,
        # Internal evidence is consumed by the adapter, not exposed as legacy fields.
        "_source_records": source_records,
        "_source_sha256": sha256(html_content.encode("utf-8")).hexdigest(),
        "_metadata_text": "\n".join(
            [match.group(0) for match in re.finditer(r"<strong>\s*\d{6}\s+Semester\s*</strong>", html_content, re.IGNORECASE)]
            + [option.get_text(" ", strip=False) for option in soup.select("#fweek option[selected]")]
        ),
    }


# ---------------------------------------------------------
# Run parser locally
# ---------------------------------------------------------

if __name__ == "__main__":

    project_root = Path(
        __file__
    ).resolve().parents[2]

    response_file = (
        project_root
        / "data"
        / "samples"
        / "intranet_response.txt"
    )

    output_directory = (
        project_root
        / "data"
        / "temp"
    )

    output_directory.mkdir(
        parents=True,
        exist_ok=True
    )

    output_file = (
        output_directory
        / "parsed_timetable.json"
    )

    if not response_file.exists():

        raise FileNotFoundError(
            f"Response file not found: {response_file}"
        )

    html_content = response_file.read_text(
        encoding="utf-8",
        errors="ignore"
    )

    result = parse_intranet_response(
        html_content
    )

    output_file.write_text(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    print()
    print("=" * 60)
    print("STAIR - TAR UMT Timetable Parser")
    print("=" * 60)

    print(
        f"Semester : {result['semester']}"
    )

    print(
        f"Week     : {result['week']}"
    )

    print(
        f"Period   : "
        f"{result['week_start']} "
        f"to "
        f"{result['week_end']}"
    )

    print(
        f"Events   : {result['event_count']}"
    )

    print()
    print(
        f"JSON saved to:"
    )

    print(
        output_file
    )

    print("=" * 60)
