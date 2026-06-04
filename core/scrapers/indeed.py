"""
Scraper de Indeed Colombia (https://co.indeed.com).

Selectores verificados contra la estructura real (jun 2026):
- Job card:    div.job_seen_beacon
- Titulo:      a.jcs-JobTitle  (data-jk="...")
- Empresa:     span[data-testid="company-name"]
- Ubicacion:   div[data-testid="text-location"]
- Job key:     en href (?jk=...) o en data-jk

Notas importantes:
1. Indeed renderiza los datos basicos (titulo/empresa/ubicacion/salario) en el
   card del listing. NO hay snippet de descripcion.
2. La pagina de detalle (/viewjob?jk=...) esta bloqueada por Cloudflare para
   requests headless ("Additional Verification Required"). Por lo tanto, este
   scraper NO visita la pagina de detalle; usa solo los datos del card.
3. El "salario" se extrae del card por regex sobre el texto (no hay selector
   estable para el monto).
4. La "descripcion" se arma a partir de los datos del card para que el agente
   pueda hacer language filter + AI scoring basico.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, urlencode, urlparse

from playwright.sync_api import Page

from core.scrapers.base import BaseScraper, VacanteRaw

log = logging.getLogger(__name__)

BASE_URL = "https://co.indeed.com"

# Regex para extraer salario del texto: "$1.750.000" o "$1.5M" o "1.750.000 COP"
SALARY_PATTERN = re.compile(
    r"(?:\$\s*|COP\s*|COL\s*)?[\d]{1,3}(?:[.,]\d{3})+(?:\s*(?:a|-|al|hasta)\s*[\d.,\s]+)?"
    r"(?:\s*(?:COP|COL|mensual|mensuales|por mes|al mes|hour|hora))?",
    re.IGNORECASE,
)

# Regex mas flexible: busca cualquier "$X.XXX.XXX" o similar
SALARY_QUICK = re.compile(r"\$\s*[\d.,]+(?:\s*(?:por\s*mes|mensual|COP|COL|mil|K))?", re.IGNORECASE)


def _extract_jk(href: str) -> str:
    if not href:
        return ""
    try:
        qs = parse_qs(urlparse(href).query)
        return qs.get("jk", [""])[0]
    except Exception:
        return ""


def _build_job_url(jk: str) -> str:
    if not jk:
        return ""
    return f"{BASE_URL}/viewjob?jk={jk}"


def _clean_text(s: str | None) -> str:
    if not s:
        return ""
    return " ".join(s.split()).strip()


def _extract_salary(card_text: str) -> str:
    """Extrae el salario del texto del card usando regex.

    Indeed renderiza el salario en texto plano (ej: '$2.000.000 por mes' o
    '$1.5M'). Buscamos el primer match que tenga sentido.
    """
    if not card_text:
        return ""
    # Primero intentamos con el patron estricto
    for line in card_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if "$" in line and any(c.isdigit() for c in line):
            return _clean_text(line)
    # Fallback: buscar en todo el texto
    m = SALARY_QUICK.search(card_text)
    if m:
        return _clean_text(m.group(0))
    return ""


class IndeedScraper(BaseScraper):
    portal_name = "indeed"

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("navigation_timeout_ms", 35_000)
        super().__init__(**kwargs)

    def search_url(self, keyword: str, page_num: int = 1) -> str:
        params = {"q": keyword, "l": "Colombia"}
        if page_num > 1:
            params["start"] = (page_num - 1) * 10
        return f"{BASE_URL}/jobs?{urlencode(params)}"

    def _extract_one(self, card, keyword: str) -> VacanteRaw | None:
        try:
            link_loc = card.locator("a.jcs-JobTitle").first
            if link_loc.count() == 0:
                link_loc = card.locator("h2 a").first
                if link_loc.count() == 0:
                    return None

            href = link_loc.get_attribute("href") or ""
            jk = _extract_jk(href) or link_loc.get_attribute("data-jk") or ""
            if not jk:
                return None
            titulo = _clean_text(link_loc.inner_text())
            if not titulo:
                return None
            url = _build_job_url(jk)

            empresa = ""
            comp = card.locator("[data-testid='company-name']").first
            if comp.count() > 0:
                empresa = _clean_text(comp.inner_text())

            ubicacion = ""
            ubi = card.locator("[data-testid='text-location']").first
            if ubi.count() > 0:
                ubicacion = _clean_text(ubi.inner_text())

            card_text = ""
            try:
                card_text = card.inner_text() or ""
            except Exception:
                pass
            salario = _extract_salary(card_text)

            return VacanteRaw(
                portal=self.portal_name,
                titulo=titulo,
                url=url,
                external_id=jk,
                empresa=empresa,
                ubicacion=ubicacion,
                salario=salario,
            )
        except Exception as e:
            log.debug("[%s] fallo extrayendo card: %s", self.portal_name, e)
            return None

    def parse_listing(self, page: Page, keyword: str) -> list[VacanteRaw]:
        page.wait_for_timeout(2000)
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(500)
        except Exception:
            pass

        results: list[VacanteRaw] = []
        seen: set[str] = set()

        cards = page.locator("div.job_seen_beacon").all()
        if not cards:
            cards = page.locator("td.resultContent").all()
        if not cards:
            cards = page.locator("li[data-jk]").all()

        for card in cards:
            vac = self._extract_one(card, keyword)
            if vac and vac.external_id and vac.external_id not in seen:
                seen.add(vac.external_id)
                results.append(vac)
        return results

    def parse_detail(self, page: Page, raw: VacanteRaw) -> VacanteRaw:
        """Indeed bloquea el detail con Cloudflare. Usamos solo los datos del card.

        Si la pagina de detalle no esta bloqueada, extraemos la descripcion
        completa. Si esta bloqueada (caso comun), armamos una descripcion
        sintetica con los datos del card para que el language filter y la
        AI scoring tengan algo con que trabajar.
        """
        try:
            page.wait_for_timeout(800)

            page_title = ""
            try:
                page_title = (page.title() or "").lower()
            except Exception:
                pass

            blocked = any(
                token in page_title
                for token in (
                    "verification",
                    "verificaci",  # "Verificación adicional requerida" (ES)
                    "cloudflare",
                    "just a moment",
                    "security check",
                    "attention required",
                    "challenge",
                    "access denied",
                    "ray id",
                )
            )
            # Fallback: si el titulo no parece un titulo de oferta real
            # (los reales terminan en "Indeed.com" y son > 30 chars),
            # probablemente es un challenge.
            if not blocked and page_title and "indeed.com" not in page_title:
                blocked = True

            if not blocked:
                h1 = page.locator("h1").first
                if h1.count() > 0:
                    t = _clean_text(h1.inner_text())
                    if t and t != raw.titulo:
                        raw.titulo = t

                for sel, field in [
                    ("[data-testid='company-name']", "empresa"),
                    ("[data-testid='location']", "ubicacion"),
                    ("[data-testid='jobLocation']", "ubicacion"),
                    ("[data-testid='salary-snippet']", "salario"),
                ]:
                    loc = page.locator(sel).first
                    if loc.count() > 0:
                        t = _clean_text(loc.inner_text())
                        if t:
                            setattr(raw, field, t)

                desc = page.locator("#jobDescriptionText").first
                if desc.count() > 0:
                    raw.descripcion = _clean_text(desc.inner_text())
            else:
                log.debug(
                    "[%s] detail bloqueado por Cloudflare, uso solo datos del card",
                    self.portal_name,
                )
        except Exception as e:
            log.warning(
                "[%s] parse_detail parcial: %s url=%s",
                self.portal_name,
                e,
                raw.url,
            )

        # Construir descripcion sintetica si esta vacia (caso Cloudflare comun)
        if not raw.descripcion:
            parts = []
            if raw.titulo:
                parts.append(f"Titulo: {raw.titulo}")
            if raw.empresa:
                parts.append(f"Empresa: {raw.empresa}")
            if raw.ubicacion:
                parts.append(f"Ubicacion: {raw.ubicacion}")
            if raw.salario:
                parts.append(f"Salario: {raw.salario}")
            parts.append("(Descripcion completa solo visible en el portal de Indeed)")
            raw.descripcion = "\n".join(parts)

        return raw

        # Construir descripcion sintetica si esta vacia (caso Cloudflare)
        if not raw.descripcion:
            parts = []
            if raw.titulo:
                parts.append(f"Titulo: {raw.titulo}")
            if raw.empresa:
                parts.append(f"Empresa: {raw.empresa}")
            if raw.ubicacion:
                parts.append(f"Ubicacion: {raw.ubicacion}")
            if raw.salario:
                parts.append(f"Salario: {raw.salario}")
            parts.append("(Descripcion completa solo visible en el portal de Indeed)")
            raw.descripcion = "\n".join(parts)

        return raw
