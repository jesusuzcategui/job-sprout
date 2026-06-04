## 🗺️ Roadmap de Desarrollo: Agente Scraper Inteligente (Colombia)

### 🏗️ Fase 1: El Cerebro y los Datos (Configuración y Almacenamiento)

*El objetivo de esta fase es crear la estructura donde el agente guardará lo que encuentre y sabrá qué buscar.*

* [ ] **Paso 1.1: Base de Datos (SQLite):** Crea un script `database.py`. Diseña las tablas (`perfiles`, `keywords`, `vacantes`). Asegúrate de que la tabla `vacantes` tenga un campo único (como la URL o un ID del portal) para evitar registrar la misma oferta dos veces.
* [ ] **Paso 1.2: Gestión de Configuración:** Crea las funciones para insertar perfiles (ej. "Esposa - Nómina", "Jesús - Full Stack") y asociarles sus respectivas *keywords* y ubicaciones permitidas.

---

### 🔍 Fase 2: El Motor de Extracción (Scraping con Playwright)

*Aquí construimos las "manos" del agente, encargadas de ir a los portales reales.*

* [ ] **Paso 2.1: Setup de Playwright:** Configura el entorno aislado en Python e instala Playwright con soporte para navegadores *headless* (sin interfaz visible para que consuma menos recursos).
* [ ] **Paso 2.2: Módulo Base de Extracción:** Crea un script por cada portal (ej. `scraper_computrabajo.py`, `scraper_elempleo.py`). Este script debe recibir una palabra clave, hacer la búsqueda en el portal, y extraer: *Título, Empresa, Ubicación, Descripción completa y URL*.
* [ ] **Paso 2.3: Capa de Evasión Básica:** Implementa tiempos de espera aleatorios (`time.sleep` con rangos de 2 a 5 segundos) y añade un *User-Agent* real para evitar que los portales bloqueen la IP de tu casa en Mosquera.

---

### 🧠 Fase 3: La Lógica del Agente (Filtros Inteligentes)

*Esta es la fase más importante: donde solucionamos el desorden geográfico de las plataformas y detectamos el idioma.*

* [ ] **Paso 3.1: Filtro Geográfico Estricto:** Crea una función que analice el campo "ubicación" y el texto de la descripción. Si dice "Bogotá", pero en ninguna parte menciona "Mosquera", "Madrid", "Funza" o "Remoto/Teletrabajo", el agente la descarta.
* [ ] **Paso 3.2: Analizador de Bilingüismo:** Implementa la detección de inglés. Puedes usar expresiones regulares para buscar niveles (`C1, B2, Advanced, Fluent`) o integrar la librería ligera `langdetect`. Si la descripción pasa del 50% en inglés, el agente la marca como `bilingue = True`.
* [ ] **Paso 3.3: Orquestador:** Crea un script central (`agent.py`) que tome las palabras clave de la base de datos, corra los scrapers, pase los resultados por los filtros de geografía e idioma, y guarde en la base de datos **solo las vacantes aprobadas**.

---

### 💻 Fase 4: La Interfaz Gráfica (PyQt6)

*Le ponemos rostro al agente para que sea fácil de usar para ti y para tu esposa.*

* [ ] **Paso 4.1: Esqueleto de la GUI:** Diseña la ventana principal con `QTabWidget` (Pestañas: Panel de Control, Resultados, Configuración de Perfiles).
* [ ] **Paso 4.2: Multihilo (QThread):** Configura la conexión entre la interfaz y el orquestador del agente. El agente debe correr dentro de un `QThread` para que la ventana de PyQt6 no se congele mientras extrae datos.
* [ ] **Paso 4.3: Tabla de Resultados (`QTableView`):** Conecta la base de datos SQLite a la tabla de la interfaz para mostrar las vacantes en tiempo real a medida que el agente las procesa. Añade la función para que, al hacer doble clic, se abra la URL en el navegador.

---

### 🚀 Fase 5: Pulido y Automatización (Opcional pero recomendado)

*Hacer la vida más fácil.*

* [ ] **Paso 5.1: Notificaciones locales:** Añadir alertas en el sistema o integrar un Bot de Telegram súper sencillo para que el agente envíe los enlaces aprobados directamente al celular de tu esposa.

---

### 🛠️ Código de Arranque (Estructura de Carpetas)

Para que empieces a tirar código de una vez, te sugiero organizar tus archivos así:

```text
mi_scraper_agent/
│
├── config/
│   └── settings.py          # Configuraciones globales
│
├── core/
│   ├── __init__.py
│   ├── database.py          # Conexión y queries de SQLite
│   ├── agent.py             # Lógica de filtrado e idioma
│   └── scrapers/            # Spiders específicos por portal
│       ├── computrabajo.py
│       └── elempleo.py
│
├── gui/
│   ├── __init__.py
│   ├── main_window.py       # Ventana principal PyQt6
│   └── worker_thread.py     # El QThread para el scraper
│
└── main.py                  # Punto de entrada de la aplicación

```