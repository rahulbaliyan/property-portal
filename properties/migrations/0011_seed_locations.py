from django.db import migrations


def seed_locations(apps, schema_editor):
    Location = apps.get_model("properties", "Location")
    Property = apps.get_model("properties", "Property")
    region_field = Property._meta.get_field("region")
    for value, _label in region_field.choices:
        Location.objects.get_or_create(region=value)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("properties", "0010_location"),
    ]

    operations = [
        migrations.RunPython(seed_locations, noop_reverse),
    ]
