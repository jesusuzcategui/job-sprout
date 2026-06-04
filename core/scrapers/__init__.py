"""
Registro de scrapers disponibles. Usado por el CLI y luego por el agente (Fase 3).
"""
from __future__ import annotations

from typing import Type

from config.settings import (
    PORTAL_COMPUTRABAJO,
    PORTAL_ELEMPLEO,
    PORTAL_INDEED,
    PORTAL_LINKEDIN,
    PORTAL_MAGNEMPLEOS,
)
from core.scrapers.base import BaseScraper

_REGISTRY: dict[str, Type[BaseScraper]] = {}


def _register_lazy() -> None:
    if _REGISTRY:
        return
    from core.scrapers.computrabajo import ComputrabajoScraper
    from core.scrapers.elempleo import ElEmpleoScraper
    from core.scrapers.indeed import IndeedScraper
    from core.scrapers.linkedin import LinkedInScraper

    _REGISTRY[PORTAL_COMPUTRABAJO] = ComputrabajoScraper
    _REGISTRY[PORTAL_ELEMPLEO] = ElEmpleoScraper
    _REGISTRY[PORTAL_INDEED] = IndeedScraper
    _REGISTRY[PORTAL_LINKEDIN] = LinkedInScraper
    # Magneto no implementado todavia; queda en SUPPORTED_PORTALS para futuro


def get_scraper(portal: str, **kwargs) -> BaseScraper:
    _register_lazy()
    cls = _REGISTRY.get(portal)
    if cls is None:
        raise KeyError(
            f"Portal desconocido o no implementado: {portal!r}. "
            f"Disponibles: {list(_REGISTRY)}"
        )
    return cls(**kwargs)


def list_portals() -> list[str]:
    _register_lazy()
    return list(_REGISTRY)


def list_available_portals() -> list[dict[str, str]]:
    """Retorna [{key, label, implemented}] para todos los portales soportados."""
    _register_lazy()
    from config.settings import PORTAL_LABELS
    out = []
    for p in list_portals() + [PORTAL_MAGNEMPLEOS]:
        out.append({
            "key": p,
            "label": PORTAL_LABELS.get(p, p),
            "implemented": p in _REGISTRY,
        })
    return out
