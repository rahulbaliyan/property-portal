from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils.text import slugify

CLOUDINARY_IMAGE_BACKEND = "cloudinary_storage.storage.MediaCloudinaryStorage"


def video_storage():
    """Cloudinary requires videos to go through its video-specific
    resource type — the default (image) storage backend rejects them.
    Mirrors whatever STORAGES["default"] is already configured to use,
    so local dev (FileSystemStorage) is unaffected."""
    if settings.STORAGES["default"]["BACKEND"] == CLOUDINARY_IMAGE_BACKEND:
        from cloudinary_storage.storage import VideoMediaCloudinaryStorage

        return VideoMediaCloudinaryStorage()
    from django.core.files.storage import FileSystemStorage

    return FileSystemStorage()


class Property(models.Model):
    class PropertyType(models.TextChoices):
        PLOT = "plot", "Plot"
        LAND = "land", "Land / Zameen"
        FLAT = "flat", "Flat"
        VILLA = "villa", "Villa"
        FARMHOUSE = "farmhouse", "Farmhouse"
        HOUSE = "house", "Independent House"

    class Region(models.TextChoices):
        MALDEVTA = "maldevta", "Maldevta"
        DEHRADUN = "dehradun", "Dehradun"
        DHANAULTI = "dhanaulti", "Dhanaulti"
        MUSSOORIE = "mussoorie", "Mussoorie"
        SAHASTRADHARA = "sahastradhara", "Sahastradhara"
        GARHWAL = "garhwal", "Garhwal"
        THANO = "thano", "Thano"
        RISHIKESH = "rishikesh", "Rishikesh"

    class AreaUnit(models.TextChoices):
        SQFT = "sqft", "Sq. Ft."
        GAJ = "gaj", "Gaj"
        NALI = "nali", "Nali"
        BIGHA = "bigha", "Bigha"
        ACRE = "acre", "Acre"

    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        UNDER_NEGOTIATION = "under_negotiation", "Under Negotiation"
        SOLD = "sold", "Sold"

    class ListingIntent(models.TextChoices):
        SALE = "sale", "For Sale"
        RENT = "rent", "For Rent"

    class ListingSource(models.TextChoices):
        ADMIN = "admin", "Added by Admin"
        SELLER = "seller", "Submitted by Seller"

    class ModerationStatus(models.TextChoices):
        APPROVED = "approved", "Approved"
        PENDING = "pending", "Pending Review"
        REJECTED = "rejected", "Rejected"

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    property_type = models.CharField(max_length=10, choices=PropertyType.choices)
    listing_intent = models.CharField(
        max_length=10, choices=ListingIntent.choices, default=ListingIntent.SALE
    )
    region = models.CharField(max_length=20, choices=Region.choices)
    address = models.CharField(max_length=255, blank=True)
    price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Leave blank for 'Price on Request' — shows Call/WhatsApp links instead.",
    )
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

    # Public "Sell Your Property" submissions land here as PENDING and
    # ADMIN's own listings default to APPROVED. Seller contact details
    # are for admin's eyes only — never rendered in public templates —
    # and the price is always masked publicly for seller-sourced
    # listings via the public_price property below, regardless of
    # what value is actually stored.
    listing_source = models.CharField(
        max_length=10, choices=ListingSource.choices, default=ListingSource.ADMIN
    )
    moderation_status = models.CharField(
        max_length=10,
        choices=ModerationStatus.choices,
        default=ModerationStatus.APPROVED,
    )
    seller_name = models.CharField(max_length=100, blank=True)
    seller_phone = models.CharField(max_length=20, blank=True)
    seller_email = models.EmailField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "properties"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["region"]),
            models.Index(fields=["property_type"]),
            models.Index(fields=["status"]),
            models.Index(fields=["moderation_status"]),
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

    @property
    def public_price(self):
        """The price shown to buyers. Always None for seller-submitted
        listings, regardless of the real stored value — buyers reach
        out to us for those, never see a figure directly. Templates
        and the WhatsApp message should read this, never .price,
        anywhere buyer-facing."""
        if self.listing_source == self.ListingSource.SELLER:
            return None
        return self.price

    def whatsapp_message(self):
        if self.public_price:
            return f"Hi, I'm interested in {self.title} (₹{self.public_price:.0f})"
        return f"Hi, I'm interested in {self.title} - please share the price"


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


class PropertyVideo(models.Model):
    property = models.ForeignKey(
        Property, on_delete=models.CASCADE, related_name="videos"
    )
    video = models.FileField(
        upload_to="properties/videos/%Y/%m/",
        storage=video_storage,
    )
    caption = models.CharField(max_length=200, blank=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.property.title} video {self.pk}"

    def optimized_url(self):
        """Cloudinary transcodes to a smaller codec/bitrate and serves
        the best format for the requesting browser on the fly (cached
        at their CDN edge after the first view) — cuts bandwidth per
        view substantially without re-encoding/duplicating the stored
        original. No-op locally, where videos aren't on Cloudinary.

        A plain method, not @property — this model's FK field is
        itself named "property", which shadows the property() builtin
        within this class body."""
        if settings.STORAGES["default"]["BACKEND"] != CLOUDINARY_IMAGE_BACKEND:
            return self.video.url

        import cloudinary

        url, _ = cloudinary.utils.cloudinary_url(
            self.video.name,
            resource_type="video",
            quality="auto",
            fetch_format="auto",
            secure=True,
        )
        return url

    def thumbnail_url(self):
        """A still frame Cloudinary extracts from the video, used as a
        preview image wherever a listing has a video but no photos.
        None locally — generating a frame without Cloudinary would
        need ffmpeg, which isn't worth adding for a dev-only fallback."""
        if settings.STORAGES["default"]["BACKEND"] != CLOUDINARY_IMAGE_BACKEND:
            return None

        import cloudinary

        url, _ = cloudinary.utils.cloudinary_url(
            self.video.name,
            resource_type="video",
            format="jpg",
            quality="auto",
            secure=True,
        )
        return url
