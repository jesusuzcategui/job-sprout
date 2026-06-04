"""
Scraper de El Empleo Colombia.

Selectores verificados contra la estructura real del portal (jun 2026).
Si el portal cambia sus clases/HTML, ajustar SOLO este archivo.

Estructura observada:
- Listing:    https://www.elempleo.com/co/ofertas-empleo/?q={keyword}
- Cada oferta: contenedor div.js-area-bind[data-url] (data-url tiene la URL completa)
- Atributo data-ga4-offerdata: JSON-encoded con id, title, company, location, salary
  (muy util como fallback de extraccion en el listing)
- Titulo:     a.js-offer-title.titulo
- Empresa:    span.info-company-name.js-offer-company
- Detalle:    div.description-block.e-container-keywords > p.mb-0
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import urljoin

from playwright.sync_api import Page

from config.settings import PORTAL_ELEMPLEO
from core.scrapers.base import BaseScraper, VacanteRaw

log = logging.getLogger(__name__)

BASE_URL = "https://www.elempleo.com"
ID_PATTERN = re.compile(r"(\d{6,})$")


def _extract_external_id(href: str) -> str:
    if not href:
        return ""
    href = href.split("?", 1)[0].split("#", 1)[0]
    m = ID_PATTERN.search(href)
    return m.group(1) if m else ""


class ElEmpleoScraper(BaseScraper):
    portal_name = PORTAL_ELEMPLEO

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("navigation_timeout_ms", 60_000)
        super().__init__(**kwargs)

    def search_url(self, keyword: str, page_num: int = 1) -> str:
        from urllib.parse import urlencode
        params = urlencode({"q": keyword, "page": page_num})
        return f"{BASE_URL}/co/ofertas-empleo/?{params}"

    def _parse_ga4_offerdata(self, raw_attr: str | None) -> dict:
        if not raw_attr:
            return {}
        try:
            return json.loads(raw_attr.replace("&quot;", '"'))
        except (json.JSONDecodeError, TypeError):
            return {}

    def parse_listing(self, page: Page, keyword: str) -> list[VacanteRaw]:
        results: list[VacanteRaw] = []
        seen_urls: set[str] = set()

        containers = page.locator("div.js-area-bind[data-url]").all()
        if not containers:
            containers = page.locator("div[class*='result-item']").all()

        for ctn in containers:
            try:
                data_url = ctn.get_attribute("data-url") or ""
                if not data_url:
                    link_loc = ctn.locator("a.js-offer-title").first
                    if link_loc.count() == 0:
                        continue
                    data_url = link_loc.get_attribute("href") or ""
                if not data_url:
                    continue

                url = urljoin(BASE_URL, data_url)
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                external_id = _extract_external_id(data_url)

                ga4 = self._parse_ga4_offerdata(ctn.get_attribute("data-ga4-offerdata"))
                titulo = ga4.get("title") or ""
                empresa = ga4.get("company") or ""
                ubicacion = ga4.get("location") or ""
                salario = ga4.get("salary") or ""

                if not titulo:
                    title_loc = ctn.locator("a.js-offer-title").first
                    if title_loc.count() > 0:
                        titulo = self._clean_text(
                            title_loc.get_attribute("title")
                            or title_loc.inner_text()
                        )
                if not empresa:
                    comp_loc = ctn.locator("span.info-company-name").first
                    if comp_loc.count() > 0:
                        empresa = self._clean_text(comp_loc.inner_text())

                if not titulo:
                    continue

                results.append(
                    VacanteRaw(
                        portal=self.portal_name,
                        titulo=titulo,
                        url=url,
                        external_id=external_id,
                        empresa=empresa,
                        ubicacion=ubicacion,
                        salario=salario,
                    )
                )
            except Exception as e:
                log.debug(
                    "[%s] fallo extrayendo tarjeta: %s", self.portal_name, e
                )
                continue

        return results

    def parse_detail(self, page: Page, raw: VacanteRaw) -> VacanteRaw:
        try:
            page.wait_for_timeout(800)

            h1 = page.locator("h1").first
            if h1.count() > 0:
                h1_txt = self._clean_text(h1.inner_text())
                if h1_txt:
                    raw.titulo = h1_txt

            comp = page.locator("span.info-company-name").first
            if comp.count() > 0:
                emp = self._clean_text(comp.inner_text())
                if emp:
                    raw.empresa = emp

            header = page.locator("#offer-data").first
            if header.count() > 0:
                header_text = self._clean_text(header.inner_text())
                if not raw.ubicacion:
                    ubi_m = re.search(
                        r"Ubicación\s*\n?\s*([^\n]+)", header_text
                    )
                    if ubi_m:
                        raw.ubicacion = self._clean_text(ubi_m.group(1))
                if not raw.salario:
                    sal_m = re.search(
                        r"Salario\s*\n?\s*([^\n]+)", header_text
                    )
                    if sal_m:
                        raw.salario = self._clean_text(sal_m.group(1))
                pub_m = re.search(
                    r"Publicado\s+(\d{1,2}\s+\w+\s+\d{4})", header_text
                )
                if pub_m:
                    raw.fecha_publicacion = self._clean_text(pub_m.group(1))

            desc_loc = page.locator(
                "div.description-block.e-container-keywords > p.mb-0"
            ).first
            if desc_loc.count() == 0:
                desc_loc = page.locator("div.description-block p").first
            if desc_loc.count() > 0:
                raw.descripcion = self._clean_text(desc_loc.inner_text())

            if not raw.descripcion:
                for fallback in [
                    "section.descripcion",
                    "#descripcion",
                    "div[itemprop='description']",
                ]:
                    loc = page.locator(fallback).first
                    if loc.count() > 0:
                        raw.descripcion = self._clean_text(loc.inner_text())
                        if raw.descripcion:
                            break

        except Exception as e:
            log.warning(
                "[%s] parse_detail parcial: %s url=%s",
                self.portal_name,
                e,
                raw.url,
            )
        return raw
