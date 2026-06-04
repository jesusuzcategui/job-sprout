"""
Scraper de LinkedIn Jobs (https://www.linkedin.com/jobs).

ADVERTENCIAS (leer antes de usar):
1. LinkedIn prohibe scraping en sus ToS. Tu cuenta puede ser baneada.
2. Usamos login con cookies persistentes (no relogin cada vez) para
   minimizar senales de deteccion.
3. Defaults conservadores: max 20 ofertas por sesion, delays 3-6s, logout al final.
4. Si LinkedIn muestra CAPTCHA o 2FA, el scraper aborta gracefully.

Estructura observada (jun 2026):
- Login:        /login  (form: session_key + session_password)
- Search:       /jobs/search/?keywords=...&location=...
- Job card:     div.base-card  con data-job-id, link a /jobs/view/{id}/
- Job detail:   /jobs/view/{id}/
  - title:      h1.top-card-layout__title
  - company:    a.topcard__org-name-link
  - location:   span.topcard__flavor--bullet
  - description: div.show-more-less-html__markup

Sesion:
- Cookies se guardan en data/linkedin_cookies.json (chmod 600)
- Se reutilizan en corridas siguientes para evitar relogin
- Si cookies expiran, el scraper intenta relogin una vez
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import (
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

from core.scrapers.base import BaseScraper, VacanteRaw

log = logging.getLogger(__name__)

BASE_URL = "https://www.linkedin.com"
LOGIN_URL = f"{BASE_URL}/login"
DEFAULT_COOKIES_PATH = Path("data/linkedin_cookies.json")


def _clean(s: str | None) -> str:
    if not s:
        return ""
    return " ".join(s.split()).strip()


def _human_delay(min_s: float = 3.0, max_s: float = 6.0) -> None:
    """Delay aleatorio para simular comportamiento humano."""
    time.sleep(random.uniform(min_s, max_s))


class LinkedInScraper(BaseScraper):
    portal_name = "linkedin"

    def __init__(
        self,
        *,
        email: str | None = None,
        password: str | None = None,
        cookies_path: str | Path | None = None,
        max_offers_per_session: int = 20,
        **kwargs,
    ) -> None:
        # Defaults mas relajados para no levantar sospechas
        kwargs.setdefault("min_sleep", 3.0)
        kwargs.setdefault("max_sleep", 6.0)
        kwargs.setdefault("navigation_timeout_ms", 45_000)
        super().__init__(**kwargs)

        self._email = email
        self._password = password
        self._cookies_path = Path(cookies_path) if cookies_path else DEFAULT_COOKIES_PATH
        self._max_offers = max(1, int(max_offers_per_session))
        self._logged_in = False

    def _save_cookies(self, ctx: BrowserContext) -> None:
        try:
            self._cookies_path.parent.mkdir(parents=True, exist_ok=True)
            storage = ctx.storage_state()
            self._cookies_path.write_text(json.dumps(storage, indent=2))
            try:
                self._cookies_path.chmod(0o600)
            except Exception:
                pass
            log.info(
                "[%s] cookies guardadas en %s", self.portal_name, self._cookies_path
            )
        except Exception as e:
            log.warning("[%s] fallo guardando cookies: %s", self.portal_name, e)

    def _load_cookies(self, ctx: BrowserContext) -> bool:
        if not self._cookies_path.exists():
            return False
        try:
            storage = json.loads(self._cookies_path.read_text())
            ctx.add_cookies(storage.get("cookies", []))
            log.info("[%s] cookies cargadas (%d)", self.portal_name, len(storage.get("cookies", [])))
            return True
        except Exception as e:
            log.warning("[%s] fallo cargando cookies: %s", self.portal_name, e)
            return False

    def _is_logged_in(self, page: Page) -> bool:
        try:
            # Ir a /feed (pagina principal de LinkedIn) para chequear sesion
            page.goto(f"{BASE_URL}/feed", timeout=20_000, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            url = page.url
            # Si redirige a /login o /signup, NO esta logueado
            if "/login" in url or "/signup" in url or "/authwall" in url:
                return False
            # Verificar que el nav de LinkedIn tenga el avatar
            avatar = page.locator('img[alt*="photo"], nav img[src*="profile"]').count()
            return avatar > 0
        except Exception:
            return False

    def _login(self, ctx: BrowserContext, page: Page) -> bool:
        """Intenta login. Usa cookies si existen, si no, password."""
        # 1. Intentar con cookies existentes
        if self._load_cookies(ctx):
            if self._is_logged_in(page):
                log.info("[%s] sesion restaurada desde cookies", self.portal_name)
                self._logged_in = True
                return True
            else:
                log.info("[%s] cookies expiradas o invalidas, intento relogin", self.portal_name)

        # 2. Login con password
        if not self._email or not self._password:
            raise RuntimeError(
                "LinkedIn requiere email y password. "
                "Configuralos en la pestaña Configuracion > LinkedIn."
            )

        log.info("[%s] login con password...", self.portal_name)
        try:
            page.goto(LOGIN_URL, timeout=30_000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)

            # Llenar email
            page.locator('input[name="session_key"]').first.fill(self._email)
            _human_delay(1.5, 3.0)
            page.locator('input[name="session_password"]').first.fill(self._password)
            _human_delay(1.0, 2.0)
            page.locator('button[type="submit"]').first.click()

            # Esperar a que cargue post-login
            page.wait_for_timeout(5000)

            # Verificar si hay CAPTCHA o 2FA
            page_content = page.content().lower()
            if "captcha" in page_content or "verification" in page_content:
                log.warning(
                    "[%s] CAPTCHA detectado. Aborta. Resuelve manualmente en el navegador y reintenta.",
                    self.portal_name,
                )
                return False
            if "verification code" in page_content or "enter the code" in page_content:
                log.warning(
                    "[%s] 2FA detectado. Aborta. Desactiva 2FA o resualo manualmente.",
                    self.portal_name,
                )
                return False

            # Verificar login
            if not self._is_logged_in(page):
                log.warning(
                    "[%s] login fallo (no se detecto sesion activa)", self.portal_name
                )
                return False

            # Guardar cookies para proxima corrida
            self._save_cookies(ctx)
            self._logged_in = True
            log.info("[%s] login exitoso", self.portal_name)
            return True
        except PlaywrightTimeoutError:
            log.error("[%s] timeout en login", self.portal_name)
            return False
        except Exception as e:
            log.error("[%s] error en login: %s", self.portal_name, e)
            return False

    def _ensure_login(self, ctx: BrowserContext, page: Page) -> bool:
        """Wrapper que se llama una vez al inicio del run."""
        if self._logged_in:
            return True
        return self._login(ctx, page)

    def search_url(self, keyword: str, page_num: int = 1) -> str:
        from urllib.parse import urlencode
        params = {
            "keywords": keyword,
            "location": "Colombia",
            "f_TPR": "r86400",  # ultimas 24h
        }
        if page_num > 1:
            params["start"] = (page_num - 1) * 25
        return f"{BASE_URL}/jobs/search/?{urlencode(params)}"

    def _extract_one(self, card, keyword: str) -> VacanteRaw | None:
        try:
            link = card.locator("a.base-card__full-link").first
            if link.count() == 0:
                link = card.locator("a[href*='/jobs/view/']").first
                if link.count() == 0:
                    return None

            href = link.get_attribute("href") or ""
            m = re.search(r"/jobs/view/(\d+)", href)
            if not m:
                return None
            job_id = m.group(1)
            url = href.split("?")[0] if "?" in href else href
            if not url.startswith("http"):
                url = f"{BASE_URL}{url}"

            titulo = _clean(link.inner_text() or card.locator(".base-search-card__title").first.inner_text() or "")
            if not titulo:
                return None

            empresa = ""
            emp = card.locator(".base-search-card__subtitle").first
            if emp.count() > 0:
                empresa = _clean(emp.inner_text())

            ubicacion = ""
            ubi = card.locator(".job-search-card__location").first
            if ubi.count() > 0:
                ubicacion = _clean(ubi.inner_text())

            salario = ""
            sal_meta = card.locator(".job-search-card__salary-info").first
            if sal_meta.count() > 0:
                salario = _clean(sal_meta.inner_text())

            posted = ""
            time_el = card.locator("time").first
            if time_el.count() > 0:
                posted = time_el.get_attribute("datetime") or _clean(time_el.inner_text())

            return VacanteRaw(
                portal=self.portal_name,
                titulo=titulo,
                url=url,
                external_id=job_id,
                empresa=empresa,
                ubicacion=ubicacion,
                salario=salario,
                fecha_publicacion=posted,
            )
        except Exception as e:
            log.debug("[%s] fallo extrayendo card: %s", self.portal_name, e)
            return None

    def parse_listing(self, page: Page, keyword: str) -> list[VacanteRaw]:
        page.wait_for_timeout(2000)
        results: list[VacanteRaw] = []
        seen: set[str] = set()

        # LinkedIn usa <li> con base-card
        cards = page.locator("div.base-card").all()
        if not cards:
            cards = page.locator("li.jobs-search-results__list-item").all()
        if not cards:
            cards = page.locator("[data-job-id]").all()

        for card in cards:
            vac = self._extract_one(card, keyword)
            if vac and vac.external_id and vac.external_id not in seen:
                seen.add(vac.external_id)
                results.append(vac)
                if len(results) >= self._max_offers:
                    log.info(
                        "[%s] limite de %d ofertas alcanzado, parando listing",
                        self.portal_name,
                        self._max_offers,
                    )
                    break
        return results

    def parse_detail(self, page: Page, raw: VacanteRaw) -> VacanteRaw:
        try:
            page.wait_for_timeout(1500)

            # Si la pagina es authwall o 404, skip
            page_url = page.url
            if "/authwall" in page_url or "/login" in page_url:
                log.debug("[%s] detail bloqueado por authwall, skip", self.portal_name)
                return raw

            # Titulo
            h1 = page.locator("h1.top-card-layout__title, h1.job-title").first
            if h1.count() > 0:
                t = _clean(h1.inner_text())
                if t:
                    raw.titulo = t

            # Empresa
            emp = page.locator("a.topcard__org-name-link, .job-details-jobs-unified-top-card__company-name a").first
            if emp.count() > 0:
                t = _clean(emp.inner_text())
                if t:
                    raw.empresa = t

            # Ubicacion
            loc = page.locator(
                ".topcard__flavor--bullet, .job-details-jobs-unified-top-card__primary-description-container"
            ).first
            if loc.count() > 0:
                t = _clean(loc.inner_text())
                if t:
                    raw.ubicacion = t

            # Salario
            sal = page.locator(
                ".job-details-jobs-unified-top-card__job-insight--highlight, .salary"
            ).first
            if sal.count() > 0:
                t = _clean(sal.inner_text())
                if t:
                    raw.salario = t

            # Descripcion
            desc = page.locator(
                "div.show-more-less-html__markup, div.description__text"
            ).first
            if desc.count() > 0:
                raw.descripcion = _clean(desc.inner_text())
        except Exception as e:
            log.warning(
                "[%s] parse_detail parcial: %s", self.portal_name, e
            )
        return raw

    def run(self, keyword: str, **kwargs) -> Any:
        """Override del run() base para inyectar el login ANTES del scraping.

        Hace login una vez, luego corre el flujo normal de BaseScraper.run().
        """
        from core.scrapers.base import ScrapeStats

        stats = ScrapeStats(portal=self.portal_name, keyword=keyword)
        t0 = time.monotonic()
        try:
            with self._browser_context() as (_, ctx, page):
                if not self._ensure_login(ctx, page):
                    stats.errors += 1
                    log.error("[%s] no se pudo hacer login, abortando", self.portal_name)
                    return stats

                # Llamar al metodo original con el contexto ya logueado
                self._logged_in = True
                search_url = self.search_url(keyword)
                log.info("[%s] search url=%s", self.portal_name, search_url)
                if not self._safe_goto(page, search_url):
                    stats.errors += 1
                    return stats

                self._random_sleep(multiplier=1.0)

                try:
                    raws = self.parse_listing(page, keyword)
                except Exception as e:
                    log.exception("[%s] parse_listing fallo: %s", self.portal_name, e)
                    stats.errors += 1
                    return stats

                stats.offers_seen = len(raws)
                log.info(
                    "[%s] listing devolvio %d ofertas (limite: %d)",
                    self.portal_name,
                    len(raws),
                    self._max_offers,
                )

                detail_limit = kwargs.get("detail_limit", None)
                to_fetch = raws if detail_limit is None else raws[:detail_limit]

                for vac in to_fetch:
                    self._random_sleep(multiplier=1.5)
                    try:
                        if not self._safe_goto(page, vac.url):
                            vac.error = "goto_failed"
                            stats.errors += 1
                        else:
                            self.parse_detail(page, vac)
                            stats.offers_parsed += 1
                    except Exception as e:
                        log.warning(
                            "[%s] parse fallo url=%s: %s",
                            self.portal_name,
                            vac.url,
                            e,
                        )
                        vac.error = f"parse:{type(e).__name__}"
                        stats.errors += 1
                    finally:
                        stats.vacantes.append(vac)
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
