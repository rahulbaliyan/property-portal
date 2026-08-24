import re

from django import forms

from .models import Inquiry

# A real spam submission on the live site put "To the
# http://.../fekal0911 Webmaster" in both name and message — a known
# backlink-spam bot pattern. The honeypot field didn't catch it because
# the bot only filled in the visible fields. This blocks the same
# pattern directly: URLs and script-like content have no legitimate
# reason to appear in a name or a property inquiry message.
_SPAM_PATTERN = re.compile(
    r"https?://|www\.|<script|</script|javascript:", re.IGNORECASE
)


def reject_spam_content(value):
    if value and _SPAM_PATTERN.search(value):
        raise forms.ValidationError("That doesn't look like a valid entry — please remove any links.")
    return value


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

    def clean_name(self):
        return reject_spam_content(self.cleaned_data.get("name"))

    def clean_message(self):
        return reject_spam_content(self.cleaned_data.get("message"))
