"""
QThread que envuelve el Agent y emite senales Qt en lugar de callbacks.

Uso:
  worker = AgentWorker(
      portals=["computrabajo", "elempleo"],
      detail_limit_per_keyword=2,
  )
  worker.log_message.connect(my_log_fn)
  worker.vacante_saved.connect(my_save_fn)
  worker.finished_with_stats.connect(my_done_fn)
  worker.start()

El thread se autocontrola: al terminar emite finished_with_stats y se puede
reiniciar con start() si se necesita.
"""
from __future__ import annotations

import traceback
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal

from core.agent import Agent, AgentStats
from core.ai.ranker import GeminiRanker
from core.database import get_all_perfiles_with_keywords, get_setting


class AgentWorker(QThread):
    """QThread que ejecuta Agent.run() y reporta progreso por senales."""

    log_message = pyqtSignal(str, str)
    progress = pyqtSignal(int, int, str)
    vacante_saved = pyqtSignal(dict)
    finished_with_stats = pyqtSignal(object)
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        *,
        portals: list[str] | None = None,
        detail_limit_per_keyword: int | None = None,
        min_sleep: float = 2.0,
        max_sleep: float = 5.0,
        headless: bool = True,
        perfiles: list[dict] | None = None,
        use_ai_scoring: bool = False,
        use_ai_geo_validation: bool = True,
        ai_relevance_threshold: float = 0.4,
        ranker: GeminiRanker | None = None,
        browser_engine: str = "chromium",
        browser_path: str = "",
        browser_mode: str = "ephemeral",
        browser_profile_dir: str = "data/browser_profile",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._portals = portals
        self._detail_limit = detail_limit_per_keyword
        self._min_sleep = min_sleep
        self._max_sleep = max_sleep
        self._headless = headless
        self._perfiles = perfiles
        self._use_ai_scoring = use_ai_scoring
        self._use_ai_geo_validation = use_ai_geo_validation
        self._ai_relevance_threshold = ai_relevance_threshold
        self._ranker = ranker
        self._browser_engine = browser_engine
        self._browser_path = browser_path
        self._browser_mode = browser_mode
        self._browser_profile_dir = browser_profile_dir
        self._cancel_requested = False

    def request_cancel(self) -> None:
        """Marca el thread para que termine al finalizar la unidad de trabajo actual."""
        self._cancel_requested = True

    def _emit_log(self, msg: str, level: str = "info") -> None:
        self.log_message.emit(msg, level)

    def _emit_progress(self, current: int, total: int, msg: str) -> None:
        self.progress.emit(current, total, msg)

    def _emit_vacante(self, v: dict[str, Any]) -> None:
        self.vacante_saved.emit(v)

    def run(self) -> None:
        try:
            if self._perfiles is not None:
                perfiles = self._perfiles
            else:
                perfiles = get_all_perfiles_with_keywords(only_active=True)
            if not perfiles:
                self._emit_log("No hay perfiles seleccionados con keywords.", "warning")
                self.finished_with_stats.emit(AgentStats())
                return

            nombres = ", ".join(p.get("nombre", "?") for p in perfiles)
            self._emit_log(
                f"Worker iniciando: {len(perfiles)} perfiles ({nombres})", "info"
            )

            ranker = self._ranker
            if ranker is None and (self._use_ai_scoring or self._use_ai_geo_validation):
                model = get_setting("ai_model", "gemini-2.5-flash") or "gemini-2.5-flash"
                ranker = GeminiRanker(model=model)
                if ranker.is_available:
                    self._emit_log(
                        f"AI habilitada: {ranker.status} (umbral={self._ai_relevance_threshold})",
                        "info",
                    )
                else:
                    self._emit_log(f"AI no disponible: {ranker.status}", "warning")
                    ranker = None
            elif ranker is not None:
                if ranker.is_available:
                    self._emit_log(f"AI habilitada: {ranker.status}", "info")
                else:
                    self._emit_log(f"AI no disponible: {ranker.status}", "warning")
                    ranker = None

            agent = Agent(
                on_log=self._safe_log,
                on_progress=self._safe_progress,
                on_vacante_saved=self._safe_vacante,
                headless=self._headless,
                min_sleep=self._min_sleep,
                max_sleep=self._max_sleep,
                ranker=ranker,
                use_ai_scoring=self._use_ai_scoring and (ranker is not None),
                use_ai_geo_validation=self._use_ai_geo_validation and (ranker is not None),
                ai_relevance_threshold=self._ai_relevance_threshold,
                browser_engine=self._browser_engine,
                browser_path=self._browser_path,
                browser_mode=self._browser_mode,
                browser_profile_dir=self._browser_profile_dir,
            )

            stats: AgentStats = agent.run(
                perfiles=perfiles,
                portals=self._portals,
                detail_limit_per_keyword=self._detail_limit,
            )
            self.finished_with_stats.emit(stats)
        except Exception as e:
            tb = traceback.format_exc()
            self.error_occurred.emit(f"{type(e).__name__}: {e}\n{tb}")

    def _safe_log(self, msg: str, level: str) -> None:
        if self._cancel_requested:
            return
        self._emit_log(msg, level)

    def _safe_progress(self, current: int, total: int, msg: str) -> None:
        if self._cancel_requested:
            return
        self._emit_progress(current, total, msg)

    def _safe_vacante(self, v: dict[str, Any]) -> None:
        if self._cancel_requested:
            return
        self._emit_vacante(v)
