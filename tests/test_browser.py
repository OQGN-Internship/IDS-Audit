"""The import screens in a real browser, as a user would use them (ENG-02
12.1): Playwright drives Chromium against the application, served by
pytest-django's live server.

Run `uv run playwright install chromium` once before these (ENG-01 7.5).
"""

import re
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.sessions.models import Session
from playwright.sync_api import Page, expect

from ids_audit.imports.models import Upload
from ids_audit.reports import queries
from ids_audit.reports.models import Report
from tests.factories import PASSWORD, make_admin, make_report
from tests.files import HEADER, LINE, csv_file, total_row

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("uploads_dir"),
]

Status = Upload.Status
SAMPLE = Path(__file__).parent / "fixtures" / "sample.xlsx"
NEW_PASSWORD = "quiet-river-lantern-27"


@pytest.fixture(autouse=True, scope="module")
def database_beside_the_browser(django_db_setup):
    """Let the tests use the database while Playwright's event loop runs in
    the same thread. The live server handles each request in its own thread.

    Asking for django_db_setup creates the test database before Playwright
    starts, and the setting lasts until the module's last test has emptied
    the database again.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
        yield


def sign_in(page: Page, live_server, username: str, password: str = PASSWORD):
    """Open the application and sign in through the sign-in page."""
    page.goto(live_server.url)
    expect(page).to_have_url(re.compile(r"/sign-in\?next=/$"))
    page.get_by_label("Username").fill(username)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign in").click()


def upload_file(page: Page, path: Path) -> None:
    """Send a file from the report's Uploads page."""
    page.get_by_role("link", name="Upload a file").click()
    page.get_by_label("SAP export").set_input_files(str(path))
    page.get_by_role("button", name="Upload and read").click()


def test_a_fixed_admin_imports_and_rolls_back_a_file(page: Page, live_server) -> None:
    """Verify a new fixed administrator signs in, must choose a password,
    creates a report, which opens on its rules, uploads the sample, opens an
    exception's rows in place, approves it, and rolls it back to the figure
    the confirmation showed.
    """
    admin = make_admin(must_change_password=True)
    sign_in(page, live_server, admin.username)
    expect(page).to_have_url(re.compile(r"/password$"))
    page.get_by_label("Current password").fill(PASSWORD)
    page.get_by_label("New password", exact=True).fill(NEW_PASSWORD)
    page.get_by_label("New password again").fill(NEW_PASSWORD)
    page.get_by_role("button", name="Change password").click()
    expect(page.get_by_text("Your password is changed.")).to_be_visible()

    page.get_by_role("link", name="New report").click()
    page.get_by_label("Name").fill("IDS 2026")
    page.get_by_role("button", name="Create report").click()
    # A new report opens on its rules, to check before its first upload.
    rules = page.get_by_role("heading", name="How files are read and counted")
    expect(rules).to_be_visible()
    page.get_by_role("navigation", name="Main").get_by_role(
        "link", name="Uploads"
    ).click()
    expect(page.get_by_role("heading", name="Uploads")).to_be_visible()

    upload_file(page, SAMPLE)
    expect(page.get_by_text("Upload 1 is read and awaits approval.")).to_be_visible()
    address = page.url
    outside = page.get_by_role("row", name=re.compile("Cost center outside IDS"))
    outside.get_by_role("link").click()
    panel = page.locator("#items-outside_ids")
    expect(panel.get_by_role("cell", name="3100", exact=True)).to_be_visible()
    expect(panel.get_by_role("cell", name="3200", exact=True)).to_be_visible()
    assert page.url == address

    page.get_by_role("button", name="Approve upload 1").click()
    expect(page.get_by_text("Upload 1 is approved")).to_be_visible()
    report = Report.objects.get(name="IDS 2026")
    upload = report.uploads.get()
    assert (upload.status, upload.decided_by) == (Status.COMMITTED, admin)

    page.get_by_role("link", name="Roll back upload 1").click()
    total = f"{queries.totals(report).total:,.3f}"
    expect(page.get_by_text(f"Actual spend: {total} OMR → 0.000 OMR.")).to_be_visible()
    page.get_by_label("Reason for rolling back").fill("Demonstration")
    page.get_by_role("button", name="Roll back upload 1").click()
    expect(page.get_by_text("Upload 1 is rolled back.")).to_be_visible()
    upload.refresh_from_db()
    assert upload.status == Status.ROLLED_BACK
    assert queries.totals(report).total == Decimal("0.000")


def test_a_missing_column_is_picked_from_the_file_s_first_rows(
    page: Page, live_server, tmp_path
) -> None:
    """Verify a file with no amount column stops for a column, shows its
    first rows, reads once the column is picked, and can then be withdrawn.
    """
    admin, report = make_admin(), make_report()
    header = tuple("Value" if h == "Amount in LC" else h for h in HEADER)
    path = tmp_path / "no-amount.csv"
    path.write_bytes(csv_file(LINE, total_row("1000.000"), header=header))

    sign_in(page, live_server, admin.username)
    page.get_by_role("link", name=report.name).click()
    expect(page.get_by_text("No approved uploads yet.")).to_be_visible()
    page.get_by_role("link", name="Go to Uploads").click()
    upload_file(page, path)
    expect(page.get_by_text("In the file, no column matches Amount.")).to_be_visible()
    page.get_by_role("link", name="Pick the columns").click()
    expect(page.get_by_role("cell", name="1,000.000")).to_be_visible()
    page.get_by_label("In the file, no column matches Amount.").select_option("I")
    page.get_by_role("button", name="Read the file again with these columns").click()
    expect(page.get_by_text("Upload 1 is read and awaits approval.")).to_be_visible()

    page.get_by_label("Reason for withdrawing").fill("Wrong export")
    page.get_by_role("button", name="Withdraw upload 1").click()
    expect(page.get_by_text("Upload 1 is withdrawn.")).to_be_visible()
    assert report.uploads.get().status == Status.REJECTED


def test_a_panel_after_the_session_ends_moves_the_whole_page_to_sign_in(
    page: Page, live_server
) -> None:
    """Verify that once the session has ended, asking for an exception's rows
    sends the whole page to the sign-in page, not the page into the panel,
    and signing in again comes back to the upload.
    """
    admin = make_admin()
    sign_in(page, live_server, admin.username)
    page.get_by_role("link", name="New report").click()
    page.get_by_label("Name").fill("Timed out")
    page.get_by_role("button", name="Create report").click()
    page.get_by_role("navigation", name="Main").get_by_role(
        "link", name="Uploads"
    ).click()
    upload_file(page, SAMPLE)
    address = page.url

    Session.objects.all().delete()
    outside = page.get_by_role("row", name=re.compile("Cost center outside IDS"))
    outside.get_by_role("link").click()
    expect(page).to_have_url(re.compile(r"/sign-in\?next=/reports/\d+/uploads/1$"))
    page.get_by_label("Username").fill(admin.username)
    page.get_by_label("Password").fill(PASSWORD)
    page.get_by_role("button", name="Sign in").click()
    expect(page).to_have_url(address)


def test_going_back_after_signing_out_shows_sign_in_not_the_report(
    page: Page, live_server
) -> None:
    """Verify that after signing out, the browser's Back button asks the
    server again, which sends it to the sign-in page, so the next person at
    the machine can't see the report (ENG-01 6.4).
    """
    admin, report = make_admin(), make_report()
    sign_in(page, live_server, admin.username)
    page.goto(f"{live_server.url}/reports/{report.pk}/overview")
    expect(page.get_by_role("heading", name="Overview")).to_be_visible()
    page.get_by_role("button", name="Sign out").click()
    expect(page).to_have_url(re.compile(r"/sign-in$"))
    page.go_back()
    expect(page).to_have_url(re.compile(r"/sign-in\?next=/reports/\d+/overview$"))
    expect(page.get_by_role("heading", name="Overview")).to_have_count(0)
