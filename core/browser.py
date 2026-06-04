"""
Selector de motor de navegador para Playwright.

Soporta:
- chromium (default, bundled con Playwright)
- brave (sistema, requiere executable_path)
- chrome (sistema, requiere executable_path)
- edge (sistema, requiere executable_path)

Modos de sesion:
- ephemeral:   cada corrida crea un browser limpio (sin cookies persistentes)
- persistent:  usa un user_data_dir fijo (perfil Brave real, conserva cookies)

Brave/Chrome/Edge son Chromium-based, asi que los selectores CSS funcionan igual.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


DEFAULT_PROFILE_DIR = Path("data/browser_profile")

# User-Agent por plataforma para el modo persistente
_PLATFORM_UA: dict[str, str] = {
    "linux": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "darwin": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "win32": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
}


def ensure_playwright_browser() -> None:
    """Instala Playwright Chromium si no está presente.

    La primera ejecución descarga ~300MB. Es idempotente.
    """
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            exe = p.chromium.executable_path
            if exe and os.path.isfile(exe):
                return
    except Exception:
        pass

    log.info("[browser] Chromium no encontrado. Instalando… (una vez, ~300MB)")
    import subprocess
    import sys
    try:
        subprocess.check_call(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        log.info("[browser] Chromium instalado correctamente")
    except subprocess.CalledProcessError as e:
        log.warning("[browser] Fallo instalacion de Chromium: %s", e)


def _find_bundled_browser() -> str | None:
    """Detecta si corremos dentro de un bundle PyInstaller y busca el
    Playwright Chromium empaquetado en _internal/playwright-browser/.

    Returns:
        path al ejecutable de chromium, o None si no esta empaquetado.
    """
    try:
        frozen = getattr(sys, "frozen", False)
    except Exception:
        frozen = False
    if not frozen:
        return None

    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return None
    base = Path(meipass)
    browser_root = base / "playwright-browser"

    candidates: list[str] = []
    if sys.platform == "linux":
        candidates = ["chrome-linux/chrome"]
    elif sys.platform == "darwin":
        candidates = [
            "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
            "chrome-mac/Chromium.app/Contents/MacOS/chrome",
        ]
    elif sys.platform == "win32":
        candidates = ["chrome-win/chrome.exe"]

    for rel in candidates:
        path = browser_root / rel
        if path.exists() and os.access(path, os.X_OK):
            log.info("[browser] bundled chromium en %s", path)
            return str(path)

    log.warning(
        "[browser] sys.frozen=True pero no se encontro chromium empaquetado "
        "en %s. Buscando: %s",
        browser_root,
        candidates,
    )
    return None


DEFAULT_BRAVE_PATHS: list[str] = [
    "/usr/bin/brave",
    "/usr/bin/brave-browser",
    "/opt/brave.com/brave/brave",
    "/opt/brave-bin/brave",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "C:/Program Files/BraveSoftware/Brave-Browser/Application/brave.exe",
    "C:/Program Files (x86)/BraveSoftware/Brave-Browser/Application/brave.exe",
]

DEFAULT_CHROME_PATHS: list[str] = [
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
]

DEFAULT_EDGE_PATHS: list[str] = [
    "/usr/bin/microsoft-edge",
    "/usr/bin/microsoft-edge-stable",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
]


def find_browser(name: str) -> str | None:
    """Busca un navegador del sistema en ubicaciones comunes. Retorna path o None."""
    if name == "brave":
        candidates = DEFAULT_BRAVE_PATHS
    elif name == "chrome":
        candidates = DEFAULT_CHROME_PATHS
    elif name == "edge":
        candidates = DEFAULT_EDGE_PATHS
    else:
        return None

    for path in candidates:
        if Path(path).exists() and os.access(path, os.X_OK):
            log.info("[browser] %s detectado en %s", name, path)
            return path

    # Fallback: shutil.which (busca en PATH)
    found = shutil.which(name) or shutil.which(f"{name}-browser") or shutil.which(f"{name}-stable")
    if found:
        log.info("[browser] %s detectado via PATH en %s", name, found)
        return found

    return None


def get_browser_config(engine: str, custom_path: str = "") -> dict[str, Any]:
    """Retorna config de Playwright para el engine seleccionado.

    Returns:
        dict con: {name, executable_path (o None), is_system_browser}
    """
    engine = (engine or "chromium").lower().strip()

    if engine == "chromium":
        bundled = _find_bundled_browser()
        return {
            "name": "chromium",
            "executable_path": bundled,  # None si no hay bundled, Playwright usa su default
            "is_system": False,
        }

    # Para brave/chrome/edge: necesita executable_path
    if custom_path and Path(custom_path).exists():
        path = custom_path
    else:
        path = find_browser(engine) or ""

    return {
        "name": engine,
        "executable_path": path or None,
        "is_system": True,
    }


def launch_browser(p, engine: str, custom_path: str = "", **launch_kwargs):
    """Lanza un browser efimero de Playwright segun el engine.

    Args:
        p: instancia de sync_playwright()
        engine: "chromium", "brave", "chrome", "edge"
        custom_path: path custom al ejecutable (opcional)
        **launch_kwargs: headless, args, etc.

    Returns:
        browser instance
    """
    config = get_browser_config(engine, custom_path)
    name = config["name"]
    exec_path = config["executable_path"]

    if name == "chromium":
        return p.chromium.launch(executable_path=exec_path, **launch_kwargs)

    # brave/chrome/edge son chromium internamente, solo cambian el binary
    if not exec_path:
        raise RuntimeError(
            f"{name.title()} no encontrado. Instala {name} o configura el path manualmente "
            f"en Configuracion > Navegador."
        )

    log.info("[browser] lanzando %s desde %s (headless=%s)", name, exec_path, launch_kwargs.get("headless", True))
    return p.chromium.launch(executable_path=exec_path, **launch_kwargs)


def launch_persistent_browser(
    p,
    engine: str,
    custom_path: str = "",
    profile_dir: str | Path = DEFAULT_PROFILE_DIR,
    headed: bool = False,
    **launch_kwargs,
):
    """Lanza un browser PERSISTENTE (perfil real, conserva cookies).

    Args:
        p: instancia de sync_playwright()
        engine: "chromium", "brave", "chrome", "edge"
        custom_path: path al ejecutable (requerido para brave/chrome/edge)
        profile_dir: directorio del perfil. Si no existe, se crea.
                     Contiene cookies, localStorage, historial, etc.
        headed: si True, abre ventana visible. Usar para login manual
                (ej: resolver LinkedIn por primera vez).
        **launch_kwargs: user_agent, viewport, locale, timezone_id, etc.

    Returns:
        BrowserContext (no Browser: persistent context = context pre-creado)

    Uso:
        with sync_playwright() as p:
            ctx = launch_persistent_browser(p, 'brave', '', 'data/profile', headed=True)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto('https://linkedin.com')  # si ya estas logueado, ok
            # ... usar ctx ...
            ctx.close()
    """
    profile_dir = Path(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    config = get_browser_config(engine, custom_path)

    name = config["name"]
    exec_path = config["executable_path"]
    if name != "chromium" and not exec_path:
        raise RuntimeError(
            f"{name.title()} no encontrado. Configura el path en Configuracion > Navegador."
        )

    # Defaults utiles para que el browser se vea "humano"
    _default_ua = _PLATFORM_UA.get(
        sys.platform,
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    )
    launch_kwargs.setdefault("user_agent", _default_ua)
    launch_kwargs["headless"] = bool(headed)
    launch_kwargs["viewport"] = {"width": 1440, "height": 900}
    launch_kwargs["locale"] = "en-US"

    # Args anti-deteccion
    args = list(launch_kwargs.get("args", []))
    args.extend([
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
    ])
    launch_kwargs["args"] = args

    log.info(
        "[browser] lanzando PERSISTENT %s desde %s, profile=%s, headless=%s",
        name, exec_path or "chromium bundled", profile_dir, headed,
    )

    if name == "chromium":
        return p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=launch_kwargs["headless"],
            user_agent=launch_kwargs["user_agent"],
            viewport=launch_kwargs["viewport"],
            locale=launch_kwargs["locale"],
            args=launch_kwargs["args"],
            **{k: v for k, v in launch_kwargs.items()
               if k not in ("headless", "user_agent", "viewport", "locale", "args")},
        )
    return p.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        executable_path=exec_path,
        headless=launch_kwargs["headless"],
        user_agent=launch_kwargs["user_agent"],
        viewport=launch_kwargs["viewport"],
        locale=launch_kwargs["locale"],
        args=launch_kwargs["args"],
        **{k: v for k, v in launch_kwargs.items()
           if k not in ("headless", "user_agent", "viewport", "locale", "args", "executable_path")},
    )
