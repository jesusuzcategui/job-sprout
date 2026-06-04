"""
Scraper de Computrabajo Colombia.

Selectores verificados contra la estructura real del portal (jun 2026).
Si el portal cambia sus clases/HTML, ajustar SOLO este archivo.

Estructura observada:
- Listing:    https://co.computrabajo.com/trabajo-de-{keyword}
- Cada oferta: <article>  (selector: 'article')
- Link titulo: a.js-o-link  (href = /ofertas-de-trabajo/oferta-de-trabajo-de-...-{ID32})
- Empresa:    a[offer-grid-article-company-url]   (atributo custom)
- Ubicacion:  p.fs16.fc_base.mt5 > span.mr10
- Detalle:    h1.fs24 (titulo), <main> contiene empresa/ubicacion/salario/descripcion
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from playwright.sync_api import Page

from config.settings import PORTAL_COMPUTRABAJO
from core.scrapers.base import BaseScraper, VacanteRaw

log = logging.getLogger(__name__)

BASE_URL = "https://co.computrabajo.com"
ID_PATTERN = re.compile(r"([A-F0-9]{30,40})")


def _slugify_keyword(keyword: str) -> str:
    s = keyword.lower().strip()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
    return s.strip("-")


def _extract_external_id(href: str) -> str:
    if not href:
        return ""
    href = href.split("#", 1)[0]
    m = ID_PATTERN.search(href)
    return m.group(1) if m else ""


class ComputrabajoScraper(BaseScraper):
    portal_name = PORTAL_COMPUTRABAJO

    def search_url(self, keyword: str, page_num: int = 1) -> str:
        slug = _slugify_keyword(keyword) or "trabajo"
        return f"{BASE_URL}/trabajo-de-{slug}"

    def parse_listing(self, page: Page, keyword: str) -> list[VacanteRaw]:
        results: list[VacanteRaw] = []
        seen_urls: set[str] = set()

        articles = page.locator("article").all()
        for art in articles:
            try:
                link_loc = art.locator("a.js-o-link").first
                if link_loc.count() == 0:
                    continue
                href = link_loc.get_attribute("href") or ""
                if not href:
                    continue
                url = urljoin(BASE_URL, href.split("#", 1)[0])
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                titulo = self._clean_text(link_loc.inner_text())
                if not titulo:
                    continue

                empresa = ""
                comp_loc = art.locator("a[offer-grid-article-company-url]").first
                if comp_loc.count() > 0:
                    empresa = self._clean_text(comp_loc.inner_text())

                ubicacion = ""
                ubi_loc = art.locator("p.fs16.fc_base.mt5 > span.mr10").first
                if ubi_loc.count() > 0:
                    ubicacion = self._clean_text(ubi_loc.inner_text())

                external_id = _extract_external_id(href)

                results.append(
                    VacanteRaw(
                        portal=self.portal_name,
                        titulo=titulo,
                        url=url,
                        external_id=external_id,
                        empresa=empresa,
                        ubicacion=ubicacion,
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
            h1 = page.locator("h1").first
            if h1.count() > 0:
                h1_txt = self._clean_text(h1.inner_text())
                if h1_txt:
                    raw.titulo = h1_txt

            subtitle = page.locator("h1 ~ p").first
            if subtitle.count() > 0:
                sub_txt = self._clean_text(subtitle.inner_text())
                if " - " in sub_txt:
                    emp_part, ubi_part = sub_txt.split(" - ", 1)
                    raw.empresa = self._clean_text(emp_part)
                    raw.ubicacion = self._clean_text(ubi_part)

            main_loc = page.locator("main").first
            full_text = (
                self._clean_text(main_loc.inner_text())
                if main_loc.count()
                else (self._clean_text(page.locator("body").first.inner_text())
                      if page.locator("body").count() else "")
            )

            if full_text:
                sal_match = re.search(
                    r"\$\s*[\d\.,]+(?:\s*a\s*\$?\s*[\d\.,]+)?(?:\s*\([^\)]*\))?",
                    full_text,
                )
                if sal_match:
                    raw.salario = self._clean_text(sal_match.group(0))

                desc_start = full_text.find("Descripción de la oferta")
                if desc_start >= 0:
                    raw.descripcion = full_text[desc_start:].strip()
                else:
                    raw.descripcion = full_text

            if not raw.fecha_publicacion:
                pub = page.locator("p.fs13.fc_aux").first
                if pub.count() > 0:
                    raw.fecha_publicacion = self._clean_text(pub.inner_text())

        except Exception as e:
            log.warning(
                "[%s] parse_detail parcial: %s url=%s", self.portal_name, e, raw.url
            )
        return raw
