"""A report of generated postings for the speed tests (ENG-03 10).

The report has 10 committed uploads in replace-by-period mode, each covering
a month of its own, so every posting counts. Each upload maps 40 of its 45
GL accounts, 5 of them without a commitment item, and 25 of its 30 cost
centers, 2 without a department, so every catch-all and exception list has
rows. The postings go in with COPY, as staging does, since inserting 200,000
rows one by one would take minutes.

A report of any size has the same codes, uploads and mappings, so its
screens run the same queries whatever its number of postings.
"""

import datetime
from decimal import Decimal

from django.db import connection

from ids_audit.ledger.models import CcDeptMap, GlCiMap, Posting
from ids_audit.reports.models import Report
from tests.factories import commit, make_report, make_upload

UPLOADS = 10
GL_CODES = [f"6{n:05d}" for n in range(1, 46)]
COST_CENTERS = [f"4{n:03d}" for n in range(1, 31)]
DEPARTMENTS = ["APP", "INF", "SEC", "GOV", "PMO"]


def build(postings_per_upload: int) -> Report:
    """Return a committed report of UPLOADS uploads, each with this many
    postings.
    """
    report = make_report()
    for index in range(UPLOADS):
        month = datetime.date(2025, index + 1, 1)
        upload = make_upload(report)
        GlCiMap.objects.bulk_create(
            GlCiMap(
                upload=upload,
                report=report,
                gl_code=gl,
                gl_name=f"GL {gl}",
                ci_code="" if n >= 35 else f"2{n % 8:03d}",
                ci_name="" if n >= 35 else f"CI {n % 8}",
            )
            for n, gl in enumerate(GL_CODES[:40])
        )
        CcDeptMap.objects.bulk_create(
            CcDeptMap(
                upload=upload,
                report=report,
                cc_code=cc,
                cc_name=f"CC {cc}",
                dept_code="" if n >= 23 else DEPARTMENTS[n % 5],
                dept_name="" if n >= 23 else f"Dept {n % 5}",
            )
            for n, cc in enumerate(COST_CENTERS[:25])
        )
        copy_postings(upload, month, postings_per_upload, index * postings_per_upload)
        commit(upload, minute=index + 1)
    return report


def copy_postings(upload, month: datetime.date, count: int, first: int) -> None:
    """Load count postings into the upload's month with COPY. Each posting's
    codes follow from its place among all the report's postings, from first,
    so even 10 postings in each of 10 uploads use every code.
    """
    fields = [f for f in Posting._meta.concrete_fields if not f.primary_key]
    columns = ", ".join(connection.ops.quote_name(f.column) for f in fields)
    with connection.cursor() as cursor:
        with cursor.copy(f"COPY posting ({columns}) FROM STDIN") as copy:
            for n in range(count):
                k = first + n
                values = {
                    "report_id": upload.report_id,
                    "upload_id": upload.pk,
                    "sheet": "Transactions",
                    "row_number": n + 5,
                    "company_code": "1000",
                    "fiscal_year": month.year,
                    "document_number": f"51{n:08d}",
                    "line_item": "1",
                    "line_key": f"1000-{month.year}-51{n:08d}-1-{upload.number}",
                    "posting_date": month.replace(day=n % 28 + 1),
                    "gl_code": GL_CODES[k % len(GL_CODES)],
                    "cost_center_code": COST_CENTERS[k * 7 % len(COST_CENTERS)],
                    "value_type": "",
                    "is_actual": n % 50 != 0,
                    "amount": Decimal(n % 997) + Decimal("0.123")
                    if n % 9
                    else -Decimal(n % 311),
                    "currency": "OMR",
                    "item_text": f"Line {n}",
                }
                copy.write_row([values[f.attname] for f in fields])
