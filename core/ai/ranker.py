"""
Ranker AI multi-provider (Gemini, OpenRouter, ...).

Arquitectura:
- BaseRanker: interfaz comun (score_relevance, validate_geo, status, is_available).
- GeminiRanker: usa google-genai (Google AI Studio).
- OpenRouterRanker: usa API OpenAI-compatible de OpenRouter (cualquier modelo).
- get_ranker_from_settings(): factory que lee ai_provider de la DB y devuelve
  el ranker correcto. Si el usuario guardo una API key en la UI, esa se usa;
  si no, se usa la variable de entorno.

Precedencia de API key (de mayor a menor):
  1. setting "ai_api_key_override" (lo que el usuario escribio en la UI)
  2. variable de entorno (GEMINI_API_KEY o OPENROUTER_API_KEY segun provider)
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_OPENROUTER_MODEL = "meta-llama/llama-3.3-70b-instruct:free"
DEFAULT_PROVIDER = "gemini"

PROVIDER_MODELS: dict[str, list[str]] = {
    "gemini": [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
    ],
    "openrouter": [
        "meta-llama/llama-3.3-70b-instruct:free",
        "google/gemini-2.0-flash-exp:free",
        "qwen/qwen-2.5-72b-instruct:free",
        "deepseek/deepseek-chat:free",
        "mistralai/mistral-small-3.1-24b-instruct:free",
    ],
}


@dataclass(frozen=True)
class RelevanceResult:
    score: float | None
    reason: str = ""
    model: str = ""


@dataclass(frozen=True)
class GeoValidationResult:
    approved: bool | None
    reason: str = ""
    model: str = ""


class BaseRanker(ABC):
    model_name: str = ""
    provider: str = "base"

    @property
    @abstractmethod
    def is_available(self) -> bool: ...

    @property
    @abstractmethod
    def status(self) -> str: ...

    @abstractmethod
    def score_relevance(
        self, *, vacante: dict, keyword: str, perfil: dict
    ) -> RelevanceResult: ...

    @abstractmethod
    def validate_geo(
        self,
        *,
        vacante: dict,
        allowed_locations: list[str],
        acepta_remoto: bool,
    ) -> GeoValidationResult: ...

    def _clean_text_for_ai(self, text: str, max_chars: int) -> str:
        if not text:
            return ""
        text = re.sub(r"\s+", " ", text)
        return text[:max_chars].strip()


# ============================================================
# Gemini (Google AI Studio via google-genai)
# ============================================================


class GeminiRanker(BaseRanker):
    provider = "gemini"

    def __init__(self, api_key: str | None = None, model: str = DEFAULT_GEMINI_MODEL):
        self.model_name = model
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self._client: Any = None
        self._init_error: str = ""
        if self.api_key:
            try:
                from google import genai
                self._client = genai.Client(api_key=self.api_key)
            except Exception as e:
                self._init_error = f"{type(e).__name__}: {e}"
                log.warning("Gemini no inicializado: %s", self._init_error)
        else:
            log.info("GEMINI_API_KEY no configurada. AI scoring deshabilitado.")

    @property
    def is_available(self) -> bool:
        return self._client is not None

    @property
    def status(self) -> str:
        if self.is_available:
            return f"OK ({self.model_name})"
        if self._init_error:
            return f"Error: {self._init_error}"
        return "Sin GEMINI_API_KEY (ni en .env ni en Config)"

    def _generate(self, prompt: str, *, max_retries: int = 2) -> str | None:
        if not self.is_available:
            return None
        for attempt in range(max_retries + 1):
            try:
                resp = self._client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                )
                return (resp.text or "").strip()
            except Exception as e:
                log.warning(
                    "Gemini generate_content fallo (intento %d/%d): %s",
                    attempt + 1,
                    max_retries + 1,
                    e,
                )
                if attempt < max_retries:
                    time.sleep(1.5 * (attempt + 1))
        return None

    def _prompt_relevance(self, *, vacante: dict, keyword: str, perfil: dict) -> str:
        perfil_kws = ", ".join(perfil.get("keywords", [])[:8])
        return (
            "Eres un asistente de matching de ofertas de empleo en Colombia.\n"
            "Evalua que tan bien matchea esta oferta al perfil de busqueda.\n\n"
            f"PERFIL: {perfil.get('nombre', '?')}\n"
            f"Descripcion del perfil: {perfil.get('descripcion', '')}\n"
            f"Keywords del perfil: {perfil_kws}\n"
            f"Keyword de busqueda actual: {keyword!r}\n\n"
            "OFERTA:\n"
            f"- Titulo: {vacante.get('titulo', '')}\n"
            f"- Empresa: {vacante.get('empresa', '')}\n"
            f"- Ubicacion: {vacante.get('ubicacion', '')}\n"
            f"- Salario: {vacante.get('salario', '')}\n"
            f"- Descripcion: {self._clean_text_for_ai(vacante.get('descripcion') or '', 1500)}\n\n"
            "Responde SOLO con un JSON valido, sin texto adicional:\n"
            '{"score": <float 0.0-1.0>, "reason": "<una oracion corta, max 80 chars>"}\n\n'
            "Criterios:\n"
            "- 0.0-0.3: no matchea (otro rubro, otro nivel, no aplica)\n"
            "- 0.4-0.6: matchea parcialmente (rubro similar pero no exacto)\n"
            "- 0.7-0.9: buen match (rubro correcto, nivel similar)\n"
            "- 1.0: match perfecto (keywords coinciden exactamente)\n"
        )

    def _prompt_geo(self, *, vacante: dict, allowed_locations: list[str], acepta_remoto: bool) -> str:
        locs = ", ".join(allowed_locations) or "(ninguna)"
        return (
            "Eres un asistente de validacion geografica de ofertas de empleo en Colombia.\n"
            "Decide si esta oferta es realmente apta geograficamente para el usuario.\n\n"
            f"Ubicaciones aceptadas por el usuario: {locs}\n"
            f"Acepta vacantes remotas/teletrabajo: {'SI' if acepta_remoto else 'NO'}\n\n"
            "OFERTA:\n"
            f"- Titulo: {vacante.get('titulo', '')}\n"
            f"- Ubicacion declarada: {vacante.get('ubicacion', '')}\n"
            f"- Descripcion (primeros 800 chars): {self._clean_text_for_ai(vacante.get('descripcion') or '', 800)}\n\n"
            "Reglas:\n"
            "- Aprobado SI el trabajo es presencial/remoto claramente en alguna de las ubicaciones aceptadas.\n"
            "- Aprobado SI la oferta es totalmente remota Y el usuario acepta remoto.\n"
            "- Rechazado SI el trabajo requiere presencia fisica en otra ciudad/PAIS no aceptada.\n"
            "- Rechazado SI la 'ubicacion aceptada' aparece como referencia (oficina en Madrid Espana, "
            "sede en Bogota, sucursal) y NO como el lugar real de trabajo.\n\n"
            "Responde SOLO con JSON valido:\n"
            '{"approved": <true/false>, "reason": "<una oracion corta, max 100 chars>"}\n'
        )

    def score_relevance(
        self, *, vacante: dict, keyword: str, perfil: dict
    ) -> RelevanceResult:
        text = self._generate(self._prompt_relevance(vacante=vacante, keyword=keyword, perfil=perfil))
        if text is None:
            return RelevanceResult(score=None, reason="AI no disponible", model="")
        score, reason = _parse_score_json(text)
        if score is None:
            return RelevanceResult(score=None, reason=f"Parse fail: {text[:60]}", model="")
        return RelevanceResult(
            score=max(0.0, min(1.0, score)),
            reason=reason[:200],
            model=self.model_name,
        )

    def validate_geo(
        self,
        *,
        vacante: dict,
        allowed_locations: list[str],
        acepta_remoto: bool,
    ) -> GeoValidationResult:
        text = self._generate(self._prompt_geo(vacante=vacante, allowed_locations=allowed_locations, acepta_remoto=acepta_remoto))
        if text is None:
            return GeoValidationResult(approved=None, reason="AI no disponible", model="")
        approved, reason = _parse_geo_json(text)
        if approved is None:
            return GeoValidationResult(approved=None, reason=f"Parse fail: {text[:60]}", model="")
        return GeoValidationResult(approved=approved, reason=reason[:200], model=self.model_name)


# ============================================================
# OpenRouter (API OpenAI-compatible, cualquier modelo)
# ============================================================


class OpenRouterRanker(BaseRanker):
    provider = "openrouter"
    BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, api_key: str | None = None, model: str = DEFAULT_OPENROUTER_MODEL):
        self.model_name = model
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        self._init_error: str = ""
        if not self.api_key:
            log.info("OPENROUTER_API_KEY no configurada. AI scoring deshabilitado.")

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

    @property
    def status(self) -> str:
        if self.is_available:
            return f"OK ({self.model_name})"
        return "Sin OPENROUTER_API_KEY (ni en .env ni en Config)"

    def _call(self, prompt: str, *, max_retries: int = 2) -> str | None:
        if not self.is_available:
            return None
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/jesusuzcategui/py-find-jobs",
            "X-Title": "Job Sprout",
        }
        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1024,
            "temperature": 0.1,
        }

        for attempt in range(max_retries + 1):
            try:
                import httpx
                with httpx.Client(timeout=30) as client:
                    resp = client.post(self.BASE_URL, json=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get("choices") or []
                    if not choices:
                        log.warning("OpenRouter: respuesta sin choices: %r", data)
                        return None
                    return (choices[0].get("message", {}).get("content") or "").strip()
                if resp.status_code == 429:
                    log.warning("OpenRouter: rate limit (intento %d)", attempt + 1)
                    if attempt < max_retries:
                        time.sleep(3.0 * (attempt + 1))
                    continue
                log.warning(
                    "OpenRouter: HTTP %d - %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return None
            except Exception as e:
                log.warning(
                    "OpenRouter call fallo (intento %d/%d): %s",
                    attempt + 1,
                    max_retries + 1,
                    e,
                )
                if attempt < max_retries:
                    time.sleep(1.5 * (attempt + 1))
        return None

    def score_relevance(
        self, *, vacante: dict, keyword: str, perfil: dict
    ) -> RelevanceResult:
        prompt = (
            "Eres un asistente de matching de ofertas de empleo en Colombia. "
            "Evalua que tan bien matchea esta oferta al perfil de busqueda.\n\n"
            f"PERFIL: {perfil.get('nombre', '?')}\n"
            f"Descripcion del perfil: {perfil.get('descripcion', '')}\n"
            f"Keywords del perfil: {', '.join(perfil.get('keywords', [])[:8])}\n"
            f"Keyword de busqueda actual: {keyword!r}\n\n"
            "OFERTA:\n"
            f"- Titulo: {vacante.get('titulo', '')}\n"
            f"- Empresa: {vacante.get('empresa', '')}\n"
            f"- Ubicacion: {vacante.get('ubicacion', '')}\n"
            f"- Salario: {vacante.get('salario', '')}\n"
            f"- Descripcion: {self._clean_text_for_ai(vacante.get('descripcion') or '', 1500)}\n\n"
            "Responde SOLO con un JSON valido:\n"
            '{"score": <float 0.0-1.0>, "reason": "<oracion corta, max 80 chars>"}\n'
            "Criterios: 0.0-0.3 no matchea, 0.4-0.6 parcial, 0.7-0.9 buen match, 1.0 perfecto.\n"
        )
        text = self._call(prompt)
        if text is None:
            return RelevanceResult(score=None, reason="AI no disponible", model="")
        score, reason = _parse_score_json(text)
        if score is None:
            return RelevanceResult(score=None, reason=f"Parse fail: {text[:60]}", model="")
        return RelevanceResult(
            score=max(0.0, min(1.0, score)),
            reason=reason[:200],
            model=self.model_name,
        )

    def validate_geo(
        self,
        *,
        vacante: dict,
        allowed_locations: list[str],
        acepta_remoto: bool,
    ) -> GeoValidationResult:
        locs = ", ".join(allowed_locations) or "(ninguna)"
        prompt = (
            "Eres un asistente de validacion geografica de ofertas de empleo en Colombia. "
            "Decide si esta oferta es realmente apta geograficamente para el usuario.\n\n"
            f"Ubicaciones aceptadas: {locs}\n"
            f"Acepta vacantes remotas/teletrabajo: {'SI' if acepta_remoto else 'NO'}\n\n"
            "OFERTA:\n"
            f"- Titulo: {vacante.get('titulo', '')}\n"
            f"- Ubicacion declarada: {vacante.get('ubicacion', '')}\n"
            f"- Descripcion (800 chars): {self._clean_text_for_ai(vacante.get('descripcion') or '', 800)}\n\n"
            "Reglas: aprobado si presencial/remoto en ubicacion aceptada, o si totalmente remoto y usuario acepta remoto. "
            "Rechazado si requiere presencia en otra ciudad, o si la 'ubicacion aceptada' aparece como referencia "
            "(oficina en Madrid Espana, sede en Bogota) y NO como el lugar real de trabajo.\n\n"
            "Responde SOLO con JSON: {\"approved\": <true/false>, \"reason\": \"<oracion corta, max 100 chars>\"}\n"
        )
        text = self._call(prompt)
        if text is None:
            return GeoValidationResult(approved=None, reason="AI no disponible", model="")
        approved, reason = _parse_geo_json(text)
        if approved is None:
            return GeoValidationResult(approved=None, reason=f"Parse fail: {text[:60]}", model="")
        return GeoValidationResult(approved=approved, reason=reason[:200], model=self.model_name)


# ============================================================
# JSON parsing helpers (compartidos)
# ============================================================


def _parse_score_json(text: str) -> tuple[float | None, str]:
    try:
        cleaned = re.sub(r"```(?:json)?", "", text).strip().strip("`").strip()
        data = json.loads(cleaned)
        return float(data.get("score", -1)), str(data.get("reason", ""))
    except (json.JSONDecodeError, ValueError, TypeError):
        m = re.search(r"(\d+\.?\d*)", text)
        if m:
            return float(m.group(1)), ""
        return None, ""


def _parse_geo_json(text: str) -> tuple[bool | None, str]:
    try:
        cleaned = re.sub(r"```(?:json)?", "", text).strip().strip("`").strip()
        data = json.loads(cleaned)
        approved = data.get("approved")
        if isinstance(approved, bool):
            return approved, str(data.get("reason", ""))
        if isinstance(approved, str):
            low = approved.lower()
            if "si" in low or "true" in low:
                return True, str(data.get("reason", ""))
            if "no" in low or "false" in low:
                return False, str(data.get("reason", ""))
        return None, ""
    except (json.JSONDecodeError, ValueError, TypeError):
        low = text.lower()
        if '"approved": true' in low or "approved: true" in low or low.strip() == "si":
            return True, ""
        if '"approved": false' in low or "approved: false" in low or low.strip() == "no":
            return False, ""
        return None, ""


# ============================================================
# Factory
# ============================================================


def get_ranker_from_settings() -> BaseRanker:
    """Lee ai_provider/ai_model/ai_api_key_override de la DB y devuelve el ranker."""
    from core.database import get_setting

    provider = (get_setting("ai_provider", DEFAULT_PROVIDER) or DEFAULT_PROVIDER).lower()
    model = (get_setting("ai_model", "") or "").strip()
    override = (get_setting("ai_api_key_override", "") or "").strip()

    if not model:
        if provider == "openrouter":
            model = DEFAULT_OPENROUTER_MODEL
        else:
            model = DEFAULT_GEMINI_MODEL

    if provider == "openrouter":
        return OpenRouterRanker(api_key=override or None, model=model)
    return GeminiRanker(api_key=override or None, model=model)
