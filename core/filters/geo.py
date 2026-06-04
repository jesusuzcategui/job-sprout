"""
Filtro geografico parametrizable por perfil (Fase 3 + extension Hito 4+).

Logica:
  1. Si la oferta menciona alguna de `ubicaciones_aceptadas` (case/acentos/plural
     insensitive, con negaciones filtradas) => APROBAR.
  2. Si `acepta_remoto` y la oferta es realmente remota (remoto / teletrabajo /
     home office / desde casa, con negaciones filtradas) => APROBAR.
  3. Resto => RECHAZAR (modo estricto).

Esto resuelve el bug donde una oferta de Bogota+Remoto pasaba siempre: con
ubicaciones_aceptadas = ["Mosquera", "Madrid", "Funza"] y acepta_remoto=False,
la oferta de Bogota ya no califica.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from config.settings import ALLOWED_LOCATIONS


def _norm(text: str) -> str:
    if not text:
        return ""
    t = text.lower()
    t = (
        t.replace("á", "a").replace("é", "e").replace("í", "i")
        .replace("ó", "o").replace("ú", "u").replace("ñ", "n")
    )
    return t


def _to_word_pattern(phrase: str) -> re.Pattern[str]:
    """Regex flexible: matchea genero/plural con word boundaries."""
    norm = _norm(phrase).strip()
    norm = re.sub(r"\s+", " ", norm)
    parts = norm.split(" ")
    flexible_parts: list[str] = []
    for p in parts:
        if len(p) >= 4 and p[-1] in {"o", "a"}:
            stem = re.escape(p[:-1])
            flexible_parts.append(rf"{stem}[ao]?s?")
        else:
            flexible_parts.append(re.escape(p))
    body = r"\s+".join(flexible_parts)
    return re.compile(r"\b" + body + r"\b", re.IGNORECASE)


_NEGATION_PREFIX_RE = re.compile(
    r"(?:no|sin|ni|tampoco|nunca)\s+$", re.IGNORECASE
)


def _strip_negations_for_patterns(
    text: str, patterns: list[re.Pattern[str]]
) -> str:
    """Borra matches de los patterns que esten precedidos por una negacion.

    A diferencia de la version anterior, NO borra 30 chars de texto arbitrario:
    solo reemplaza la coincidencia misma cuando va precedida de 'no/sin/ni/...'
    dentro de los 12 chars previos.
    """
    for pat in patterns:
        matches = list(pat.finditer(text))
        for m in reversed(matches):
            prefix = text[max(0, m.start() - 12):m.start()]
            if _NEGATION_PREFIX_RE.search(prefix):
                text = text[:m.start()] + (" " * len(m.group(0))) + text[m.end():]
    return text


_REMOTE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (a, _to_word_pattern(a)) for a in ALLOWED_LOCATIONS
]
_lookup_remote = re.compile(
    "|".join(p.pattern for _, p in _REMOTE_PATTERNS), re.IGNORECASE
)


@dataclass(frozen=True)
class GeoResult:
    approved: bool
    reason: str

    def __bool__(self) -> bool:
        return self.approved


def _normalize_locs(locs: list[str] | str | None) -> list[str]:
    """Acepta lista, string separado por comas, o None. Devuelve lista limpia."""
    if not locs:
        return []
    if isinstance(locs, str):
        locs = [s.strip() for s in locs.split(",")]
    return [l for l in (s.strip() for s in locs) if l]


def _find_first(
    text: str, patterns: list[tuple[str, re.Pattern[str]]]
) -> str | None:
    earliest: tuple[int, str] | None = None
    for label, pat in patterns:
        m = pat.search(text)
        if m and (earliest is None or m.start() < earliest[0]):
            earliest = (m.start(), label)
    return earliest[1] if earliest else None


def geo_filter(
    ubicacion: str,
    descripcion: str = "",
    *,
    ubicaciones_aceptadas: list[str] | str | None = None,
    acepta_remoto: bool = True,
) -> GeoResult:
    """Evalua la vacante contra la config geografica del perfil.

    Args:
        ubicacion: Campo de ubicacion de la oferta.
        descripcion: Texto completo de la oferta.
        ubicaciones_aceptadas: Lista (o CSV) de ciudades/zonas aceptadas.
            Ej: ["Mosquera", "Madrid", "Funza"] o "Mosquera,Madrid,Funza".
            Vacio significa que ninguna ciudad local es aceptada (solo remoto).
        acepta_remoto: Si True, vacantes con "remoto/teletrabajo/home office"
            en la descripcion se aprueban independientemente de la ciudad.
    """
    raw = f" {ubicacion or ''}  {descripcion or ''} "
    combined = _norm(raw)
    if not combined.strip():
        return GeoResult(False, "sin_informacion_geografica")

    locs = _normalize_locs(ubicaciones_aceptadas)
    loc_patterns: list[tuple[str, re.Pattern[str]]] = [
        (l, _to_word_pattern(l)) for l in locs
    ]
    active_patterns: list[re.Pattern[str]] = [p for _, p in loc_patterns]
    if acepta_remoto:
        active_patterns.extend(p for _, p in _REMOTE_PATTERNS)

    cleaned = _strip_negations_for_patterns(combined, active_patterns)

    if locs:
        loc_hit = _find_first(cleaned, loc_patterns)
        if loc_hit:
            return GeoResult(True, f"permitido:{loc_hit}")

    if acepta_remoto:
        remote_hit = _find_first(cleaned, _REMOTE_PATTERNS)
        if remote_hit:
            return GeoResult(True, f"permitido:{remote_hit}")

    if not locs and not acepta_remoto:
        return GeoResult(False, "sin_lugares_aceptados")

    return GeoResult(False, "ubicacion_no_reconocida")


def quick_geo_check(
    ubicacion: str,
    *,
    ubicaciones_aceptadas: list[str] | str | None = None,
    acepta_remoto: bool = True,
) -> GeoResult:
    """Version rapida para pre-filtrar en el listing (sin descripcion)."""
    return geo_filter(
        ubicacion,
        "",
        ubicaciones_aceptadas=ubicaciones_aceptadas,
        acepta_remoto=acepta_remoto,
    )


_WS_RE = re.compile(r"\s+")


def excerpt_for_reason(ubicacion: str, descripcion: str, reason: str) -> str:
    haystack = f"{ubicacion} || {descripcion}"
    haystack_norm = _norm(haystack)

    earliest: tuple[int, str] | None = None
    for label, pat in _REMOTE_PATTERNS:
        m = pat.search(haystack_norm)
        if m and (earliest is None or m.start() < earliest[0]):
            earliest = (m.start(), label)

    if earliest is None:
        return (ubicacion or "").strip()[:60] or "(sin ubicacion)"

    idx, _ = earliest
    start = max(0, idx - 20)
    end = min(len(haystack), idx + 40)
    return _WS_RE.sub(" ", haystack[start:end].strip())
