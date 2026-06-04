"""
Entry point de Job Sprout.

Comportamiento:
  - Sin argumentos: lanza la GUI de PyQt6.
  - --init-db: solo inicializa la DB y siembra perfiles.
  - --cli: corre el agente completo desde la terminal.
  - --cli-scraper: corre un scraper individual (modo debug).
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def cmd_init_db() -> int:
    from core.database import count_vacantes, init_db
    from scripts.seed_profiles import SEED_PROFILES, upsert_perfil

    print("[init-db] Inicializando base de datos…")
    init_db()
    for p in SEED_PROFILES:
        upsert_perfil(
            nombre=p["nombre"],
            descripcion=p["descripcion"],
            keywords=p["keywords"],
        )
    print(f"[init-db] Listo. Vacantes registradas: {count_vacantes()}")
    return 0


def cmd_gui() -> int:
    from core.browser import ensure_playwright_browser
    from core.database import init_db
    init_db()
    ensure_playwright_browser()
    try:
        from gui.main_window import launch_gui
    except ImportError as e:
        print(
            "No se pudo importar PyQt6. Instala con: "
            f"pip install -r requirements.txt\n  Detalle: {e}",
            file=sys.stderr,
        )
        return 1
    return launch_gui()


def cmd_cli(args: argparse.Namespace) -> int:
    from core.browser import ensure_playwright_browser
    from core.database import init_db
    init_db()
    ensure_playwright_browser()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )
    from core.agent import Agent
    from core.database import get_all_perfiles_with_keywords
    from core.scrapers import list_portals

    perfiles = get_all_perfiles_with_keywords(only_active=True)
    if not perfiles:
        print("No hay perfiles activos. Ejecuta: python main.py --init-db")
        return 1
    portals = args.portal if args.portal else list_portals()

    stats = Agent(
        headless=not args.show,
        min_sleep=args.min_sleep,
        max_sleep=args.max_sleep,
    ).run(
        perfiles=perfiles,
        portals=portals,
        detail_limit_per_keyword=args.limit or None,
    )
    print()
    print(f"vistas={stats.vacantes_seen} guardadas={stats.vacantes_saved} "
          f"geo_descartadas={stats.vacantes_geo_rejected} "
          f"duplicadas={stats.vacantes_duplicated} "
          f"elapsed={stats.elapsed_seconds:.1f}s")
    return 0


def cmd_cli_scraper(args: argparse.Namespace) -> int:
    from core.browser import ensure_playwright_browser
    from core.database import init_db
    init_db()
    ensure_playwright_browser()
    from scripts.run_cli import main as run_cli_main
    sys.argv = [
        "run_cli.py",
        "--keyword", args.keyword,
        "--portal", args.portal,
    ]
    if args.limit:
        sys.argv += ["--limit", str(args.limit)]
    if args.save:
        sys.argv += ["--save"]
    if args.verbose:
        sys.argv += ["-v"]
    return run_cli_main()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="job-sprout",
        description="Agente scraper inteligente para portales de empleo en Colombia.",
    )
    p.add_argument("--init-db", action="store_true", help="Inicializa la DB y siembra perfiles")
    p.add_argument(
        "--cli", action="store_true",
        help="Ejecuta el agente completo en la terminal (sin GUI)",
    )
    p.add_argument(
        "--cli-scraper", action="store_true",
        help="Ejecuta un scraper individual (modo debug)",
    )
    p.add_argument("--keyword", help="(con --cli-scraper) keyword a buscar")
    p.add_argument("--portal", help="(con --cli/--cli-scraper) portal especifico")
    p.add_argument("--limit", type=int, default=0, help="Limite de detalle por keyword")
    p.add_argument("--save", action="store_true", help="(con --cli-scraper) persiste resultados")
    p.add_argument("--show", action="store_true", help="(con --cli) mostrar navegador")
    p.add_argument("--min-sleep", type=float, default=2.0)
    p.add_argument("--max-sleep", type=float, default=5.0)
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.init_db:
        return cmd_init_db()
    if args.cli:
        return cmd_cli(args)
    if args.cli_scraper:
        if not args.keyword or not args.portal:
            print("--cli-scraper requiere --keyword y --portal", file=sys.stderr)
            return 2
        return cmd_cli_scraper(args)
    return cmd_gui()


if __name__ == "__main__":
    raise SystemExit(main())
