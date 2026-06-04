# py-find-jobs

Agente scraper inteligente para portales de empleo en Colombia. Filtra vacantes por geografia (configurable por perfil) e idioma (deteccion de bilinguismo), y las persiste en SQLite para revisarlas desde una GUI de escritorio.

## Estado

| Fase | Descripcion | Estado |
|---|---|---|
| 1 | DB + Config | ✅ |
| 2 | Scrapers (Playwright) | ✅ |
| 3 | Filtros + Orquestador | ✅ |
| 4 | GUI (PyQt6) + paginacion + delete | ✅ |
| - | Geo config per-perfil (fix Bogota) | ✅ |
| - | AI scoring con Gemini (relevance + geo context) | ✅ |

## Stack

- Python 3.14
- SQLite (stdlib)
- Playwright (sync API) + Chromium
- langdetect
- PyQt6

## Setup

```bash
cd /home/jesusu/Workspace/agents/py-find-jobs
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
python main.py --init-db
```

## Uso

```bash
# GUI (recomendado)
python main.py

# Inicializar / reinicializar la DB
python main.py --init-db

# Agente en CLI (sin GUI)
python main.py --cli --min-sleep 0.5 --max-sleep 1.0
python main.py --cli --portal computrabajo --limit 2

# Probar un scraper individual
python main.py --cli-scraper --keyword "python" --portal computrabajo --save
python scripts/run_cli.py -k "python" -p both --limit 3
```

## GUI

3 tabs:

1. **Panel de Control**:
   - Lista checkable de perfiles a buscar
   - Portales (Computrabajo, El Empleo)
   - Limite de detalle por keyword, sleep min/max
   - Boton Iniciar/Detener, progress bar
   - Log en vivo

2. **Resultados**:
   - Busqueda por texto (titulo, empresa, ubicacion, portal)
   - Filtros: solo bilingues, por perfil
   - **Paginacion** (25/50/100/200 por pagina, ◄ ►)
   - **Checkbox** por fila para seleccion multiple
   - Columna **Aplicar** con link clickable (abre en navegador)
   - Boton **Eliminar seleccionados** (borra de DB + vista)

3. **Configuracion**:
   - CRUD de perfiles
   - **Preferencias geograficas** por perfil:
     - `Ubicaciones aceptadas`: lista separada por comas (ej: `Mosquera, Madrid, Funza`)
     - Checkbox `Aceptar vacantes remotas / home office`

## Filtro geografico (per-perfil)

Logica del filtro (configurable por perfil):

1. La oferta menciona alguna de las `ubicaciones_aceptadas` -> APROBADA
2. La oferta menciona remoto/teletrabajo/home office Y `acepta_remoto=True` -> APROBADA
3. Resto -> RECHAZADA (estricto)

Esto resuelve el bug donde ofertas de Bogota+Remoto siempre pasaban. Ahora:
- Esposa con `acepta_remoto=False` y `ubicaciones="Mosquera,Madrid,Funza"` -> Bogota se rechaza
- Jesus con `acepta_remoto=True` y `ubicaciones="Mosquera,Madrid,Funza,Bogota"` -> todo pasa

Defaults del seed:
- **Esposa - Nomina**: `Mosquera,Madrid,Funza` + acepta_remoto=Si
- **Jesus - Full Stack**: `Mosquera,Madrid,Funza,Bogota` + acepta_remoto=Si

## Estructura

```
py-find-jobs/
├── config/settings.py
├── core/
│   ├── database.py              # SQLite + schema (con migracion auto)
│   ├── agent.py                 # orquestador con callbacks
│   ├── filters/                 # geo.py + language.py
│   └── scrapers/                # base.py + computrabajo.py + elempleo.py
├── gui/
│   ├── main_window.py
│   ├── results_model.py         # QAbstractTableModel + ResultsViewProxy
│   └── worker_thread.py
├── scripts/
│   ├── seed_profiles.py
│   ├── run_cli.py
│   └── run_agent.py
├── data/                        # DB SQLite (gitignored)
└── main.py
```

## Perfiles semilla

- **Esposa - Nomina**: auxiliar contable, asistente administrativo, nomina, facturacion...
- **Jesus - Full Stack**: full stack, backend, python, typescript, react, vue...

Editables desde la GUI (tab Configuracion) o re-sembrando con `scripts/seed_profiles.py`.

## AI Scoring (multi-provider)

Integrado y funcional. Dos capacidades:

1. **`score_relevance(vacante, keyword, perfil)`** — Puntua 0.0-1.0 que tan bien
   matchea una vacante al perfil. Si `score < ai_relevance_threshold` (default 0.4),
   se descarta.
2. **`validate_geo(vacante, allowed_locations, acepta_remoto)`** — Valida el
   contexto geografico cuando hay ambiguedad (ej: "Madrid Espana" vs "Madrid
   Cundinamarca", o Bogota+Remoto con perfil que no acepta remoto).

### Providers soportados

| Provider | Como se configura | Modelos default | Free tier |
|---|---|---|---|
| **Google Gemini** (default) | `GEMINI_API_KEY` (env o UI) | `gemini-2.5-flash` | 20 req/dia en 2.5-flash |
| **OpenRouter** | `OPENROUTER_API_KEY` (env o UI) | `meta-llama/llama-3.3-70b-instruct:free` | Varios modelos `:free` sin limite estricto |

OpenRouter te da acceso a Llama, DeepSeek, Qwen, Mistral, Gemini (via OpenRouter) y muchos mas con una sola API key.

### Configuracion

#### Opcion A: por GUI (recomendado, no editas archivos)

1. Conseguir API key:
   - **Gemini**: gratis en [aistudio.google.com](https://aistudio.google.com)
   - **OpenRouter**: gratis en [openrouter.ai/keys](https://openrouter.ai/keys)
2. Abrir la app, tab **Configuracion** > "AI (opcional)":
   - Seleccionar Provider (Gemini o OpenRouter)
   - Pegar la API key (queda oculta por default, "Mostrar" la muestra)
   - Elegir modelo (o escribir uno custom en el campo libre)
   - Marcar "Usar AI scoring" y/o "Validacion geografica contextual con AI"
   - Ajustar umbral (default 0.4)
   - Click "Guardar AI settings"

La key se guarda en la DB SQLite local. Solo se envia al proveedor correspondiente cuando el agente corre.

#### Opcion B: por variable de entorno

Crear `.env` (ya esta en `.gitignore`):
```
GEMINI_API_KEY=tu_key_de_ai_studio
# y/o
OPENROUTER_API_KEY=tu_key_de_openrouter
```

Si la GUI tiene una key vacia, se usa la variable de entorno. Las keys en la UI tienen precedencia sobre `.env`.

### Costos (free tiers)

- **Gemini 2.5 Flash**: 20 req/dia gratis. Suficiente para ~20-40 vacantes/dia con cache.
- **OpenRouter `:free` models**: varios modelos son totalmente gratis. Rate limits variables por modelo.
- **Cache**: `relevance_score` y `geo_validated` se guardan en la DB por vacante, no se vuelve a llamar Gemini en duplicados.
- **Fallback**: si la API falla (rate limit, network), la vacante se guarda igual con `ai_score=NULL`.

### Toggle rapido en Panel de Control

Hay un toggle **"Usar AI"** en el Panel de Control que enciende/apaga ambas features (scoring + geo) sin tener que ir a Config. Cambia inmediatamente.

### Archivos relevantes

- `core/ai/ranker.py` — `BaseRanker` (ABC), `GeminiRanker`, `OpenRouterRanker`, `get_ranker_from_settings()` factory
- `core/agent.py:_process_vacante` — Integra AI despues de los filtros regex
- `gui/main_window.py` — Grupo AI en tab Configuracion + status en Panel de Control
- `gui/worker_thread.py` — Pasa ranker + flags al Agent

## Estructura

```
py-find-jobs/
├── config/settings.py
├── core/
│   ├── database.py              # SQLite + schema (con migracion auto)
│   ├── agent.py                 # orquestador con callbacks
│   ├── ai/ranker.py             # GeminiRanker (score + geo validation)
│   ├── filters/                 # geo.py + language.py
│   └── scrapers/                # base.py + computrabajo.py + elempleo.py
├── gui/
│   ├── main_window.py
│   ├── results_model.py         # QAbstractTableModel + ResultsViewProxy
│   └── worker_thread.py
├── scripts/
│   ├── seed_profiles.py
│   ├── run_cli.py
│   └── run_agent.py
├── data/                        # DB SQLite (gitignored)
├── .env                         # GEMINI_API_KEY (gitignored)
├── .env.example                 # template
└── main.py
```
