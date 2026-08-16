from django import forms

from .models import TitleCheckReport


class TitleCheckReportForm(forms.ModelForm):
    """The location fields (district/tehsil/village/pargana name+code) are
    populated by static/duediligence/admin_lookup.js from the live
    cascading picker, not typed directly — hidden here so the admin change
    form doesn't offer a free-text alternative that could bypass the
    picker's village-collision disambiguation."""

    class Meta:
        model = TitleCheckReport
        fields = [
            "property",
            "district_name",
            "district_code",
            "tehsil_name",
            "tehsil_code",
            "village_name",
            "village_code",
            "pargana_name",
            "pargana_code",
            "deed_date",
            "consideration_amount",
            "seller_name",
            "buyer_name",
            "registration_number",
            "book_number",
            "volume_number",
            "page_number",
            "deed_area_value",
            "deed_area_unit",
            "deed_pdf",
        ]
        widgets = {
            "district_name": forms.HiddenInput(),
            "district_code": forms.HiddenInput(),
            "tehsil_name": forms.HiddenInput(),
            "tehsil_code": forms.HiddenInput(),
            "village_name": forms.HiddenInput(),
            "village_code": forms.HiddenInput(),
            "pargana_name": forms.HiddenInput(),
            "pargana_code": forms.HiddenInput(),
        }
