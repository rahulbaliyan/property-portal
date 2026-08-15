from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Property, PropertyImage, PropertyVideo


@receiver(post_save, sender=Property)
@receiver(post_delete, sender=Property)
@receiver(post_save, sender=PropertyImage)
@receiver(post_delete, sender=PropertyImage)
@receiver(post_save, sender=PropertyVideo)
@receiver(post_delete, sender=PropertyVideo)
def clear_page_cache_on_property_change(sender, **kwargs):
    """cache_page on home/listings has no way to target just the
    affected entries — cache_page keys are hashed from the full URL
    (including every filter combination on the listings page), and
    LocMemCache has no pattern-delete. A full flush is cheap at this
    traffic level and guarantees admin edits/seller approvals show up
    immediately instead of waiting out the cache TTL."""
    cache.clear()
