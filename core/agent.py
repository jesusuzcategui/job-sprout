"""
Orquestador del agente (Paso 3.3 del roadmap).

Pipeline:
  for perfil in perfiles_activos:
    for keyword in perfil.keywords:
      for portal in portales:
        stats = scraper.run(keyword, fetch_details=True)
        for vacante in stats.vacantes:
          if not vacante.titulo or not vacante.url: continue
          if vacante.error: continue

          geo = geo_filter(vacante.ubicacion, vacante.descripcion)
          if not geo: skip("geo", geo.reason); continue

          lang = language_filter(vacante.descripcion, vacante.titulo)
          saved_id = insert_vacante(..., aprobado=True, bilingue=lang.bilingual)
          on_vacante_saved(...)

El agente expone callbacks (log, progress, vacante_saved) para que la GUI
de Fase 4 (QThread) los conecte a senales Qt. La CLI los implementa con print.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from core.database import (
    get_all_perfiles_with_keywords,
    insert_vacante,
    now_iso,
)
from core.filters import geo_filter, language_filter
from core.scrapers import get_scraper, list_portals
from core.scrapers.base import ScrapeStats, VacanteRaw

log = logging.getLogger(__name__)


LogFn = Callable[[str, str], None]
ProgressFn = Callable[[int, int, str], None]
VacanteSavedFn = Callable[[dict[str, Any]], None]


def _noop_log(msg: str, level: str = "info") -> None:
    pass


def _noop_progress(current: int, total: int, msg: str) -> None:
    pass


def _noop_vacante_saved(v: dict[str, Any]) -> None:
    pass


@dataclass
class AgentStats:
    started_at: str = ""
    finished_at: str = ""
    elapsed_seconds: float = 0.0

    perfiles_run: int = 0
    keywords_run: int = 0
    portals_run: int = 0

    vacantes_seen: int = 0
    vacantes_geo_rejected: int = 0
    vacantes_saved: int = 0
    vacantes_duplicated: int = 0
    vacantes_with_errors: int = 0
    vacantes_ai_rejected: int = 0

    rejected_by_reason: dict[str, int] = field(default_factory=dict)
    saved_by_portal: dict[str, int] = field(default_factory=dict)
    saved_by_perfil: dict[str, int] = field(default_factory=dict)

    last_rejections: list[dict[str, str]] = field(default_factory=list)
    ai_calls: int = 0
    ai_failures: int = 0


class Agent:
    def __init__(
        self,
        *,
        on_log: LogFn | None = None,
        on_progress: ProgressFn | None = None,
        on_vacante_saved: VacanteSavedFn | None = None,
        headless: bool = True,
        min_sleep: float = 2.0,
        max_sleep: float = 5.0,
        ranker: Any | None = None,
        use_ai_scoring: bool = False,
        use_ai_geo_validation: bool = True,
        ai_relevance_threshold: float = 0.4,
        browser_engine: str = "chromium",
        browser_path: str = "",
        browser_mode: str = "ephemeral",
        browser_profile_dir: str = "data/browser_profile",
    ) -> None:
        self.on_log = on_log or _noop_log
        self.on_progress = on_progress or _noop_progress
        self.on_vacante_saved = on_vacante_saved or _noop_vacante_saved
        self.headless = headless
        self.min_sleep = min_sleep
        self.max_sleep = max_sleep
        self.ranker = ranker
        self.use_ai_scoring = use_ai_scoring
        self.use_ai_geo_validation = use_ai_geo_validation
        self.ai_relevance_threshold = ai_relevance_threshold
        self.browser_engine = browser_engine
        self.browser_path = browser_path
        self.browser_mode = browser_mode
        self.browser_profile_dir = browser_profile_dir

    def _log(self, msg: str, level: str = "info") -> None:
        log_func = getattr(log, level, log.info)
        log_func(msg)
        self.on_log(msg, level)

    def _total_steps(self, perfiles: list[dict], portals: list[str]) -> int:
        return sum(len(p["keywords"]) for p in perfiles) * len(portals)

    def run(
        self,
        *,
        perfiles: list[dict] | None = None,
        portals: list[str] | None = None,
        detail_limit_per_keyword: int | None = None,
    ) -> AgentStats:
        if perfiles is None:
            perfiles = get_all_perfiles_with_keywords(only_active=True)
        if not perfiles:
            self._log("No hay perfiles activos con keywords.", "warning")
            return AgentStats(started_at=now_iso(), finished_at=now_iso())
        if portals is None:
            portals = list_portals()
        if not portals:
            self._log("No hay portales configurados.", "error")
            return AgentStats(started_at=now_iso(), finished_at=now_iso())

        stats = AgentStats(started_at=now_iso())
        t0 = time.monotonic()
        total_steps = self._total_steps(perfiles, portals)
        step = 0

        self._log(
            f"Agente iniciando: {len(perfiles)} perfiles, "
            f"{total_steps} combinaciones portal/keyword",
            "info",
        )

        for perfil in perfiles:
            stats.perfiles_run += 1
            perfil_nombre = perfil["nombre"]
            geo_locs = perfil.get("ubicaciones_aceptadas", "")
            acepta_rem = bool(perfil.get("acepta_remoto", 1))
            self._log(
                f"[perfil] {perfil_nombre} ({len(perfil['keywords'])} kws) | "
                f"geo={geo_locs!r} remoto={acepta_rem}",
                "info",
            )

            for keyword in perfil["keywords"]:
                stats.keywords_run += 1
                for portal in portals:
                    step += 1
                    self.on_progress(
                        step,
                        total_steps,
                        f"{portal} :: {perfil_nombre} :: {keyword}",
                    )
                    self._log(
                        f"[scrape] portal={portal} kw={keyword!r} perfil={perfil_nombre!r}",
                        "info",
                    )
                    stats.portals_run += 1

                    try:
                        scraper = get_scraper(
                            portal,
                            browser_engine=self.browser_engine,
                            browser_path=self.browser_path,
                            browser_mode=self.browser_mode,
                            browser_profile_dir=self.browser_profile_dir,
                        )
                        scraper.headless = self.headless
                        scraper.min_sleep = self.min_sleep
                        scraper.max_sleep = self.max_sleep

                        scrape_stats: ScrapeStats = scraper.run(
                            keyword=keyword,
                            fetch_details=True,
                            detail_limit=detail_limit_per_keyword,
                        )
                    except Exception as e:
                        self._log(
                            f"scraper {portal} fallo: {type(e).__name__}: {e}",
                            "error",
                        )
                        continue

                    self._log(
                        f"  -> listing={scrape_stats.offers_seen} "
                        f"parsed={scrape_stats.offers_parsed} "
                        f"errors={scrape_stats.errors} "
                        f"elapsed={scrape_stats.elapsed_seconds:.1f}s",
                        "info",
                    )

                    for vac in scrape_stats.vacantes:
                        self._process_vacante(
                            vac,
                            perfil_id=perfil["id"],
                            perfil_nombre=perfil_nombre,
                            ubicaciones_aceptadas=perfil.get(
                                "ubicaciones_aceptadas",
                                "Mosquera,Madrid,Funza",
                            ),
                            acepta_remoto=bool(perfil.get("acepta_remoto", 1)),
                            keyword=keyword,
                            perfil=perfil,
                            stats=stats,
                        )

        stats.finished_at = now_iso()
        stats.elapsed_seconds = round(time.monotonic() - t0, 2)
        self._log(
            f"Agente terminado en {stats.elapsed_seconds:.1f}s. "
            f"vistas={stats.vacantes_seen} "
            f"geo_descartadas={stats.vacantes_geo_rejected} "
            f"guardadas={stats.vacantes_saved} "
            f"duplicadas={stats.vacantes_duplicated}",
            "info",
        )
        return stats

    def _process_vacante(
        self,
        vac: VacanteRaw,
        *,
        perfil_id: int,
        perfil_nombre: str,
        ubicaciones_aceptadas,
        acepta_remoto: bool,
        keyword: str,
        perfil: dict,
        stats: AgentStats,
    ) -> None:
        stats.vacantes_seen += 1

        if vac.error or not vac.titulo or not vac.url:
            stats.vacantes_with_errors += 1
            return

        geo = geo_filter(
            vac.ubicacion,
            vac.descripcion,
            ubicaciones_aceptadas=ubicaciones_aceptadas,
            acepta_remoto=acepta_remoto,
        )
        if not geo:
            stats.vacantes_geo_rejected += 1
            reason_key = geo.reason.split(":", 1)[0]
            stats.rejected_by_reason[reason_key] = (
                stats.rejected_by_reason.get(reason_key, 0) + 1
            )
            if len(stats.last_rejections) < 20:
                from core.filters.geo import excerpt_for_reason
                stats.last_rejections.append(
                    {
                        "titulo": vac.titulo[:80],
                        "portal": vac.portal,
                        "razon": geo.reason,
                        "extracto": excerpt_for_reason(
                            vac.ubicacion, vac.descripcion, geo.reason
                        ),
                    }
                )
            return

        lang = language_filter(vac.descripcion, vac.titulo)

        # --- AI scoring (relevance) ---
        relevance_score: float | None = None
        relevance_reason: str = ""
        ai_model: str = ""
        if self.ranker and self.ranker.is_available and self.use_ai_scoring:
            stats.ai_calls += 1
            rel = self.ranker.score_relevance(
                vacante={
                    "titulo": vac.titulo,
                    "empresa": vac.empresa,
                    "ubicacion": vac.ubicacion,
                    "salario": vac.salario,
                    "descripcion": vac.descripcion,
                },
                keyword=keyword,
                perfil=perfil,
            )
            if rel.score is None:
                stats.ai_failures += 1
            else:
                relevance_score = rel.score
                relevance_reason = rel.reason
                ai_model = rel.model
                if rel.score < self.ai_relevance_threshold:
                    stats.vacantes_ai_rejected += 1
                    reason_key = f"ai_score<{self.ai_relevance_threshold}"
                    stats.rejected_by_reason[reason_key] = (
                        stats.rejected_by_reason.get(reason_key, 0) + 1
                    )
                    self._log(
                        f"  - AI descarto: {vac.titulo[:50]} "
                        f"(score={rel.score:.2f} < {self.ai_relevance_threshold}) "
                        f"{rel.reason}",
                        "info",
                    )
                    return

        # --- AI geo validation (contextual) ---
        geo_validated: bool | None = None
        ai_geo_reason: str = ""
        if self.ranker and self.ranker.is_available and self.use_ai_geo_validation:
            stats.ai_calls += 1
            from core.filters.geo import _normalize_locs
            allowed = _normalize_locs(ubicaciones_aceptadas)
            if allowed:
                gv = self.ranker.validate_geo(
                    vacante={
                        "titulo": vac.titulo,
                        "ubicacion": vac.ubicacion,
                        "descripcion": vac.descripcion,
                    },
                    allowed_locations=allowed,
                    acepta_remoto=acepta_remoto,
                )
                if gv.approved is None:
                    stats.ai_failures += 1
                else:
                    geo_validated = gv.approved
                    ai_geo_reason = gv.reason
                    if not ai_model:
                        ai_model = gv.model
                    if gv.approved is False:
                        stats.vacantes_ai_rejected += 1
                        stats.rejected_by_reason["ai_geo_rejected"] = (
                            stats.rejected_by_reason.get("ai_geo_rejected", 0) + 1
                        )
                        self._log(
                            f"  - AI geo descarto: {vac.titulo[:50]} "
                            f"({gv.reason})",
                            "info",
                        )
                        return

        new_id = insert_vacante(
            portal=vac.portal,
            external_id=vac.external_id,
            titulo=vac.titulo,
            empresa=vac.empresa,
            ubicacion=vac.ubicacion,
            salario=vac.salario,
            descripcion=vac.descripcion,
            url=vac.url,
            fecha_publicacion=vac.fecha_publicacion,
            perfil_id=perfil_id,
            bilingue=lang.bilingual,
            aprobado=True,
            relevance_score=relevance_score,
            geo_validated=geo_validated,
            ai_model=ai_model,
            ai_checked_at=now_iso() if (relevance_score is not None or geo_validated is not None) else "",
            ai_reason=relevance_reason or ai_geo_reason,
        )
        if new_id is None:
            stats.vacantes_duplicated += 1
            return

        stats.vacantes_saved += 1
        stats.saved_by_portal[vac.portal] = stats.saved_by_portal.get(vac.portal, 0) + 1
        stats.saved_by_perfil[perfil_nombre] = (
            stats.saved_by_perfil.get(perfil_nombre, 0) + 1
        )

        ai_tag = (
            f"ai_score={relevance_score:.2f}"
            if relevance_score is not None
            else ("ai_geo=ok" if geo_validated is True else "")
        )
        self._log(
            f"  + [id={new_id}] {vac.portal} :: {vac.titulo[:60]} "
            f"bilingue={'SI' if lang.bilingual else 'no'} "
            f"perfil={perfil_nombre!r} geo={geo.reason} {ai_tag}".rstrip(),
            "info",
        )
        self.on_vacante_saved(
            {
                "id": new_id,
                "portal": vac.portal,
                "titulo": vac.titulo,
                "empresa": vac.empresa,
                "ubicacion": vac.ubicacion,
                "salario": vac.salario,
                "url": vac.url,
                "bilingue": lang.bilingual,
                "perfil_id": perfil_id,
                "perfil_nombre": perfil_nombre,
                "english_ratio": lang.english_ratio,
                "level_hits": list(lang.level_hits),
                "relevance_score": relevance_score,
                "geo_validated": geo_validated,
            }
        )


def run_agent_cli() -> AgentStats:
    """Helper para invocar desde CLI sin reimplementar setup."""
    return Agent().run()


if __name__ == "__main__":
    import argparse
    import json
    import sys
    from pathlib import Path

    ROOT = Path(__file__).resolve().parent.parent
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )

    ap = argparse.ArgumentParser(description="Ejecuta el agente completo.")
    ap.add_argument("--portal", "-p", action="append", help="Limitar a portal(es)")
    args = ap.parse_args()

    stats = Agent().run(portals=args.portal)
    print()
    print(json.dumps(
        {
            "elapsed": stats.elapsed_seconds,
            "vistas": stats.vacantes_seen,
            "geo_descartadas": stats.vacantes_geo_rejected,
            "guardadas": stats.vacantes_saved,
            "duplicadas": stats.vacantes_duplicated,
            "rejected_by_reason": stats.rejected_by_reason,
            "saved_by_portal": stats.saved_by_portal,
            "saved_by_perfil": stats.saved_by_perfil,
        },
        indent=2,
        ensure_ascii=False,
    ))
