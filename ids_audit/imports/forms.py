"""The forms the upload screens and the Rules screen send (ENG-03 4)."""

from decimal import Decimal

from django import forms

from ids_audit.pipeline.cleaning import clean_amount
from ids_audit.pipeline.columns import FIELD_NAMES, normalize_header


class UploadForm(forms.Form):
    """A file, and SAP's total for it when the file has no total row
    (ENG-02 9.1).
    """

    file = forms.FileField(label="SAP export")
    control_total = forms.CharField(
        label="SAP's total for this file",
        required=False,
        help_text=(
            "Only for a file with no total row: the total SAP shows for the same "
            "selection, as SAP shows it, such as 1,234.500-."
        ),
    )

    def __init__(self, *args, decimal_separator: str, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.decimal_separator = decimal_separator

    def clean_control_total(self) -> Decimal | None:
        """Read the typed total by the rule for an amount written as text in
        the file, with the report's decimal separator (ENG-02 9.1).
        """
        typed = self.cleaned_data["control_total"]
        try:
            return clean_amount(typed, self.decimal_separator)
        except ValueError:
            raise forms.ValidationError(
                f"{typed} isn't an amount. Type it as SAP shows it, such as "
                "1,234.500 or 1,234.500-."
            ) from None


class MappingForm(forms.Form):
    """A workbook with the mapping sheets on their own (ENG-02 8.5)."""

    file = forms.FileField(label="Mapping file")


class RulesForm(forms.Form):
    """A change to a report's rules, sent from the version its page showed,
    which the change is checked against (ENG-02 3). Each kind of change is a
    form of its own, and changes() gives the change as change_ruleset()
    takes it.
    """

    version = forms.IntegerField(widget=forms.HiddenInput)

    def __init__(self, *args, settings: dict, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.settings = settings

    def changes(self) -> dict:
        raise NotImplementedError


class ChoiceRuleForm(RulesForm):
    """A setting with a fixed list of values, as radio buttons: date_order,
    decimal_separator or mapping_rule.
    """

    value = forms.ChoiceField(widget=forms.RadioSelect)

    def __init__(self, *args, key: str, labels: dict[str, str], **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.key = key
        self.fields["value"].choices = list(labels.items())
        self.fields["value"].initial = self.settings[key]

    def changes(self) -> dict:
        return {self.key: self.cleaned_data["value"]}


class CurrencyForm(RulesForm):
    """The currency every amount must be in, as three letters."""

    currency = forms.CharField(label="Currency", max_length=20)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["currency"].initial = self.settings["currency"]

    def changes(self) -> dict:
        # Capitals for the code, so omr is OMR. Anything else is the
        # ruleset's check to refuse (ENG-02 3).
        return {"currency": self.cleaned_data["currency"].strip().upper()}


class ActualSpendForm(RulesForm):
    """Whether every row counts as actual spend, or rows by their value type,
    with the two lists one value type per line (ENG-02 3 and 5).
    """

    EVERY, BY_VALUE_TYPE = "every", "by_value_type"
    counts = forms.ChoiceField(
        widget=forms.RadioSelect,
        choices=[
            (EVERY, "Every row counts as actual spend"),
            (BY_VALUE_TYPE, "Only rows whose value type is listed as actual spend"),
        ],
    )
    actual = forms.CharField(
        label="Value types that are actual spend, one per line",
        widget=forms.Textarea(attrs={"rows": 3}),
        required=False,
    )
    not_actual = forms.CharField(
        label="Value types that are kept but aren't actual spend, one per line",
        widget=forms.Textarea(attrs={"rows": 3}),
        required=False,
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        rule = self.settings["actual_spend_rule"]
        self.fields["counts"].initial = (
            self.EVERY if rule is None else self.BY_VALUE_TYPE
        )
        if rule is not None:
            self.fields["actual"].initial = "\n".join(rule["actual"])
            self.fields["not_actual"].initial = "\n".join(rule["not_actual"])

    def changes(self) -> dict:
        if self.cleaned_data["counts"] == self.EVERY:
            return {"actual_spend_rule": None}
        return {
            "actual_spend_rule": {
                key: lines(self.cleaned_data[key]) for key in ("actual", "not_actual")
            }
        }


def lines(text: str) -> list[str]:
    """Return each line of the text that holds something, trimmed, once."""
    return list(
        dict.fromkeys(line.strip() for line in text.splitlines() if line.strip())
    )


class HeaderNameForm(RulesForm):
    """One header name added to a field, or one removed (ENG-02 3 and 4.4)."""

    add = forms.CharField(label="Add a header name", max_length=200, required=False)
    remove = forms.CharField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, key: str, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.key = key
        self.names = list(self.settings["header_names"][key])

    def clean(self) -> dict:
        data = super().clean()
        add, remove = data.get("add", "").strip(), data.get("remove", "")
        if bool(add) == bool(remove):
            raise forms.ValidationError(
                "Type one header name to add, or press Remove beside one."
            )
        if remove and remove not in self.names:
            raise forms.ValidationError(
                f"{remove} isn't one of {FIELD_NAMES[self.key]}'s header names."
            )
        if add:
            same = [
                n for n in self.names if normalize_header(n) == normalize_header(add)
            ]
            if same:
                raise forms.ValidationError(
                    f"{add} matches the same headers as {same[0]}, which "
                    f"{FIELD_NAMES[self.key]} has already: capitals, spaces and "
                    "punctuation are ignored."
                )
        data["add"] = add
        return data

    def changes(self) -> dict:
        add, remove = self.cleaned_data["add"], self.cleaned_data["remove"]
        names = [*self.names, add] if add else [n for n in self.names if n != remove]
        return {"header_names": {self.key: names}}
