"""Loads the verified government-source registry for the report's
'not automated — check manually' section."""

from functools import lru_cache

import yaml
from django.conf import settings

SOURCES_YAML_PATH = settings.BASE_DIR / "configs" / "sources.yaml"

# The only source this app automates — every other verified source is
# surfaced as a manual-check link instead.
AUTOMATED_SOURCE_ID = "bhulekh_public_ror"


@lru_cache(maxsize=1)
def load_non_bhulekh_sources() -> list[dict]:
    """Every entry in configs/sources.yaml except the one this app already
    automates, grouped implicitly by 'category' for the template to
    iterate. Static file content — cached for the life of the process."""
    with open(SOURCES_YAML_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return [s for s in data.get("sources", []) if s.get("id") != AUTOMATED_SOURCE_ID]
