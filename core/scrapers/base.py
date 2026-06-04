"""
Clase base para todos los scrapers de portales.

Define el contrato (search + parse_listing + parse_detail) y la logica comun:
- Crear browser Playwright con UA realista
- Sleep aleatorio entre 2-5s (configurable)
- Reintentos en caso de error
- Template method `run()` que coordina el flujo
"""
from __future__ import annotations

import logging
import random
import re
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Iterator

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from config.settings import (
    HEADLESS,
    MAX_SLEEP_SECONDS,
    MIN_SLEEP_SECONDS,
    NAVIGATION_TIMEOUT_MS,
    PAGE_LOAD_TIMEOUT_MS,
    USER_AGENT,
)

log = logging.getLogger(__name__)


@dataclass
class VacanteRaw:
    """Representacion en memoria de una vacante scrapeada, previa a la DB."""

    portal: str
    titulo: str
    url: str
    external_id: str = ""
    empresa: str = ""
    ubicacion: str = ""
    salario: str = ""
    descripcion: str = ""
    fecha_publicacion: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScrapeStats:
    """Trazabilidad de la corrida (sirve para la GUI en Fase 4)."""

    portal: str
    keyword: str = ""
    offers_seen: int = 0
    offers_parsed: int = 0
    errors: int = 0
    elapsed_seconds: float = 0.0
    vacantes: list[VacanteRaw] = field(default_factory=list)


class BaseScraper(ABC):
    """Contrato que todo scraper de portal debe implementar."""

    portal_name: str = "base"

    def __init__(
        self,
        *,
        headless: bool = HEADLESS,
        min_sleep: float = MIN_SLEEP_SECONDS,
        max_sleep: float = MAX_SLEEP_SECONDS,
        user_agent: str = USER_AGENT,
        navigation_timeout_ms: int = NAVIGATION_TIMEOUT_MS,
        max_retries: int = 2,
        browser_engine: str = "chromium",
        browser_path: str = "",
        browser_mode: str = "ephemeral",
        browser_profile_dir: str = "data/browser_profile",
    ) -> None:
        if min_sleep < 0 or max_sleep < min_sleep:
            raise ValueError("Rango de sleep invalido")
        self.headless = headless
        self.min_sleep = float(min_sleep)
        self.max_sleep = float(max_sleep)
        self.user_agent = user_agent
        self.nav_timeout = navigation_timeout_ms
        self.max_retries = max_retries
        self.browser_engine = browser_engine
        self.browser_path = browser_path
        self.browser_mode = browser_mode
        self.browser_profile_dir = browser_profile_dir

    @abstractmethod
    def search_url(self, keyword: str, page_num: int = 1) -> str:
        """URL de busqueda del portal para una keyword dada."""

    @abstractmethod
    def parse_listing(self, page: Page, keyword: str) -> list[VacanteRaw]:
        """Extrae la lista de ofertas (basico, sin descripcion completa)."""

    @abstractmethod
    def parse_detail(self, page: Page, raw: VacanteRaw) -> VacanteRaw:
        """Visita la URL de detalle y completa la descripcion.
        Mutates and returns `raw`.
        """

    def _random_sleep(self, multiplier: float = 1.0) -> None:
        delta = random.uniform(self.min_sleep, self.max_sleep) * multiplier
        time.sleep(max(0.0, delta))

    @contextmanager
    def _browser_context(self) -> Iterator[tuple[Browser, BrowserContext, Page]]:
        if self.browser_mode == "persistent":
            yield from self._persistent_context()
        else:
            yield from self._ephemeral_context()

    def _ephemeral_context(self) -> Iterator[tuple[Browser, BrowserContext, Page]]:
        with sync_playwright() as p:
            from core.browser import launch_browser
            browser = launch_browser(
                p,
                engine=self.browser_engine,
                custom_path=self.browser_path,
                headless=self.headless,
            )
            try:
                ctx = browser.new_context(
                    user_agent=self.user_agent,
                    viewport={"width": 1366, "height": 768},
                    locale="es-CO",
                    timezone_id="America/Bogota",
                )
                ctx.set_default_navigation_timeout(self.nav_timeout)
                page = ctx.new_page()
                yield browser, ctx, page
            finally:
                browser.close()

    def _persistent_context(self) -> Iterator[tuple[Browser, BrowserContext, Page]]:
        with sync_playwright() as p:
            from core.browser import launch_persistent_browser
            ctx = launch_persistent_browser(
                p,
                engine=self.browser_engine,
                custom_path=self.browser_path,
                profile_dir=self.browser_profile_dir,
                headed=not self.headless,
                user_agent=self.user_agent,
                viewport={"width": 1440, "height": 900},
                locale="en-US",
            )
            try:
                ctx.set_default_navigation_timeout(self.nav_timeout)
                if ctx.pages:
                    page = ctx.pages[0]
                else:
                    page = ctx.new_page()
                # En persistent mode, "browser" y "ctx" son el mismo objeto
                yield ctx, ctx, page
            finally:
                ctx.close()

    def _safe_goto(self, page: Page, url: str) -> bool:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                page.goto(url, timeout=self.nav_timeout, wait_until="domcontentloaded")
                return True
            except PlaywrightTimeoutError as e:
                last_error = e
                log.warning(
                    "[%s] goto timeout (intento %d/%d) url=%s",
                    self.portal_name,
                    attempt + 1,
                    self.max_retries + 1,
                    url,
                )
                if attempt < self.max_retries:
                    self._random_sleep(multiplier=1.5)
            except Exception as e:
                last_error = e
                log.exception("[%s] goto error url=%s", self.portal_name, url)
                break
        log.error("[%s] goto fallo definitivamente: %s", self.portal_name, last_error)
        return False

    @staticmethod
    def _clean_text(s: str | None) -> str:
        if not s:
            return ""
        s = re.sub(r"\s+", " ", s)
        return s.strip()

    @staticmethod
    def _truncate(s: str, max_chars: int) -> str:
        if len(s) <= max_chars:
            return s
        return s[:max_chars].rstrip() + "..."

    def run(
        self,
        keyword: str,
        *,
        fetch_details: bool = True,
        detail_limit: int | None = None,
    ) -> ScrapeStats:
        """Template method: abre browser, parsea listado y (opcional) detalles."""
        from config.settings import MAX_DESCRIPTION_CHARS

        stats = ScrapeStats(portal=self.portal_name, keyword=keyword)
        t0 = time.monotonic()

        try:
            with self._browser_context() as (_, ctx, page):
                url = self.search_url(keyword)
                log.info("[%s] search url=%s", self.portal_name, url)
                if not self._safe_goto(page, url):
                    stats.errors += 1
                    return stats

                self._random_sleep(multiplier=0.6)

                try:
                    raws = self.parse_listing(page, keyword)
                except Exception as e:
                    log.exception("[%s] parse_listing fallo: %s", self.portal_name, e)
                    stats.errors += 1
                    return stats

                stats.offers_seen = len(raws)
                log.info(
                    "[%s] listing devolvio %d ofertas", self.portal_name, len(raws)
                )

                if not fetch_details:
                    stats.vacantes = raws
                    stats.offers_parsed = len(raws)
                    return stats

                to_fetch = raws if detail_limit is None else raws[:detail_limit]
                for i, raw in enumerate(to_fetch, 1):
                    self._random_sleep()
                    try:
                        if not self._safe_goto(page, raw.url):
                            raw.error = "goto_failed"
                            stats.errors += 1
                        else:
                            try:
                                self.parse_detail(page, raw)
                                raw.descripcion = self._truncate(
                                    raw.descripcion or "", MAX_DESCRIPTION_CHARS
                                )
                                stats.offers_parsed += 1
                            except Exception as e:
                                log.warning(
                                    "[%s] parse_detail fallo url=%s err=%s",
                                    self.portal_name,
                                    raw.url,
                                    e,
                                )
                                raw.error = f"parse_detail:{type(e).__name__}"
                                stats.errors += 1
                    except Exception as e:
                        log.exception(
                            "[%s] error inesperado en oferta #%d: %s",
                            self.portal_name,
                            i,
                            e,
                        )
                        raw.error = f"unexpected:{type(e).__name__}"
                        stats.errors += 1
                    finally:
                        stats.vacantes.append(raw)
        finally:
            stats.elapsed_seconds = round(time.monotonic() - t0, 2)
            log.info(
                "[%s] fin: parsed=%d errors=%d elapsed=%.1fs",
                self.portal_name,
                stats.offers_parsed,
                stats.errors,
                stats.elapsed_seconds,
            )

        return stats
