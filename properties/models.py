from django.db import models
from django.urls import reverse
from django.utils.text import slugify


class Property(models.Model):
    class PropertyType(models.TextChoices):
        PLOT = "plot", "Plot"
        FLAT = "flat", "Flat"
        VILLA = "villa", "Villa"

    class Region(models.TextChoices):
        MALDEVTA = "maldevta", "Maldevta"
        DEHRADUN = "dehradun", "Dehradun"
        DHANAULTI = "dhanaulti", "Dhanaulti"
        MUSSOORIE = "mussoorie", "Mussoorie"

    class AreaUnit(models.TextChoices):
        SQFT = "sqft", "Sq. Ft."
        NALI = "nali", "Nali"
        BIGHA = "bigha", "Bigha"
        ACRE = "acre", "Acre"

    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        UNDER_NEGOTIATION = "under_negotiation", "Under Negotiation"
        SOLD = "sold", "Sold"

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    property_type = models.CharField(max_length=10, choices=PropertyType.choices)
    region = models.CharField(max_length=20, choices=Region.choices)
    address = models.CharField(max_length=255, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    area_value = models.DecimalField(max_digits=10, decimal_places=2)
    area_unit = models.CharField(
        max_length=10, choices=AreaUnit.choices, default=AreaUnit.SQFT
    )
    bedrooms = models.PositiveSmallIntegerField(null=True, blank=True)
    bathrooms = models.PositiveSmallIntegerField(null=True, blank=True)
    description = models.TextField(blank=True)
    latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.AVAILABLE
    )
    is_featured = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "properties"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["region"]),
            models.Index(fields=["property_type"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.title)
            slug = base_slug
            n = 1
            while Property.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                n += 1
                slug = f"{base_slug}-{n}"
            self.slug = slug
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("properties:detail", kwargs={"slug": self.slug})


class PropertyImage(models.Model):
    property = models.ForeignKey(
        Property, on_delete=models.CASCADE, related_name="images"
    )
    image = models.ImageField(upload_to="properties/%Y/%m/")
    caption = models.CharField(max_length=200, blank=True)
    is_primary = models.BooleanField(default=False)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.property.title} image {self.pk}"
