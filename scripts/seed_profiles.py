"""
Seed de los perfiles iniciales del roadmap.
Idempotente: se puede correr multiples veces sin duplicar.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.database import (
    delete_perfil,
    get_all_perfiles_with_keywords,
    init_db,
    list_perfiles,
    upsert_perfil,
)

SEED_PROFILES: list[dict] = [
    {
        "nombre": "Esposa - Gestion Humana",
        "descripcion": (
            "Perfil de gestion humana / recursos humanos: seleccion, "
            "reclutamiento, capacitacion, bienestar, SST, nomina (area "
            "de personal) y tareas administrativas generales. NO contable."
        ),
        "keywords": [
            # Nucleo RRHH / gestion humana
            "auxiliar de gestion humana",
            "auxiliar de recursos humanos",
            "asistente de recursos humanos",
            "analista de recursos humanos",
            "coordinador de recursos humanos",
            "jefe de recursos humanos",
            "auxiliar de talento humano",
            "gestor de talento humano",
            "gestion humana",
            "recursos humanos",
            "rrhh",
            "talento humano",
            # Subareas
            "seleccion de personal",
            "reclutamiento",
            "reclutador",
            "reclutadora",
            "capacitacion",
            "induccion de personal",
            "bienestar social",
            "nomina",  # contexto gestion humana, no contable
            "contratacion",
            "vinculacion",
            "liquidacion de nomina",
            "seguridad y salud en el trabajo",
            "sst",
            "copasst",
            "comite de convivencia",
            # Administrativo general
            "auxiliar administrativo",
            "asistente administrativo",
            "auxiliar de oficina",
            "asistente de administracion",
            "gestion documental",
        ],
        "ubicaciones_aceptadas": "Mosquera,Madrid,Funza",
        "acepta_remoto": False,
    },
    {
        "nombre": "Jesus - Full Stack",
        "descripcion": (
            "Perfil de desarrollo de software full stack "
            "con enfoque en Python, TypeScript y backend moderno."
        ),
        "keywords": [
            "full stack",
            "fullstack",
            "desarrollador",
            "desarrolladora",
            "backend",
            "frontend",
            "python",
            "django",
            "fastapi",
            "typescript",
            "node",
            "node.js",
            "react",
            "vue",
            "next.js",
            "nestjs",
            "devops",
        ],
        "ubicaciones_aceptadas": "Mosquera,Madrid,Funza",
        "acepta_remoto": False,
    },
]

# Perfil legacy que se elimina automaticamente al re-sembrar.
# (Sus vacantes quedan con perfil_id = NULL gracias al ON DELETE SET NULL,
# no se pierden datos.)
LEGACY_PROFILE_NAMES_TO_DELETE: list[str] = [
    "Esposa - Nomina",
]


def main() -> int:
    init_db()

    # 1. Eliminar perfiles legacy (sus vacantes quedan con perfil_id NULL)
    for legacy_name in LEGACY_PROFILE_NAMES_TO_DELETE:
        existing = [p for p in list_perfiles() if p["nombre"] == legacy_name]
        if existing:
            delete_perfil(legacy_name)
            print(
                f"  - [legacy] {legacy_name} eliminado "
                f"(id={existing[0]['id']}, vacantes asociadas con perfil_id=NULL)"
            )

    print("Inicializando perfiles semilla...")
    for perfil in SEED_PROFILES:
        pid = upsert_perfil(
            nombre=perfil["nombre"],
            descripcion=perfil["descripcion"],
            keywords=perfil["keywords"],
            ubicaciones_aceptadas=perfil.get(
                "ubicaciones_aceptadas", "Mosquera,Madrid,Funza"
            ),
            acepta_remoto=perfil.get("acepta_remoto", True),
        )
        print(
            f"  - {perfil['nombre']} (id={pid}, kws={len(perfil['keywords'])}, "
            f"geo={perfil.get('ubicaciones_aceptadas')}, "
            f"remoto={perfil.get('acepta_remoto')})"
        )

    print()
    print("=== Estado actual de la DB ===")
    for p in get_all_perfiles_with_keywords():
        print(
            f"  [{p['id']}] {p['nombre']}  (activo={p['activo']}, "
            f"geo={p.get('ubicaciones_aceptadas')!r}, "
            f"remoto={bool(p.get('acepta_remoto'))})"
        )
        for kw in p["keywords"]:
            print(f"      - {kw}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
