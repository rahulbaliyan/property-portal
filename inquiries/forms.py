from django import forms

from .models import Inquiry


class InquiryForm(forms.ModelForm):
    # Same honeypot pattern as SellerListingForm: hidden via CSS, invisible
    # and unreachable by keyboard for real visitors. This form previously
    # had no spam protection at all, unlike the sell form.
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = Inquiry
        fields = ["name", "phone", "email", "message"]
        widgets = {
            "name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Your name"}
            ),
            "phone": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Phone number"}
            ),
            "email": forms.EmailInput(
                attrs={"class": "form-control", "placeholder": "Email (optional)"}
            ),
            "message": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "placeholder": "I'm interested in this property...",
                    "rows": 4,
                }
            ),
        }

    def clean_website(self):
        if self.cleaned_data.get("website"):
            raise forms.ValidationError("Spam detected.")
        return self.cleaned_data.get("website")
