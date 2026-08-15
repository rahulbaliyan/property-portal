from django import forms

from .models import Property


class SellerListingForm(forms.ModelForm):
    # Honeypot: hidden via CSS in the template, invisible and unreachable
    # by keyboard for real visitors. Bots that blindly fill every field
    # trip it, and the submission is silently rejected in clean_website.
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = Property
        fields = [
            "title",
            "property_type",
            "listing_intent",
            "region",
            "address",
            "area_value",
            "area_unit",
            "bedrooms",
            "bathrooms",
            "description",
            "price",
            "seller_name",
            "seller_phone",
            "seller_email",
        ]
        widgets = {
            "title": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "e.g. 2BHK Flat near Rajpur Road"}
            ),
            "property_type": forms.Select(attrs={"class": "form-select"}),
            "listing_intent": forms.Select(attrs={"class": "form-select"}),
            "region": forms.Select(attrs={"class": "form-select"}),
            "address": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Locality / landmark"}
            ),
            "area_value": forms.NumberInput(attrs={"class": "form-control"}),
            "area_unit": forms.Select(attrs={"class": "form-select"}),
            "bedrooms": forms.NumberInput(
                attrs={"class": "form-control", "placeholder": "Optional"}
            ),
            "bathrooms": forms.NumberInput(
                attrs={"class": "form-control", "placeholder": "Optional"}
            ),
            "description": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 4,
                    "placeholder": "Condition, nearby landmarks, why it's a good deal...",
                }
            ),
            "price": forms.NumberInput(
                attrs={"class": "form-control", "placeholder": "Optional — kept private, never shown publicly"}
            ),
            "seller_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Your full name"}
            ),
            "seller_phone": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Your phone number"}
            ),
            "seller_email": forms.EmailInput(
                attrs={"class": "form-control", "placeholder": "Optional"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # blank=True on the model so admin-created listings don't need
        # these — but sellers submitting their own listing always must.
        self.fields["seller_name"].required = True
        self.fields["seller_phone"].required = True

    def clean_website(self):
        if self.cleaned_data.get("website"):
            raise forms.ValidationError("Spam detected.")
        return self.cleaned_data.get("website")
