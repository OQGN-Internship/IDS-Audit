"""The forms the upload screens send (ENG-03 4)."""

from decimal import Decimal

from django import forms

from ids_audit.pipeline.cleaning import clean_amount


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
