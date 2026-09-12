from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from .models import Property


class PropertyListPaginationTests(TestCase):
    """A live 500 (django.core.paginator.EmptyPage) hit production:
    list.html unconditionally rendered {{ page_obj.previous_page_number }}
    / {{ page_obj.next_page_number }} even when there was no such page —
    those raise EmptyPage rather than returning None, so evaluating them
    on page 1 (no previous) or the last page (no next) crashed the whole
    render instead of just leaving a dead link. Needs >12 properties
    (Paginator's page size here) to actually produce a second page."""

    def setUp(self):
        cache.clear()
        for i in range(13):
            Property.objects.create(
                title=f"Test Property {i}",
                property_type=Property.PropertyType.PLOT,
                region=Property.Region.DEHRADUN,
                area_value=100,
                moderation_status=Property.ModerationStatus.APPROVED,
            )

    def test_first_page_does_not_crash_on_previous_link(self):
        response = self.client.get(reverse("properties:list"))
        self.assertEqual(response.status_code, 200)

    def test_last_page_does_not_crash_on_next_link(self):
        response = self.client.get(reverse("properties:list"), {"page": 2})
        self.assertEqual(response.status_code, 200)

    def test_out_of_range_page_does_not_crash(self):
        # Paginator.get_page() falls back to the last page for an
        # out-of-range number rather than raising — exercised here since
        # that's exactly the kind of input (bad ?page= from a stale link,
        # a bot, or a user editing the URL) that triggered this live.
        response = self.client.get(reverse("properties:list"), {"page": 999})
        self.assertEqual(response.status_code, 200)


class LocationDetailPaginationTests(TestCase):
    """Same bug, same fix, duplicated in location_detail.html."""

    def setUp(self):
        cache.clear()
        for i in range(13):
            Property.objects.create(
                title=f"Test Property {i}",
                property_type=Property.PropertyType.PLOT,
                region=Property.Region.DEHRADUN,
                area_value=100,
                moderation_status=Property.ModerationStatus.APPROVED,
            )

    def test_first_and_last_page_do_not_crash(self):
        url = reverse("location_detail", args=["dehradun"])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get(url, {"page": 2}).status_code, 200)
