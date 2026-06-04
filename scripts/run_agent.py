"""
CLI del agente completo (Hito 3).

Carga los perfiles activos de la DB, scrapea portales, aplica filtros
geograficos y de idioma, y guarda solo las vacantes aprobadas en la DB.

Uso:
  python scripts/run_agent.py
  python scripts/run_agent.py --limit 3
  python scripts/run_agent.py --portal computrabajo
  python scripts/run_agent.py --no-details
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent import Agent
from core.scrapers import list_portals


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Ejecuta el agente: scraping + filtros + DB.",
    )
    p.add_argument(
        "--portal", "-p",
        action="append",
        choices=list_portals(),
        help="Limita a uno o varios portales (default: todos)",
    )
    p.add_argument(
        "--limit", "-l",
        type=int,
        default=None,
        help="Limite de detalle por keyword (default: todos)",
    )
    p.add_argument(
        "--min-sleep",
        type=float,
        default=2.0,
    )
    p.add_argument(
        "--max-sleep",
        type=float,
        default=5.0,
    )
    p.add_argument(
        "--no-headless",
        action="store_true",
        help="Mostrar el navegador (default: headless)",
    )
    p.add_argument(
        "--verbose", "-v", action="store_true",
    )
    return p.parse_args()


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def cli_log(msg: str, level: str = "info") -> None:
    tag = f"[{level[:3].upper()}]"
    print(f"  {tag} {msg}", flush=True)


def main() -> int:
    args = parse_args()
    configure_logging(args.verbose)

    print("=" * 60)
    print("Job Sprout :: Agente")
    print("=" * 60)

    agent = Agent(
        on_log=cli_log,
        headless=not args.no_headless,
        min_sleep=args.min_sleep,
        max_sleep=args.max_sleep,
    )
    stats = agent.run(
        portals=args.portal,
        detail_limit_per_keyword=args.limit,
    )

    print()
    print("=" * 60)
    print("RESUMEN")
    print("=" * 60)
    print(json.dumps(
        {
            "elapsed_seconds": stats.elapsed_seconds,
            "perfiles": stats.perfiles_run,
            "keywords": stats.keywords_run,
            "portals_combos": stats.portals_run,
            "vistas": stats.vacantes_seen,
            "geo_descartadas": stats.vacantes_geo_rejected,
            "guardadas": stats.vacantes_saved,
            "duplicadas": stats.vacantes_duplicated,
            "con_errores": stats.vacantes_with_errors,
            "rejected_by_reason": stats.rejected_by_reason,
            "saved_by_portal": stats.saved_by_portal,
            "saved_by_perfil": stats.saved_by_perfil,
        },
        indent=2,
        ensure_ascii=False,
    ))

    if stats.last_rejections:
        print()
        print("Ultimos descartes geograficos:")
        for r in stats.last_rejections[:10]:
            print(f"  - [{r['portal']}] {r['titulo']}")
            print(f"      razon: {r['razon']}")
            print(f"      extracto: {r['extracto'][:80]!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
