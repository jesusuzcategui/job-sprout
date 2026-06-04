"""
Detector de bilinguismo (Paso 3.2 del roadmap).

Combina dos senales:
1. Regex de niveles/certificaciones (C1, C2, B2, Advanced, Fluent, Bilingue, ...).
2. langdetect: si >50% de la descripcion esta en ingles -> probable bilingue.

Retorna (bilingue: bool, senales: dict, razon: str).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from config.settings import BILINGUAL_MIN_ENGLISH_RATIO, BILINGUAL_REGEX

log = logging.getLogger(__name__)

_LEVEL_RE = re.compile(BILINGUAL_REGEX, re.IGNORECASE)


@dataclass(frozen=True)
class LangResult:
    bilingual: bool
    english_ratio: float = 0.0
    level_hits: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    extras: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.bilingual


def _detect_english_ratio(text: str) -> float:
    """Calcula proporcion de segmentos en ingles usando langdetect.

    langdetect trabaja por chunks. Para una sola descripcion, estimamos
    el ratio = prob(en) sobre el primer analisis.
    """
    from langdetect import DetectorFactory, detect_langs

    DetectorFactory.seed = 0

    if not text or len(text.strip()) < 30:
        return 0.0

    sample = text[:4000]
    try:
        results = detect_langs(sample)
    except Exception as e:
        log.debug("langdetect fallo: %s", e)
        return 0.0

    for r in results:
        if r.lang == "en":
            return float(r.prob)
    return 0.0


def _find_levels(text: str) -> list[str]:
    seen: list[str] = []
    for m in _LEVEL_RE.finditer(text or ""):
        hit = m.group(0)
        if hit not in seen:
            seen.append(hit)
    return seen


def language_filter(descripcion: str, titulo: str = "") -> LangResult:
    """Determina si una vacante requiere/perfil bilingue.

    Args:
        descripcion: Texto completo de la oferta.
        titulo: Titulo (a veces menciona 'Bilingue' o 'English').
    """
    text = f"{titulo or ''}\n{descripcion or ''}".strip()
    if not text:
        return LangResult(bilingual=False, reasons=("texto_vacio",))

    levels = _find_levels(text)
    en_ratio = _detect_english_ratio(descripcion or text)

    reasons: list[str] = []
    if en_ratio >= BILINGUAL_MIN_ENGLISH_RATIO:
        reasons.append(f"en_ratio={en_ratio:.2f}>={BILINGUAL_MIN_ENGLISH_RATIO:.2f}")
    if levels:
        reasons.append(f"levels={','.join(levels[:5])}")

    is_bilingual = bool(reasons)

    extras = {
        "english_ratio": en_ratio,
        "n_levels": len(levels),
        "text_len": len(text),
    }

    return LangResult(
        bilingual=is_bilingual,
        english_ratio=en_ratio,
        level_hits=tuple(levels),
        reasons=tuple(reasons),
        extras=extras,
    )
