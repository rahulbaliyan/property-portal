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

    class Ownership(models.TextChoices):
        FREEHOLD = "freehold", "Freehold"
        LEASEHOLD = "leasehold", "Leasehold"
        POWER_OF_ATTORNEY = "power_of_attorney", "Power of Attorney"
        COOPERATIVE_SOCIETY = "cooperative_society", "Cooperative Society"

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

    # Optional detail-page fields — only ever shown when actually filled
    # in for a given listing, never a placeholder "N/A" wall. Kept as
    # plain text where real-world values vary too much for a fixed
    # choice list (road width, land use, registry status).
    road_width = models.CharField(
        max_length=100, blank=True, help_text='e.g. "20 ft" — leave blank if unknown.'
    )
    ownership = models.CharField(max_length=25, choices=Ownership.choices, blank=True)
    registry_status = models.CharField(
        max_length=100, blank=True, help_text='e.g. "Registry Ready", "Registry in Process".'
    )
    land_use = models.CharField(
        max_length=100, blank=True, help_text='e.g. "Residential", "Agricultural".'
    )
    amenities = models.TextField(
        blank=True, help_text="Free text, one per line — only shown if filled in."
    )

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


class Location(models.Model):
    """Editorial content for a region's landing page (and the homepage's
    locations section) — kept out of templates so it's admin-editable
    without a code deploy. One row per Property.Region value; region
    itself isn't a separate enum here to avoid the two drifting apart."""

    region = models.CharField(
        max_length=20, choices=Property.Region.choices, unique=True
    )
    description = models.TextField(
        blank=True,
        help_text="Shown on the homepage locations section and this region's "
        "landing page. Leave blank until real copy is ready — the region "
        "still appears, just without a description.",
    )
    image = models.ImageField(
        upload_to="locations/",
        null=True,
        blank=True,
        help_text="Leave blank until a real photograph is available — falls "
        "back to a plain placeholder rather than a stock image.",
    )

    class Meta:
        ordering = ["region"]

    def __str__(self):
        return self.get_region_display()

    def optimized_image_url(self):
        """Mirrors PropertyVideo's optimized_url()/thumbnail_url() pattern
        — Cloudinary's quality=auto,fetch_format=auto on the way in, so
        this field doesn't repeat PropertyImage's existing gap (raw,
        untransformed URLs) from day one. No-op locally."""
        if not self.image:
            return ""
        if settings.STORAGES["default"]["BACKEND"] != CLOUDINARY_IMAGE_BACKEND:
            return self.image.url

        import cloudinary

        url, _ = cloudinary.utils.cloudinary_url(
            self.image.name, quality="auto", fetch_format="auto", secure=True
        )
        return url


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
