"""SYNTHETIC TEST DATA. Names, sessions and dates are invented test examples.

These fixtures are not timetable evidence, gold annotations or evaluation data.
"""


def timetable_html(*, visible="TEST1001 (L) 9:00 AM - 10:00 AM",
                   title="SYNTHETIC COURSE (Lecture) SYNTHETIC LECTURER (TEST ROOM, Test Block)",
                   header="Mon 2026-08-17", style="background-color:#46854d",
                   extra_cells="", metadata=True):
    prefix = ('<strong>202605 Semester</strong><select id="fweek">'
              '<option selected>Week 10 : 2026-08-17 ~ 2026-08-23</option></select>') if metadata else ""
    return (prefix + '<table id="simple-table"><tr><td>Day/Time</td><td>09:00</td></tr>'
            f'<tr><td>{header}</td><td style="{style}"><div data-rel="tooltip" '
            f'title="{title}">{visible}</div></td>{extra_cells}</tr></table>')
