"""Filtros de vacantes (geografico + idioma)."""
from core.filters.geo import GeoResult, excerpt_for_reason, geo_filter, quick_geo_check
from core.filters.language import LangResult, language_filter

__all__ = [
    "GeoResult",
    "LangResult",
    "excerpt_for_reason",
    "geo_filter",
    "language_filter",
    "quick_geo_check",
]
