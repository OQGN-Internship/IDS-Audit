"""The Rules screen (ENG-03 4): a report's rules in words for anyone who can
open the report, any version of them, and the forms an active fixed
administrator changes them with (ENG-02 3).

A change is a form for one setting, or for one field's header names. Sending
it saves nothing: it's checked as change_ruleset() would check it, then shown
before and after on a confirmation, and only confirming saves it, through
change_ruleset(), so it's checked, locked and recorded as a command's change
is. A refusal, at either step, shows its words and saves nothing.
"""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from ids_audit.accounts.access import VIEW, report_permission
from ids_audit.accounts.services import require_fixed_admin
from ids_audit.imports import rules
from ids_audit.imports.forms import (
    ActualSpendForm,
    ChoiceRuleForm,
    CurrencyForm,
    HeaderNameForm,
    RulesForm,
)
from ids_audit.imports.services import (
    RulesetRefused,
    change_ruleset,
    check_change,
    shown_settings_locked,
)
from ids_audit.imports.states import open_upload
from ids_audit.pipeline.columns import FIELD_NAMES
from ids_audit.pipeline.ruleset import SHOWN_SETTINGS, changes_in_words
from ids_audit.reports.models import Report, Ruleset

# What the event of a change made here keeps in place of a command (ENG-01
# 6.3).
SCREEN = "rules"

# Each form's heading.
HEADINGS = {
    "date_order": "Change the date order",
    "decimal_separator": "Change the decimal separator",
    "currency": "Change the currency",
    "actual_spend_rule": "Change what counts as actual spend",
    "mapping_rule": "Change the mapping rule",
}


@report_permission(VIEW)
def rules_page(request, report: Report):
    """The rules in force, explained, then every version (ENG-03 4)."""
    version = rules.latest_version(report)
    return render(
        request,
        "imports/rules.html",
        {
            "report": report,
            "version": version,
            "cards": rules.cards(version, shown_settings_locked(report)),
            "fields": rules.field_rows(version.settings),
            "read_applies": rules.READ_APPLIES,
            "history": rules.history(report),
            "can_change": request.user.is_fixed_admin,
        },
    )


@report_permission(VIEW)
def rules_version(request, report: Report, version: int):
    """One version of the report's rules, with no controls: whether it's in
    force, when it was made and replaced, and how many uploads were read with
    it.
    """
    found = get_object_or_404(
        report.rulesets.select_related("created_by"), version=version
    )
    return render(
        request,
        "imports/rules_version.html",
        {
            "report": report,
            "version": found,
            "latest": rules.latest_version(report),
            "until": rules.in_force_until(report, found),
            "read": rules.uploads_read_with(report, found),
            # As it was, so a lock that holds now isn't shown on it.
            "cards": rules.cards(found, locked=False),
            "fields": rules.field_rows(found.settings),
            "read_applies": rules.READ_APPLIES,
        },
    )


@report_permission(VIEW)
def rules_change(request, report: Report, setting: str):
    """Change one setting: date_order, decimal_separator, currency,
    actual_spend_rule or mapping_rule (ENG-02 3).
    """
    require_fixed_admin(request.user)
    if setting not in rules.CHANGED_BY_FORM:
        raise Http404("No form changes that setting.")
    latest = rules.latest_version(report)
    card = rules.card(setting, latest.settings, shown_settings_locked(report))
    if setting == "currency":
        form_class, options = CurrencyForm, {}
    elif setting == "actual_spend_rule":
        form_class, options = ActualSpendForm, {}
    else:
        form_class = ChoiceRuleForm
        options = {"key": setting, "labels": rules.choice_labels(setting)}
    return change(
        request,
        report,
        latest,
        form_class,
        options,
        {"card": card, "setting": setting, "heading": HEADINGS[setting]},
    )


@report_permission(VIEW)
def rules_headers(request, report: Report, field: str):
    """Add one header name to a field, or remove one (ENG-02 3 and 4.4)."""
    require_fixed_admin(request.user)
    if field not in FIELD_NAMES:
        raise Http404("There's no such field.")
    latest = rules.latest_version(report)
    row = next(row for row in rules.field_rows(latest.settings) if row.key == field)
    return change(
        request,
        report,
        latest,
        HeaderNameForm,
        {"key": field},
        {
            "row": row,
            "setting": "header_names",
            "heading": f"Change the header names for {row.name}",
        },
    )


def change(
    request,
    report: Report,
    latest: Ruleset,
    form_class: type[RulesForm],
    options: dict,
    context: dict,
):
    """Show a change's form; once it's sent, its confirmation; and once that's
    confirmed, save it and open the rules. A refused change shows the form
    again with the refusal's words.
    """
    form = form_class(
        request.POST if request.method == "POST" else None,
        settings=latest.settings,
        initial={"version": latest.version},
        **options,
    )
    context = {
        **context,
        "report": report,
        "latest": latest,
        "form": form,
        # A locked setting shows why, and no control (ENG-02 3).
        "locked": context["setting"] in SHOWN_SETTINGS
        and shown_settings_locked(report),
    }
    if request.method == "POST" and form.is_valid():
        changes, based_on = form.changes(), form.cleaned_data["version"]
        try:
            if request.POST.get("confirm") == "yes":
                saved = change_ruleset(
                    report, request.user, changes, screen=SCREEN, based_on=based_on
                )
            else:
                before, after = check_change(report, changes, based_on)
        except RulesetRefused as refused:
            form.add_error(None, f"{' '.join(refused.problems)} Nothing was saved.")
        else:
            if request.POST.get("confirm") == "yes":
                if context["setting"] in SHOWN_SETTINGS:
                    applies = "It applies at once, to every figure of the report."
                else:
                    applies = "Files received from now on are read with it."
                messages.success(
                    request, f"Rules version {saved.version} is saved. {applies}"
                )
                return redirect("rules", report.pk)
            return render(
                request,
                "imports/rules_confirm.html",
                {
                    **context,
                    "changes": changes_in_words(before.settings, after),
                    "next": before.version + 1,
                    "applies": (
                        rules.SHOWN_APPLIES
                        if context["setting"] in SHOWN_SETTINGS
                        else rules.READ_APPLIES
                    ),
                    # Not open_upload, which the sidebar sets for itself.
                    "still_open": open_upload(report),
                },
            )
    return render(request, "imports/rules_change.html", context)
