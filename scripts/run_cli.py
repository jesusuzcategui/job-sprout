"""
CLI para probar los scrapers sin la GUI.

Uso:
  python scripts/run_cli.py --keyword "python"
  python scripts/run_cli.py --keyword "auxiliar contable" --portal elempleo
  python scripts/run_cli.py --keyword "python" --portal both --limit 3
  python scripts/run_cli.py --keyword "python" --portal computrabajo --save
  python scripts/run_cli.py --keyword "python" --portal computrabajo --no-details
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

from core.database import insert_vacante
from core.scrapers import get_scraper, list_portals


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Probar scrapers de Job Sprout desde la CLI.",
    )
    p.add_argument(
        "--keyword", "-k", required=True, help="Termino de busqueda (ej. 'python')"
    )
    p.add_argument(
        "--portal",
        "-p",
        default="both",
        choices=[*list_portals(), "both"],
        help="Portal a scrapear (default: both)",
    )
    p.add_argument(
        "--limit",
        "-l",
        type=int,
        default=None,
        help="Limite de ofertas a las que se les abre el detalle (default: todas)",
    )
    p.add_argument(
        "--no-details",
        action="store_true",
        help="No visitar paginas de detalle (solo listing, mas rapido)",
    )
    p.add_argument(
        "--save",
        action="store_true",
        help="Persistir resultados en la DB (sin filtros todavia)",
    )
    p.add_argument(
        "--min-sleep",
        type=float,
        default=2.0,
        help="Sleep minimo entre requests (default: 2.0)",
    )
    p.add_argument(
        "--max-sleep",
        type=float,
        default=5.0,
        help="Sleep maximo entre requests (default: 5.0)",
    )
    p.add_argument(
        "--verbose", "-v", action="store_true", help="Logging en nivel DEBUG"
    )
    return p.parse_args()


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def print_stats(stats) -> None:
    print(
        f"\n--- {stats.portal} | '{stats.keyword}' ---"
        f"\n  listing offers : {stats.offers_seen}"
        f"\n  parsed details : {stats.offers_parsed}"
        f"\n  errors         : {stats.errors}"
        f"\n  elapsed        : {stats.elapsed_seconds:.1f}s"
    )
    for i, v in enumerate(stats.vacantes, 1):
        flag = "ERR" if v.error else "OK"
        print(
            f"  [{i:>2}][{flag}] {v.titulo[:60]:<60} | {v.empresa[:25]:<25} | {v.ubicacion[:30]}"
        )
        if v.salario:
            print(f"        salario: {v.salario}")
        if v.error:
            print(f"        error:  {v.error}")
        if v.descripcion and not v.error:
            preview = v.descripcion[:160].replace("\n", " ")
            print(f"        desc:   {preview}...")


def maybe_save(stats, save: bool) -> int:
    if not save:
        return 0
    saved = 0
    for v in stats.vacantes:
        if v.error or not v.titulo or not v.url:
            continue
        vid = insert_vacante(
            portal=v.portal,
            external_id=v.external_id,
            titulo=v.titulo,
            empresa=v.empresa,
            ubicacion=v.ubicacion,
            salario=v.salario,
            descripcion=v.descripcion,
            url=v.url,
            fecha_publicacion=v.fecha_publicacion,
            aprobado=True,
        )
        if vid is not None:
            saved += 1
    if saved:
        print(f"  -> {saved} vacante(s) guardada(s) en la DB")
    return saved


def main() -> int:
    args = parse_args()
    configure_logging(args.verbose)

    portals = list_portals() if args.portal == "both" else [args.portal]
    total_saved = 0

    for portal in portals:
        scraper = get_scraper(portal)
        scraper.min_sleep = args.min_sleep
        scraper.max_sleep = args.max_sleep

        stats = scraper.run(
            keyword=args.keyword,
            fetch_details=not args.no_details,
            detail_limit=args.limit,
        )
        print_stats(stats)
        total_saved += maybe_save(stats, args.save)

    if args.save:
        print(f"\nTotal persistido: {total_saved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
