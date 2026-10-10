from decimal import Decimal

from django.db import migrations

# Spec's own example weights (see Phase 7's initial defaults) — admin-
# editable afterwards via ScoringConfigurationAdmin, this migration only
# seeds a sane starting point so the scoring engine has something to use.
DEFAULT_WEIGHTS = {
    "legal_documentation": Decimal("30"),
    "price_valuation": Decimal("20"),
    "location": Decimal("15"),
    "infrastructure": Decimal("10"),
    "market": Decimal("10"),
    "liquidity": Decimal("10"),
    "property_quality": Decimal("5"),
}


def seed_default_configuration(apps, schema_editor):
    ScoringConfiguration = apps.get_model("investment", "ScoringConfiguration")
    ScoringFactor = apps.get_model("investment", "ScoringFactor")

    if ScoringConfiguration.objects.exists():
        return  # don't clobber configuration an admin may have already set up

    configuration = ScoringConfiguration.objects.create(name="Default", property_type="", is_active=True)
    for category, weight in DEFAULT_WEIGHTS.items():
        ScoringFactor.objects.create(configuration=configuration, category=category, weight_percent=weight)


def remove_default_configuration(apps, schema_editor):
    ScoringConfiguration = apps.get_model("investment", "ScoringConfiguration")
    ScoringConfiguration.objects.filter(name="Default", property_type="").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("investment", "0002_alter_propertydocument_file"),
    ]

    operations = [
        migrations.RunPython(seed_default_configuration, remove_default_configuration),
    ]
