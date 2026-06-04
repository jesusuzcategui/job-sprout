"""
Configuraciones globales de Job Sprout.
Todas las constantes del proyecto viven aqui para evitar valores hardcoded.
"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "py_find_jobs.db"

HEADLESS = True

MIN_SLEEP_SECONDS = 2.0
MAX_SLEEP_SECONDS = 5.0

NAVIGATION_TIMEOUT_MS = 30_000
PAGE_LOAD_TIMEOUT_MS = 20_000

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

ALLOWED_LOCATIONS: list[str] = [
    "Mosquera",
    "Madrid",
    "Funza",
    "Bojaca",
    "Remoto",
    "Teletrabajo",
    "Home Office",
    "Desde casa",
]

REJECTED_LOCATIONS: list[str] = [
    "Medellin",
    "Medellín",
    "Cali",
    "Barranquilla",
    "Cartagena",
    "Bucaramanga",
    "Pereira",
    "Manizales",
]

BILINGUAL_REGEX = (
    r"\b(C1|C2|B2|Advanced|Fluent|Bilingue|Bilingüe|"
    r"Professional|Upper-Intermediate|Native|Bilingual)\b"
)

BILINGUAL_MIN_ENGLISH_RATIO = 0.50

MAX_DESCRIPTION_CHARS = 20_000

PORTAL_COMPUTRABAJO = "computrabajo"
PORTAL_ELEMPLEO = "elempleo"
PORTAL_MAGNEMPLEOS = "magnempleos"
PORTAL_INDEED = "indeed"
PORTAL_LINKEDIN = "linkedin"
SUPPORTED_PORTALS: list[str] = [
    PORTAL_COMPUTRABAJO,
    PORTAL_ELEMPLEO,
    PORTAL_INDEED,
    PORTAL_LINKEDIN,
    PORTAL_MAGNEMPLEOS,
]

PORTAL_LABELS: dict[str, str] = {
    PORTAL_COMPUTRABAJO: "Computrabajo",
    PORTAL_ELEMPLEO: "El Empleo",
    PORTAL_INDEED: "Indeed",
    PORTAL_LINKEDIN: "LinkedIn (riesgo de ban)",
    PORTAL_MAGNEMPLEOS: "Magneto Empleos (no implementado)",
}
