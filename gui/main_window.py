"""
Ventana principal de Job Sprout (Hito 4 - Fase 4).

Estructura:
  QMainWindow
  └── QTabWidget
      ├── Panel de Control (start/stop, progreso, log en vivo)
      ├── Resultados (QTableView con VacantesTableModel)
      └── Configuracion (CRUD de perfiles + keywords)

Threading: el agente corre en un QThread (AgentWorker) que emite senales
a la UI principal, evitando que la ventana se congele.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QModelIndex, QPoint, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QDesktopServices, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStatusBar,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.database import (
    delete_perfil,
    delete_vacantes,
    get_all_perfiles_with_keywords,
    get_all_settings,
    get_keywords_for_perfil,
    get_setting,
    list_perfiles,
    set_setting,
    upsert_perfil,
)
from core.scrapers import list_portals
from gui.results_model import (
    COLUMNS,
    ResultsViewProxy,
    SELECTED_COL,
    URL_COL,
    VacantesTableModel,
)
from gui.worker_thread import AgentWorker


BILINGUE_FILTER_ROLE = Qt.ItemDataRole.UserRole + 1


def _wrap_in_widget(layout: QHBoxLayout) -> QWidget:
    w = QWidget()
    w.setLayout(layout)
    return w


class MainWindow(QMainWindow):
    request_open_url = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Job Sprout — Agente Scraper")
        self.resize(1280, 820)

        self._worker: AgentWorker | None = None
        self._current_perfil_id: int | None = None

        self._build_menu()
        self._build_central()
        self._build_statusbar()

        self._refresh_perfiles()
        self._refresh_resultados()
        self._update_ai_short_label()
        self._update_status("Listo")

    # ----- construcción UI -----

    def _build_menu(self) -> None:
        m_file = self.menuBar().addMenu("&Archivo")
        act_quit = QAction("&Salir", self)
        act_quit.setShortcut("Ctrl+Q")
        act_quit.triggered.connect(self.close)
        m_file.addAction(act_quit)

        m_view = self.menuBar().addMenu("&Ver")
        act_refresh = QAction("&Refrescar resultados", self)
        act_refresh.setShortcut("F5")
        act_refresh.triggered.connect(self._refresh_resultados)
        m_view.addAction(act_refresh)

        m_help = self.menuBar().addMenu("A&yuda")
        act_about = QAction("&Acerca de", self)
        act_about.triggered.connect(self._show_about)
        m_help.addAction(act_about)

    def _build_central(self) -> None:
        self.tabs = QTabWidget()
        self.tab_control = self._build_tab_control()
        self.tab_results = self._build_tab_results()
        self.tab_config = self._build_tab_config()
        self.tabs.addTab(self.tab_control, "Panel de Control")
        self.tabs.addTab(self.tab_results, "Resultados")
        self.tabs.addTab(self.tab_config, "Configuración")
        self.setCentralWidget(self.tabs)

    def _build_tab_control(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        # Logo
        logo_lab = QLabel()
        logo_pix = QPixmap("images/logo-horizontal-app.png")
        if not logo_pix.isNull():
            logo_lab.setPixmap(logo_pix)
            logo_lab.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            logo_lab.setContentsMargins(0, 0, 0, 4)
            layout.addWidget(logo_lab)

        # Estado
        gb_state = QGroupBox("Estado")
        state_lay = QHBoxLayout(gb_state)
        self.lbl_perfiles = QLabel("Perfiles: -")
        self.lbl_ai_short = QLabel("AI: -")
        self.btn_start = QPushButton("Iniciar búsqueda")
        self.btn_stop = QPushButton("Detener")
        self.btn_stop.setEnabled(False)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        state_lay.addWidget(self.lbl_perfiles, 2)
        state_lay.addWidget(self.lbl_ai_short, 1)
        state_lay.addWidget(self.btn_start, 1)
        state_lay.addWidget(self.btn_stop, 1)
        state_lay.addWidget(self.progress, 4)
        layout.addWidget(gb_state)

        # Perfiles a buscar (seleccion multiple)
        gb_perfiles = QGroupBox("Perfiles a buscar")
        per_lay = QVBoxLayout(gb_perfiles)
        self.list_perfiles_control = QListWidget()
        self.list_perfiles_control.setMaximumHeight(110)
        self.list_perfiles_control.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        per_lay.addWidget(self.list_perfiles_control)
        per_btns = QHBoxLayout()
        self.btn_perfil_all = QPushButton("Seleccionar todos")
        self.btn_perfil_none = QPushButton("Ninguno")
        self.btn_perfil_refresh = QPushButton("Refrescar lista")
        per_btns.addWidget(self.btn_perfil_all)
        per_btns.addWidget(self.btn_perfil_none)
        per_btns.addWidget(self.btn_perfil_refresh)
        per_btns.addStretch(1)
        per_lay.addLayout(per_btns)
        layout.addWidget(gb_perfiles)

        # AI quick toggle (acceso rapido desde Panel de Control)
        gb_ai = QGroupBox("AI (Gemini)")
        ai_lay = QHBoxLayout(gb_ai)
        self.chk_use_ai = QCheckBox("Usar AI (scoring + geo validation)")
        self.chk_use_ai.setToolTip(
            "Toggle rapido. Para configuracion avanzada (umbral, modelo, "
            "AI scoring vs geo por separado) ve a la pestaña Configuracion."
        )
        self.lbl_ai_inline = QLabel("...")
        self.lbl_ai_inline.setStyleSheet("color: #666;")
        ai_lay.addWidget(self.chk_use_ai)
        ai_lay.addWidget(self.lbl_ai_inline, 1)
        layout.addWidget(gb_ai)

        # Opciones
        gb_opts = QGroupBox("Opciones")
        opts_form = QFormLayout(gb_opts)
        # Portales: generados dinamicamente segun list_available_portals()
        self._portal_checks: dict[str, QCheckBox] = {}
        portals_lay = QHBoxLayout()
        try:
            from core.scrapers import list_available_portals
            from config.settings import PORTAL_LABELS
            available = list_available_portals()
            for p in available:
                chk = QCheckBox(p["label"])
                chk.setChecked(True)
                chk.setProperty("portal_key", p["key"])
                if not p["implemented"]:
                    chk.setEnabled(False)
                    chk.setToolTip("Scraper no implementado todavia.")
                self._portal_checks[p["key"]] = chk
                portals_lay.addWidget(chk)
        except Exception as e:
            # Fallback si algo falla: al menos Computrabajo + El Empleo
            log_path = Path("/tmp/jobsprout_startup.log")
            log_path.write_text(f"Error generando portales: {e}\n")
            self.chk_ct = QCheckBox("Computrabajo")
            self.chk_ct.setChecked(True)
            self.chk_ee = QCheckBox("El Empleo")
            self.chk_ee.setChecked(True)
            self._portal_checks["computrabajo"] = self.chk_ct
            self._portal_checks["elempleo"] = self.chk_ee
            portals_lay.addWidget(self.chk_ct)
            portals_lay.addWidget(self.chk_ee)
        portals_lay.addStretch(1)
        portals_w = QWidget()
        portals_w.setLayout(portals_lay)

        self.spn_limit = QSpinBox()
        self.spn_limit.setRange(0, 100)
        self.spn_limit.setValue(0)
        self.spn_limit.setSpecialValueText("Sin límite")

        self.spn_sleep_min = QDoubleSpinBox()
        self.spn_sleep_min.setRange(0.0, 30.0)
        self.spn_sleep_min.setValue(2.0)
        self.spn_sleep_min.setSingleStep(0.5)
        self.spn_sleep_max = QDoubleSpinBox()
        self.spn_sleep_max.setRange(0.0, 30.0)
        self.spn_sleep_max.setValue(5.0)
        self.spn_sleep_max.setSingleStep(0.5)

        opts_form.addRow("Portales:", portals_w)
        opts_form.addRow("Límite detalle/keyword:", self.spn_limit)
        opts_form.addRow("Sleep mínimo (s):", self.spn_sleep_min)
        opts_form.addRow("Sleep máximo (s):", self.spn_sleep_max)
        layout.addWidget(gb_opts)

        # Log
        gb_log = QGroupBox("Log en vivo")
        log_lay = QVBoxLayout(gb_log)
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumBlockCount(2000)
        log_lay.addWidget(self.txt_log)
        btn_clear = QPushButton("Limpiar log")
        btn_clear.clicked.connect(self.txt_log.clear)
        log_lay.addWidget(btn_clear)
        layout.addWidget(gb_log, 1)

        self.btn_start.clicked.connect(self._on_start)
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_perfil_all.clicked.connect(self._select_all_perfiles)
        self.btn_perfil_none.clicked.connect(self._select_no_perfiles)
        self.btn_perfil_refresh.clicked.connect(self._refresh_perfiles_control)
        self.list_perfiles_control.itemChanged.connect(
            lambda _item: self._update_perfiles_label()
        )
        self.chk_use_ai.toggled.connect(self._on_use_ai_toggled)
        self._load_ai_toggle_from_settings()
        return w

    def _build_tab_results(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        # --- Toolbar fila 1: busqueda + filtros ---
        toolbar1 = QHBoxLayout()
        self.in_search = QLineEdit()
        self.in_search.setPlaceholderText("Buscar por título, empresa, ubicación, portal…")
        self.in_search.setClearButtonEnabled(True)
        self.btn_refresh_results = QPushButton("Refrescar")
        self.chk_only_bilingue = QCheckBox("Solo bilingües")
        self.cmb_perfil = QComboBox()
        self.cmb_perfil.addItem("Todos los perfiles", "")
        toolbar1.addWidget(QLabel("🔍"))
        toolbar1.addWidget(self.in_search, 3)
        toolbar1.addWidget(self.btn_refresh_results)
        toolbar1.addWidget(self.chk_only_bilingue)
        toolbar1.addWidget(QLabel("Perfil:"))
        toolbar1.addWidget(self.cmb_perfil, 1)
        layout.addLayout(toolbar1)

        # --- Toolbar fila 2: acciones + paginacion ---
        toolbar2 = QHBoxLayout()
        self.btn_select_page = QPushButton("☑ Seleccionar página")
        self.btn_clear_selection = QPushButton("Limpiar selección")
        self.btn_delete_selected = QPushButton("🗑 Eliminar seleccionados")
        self.btn_delete_selected.setEnabled(False)
        self.lbl_selection = QLabel("Seleccionados: 0")
        self.btn_copy_md = QPushButton("📋 Copiar como MD")
        self.btn_copy_md.setToolTip(
            "Copia las vacantes actualmente filtradas (respeta busqueda + filtros) "
            "como tabla Markdown al portapapeles."
        )
        self.btn_export_csv = QPushButton("💾 Exportar CSV")
        self.btn_export_csv.setToolTip(
            "Guarda las vacantes filtradas como CSV (Excel/Sheets)."
        )

        self.btn_prev_page = QPushButton("◄")
        self.btn_next_page = QPushButton("►")
        self.lbl_page_info = QLabel("Página 1 de 1")
        self.cmb_page_size = QComboBox()
        self.cmb_page_size.addItems(["25", "50", "100", "200"])
        self.cmb_page_size.setCurrentText("50")
        self.lbl_total = QLabel("Mostrando 0 de 0")
        toolbar2.addWidget(self.btn_select_page)
        toolbar2.addWidget(self.btn_clear_selection)
        toolbar2.addWidget(self.lbl_selection)
        toolbar2.addWidget(self.btn_delete_selected)
        toolbar2.addWidget(self.btn_copy_md)
        toolbar2.addWidget(self.btn_export_csv)
        toolbar2.addStretch(1)
        toolbar2.addWidget(QLabel("Por página:"))
        toolbar2.addWidget(self.cmb_page_size)
        toolbar2.addWidget(self.btn_prev_page)
        toolbar2.addWidget(self.lbl_page_info)
        toolbar2.addWidget(self.btn_next_page)
        toolbar2.addStretch(1)
        toolbar2.addWidget(self.lbl_total)
        layout.addLayout(toolbar2)

        # --- Modelo + proxy ---
        self.results_model = VacantesTableModel(self)
        self.results_proxy = ResultsViewProxy(self)
        self.results_proxy.setSourceModel(self.results_model)
        self.results_proxy.set_page_size(int(self.cmb_page_size.currentText()))

        # --- Tabla ---
        self.table = QTableView()
        self.table.setModel(self.results_proxy)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self._apply_column_widths()
        layout.addWidget(self.table, 1)

        # --- Conexiones ---
        self.btn_refresh_results.clicked.connect(self._refresh_resultados)
        self.in_search.textChanged.connect(self.results_proxy.set_search)
        self.chk_only_bilingue.toggled.connect(self.results_proxy.set_only_bilingual)
        self.cmb_perfil.currentIndexChanged.connect(
            lambda _i: self.results_proxy.set_perfil_filter(
                self.cmb_perfil.currentData() or ""
            )
        )
        self.cmb_page_size.currentTextChanged.connect(
            lambda s: self.results_proxy.set_page_size(int(s))
        )
        self.btn_prev_page.clicked.connect(
            lambda: self.results_proxy.set_page(self.results_proxy.page() - 1)
        )
        self.btn_next_page.clicked.connect(
            lambda: self.results_proxy.set_page(self.results_proxy.page() + 1)
        )
        self.btn_select_page.clicked.connect(self._on_select_page)
        self.btn_clear_selection.clicked.connect(self._on_clear_selection)
        self.btn_delete_selected.clicked.connect(self._on_delete_selected)
        self.btn_copy_md.clicked.connect(self._on_copy_as_markdown)
        self.btn_export_csv.clicked.connect(self._on_export_csv)

        self.table.doubleClicked.connect(self._on_row_double_clicked)
        self.table.clicked.connect(self._on_row_clicked)

        self.results_model.countChanged.connect(self._update_total_label)
        self.results_model.selectionChanged.connect(self._update_selection_label)
        self.results_proxy.pageInfoChanged.connect(self._update_page_info)
        self.results_proxy.pageInfoChanged.connect(
            lambda *_: self._update_total_label(self.results_model.rowCount())
        )
        # Cuando el proxy se resetea (cambio de filtro), refrescar total
        self.results_proxy.modelReset.connect(
            lambda: self._update_total_label(self.results_model.rowCount())
        )

        # Re-evalua paginacion cuando el modelo fuente cambia
        self.results_model.rowsInserted.connect(lambda *_: self.results_proxy._refresh())
        self.results_model.rowsRemoved.connect(lambda *_: self.results_proxy._refresh())
        self.results_model.modelReset.connect(lambda: self.results_proxy._refresh())

        return w

    def _apply_column_widths(self) -> None:
        widths = {
            0: 30,    # checkbox
            1: 50,    # id
            2: 90,    # portal
            3: 140,   # perfil
            4: 360,   # titulo
            5: 180,   # empresa
            6: 180,   # ubicacion
            7: 130,   # salario
            8: 70,    # bilingue
            9: 100,   # url/aplicar
            10: 130,  # created_at
        }
        for col, w in widths.items():
            self.table.setColumnWidth(col, w)

    def _update_total_label(self, _n: int) -> None:
        shown = self.results_proxy.rowCount()
        total_filtered = self.results_proxy._filtered_count()
        total = self.results_model.rowCount()
        self.lbl_total.setText(
            f"Mostrando {shown} de {total_filtered} (filtro) / {total} (total)"
        )

    def _update_page_info(self, page: int, total_pages: int) -> None:
        self.lbl_page_info.setText(f"Página {page + 1} de {total_pages}")
        self.btn_prev_page.setEnabled(page > 0)
        self.btn_next_page.setEnabled(page < total_pages - 1)

    def _update_selection_label(self, n: int) -> None:
        self.lbl_selection.setText(f"Seleccionados: {n}")
        self.btn_delete_selected.setEnabled(n > 0)
        self.btn_clear_selection.setEnabled(n > 0)

    def _build_tab_config(self) -> QWidget:
        w = QWidget()
        layout = QHBoxLayout(w)

        self.list_perfiles = QListWidget()
        self.list_perfiles.setMaximumWidth(260)
        layout.addWidget(self.list_perfiles, 1)

        form_w = QWidget()
        form_lay = QVBoxLayout(form_w)
        form = QFormLayout()
        self.in_nombre = QLineEdit()
        self.in_descripcion = QLineEdit()
        self.in_keywords = QPlainTextEdit()
        self.in_keywords.setPlaceholderText("Una keyword por línea...")
        self.in_keywords.setMinimumHeight(140)
        form.addRow("Nombre:", self.in_nombre)
        form.addRow("Descripción:", self.in_descripcion)
        form.addRow("Keywords:", self.in_keywords)

        gb_geo = QGroupBox("Preferencias geograficas (Filtro estricto)")
        geo_lay = QFormLayout(gb_geo)
        self.in_ubicaciones = QLineEdit()
        self.in_ubicaciones.setPlaceholderText(
            "Mosquera, Madrid, Funza  (separadas por coma)"
        )
        self.chk_acepta_remoto = QCheckBox("Aceptar vacantes remotas / home office")
        self.chk_acepta_remoto.setChecked(True)
        self.lbl_geo_help = QLabel(
            "Las vacantes deben estar en una de las ubicaciones indicadas\n"
            "O (si la opcion esta marcada) mencionar remoto/teletrabajo/home office."
        )
        self.lbl_geo_help.setStyleSheet("color: #666; font-size: 11px;")
        geo_lay.addRow("Ubicaciones aceptadas:", self.in_ubicaciones)
        geo_lay.addRow("", self.chk_acepta_remoto)
        geo_lay.addRow("", self.lbl_geo_help)
        form_lay.addLayout(form)
        form_lay.addWidget(gb_geo)

        btn_lay = QHBoxLayout()
        self.btn_new = QPushButton("Nuevo perfil")
        self.btn_save = QPushButton("Guardar cambios")
        self.btn_delete = QPushButton("Eliminar perfil")
        self.btn_reload = QPushButton("Recargar lista")
        btn_lay.addWidget(self.btn_new)
        btn_lay.addWidget(self.btn_save)
        btn_lay.addWidget(self.btn_delete)
        btn_lay.addWidget(self.btn_reload)
        btn_lay.addStretch(1)
        form_lay.addLayout(btn_lay)

        gb_ai = QGroupBox("AI (opcional)")
        ai_lay = QFormLayout(gb_ai)
        self.lbl_ai_status = QLabel("...")

        # Provider + API key (override de UI, opcional)
        prov_lay = QHBoxLayout()
        self.cmb_ai_provider = QComboBox()
        self.cmb_ai_provider.addItem("Google Gemini (AI Studio)", "gemini")
        self.cmb_ai_provider.addItem("OpenRouter (multi-modelo)", "openrouter")
        self.cmb_ai_provider.setToolTip(
            "OpenRouter da acceso a multiples modelos (Llama, DeepSeek, Qwen, "
            "Gemini, Mistral, etc.) con una sola API key. Algunos son gratuitos."
        )
        prov_lay.addWidget(self.cmb_ai_provider, 1)

        self.in_ai_api_key = QLineEdit()
        self.in_ai_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.in_ai_api_key.setPlaceholderText("(vacio = usa variable de entorno)")
        self.in_ai_api_key.setToolTip(
            "Si lo dejas vacio, se usa GEMINI_API_KEY o OPENROUTER_API_KEY del .env.\n"
            "Si lo llenas, SOBREESCRIBE el .env para esta DB."
        )
        prov_lay.addWidget(self.in_ai_api_key, 2)
        self.chk_ai_show_key = QCheckBox("Mostrar")
        self.chk_ai_show_key.toggled.connect(self._on_toggle_show_api_key)
        prov_lay.addWidget(self.chk_ai_show_key)
        self.btn_ai_clear_key = QPushButton("Borrar")
        self.btn_ai_clear_key.clicked.connect(self._on_clear_api_key)
        prov_lay.addWidget(self.btn_ai_clear_key)
        prov_w = QWidget()
        prov_w.setLayout(prov_lay)
        ai_lay.addRow("Provider + API key:", prov_w)

        self.chk_ai_scoring = QCheckBox("Usar AI scoring (descarta vacantes con score < umbral)")
        self.chk_ai_geo = QCheckBox(
            "Validacion geografica contextual con AI (catching de Bogota+Remoto falso, etc.)"
        )
        self.spn_ai_threshold = QDoubleSpinBox()
        self.spn_ai_threshold.setRange(0.0, 1.0)
        self.spn_ai_threshold.setSingleStep(0.05)
        self.spn_ai_threshold.setValue(0.4)
        self.in_ai_model = QLineEdit()
        self.in_ai_model.setPlaceholderText("gemini-2.5-flash")
        self.cmb_ai_model = QComboBox()
        self.cmb_ai_model.setEditable(True)  # permite escribir modelo custom
        self.cmb_ai_model.setToolTip(
            "Selecciona un modelo o escribe el nombre exacto (ej: meta-llama/llama-3.3-70b-instruct:free)"
        )
        # Layout: combo editable + campo libre
        model_lay = QHBoxLayout()
        model_lay.addWidget(self.cmb_ai_model, 1)
        self.in_ai_model = QLineEdit()
        self.in_ai_model.setPlaceholderText("(o escribe el nombre exacto aqui)")
        self.in_ai_model.setMaximumWidth(280)
        model_lay.addWidget(self.in_ai_model)
        model_w = QWidget()
        model_w.setLayout(model_lay)
        ai_lay.addRow("Modelo:", model_w)

        self.lbl_ai_help = QLabel(
            "La AI se invoca DESPUES de los filtros geometricos/idioma, sobre el set superviviente.\n"
            "• Gemini free tier: 1500 req/dia.  • OpenRouter: depende del modelo (algunos son :free).\n"
            "Tu API key se guarda en la DB SQLite local. Solo se envia al proveedor correspondiente."
        )
        self.lbl_ai_help.setStyleSheet("color: #666; font-size: 11px;")
        self.lbl_ai_help.setWordWrap(True)

        ai_lay.addRow("Status:", self.lbl_ai_status)
        ai_lay.addRow("", self.chk_ai_scoring)
        ai_lay.addRow("", self.chk_ai_geo)
        ai_lay.addRow("Umbral minimo de score:", self.spn_ai_threshold)
        ai_lay.addRow("", self.lbl_ai_help)

        ai_btns = QHBoxLayout()
        self.btn_ai_save = QPushButton("Guardar AI settings")
        self.btn_ai_refresh = QPushButton("Refrescar status")
        ai_btns.addWidget(self.btn_ai_save)
        ai_btns.addWidget(self.btn_ai_refresh)
        ai_btns.addStretch(1)
        ai_lay.addRow("", _wrap_in_widget(ai_btns))
        form_lay.addWidget(gb_ai)

        # LinkedIn (opcional, requiere login)
        gb_linkedin = QGroupBox("LinkedIn (login requerido)")
        li_lay = QFormLayout(gb_linkedin)
        self.lbl_li_status = QLabel("(no configurado)")
        self.in_li_email = QLineEdit()
        self.in_li_email.setPlaceholderText("tu_email@dominio.com")
        self.in_li_password = QLineEdit()
        self.in_li_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.in_li_password.setPlaceholderText("(vacio = usar cookies guardadas)")
        self.chk_li_show = QCheckBox("Mostrar password")
        self.chk_li_show.toggled.connect(
            lambda c: self.in_li_password.setEchoMode(
                QLineEdit.EchoMode.Normal if c else QLineEdit.EchoMode.Password
            )
        )
        self.spn_li_max = QSpinBox()
        self.spn_li_max.setRange(1, 100)
        self.spn_li_max.setValue(20)
        self.spn_li_max.setToolTip("Maximo de ofertas a scrapear por sesion (reduce deteccion).")
        self.lbl_li_help = QLabel(
            "ADVERTENCIA: LinkedIn prohibe scraping en sus ToS.\n"
            "Tu cuenta puede ser baneada si LinkedIn detecta el scraping.\n"
            "Las cookies se guardan en data/linkedin_cookies.json (chmod 600).\n"
            "Si LinkedIn muestra CAPTCHA o 2FA, el scraper aborta gracefully."
        )
        self.lbl_li_help.setStyleSheet("color: #c0392b; font-size: 11px;")
        self.lbl_li_help.setWordWrap(True)

        li_btns = QHBoxLayout()
        self.btn_li_save = QPushButton("Guardar")
        self.btn_li_test = QPushButton("Test login")
        self.btn_li_clear = QPushButton("Borrar cookies")
        li_btns.addWidget(self.btn_li_save)
        li_btns.addWidget(self.btn_li_test)
        li_btns.addWidget(self.btn_li_clear)
        li_btns.addStretch(1)
        li_btns_w = QWidget()
        li_btns_w.setLayout(li_btns)

        li_lay.addRow("Status:", self.lbl_li_status)
        li_lay.addRow("Email:", self.in_li_email)
        li_lay.addRow("Password:", self.in_li_password)
        li_lay.addRow("", self.chk_li_show)
        li_lay.addRow("Max ofertas/sesion:", self.spn_li_max)
        li_lay.addRow("", li_btns_w)
        li_lay.addRow("", self.lbl_li_help)
        form_lay.addWidget(gb_linkedin)

        # Navegador (Chromium bundled / Brave / Chrome / Edge del sistema)
        gb_browser = QGroupBox("Navegador")
        br_lay = QFormLayout(gb_browser)
        self.lbl_br_status = QLabel("(cargando...)")
        self.cmb_browser = QComboBox()
        self.cmb_browser.addItem("Chromium (incluido con Playwright)", "chromium")
        self.cmb_browser.addItem("Brave (sistema)", "brave")
        self.cmb_browser.addItem("Google Chrome (sistema)", "chrome")
        self.cmb_browser.addItem("Microsoft Edge (sistema)", "edge")
        self.cmb_browser.setToolTip(
            "Chromium es el default (incluido, siempre funciona). "
            "Brave/Chrome/Edge usan el binario del sistema y permiten "
            "compartir sesion/cookies ya iniciadas con LinkedIn u otros."
        )
        self.in_browser_path = QLineEdit()
        self.in_browser_path.setPlaceholderText("(vacio = autodetectar)")
        self.lbl_browser_help = QLabel(
            "Brave/Chrome/Edge requieren que el binario este instalado. "
            "Si dejas el path vacio, busca en ubicaciones comunes del sistema."
        )
        self.lbl_browser_help.setStyleSheet("color: #666; font-size: 11px;")
        self.lbl_browser_help.setWordWrap(True)

        br_btns = QHBoxLayout()
        self.btn_br_detect = QPushButton("Autodetectar")
        self.btn_br_test = QPushButton("Test")
        self.btn_br_save = QPushButton("Guardar")
        br_btns.addWidget(self.btn_br_detect)
        br_btns.addWidget(self.btn_br_test)
        br_btns.addWidget(self.btn_br_save)
        br_btns_w = QWidget()
        br_btns_w.setLayout(br_btns)

        br_lay.addRow("Status:", self.lbl_br_status)
        br_lay.addRow("Motor:", self.cmb_browser)
        br_lay.addRow("Path (opcional):", self.in_browser_path)
        br_lay.addRow("", br_btns_w)

        # Modo de sesion
        self.cmb_browser_mode = QComboBox()
        self.cmb_browser_mode.addItem(
            "Ephemeral (browser limpio cada corrida, sin cookies)",
            "ephemeral",
        )
        self.cmb_browser_mode.addItem(
            "Persistent (perfil real, conserva LinkedIn y otras sesiones)",
            "persistent",
        )
        self.cmb_browser_mode.setToolTip(
            "Persistent: usa data/browser_profile/ como perfil. La primera vez "
            "click 'Abrir navegador' para hacer login manual a LinkedIn. "
            "Las siguientes corridas usan esas cookies automaticamente."
        )
        self.in_browser_profile = QLineEdit()
        self.in_browser_profile.setPlaceholderText("data/browser_profile")
        self.chk_browser_headed = QCheckBox("Headed (mostrar ventana del navegador)")
        self.chk_browser_headed.setChecked(False)
        self.lbl_browser_help2 = QLabel(
            "Modo 'Persistent' + Headed = ideal para login manual a LinkedIn.\n"
            "Las cookies (y todo el perfil) se guardan en la carpeta indicada.\n"
            "En modo headless, Playwright usa el mismo perfil pero sin ventana."
        )
        self.lbl_browser_help2.setStyleSheet("color: #666; font-size: 11px;")
        self.lbl_browser_help2.setWordWrap(True)

        br_btns2 = QHBoxLayout()
        self.btn_br_open = QPushButton("🌐 Abrir navegador (login manual)")
        self.btn_br_open.setStyleSheet("font-weight: bold;")
        br_btns2.addWidget(self.btn_br_open)
        br_btns2.addStretch(1)
        br_btns2_w = QWidget()
        br_btns2_w.setLayout(br_btns2)

        br_lay.addRow("Modo sesion:", self.cmb_browser_mode)
        br_lay.addRow("Profile dir:", self.in_browser_profile)
        br_lay.addRow("", self.chk_browser_headed)
        br_lay.addRow("", br_btns2_w)
        br_lay.addRow("", self.lbl_browser_help2)
        br_lay.addRow("", self.lbl_browser_help)
        form_lay.addWidget(gb_browser)

        form_lay.addStretch(1)
        layout.addWidget(form_w, 2)

        self.list_perfiles.currentItemChanged.connect(self._on_select_perfil)
        self.btn_new.clicked.connect(self._on_new_perfil)
        self.btn_save.clicked.connect(self._on_save_perfil)
        self.btn_delete.clicked.connect(self._on_delete_perfil)
        self.btn_reload.clicked.connect(self._refresh_perfiles)
        self.btn_ai_save.clicked.connect(self._on_save_ai_settings)
        self.btn_ai_refresh.clicked.connect(self._refresh_ai_status)
        self.cmb_ai_provider.currentIndexChanged.connect(
            lambda _i: self._on_provider_changed()
        )
        self.btn_li_save.clicked.connect(self._on_save_linkedin)
        self.btn_li_test.clicked.connect(self._on_test_linkedin)
        self.btn_li_clear.clicked.connect(self._on_clear_linkedin_cookies)
        self.btn_br_detect.clicked.connect(self._on_browser_detect)
        self.btn_br_test.clicked.connect(self._on_browser_test)
        self.btn_br_save.clicked.connect(self._on_browser_save)
        self.btn_br_open.clicked.connect(self._on_browser_open)
        self.cmb_browser.currentIndexChanged.connect(
            lambda _i: self._refresh_browser_status()
        )
        self._refresh_ai_status()
        self._load_ai_settings_into_ui()
        self._load_linkedin_settings_into_ui()
        self._load_browser_settings_into_ui()
        return w

    def _build_statusbar(self) -> None:
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.lbl_status = QLabel("Listo")
        self.lbl_db_info = QLabel("")
        self.lbl_db_info.setToolTip(
            "Base de datos SQLite donde se guardan vacantes, perfiles, settings y API keys."
        )
        self.lbl_last_run = QLabel("Última corrida: nunca")
        sb.addWidget(self.lbl_status, 1)
        sb.addPermanentWidget(self.lbl_db_info)
        sb.addPermanentWidget(self.lbl_last_run)
        self._refresh_db_info()

    def _refresh_db_info(self) -> None:
        from core.database import DB_PATH
        from pathlib import Path
        try:
            size = DB_PATH.stat().st_size if DB_PATH.exists() else 0
            size_kb = size / 1024
            rel = DB_PATH.name
            self.lbl_db_info.setText(f"DB: {rel} ({size_kb:.1f} KB)")
            self.lbl_db_info.setStyleSheet("color: #555;")
        except Exception:
            self.lbl_db_info.setText("DB: ?")
            self.lbl_db_info.setStyleSheet("color: #c0392b;")

    # ----- Perfiles (Config tab) -----

    def _refresh_perfiles(self) -> None:
        self.list_perfiles.clear()
        for p in list_perfiles():
            item = QListWidgetItem(p["nombre"])
            item.setData(Qt.ItemDataRole.UserRole, p["id"])
            self.list_perfiles.addItem(item)

        prev_id = self.cmb_perfil.currentData() if hasattr(self, "cmb_perfil") else ""
        self.cmb_perfil.blockSignals(True)
        self.cmb_perfil.clear()
        self.cmb_perfil.addItem("Todos los perfiles", "")
        for p in list_perfiles():
            self.cmb_perfil.addItem(p["nombre"], p["nombre"])
        if prev_id:
            idx = self.cmb_perfil.findData(prev_id)
            if idx >= 0:
                self.cmb_perfil.setCurrentIndex(idx)
        self.cmb_perfil.blockSignals(False)

        self._refresh_perfiles_control()

        if self.list_perfiles.count() > 0 and self.list_perfiles.currentRow() < 0:
            self.list_perfiles.setCurrentRow(0)

    def _refresh_perfiles_control(self) -> None:
        """Recarga el QListWidget checkable de la pestaña Control."""
        perfiles_full = {
            p["id"]: p for p in get_all_perfiles_with_keywords(only_active=False)
        }
        previously_checked_ids: set[int] = {
            self.list_perfiles_control.item(i).data(Qt.ItemDataRole.UserRole + 1)
            for i in range(self.list_perfiles_control.count())
            if self.list_perfiles_control.item(i).checkState()
            == Qt.CheckState.Checked
        }

        self.list_perfiles_control.blockSignals(True)
        self.list_perfiles_control.clear()
        any_checked = False
        for pid, p in perfiles_full.items():
            n_kw = len(p.get("keywords", []))
            label = f"{p['nombre']}  ({n_kw} kw)"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, pid)
            item.setData(Qt.ItemDataRole.UserRole + 1, pid)
            if not p["activo"]:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            if pid in previously_checked_ids and p["activo"]:
                item.setCheckState(Qt.CheckState.Checked)
                any_checked = True
            else:
                item.setCheckState(Qt.CheckState.Unchecked)
            self.list_perfiles_control.addItem(item)
        if not any_checked:
            for i in range(self.list_perfiles_control.count()):
                it = self.list_perfiles_control.item(i)
                if it.flags() & Qt.ItemFlag.ItemIsEnabled:
                    it.setCheckState(Qt.CheckState.Checked)
        self.list_perfiles_control.blockSignals(False)
        self._update_perfiles_label()

    def _select_all_perfiles(self) -> None:
        self.list_perfiles_control.blockSignals(True)
        for i in range(self.list_perfiles_control.count()):
            it = self.list_perfiles_control.item(i)
            if it.flags() & Qt.ItemFlag.ItemIsEnabled:
                it.setCheckState(Qt.CheckState.Checked)
        self.list_perfiles_control.blockSignals(False)
        self._update_perfiles_label()

    def _select_no_perfiles(self) -> None:
        self.list_perfiles_control.blockSignals(True)
        for i in range(self.list_perfiles_control.count()):
            self.list_perfiles_control.item(i).setCheckState(Qt.CheckState.Unchecked)
        self.list_perfiles_control.blockSignals(False)
        self._update_perfiles_label()

    def _selected_perfiles(self) -> list[dict]:
        """Retorna la lista de dicts de perfiles marcados."""
        perfiles_full = {
            p["id"]: p for p in get_all_perfiles_with_keywords(only_active=False)
        }
        out: list[dict] = []
        for i in range(self.list_perfiles_control.count()):
            it = self.list_perfiles_control.item(i)
            if it.checkState() != Qt.CheckState.Checked:
                continue
            pid = it.data(Qt.ItemDataRole.UserRole)
            p = perfiles_full.get(pid)
            if p and p["activo"]:
                out.append(p)
        return out

    def _update_perfiles_label(self) -> None:
        total_activos = sum(1 for p in list_perfiles() if p["activo"])
        sel = len(self._selected_perfiles())
        self.lbl_perfiles.setText(
            f"Perfiles activos: {total_activos}  |  Seleccionados: {sel}"
        )

    def _on_select_perfil(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if current is None:
            self._current_perfil_id = None
            self.in_nombre.clear()
            self.in_descripcion.clear()
            self.in_keywords.clear()
            self.in_ubicaciones.clear()
            self.chk_acepta_remoto.setChecked(True)
            return
        pid = current.data(Qt.ItemDataRole.UserRole)
        self._current_perfil_id = pid
        perfiles = {p["id"]: p for p in get_all_perfiles_with_keywords(only_active=False)}
        p = perfiles.get(pid)
        if p:
            self.in_nombre.setText(p["nombre"])
            self.in_descripcion.setText(p["descripcion"])
            self.in_keywords.setPlainText("\n".join(p.get("keywords", [])))
            self.in_ubicaciones.setText(p.get("ubicaciones_aceptadas") or "")
            self.chk_acepta_remoto.setChecked(bool(p.get("acepta_remoto", 1)))

    def _on_new_perfil(self) -> None:
        self.list_perfiles.clearSelection()
        self._current_perfil_id = None
        self.in_nombre.setFocus()
        self.in_nombre.setText("Nuevo perfil")
        self.in_descripcion.clear()
        self.in_keywords.clear()
        self.in_ubicaciones.setText("Mosquera,Madrid,Funza")
        self.chk_acepta_remoto.setChecked(True)
        self.in_keywords.setPlaceholderText(
            "Ej:\nauxiliar contable\nasistente administrativo\nnomina"
        )

    def _on_save_perfil(self) -> None:
        nombre = self.in_nombre.text().strip()
        if not nombre:
            QMessageBox.warning(self, "Falta nombre", "El nombre del perfil no puede estar vacío.")
            return
        desc = self.in_descripcion.text().strip()
        kws = [
            ln.strip() for ln in self.in_keywords.toPlainText().splitlines() if ln.strip()
        ]
        if not kws:
            QMessageBox.warning(
                self, "Faltan keywords",
                "Agrega al menos una keyword (una por línea).",
            )
            return
        ubicaciones = self.in_ubicaciones.text().strip()
        acepta_remoto = self.chk_acepta_remoto.isChecked()
        try:
            new_id = upsert_perfil(
                nombre,
                desc,
                kws,
                ubicaciones_aceptadas=ubicaciones or "Mosquera,Madrid,Funza",
                acepta_remoto=acepta_remoto,
            )
        except Exception as e:
            QMessageBox.critical(self, "Error al guardar", str(e))
            return
        self._log_message(
            f"Perfil guardado: {nombre} (id={new_id}, "
            f"geo={ubicaciones!r}, remoto={acepta_remoto})",
            "info",
        )
        self._refresh_perfiles()

    def _on_delete_perfil(self) -> None:
        if self._current_perfil_id is None:
            return
        nombre = self.in_nombre.text().strip() or "(sin nombre)"
        r = QMessageBox.question(
            self,
            "Eliminar perfil",
            f"¿Eliminar el perfil '{nombre}'? Esto borra también sus keywords.",
        )
        if r != QMessageBox.StandardButton.Yes:
            return
        delete_perfil(nombre)
        self._log_message(f"Perfil eliminado: {nombre}", "info")
        self._refresh_perfiles()

    # ----- Resultados tab -----

    def _refresh_resultados(self) -> None:
        self.results_proxy.set_page(0)
        n = self.results_model.refresh()
        self._update_total_label(n)
        self._update_page_info(self.results_proxy.page(), self.results_proxy.page_count())
        self._apply_column_widths()
        self._refresh_db_info()

    def _on_row_double_clicked(self, proxy_index) -> None:
        if not proxy_index.isValid():
            return
        source_index = self.results_proxy.mapToSource(proxy_index)
        url = self.results_model.url_at(source_index)
        if not url:
            return
        QDesktopServices.openUrl(QUrl(url))
        self._log_message(f"URL abierta en navegador: {url}", "info")

    def _on_row_clicked(self, proxy_index) -> None:
        """Single-click en la columna URL abre la vacante en el navegador."""
        if not proxy_index.isValid():
            return
        source_index = self.results_proxy.mapToSource(proxy_index)
        if source_index.column() != URL_COL:
            return
        url = self.results_model.url_at(source_index)
        if not url:
            return
        QDesktopServices.openUrl(QUrl(url))
        self._log_message(f"URL abierta en navegador: {url}", "info")

    def _on_select_page(self) -> None:
        """Marca todas las filas visibles en la pagina actual."""
        ids: list[int] = []
        for r in range(self.results_proxy.rowCount()):
            src_idx = self.results_proxy.mapToSource(
                self.results_proxy.index(r, SELECTED_COL)
            )
            vid = self.results_model.vacante_id_at(src_idx)
            if vid:
                ids.append(vid)
        if ids:
            self.results_model.set_selected(ids, True)
            self._log_message(f"Seleccionadas {len(ids)} filas visibles", "info")

    def _on_clear_selection(self) -> None:
        self.results_model.clear_selection()
        self._log_message("Seleccion limpiada", "info")

    def _on_delete_selected(self) -> None:
        ids = self.results_model.selected_ids()
        if not ids:
            return
        r = QMessageBox.question(
            self,
            "Eliminar vacantes",
            f"Vas a eliminar {len(ids)} vacante(s) de la base de datos.\n"
            f"Esta accion no se puede deshacer.\n\n¿Continuar?",
        )
        if r != QMessageBox.StandardButton.Yes:
            return
        deleted = delete_vacantes(ids)
        self.results_model.remove_ids(ids)
        self._log_message(
            f"Eliminadas {deleted} vacante(s) de la DB y de la vista", "info"
        )
        self._update_total_label(self.results_model.rowCount())
        self._update_page_info(self.results_proxy.page(), self.results_proxy.page_count())
        self._refresh_db_info()

    def _collect_filtered_vacantes(self) -> list[dict]:
        """Retorna TODAS las vacantes que pasan los filtros actuales (no paginadas)."""
        src = self.results_model
        out = []
        for r in range(src.rowCount()):
            row = src.row_dict(src.index(r, 0))
            if row and self.results_proxy.filterAcceptsRow(r, QModelIndex()):
                out.append(row)
        return out

    @staticmethod
    def _md_escape(text: str) -> str:
        """Escapa caracteres problematicos para celdas de tabla Markdown."""
        if text is None:
            return ""
        text = str(text)
        text = text.replace("|", "\\|").replace("\n", " ").replace("\r", " ")
        text = text.replace("\t", " ")
        return text.strip()

    def _build_markdown_table(self, vacantes: list[dict]) -> str:
        if not vacantes:
            return "_Sin vacantes en el filtro actual._\n"

        headers = [
            "#", "Titulo", "Empresa", "Ubicacion", "Salario",
            "Bilingue", "Perfil", "URL",
        ]
        lines = []
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("|" + "|".join(["---"] * len(headers)) + "|")

        for i, v in enumerate(vacantes, 1):
            titulo = self._md_escape(v.get("titulo", ""))
            url = v.get("url", "") or ""
            if url:
                titulo_cell = f"[{titulo}]({url})"
            else:
                titulo_cell = titulo
            bilingue = "Si" if v.get("bilingue") in (1, True, "1") else "No"
            row = [
                str(i),
                titulo_cell,
                self._md_escape(v.get("empresa", "")),
                self._md_escape(v.get("ubicacion", "")),
                self._md_escape(v.get("salario", "")),
                bilingue,
                self._md_escape(v.get("perfil_nombre", "")),
                f"<{url}>" if url else "",
            ]
            lines.append("| " + " | ".join(row) + " |")

        header_meta = (
            f"_Generado por Job Sprout - {len(vacantes)} vacantes. "
            f"Proveedor AI: {get_setting('ai_provider', 'gemini')}._\n"
        )
        return header_meta + "\n".join(lines) + "\n"

    def _on_copy_as_markdown(self) -> None:
        vacantes = self._collect_filtered_vacantes()
        if not vacantes:
            QMessageBox.information(
                self, "Copiar como MD",
                "No hay vacantes en el filtro actual para copiar.",
            )
            return
        md = self._build_markdown_table(vacantes)
        QApplication.clipboard().setText(md)
        self._update_status(
            f"📋 Copiadas {len(vacantes)} vacantes al portapapeles (Markdown)"
        )
        self._log_message(
            f"Copiado al portapapeles: {len(vacantes)} vacantes en formato MD", "info"
        )

    def _on_export_csv(self) -> None:
        vacantes = self._collect_filtered_vacantes()
        if not vacantes:
            QMessageBox.information(
                self, "Exportar CSV",
                "No hay vacantes en el filtro actual para exportar.",
            )
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Exportar vacantes a CSV",
            f"job_sprout_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            "CSV files (*.csv)",
        )
        if not path:
            return
        try:
            import csv
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(
                    ["id", "titulo", "empresa", "ubicacion", "salario",
                     "bilingue", "perfil", "url", "portal",
                     "relevance_score", "created_at"]
                )
                for v in vacantes:
                    w.writerow(
                        [
                            v.get("id", ""),
                            v.get("titulo", ""),
                            v.get("empresa", ""),
                            v.get("ubicacion", ""),
                            v.get("salario", ""),
                            "Si" if v.get("bilingue") in (1, True, "1") else "No",
                            v.get("perfil_nombre", ""),
                            v.get("url", ""),
                            v.get("portal", ""),
                            v.get("relevance_score", ""),
                            v.get("created_at", ""),
                        ]
                    )
        except OSError as e:
            QMessageBox.critical(self, "Error al exportar", str(e))
            return
        self._update_status(f"💾 Exportadas {len(vacantes)} vacantes a {path}")
        self._log_message(f"Exportadas {len(vacantes)} vacantes a CSV: {path}", "info")
        QMessageBox.information(
            self, "Exportar CSV",
            f"Exportadas {len(vacantes)} vacantes a:\n{path}",
        )

    # ----- Control tab (start/stop) -----

    def _update_ai_short_label(self) -> None:
        try:
            use_ai = get_setting("use_ai_scoring", "0") == "1"
            use_geo = get_setting("ai_validate_geo", "1") == "1"
            from core.ai.ranker import GeminiRanker
            model = get_setting("ai_model", "gemini-2.5-flash") or "gemini-2.5-flash"
            r = GeminiRanker(model=model)
            if r.is_available:
                if use_ai or use_geo:
                    self.lbl_ai_short.setText(f"AI: ON ({model})")
                    self.lbl_ai_short.setStyleSheet("color: #1b8a3a;")
                    self.lbl_ai_inline.setText(f"habilitada - {r.status}")
                    self.lbl_ai_inline.setStyleSheet("color: #1b8a3a;")
                else:
                    self.lbl_ai_short.setText("AI: OFF")
                    self.lbl_ai_short.setStyleSheet("color: #888;")
                    self.lbl_ai_inline.setText(f"deshabilitada - {r.status}")
                    self.lbl_ai_inline.setStyleSheet("color: #666;")
            else:
                self.lbl_ai_short.setText("AI: sin key")
                self.lbl_ai_short.setStyleSheet("color: #c0392b;")
                self.lbl_ai_inline.setText(r.status)
                self.lbl_ai_inline.setStyleSheet("color: #c0392b;")
        except Exception as e:
            self.lbl_ai_short.setText("AI: error")
            self.lbl_ai_short.setStyleSheet("color: #c0392b;")
            self.lbl_ai_inline.setText(str(e)[:60])
            self.lbl_ai_inline.setStyleSheet("color: #c0392b;")

    def _load_ai_toggle_from_settings(self) -> None:
        """Carga el estado del toggle desde DB al iniciar."""
        s = get_all_settings()
        use_ai = s.get("use_ai_scoring", "0") == "1"
        use_geo = s.get("ai_validate_geo", "1") == "1"
        self.chk_use_ai.blockSignals(True)
        # El toggle esta ON si CUALQUIERA de las AI features esta activa
        self.chk_use_ai.setChecked(use_ai or use_geo)
        self.chk_use_ai.blockSignals(False)

    def _on_use_ai_toggled(self, checked: bool) -> None:
        """Toggle rapido en Panel de Control: activa/desactiva AI (ambas features)."""
        set_setting("use_ai_scoring", "1" if checked else "0")
        set_setting("ai_validate_geo", "1" if checked else "0")
        # Sincronizar con el form de Config si existe
        if hasattr(self, "chk_ai_scoring"):
            self.chk_ai_scoring.blockSignals(True)
            self.chk_ai_geo.blockSignals(True)
            self.chk_ai_scoring.setChecked(checked)
            self.chk_ai_geo.setChecked(checked)
            self.chk_ai_scoring.blockSignals(False)
            self.chk_ai_geo.blockSignals(False)
        self._update_ai_short_label()
        self._log_message(
            f"AI {'activada' if checked else 'desactivada'} (toggle Panel de Control)",
            "info",
        )

    def _on_start(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return

        perfiles = self._selected_perfiles()
        if not perfiles:
            QMessageBox.warning(
                self,
                "Sin perfiles seleccionados",
                "Marca al menos un perfil en la sección 'Perfiles a buscar'.",
            )
            return

        portals: list[str] = []
        for key, chk in self._portal_checks.items():
            if chk.isChecked() and chk.isEnabled():
                portals.append(key)
        if not portals:
            QMessageBox.warning(
                self, "Sin portales",
                "Selecciona al menos un portal en la pestaña de control.",
            )
            return

        # Warning si LinkedIn esta seleccionado
        if "linkedin" in portals:
            r = QMessageBox.warning(
                self,
                "LinkedIn activado",
                "Estas a punto de scrapear LinkedIn con tu cuenta.\n\n"
                "ADVERTENCIA: LinkedIn prohibe scraping en sus ToS. "
                "Tu cuenta puede ser baneada si LinkedIn detecta el scraping.\n\n"
                "Recomendaciones:\n"
                "- No corras esto mas de 1-2 veces al dia\n"
                "- Usa 'Max ofertas/sesion' bajo (20-30)\n"
                "- Si LinkedIn te pide CAPTCHA, espera 24h\n\n"
                "¿Continuar bajo tu responsabilidad?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if r != QMessageBox.StandardButton.Yes:
                return

        nombres = ", ".join(p["nombre"] for p in perfiles)
        self._log_message(
            f"Agente arrancando con {len(perfiles)} perfil(es): {nombres}", "info"
        )
        self._update_ai_short_label()

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress.setRange(0, 0)
        self.progress.setValue(0)
        self._update_status("Ejecutando agente…")

        self._worker = AgentWorker(
            portals=portals,
            detail_limit_per_keyword=self.spn_limit.value() or None,
            min_sleep=self.spn_sleep_min.value(),
            max_sleep=self.spn_sleep_max.value(),
            perfiles=perfiles,
            browser_engine=get_setting("browser_engine", "chromium"),
            browser_path=get_setting("browser_path", ""),
            browser_mode=get_setting("browser_mode", "ephemeral"),
            browser_profile_dir=get_setting("browser_profile_dir", "data/browser_profile"),
        )
        self._worker.log_message.connect(self._log_message)
        self._worker.progress.connect(self._on_progress)
        self._worker.vacante_saved.connect(self._on_vacante_saved)
        self._worker.finished_with_stats.connect(self._on_worker_finished)
        self._worker.error_occurred.connect(self._on_worker_error)
        self._worker.finished.connect(self._on_thread_finished)
        self._worker.start()

    def _on_stop(self) -> None:
        if self._worker is None:
            return
        self._worker.request_cancel()
        self._log_message("Cancelación solicitada. Terminando unidad actual…", "warning")

    def _on_progress(self, current: int, total: int, msg: str) -> None:
        if total <= 0:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, total)
            self.progress.setValue(current)
        self._update_status(f"{current}/{total} — {msg}")

    def _on_vacante_saved(self, vacante: dict[str, Any]) -> None:
        self.results_model.append_row(
            {
                "id": vacante.get("id", 0),
                "portal": vacante.get("portal", ""),
                "perfil_nombre": vacante.get("perfil_nombre", ""),
                "titulo": vacante.get("titulo", ""),
                "empresa": vacante.get("empresa", ""),
                "ubicacion": vacante.get("ubicacion", ""),
                "salario": vacante.get("salario", ""),
                "bilingue": vacante.get("bilingue", False),
                "url": vacante.get("url", ""),
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        titulo = vacante.get("titulo", "")[:60]
        self._log_message(
            f"+ nueva vacante: [{vacante.get('portal')}] {titulo}", "info"
        )
        self._refresh_db_info()

    def _on_worker_finished(self, stats) -> None:
        ai_part = ""
        if stats.ai_calls or stats.ai_failures:
            ai_part = (
                f" | AI: {stats.ai_calls} calls, {stats.ai_failures} fallos, "
                f"{stats.vacantes_ai_rejected} rechazadas"
            )
        self._update_status(
            f"Agente terminado en {stats.elapsed_seconds:.1f}s — "
            f"vistas={stats.vacantes_seen} guardadas={stats.vacantes_saved} "
            f"geo_descartadas={stats.vacantes_geo_rejected} "
            f"duplicadas={stats.vacantes_duplicated}{ai_part}"
        )
        self.lbl_last_run.setText(
            f"Última corrida: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        self._refresh_resultados()

    def _on_worker_error(self, msg: str) -> None:
        self._log_message(f"ERROR: {msg}", "error")
        QMessageBox.critical(self, "Error del agente", msg)

    def _on_thread_finished(self) -> None:
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self._worker = None

    # ----- helpers -----

    def _log_message(self, msg: str, level: str = "info") -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        prefix = {"info": "ℹ", "warning": "⚠", "error": "✖", "debug": "…"}.get(level, "·")
        self.txt_log.appendPlainText(f"{ts} {prefix} {msg}")

    def _update_status(self, text: str) -> None:
        self.lbl_status.setText(text)

    def _refresh_ai_status(self) -> None:
        try:
            from core.ai.ranker import get_ranker_from_settings
            ranker = get_ranker_from_settings()
            provider_name = {"gemini": "Gemini", "openrouter": "OpenRouter"}.get(
                ranker.provider, ranker.provider
            )
            self.lbl_ai_status.setText(
                f"[{provider_name}] {ranker.status}"
            )
            if ranker.is_available:
                self.lbl_ai_status.setStyleSheet("color: #1b8a3a; font-weight: bold;")
            else:
                self.lbl_ai_status.setStyleSheet("color: #c0392b;")
        except Exception as e:
            self.lbl_ai_status.setText(f"Error: {e}")
            self.lbl_ai_status.setStyleSheet("color: #c0392b;")

    def _load_ai_settings_into_ui(self) -> None:
        from core.ai.ranker import PROVIDER_MODELS, DEFAULT_GEMINI_MODEL, DEFAULT_OPENROUTER_MODEL

        s = get_all_settings()
        self.chk_ai_scoring.setChecked(s.get("use_ai_scoring", "0") == "1")
        self.chk_ai_geo.setChecked(s.get("ai_validate_geo", "1") == "1")
        try:
            self.spn_ai_threshold.setValue(float(s.get("ai_relevance_threshold", "0.4")))
        except ValueError:
            self.spn_ai_threshold.setValue(0.4)

        provider = s.get("ai_provider", "gemini") or "gemini"
        idx = self.cmb_ai_provider.findData(provider)
        if idx >= 0:
            self.cmb_ai_provider.setCurrentIndex(idx)
        else:
            self.cmb_ai_provider.setCurrentIndex(0)

        # Llenar el combo de modelos segun provider
        self.cmb_ai_model.blockSignals(True)
        self.cmb_ai_model.clear()
        for m in PROVIDER_MODELS.get(provider, []):
            self.cmb_ai_model.addItem(m)
        self.cmb_ai_model.blockSignals(False)

        model = s.get("ai_model", "") or ""
        if not model:
            model = DEFAULT_OPENROUTER_MODEL if provider == "openrouter" else DEFAULT_GEMINI_MODEL
        # Si el modelo guardado no esta en la lista, lo agregamos al inicio
        if self.cmb_ai_model.findText(model) < 0:
            self.cmb_ai_model.insertItem(0, model)
        self.cmb_ai_model.setCurrentText(model)
        self.in_ai_model.setText(model)

        # API key override (no mostrar por seguridad)
        key_override = s.get("ai_api_key_override", "") or ""
        self.in_ai_api_key.setText(key_override)
        if key_override:
            self.lbl_ai_status.setText(
                self.lbl_ai_status.text() + "  (key en DB)"
            )

    def _on_provider_changed(self) -> None:
        """Cuando cambia el provider, actualiza el combo de modelos disponibles."""
        from core.ai.ranker import PROVIDER_MODELS

        provider = self.cmb_ai_provider.currentData() or "gemini"
        self.cmb_ai_model.blockSignals(True)
        self.cmb_ai_model.clear()
        for m in PROVIDER_MODELS.get(provider, []):
            self.cmb_ai_model.addItem(m)
        # Seleccionar el primero o el actual
        current = self.in_ai_model.text().strip()
        if current and self.cmb_ai_model.findText(current) >= 0:
            self.cmb_ai_model.setCurrentText(current)
        else:
            self.cmb_ai_model.setCurrentIndex(0)
            self.in_ai_model.setText(self.cmb_ai_model.currentText())
        self.cmb_ai_model.blockSignals(False)

    def _on_toggle_show_api_key(self, checked: bool) -> None:
        self.in_ai_api_key.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )

    def _on_clear_api_key(self) -> None:
        self.in_ai_api_key.clear()
        QMessageBox.information(
            self,
            "API key",
            "Campo limpiado. Recuerda hacer click en 'Guardar AI settings' para persistir.",
        )

    def _on_save_ai_settings(self) -> None:
        set_setting("use_ai_scoring", "1" if self.chk_ai_scoring.isChecked() else "0")
        set_setting("ai_validate_geo", "1" if self.chk_ai_geo.isChecked() else "0")
        set_setting("ai_relevance_threshold", f"{self.spn_ai_threshold.value():.2f}")
        set_setting("ai_provider", self.cmb_ai_provider.currentData() or "gemini")
        # Modelo: prioridad al in_ai_model (libre), fallback al combo
        model = self.in_ai_model.text().strip() or self.cmb_ai_model.currentText().strip()
        if not model:
            model = "gemini-2.5-flash"
        set_setting("ai_model", model)
        set_setting("ai_api_key_override", self.in_ai_api_key.text().strip())
        self._refresh_ai_status()
        self._update_ai_short_label()
        # Sincronizar el toggle rapido del Panel de Control
        if hasattr(self, "chk_use_ai"):
            use_ai = self.chk_ai_scoring.isChecked()
            use_geo = self.chk_ai_geo.isChecked()
            self.chk_use_ai.blockSignals(True)
            self.chk_use_ai.setChecked(use_ai or use_geo)
            self.chk_use_ai.blockSignals(False)
        QMessageBox.information(
            self,
            "AI settings",
            "Configuracion AI guardada. El proximo arranque del agente la usara.",
        )

    # ----- LinkedIn -----

    def _load_linkedin_settings_into_ui(self) -> None:
        s = get_all_settings()
        self.in_li_email.setText(s.get("linkedin_email", "") or "")
        try:
            self.spn_li_max.setValue(int(s.get("linkedin_max_per_session", "20")))
        except ValueError:
            self.spn_li_max.setValue(20)
        # El password no se carga por seguridad: el usuario debe re-ingresarlo
        self.in_li_password.clear()
        self._refresh_linkedin_status()

    def _refresh_linkedin_status(self) -> None:
        from pathlib import Path
        from core.scrapers.linkedin import DEFAULT_COOKIES_PATH
        cookies = Path(s.get("linkedin_cookies_path", str(DEFAULT_COOKIES_PATH))) if False else DEFAULT_COOKIES_PATH
        if cookies.exists():
            self.lbl_li_status.setText(f"✓ Cookies guardadas ({cookies})")
            self.lbl_li_status.setStyleSheet("color: #1b8a3a;")
        else:
            email = get_setting("linkedin_email", "") or ""
            if email:
                self.lbl_li_status.setText(f"Sesion: {email} (sin cookies guardadas)")
                self.lbl_li_status.setStyleSheet("color: #666;")
            else:
                self.lbl_li_status.setText("(no configurado)")
                self.lbl_li_status.setStyleSheet("color: #c0392b;")

    def _on_save_linkedin(self) -> None:
        email = self.in_li_email.text().strip()
        if email:
            set_setting("linkedin_email", email)
        set_setting("linkedin_max_per_session", str(self.spn_li_max.value()))
        # Password NO se persiste a la DB (se pide cada vez o via cookies)
        self.in_li_password.clear()
        self._refresh_linkedin_status()
        QMessageBox.information(
            self,
            "LinkedIn",
            "Configuracion guardada.\n\n"
            "El password NO se guarda en la DB por seguridad. "
            "Ingresalo cada vez que hagas 'Test login' o se usaran las cookies guardadas si existen.",
        )

    def _on_test_linkedin(self) -> None:
        email = self.in_li_email.text().strip() or get_setting("linkedin_email", "")
        password = self.in_li_password.text()
        if not email or not password:
            QMessageBox.warning(
                self, "LinkedIn",
                "Necesito email y password para hacer test de login.",
            )
            return
        # Warning de seguridad
        r = QMessageBox.warning(
            self,
            "LinkedIn - Test login",
            "Esto abrira un navegador, ira a linkedin.com/login e ingresara tus credenciales.\n\n"
            "ADVERTENCIA: Tu cuenta puede ser baneada si LinkedIn detecta el scraping.\n"
            "Usa bajo tu propio riesgo.\n\n¿Continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if r != QMessageBox.StandardButton.Yes:
            return

        self.btn_li_test.setEnabled(False)
        self.btn_li_test.setText("Probando...")
        QApplication.processEvents()

        try:
            from core.scrapers.linkedin import LinkedInScraper
            scraper = LinkedInScraper(
                email=email, password=password, headless=True,
                min_sleep=0.5, max_sleep=1.0,  # rapido para test
            )
            with scraper._browser_context() as (_, ctx, page):
                if scraper._login(ctx, page):
                    QMessageBox.information(
                        self, "LinkedIn - OK",
                        f"Login exitoso para {email}.\n"
                        f"Cookies guardadas para futuras corridas.",
                    )
                else:
                    QMessageBox.critical(
                        self, "LinkedIn - Fallo",
                        "Login fallo. Posibles causas:\n"
                        "- Credenciales incorrectas\n"
                        "- LinkedIn mostro CAPTCHA o 2FA (resuelve manualmente)\n"
                        "- Rate limit (intenta mas tarde)",
                    )
            self._refresh_linkedin_status()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
        finally:
            self.btn_li_test.setEnabled(True)
            self.btn_li_test.setText("Test login")

    def _on_clear_linkedin_cookies(self) -> None:
        from core.scrapers.linkedin import DEFAULT_COOKIES_PATH
        r = QMessageBox.question(
            self, "Borrar cookies",
            f"Vas a borrar las cookies de LinkedIn ({DEFAULT_COOKIES_PATH}).\n"
            "La proxima corrida pedira email + password de nuevo.\n\n¿Continuar?",
        )
        if r != QMessageBox.StandardButton.Yes:
            return
        try:
            if DEFAULT_COOKIES_PATH.exists():
                DEFAULT_COOKIES_PATH.unlink()
            QMessageBox.information(self, "Cookies borradas", "Cookies de LinkedIn eliminadas.")
            self._refresh_linkedin_status()
        except OSError as e:
            QMessageBox.critical(self, "Error", str(e))

    # ----- Browser (Chromium / Brave / Chrome / Edge) -----

    def _load_browser_settings_into_ui(self) -> None:
        s = get_all_settings()
        engine = s.get("browser_engine", "chromium") or "chromium"
        idx = self.cmb_browser.findData(engine)
        if idx < 0:
            idx = 0
        self.cmb_browser.blockSignals(True)
        self.cmb_browser.setCurrentIndex(idx)
        self.cmb_browser.blockSignals(False)
        self.in_browser_path.setText(s.get("browser_path", "") or "")
        self.in_browser_profile.setText(s.get("browser_profile_dir", "data/browser_profile") or "data/browser_profile")

        mode = s.get("browser_mode", "ephemeral") or "ephemeral"
        midx = self.cmb_browser_mode.findData(mode)
        if midx < 0:
            midx = 0
        self.cmb_browser_mode.blockSignals(True)
        self.cmb_browser_mode.setCurrentIndex(midx)
        self.cmb_browser_mode.blockSignals(False)
        self._refresh_browser_status()

    def _refresh_browser_status(self) -> None:
        from core.browser import get_browser_config
        engine = self.cmb_browser.currentData() or "chromium"
        custom = self.in_browser_path.text().strip()
        config = get_browser_config(engine, custom)
        if config["name"] == "chromium":
            self.lbl_br_status.setText("OK - Chromium incluido con Playwright")
            self.lbl_br_status.setStyleSheet("color: #1b8a3a;")
        elif config["executable_path"]:
            self.lbl_br_status.setText(
                f"OK - {config['name'].title()} en {config['executable_path']}"
            )
            self.lbl_br_status.setStyleSheet("color: #1b8a3a;")
        else:
            self.lbl_br_status.setText(
                f"{config['name'].title()} no encontrado. Click 'Autodetectar' o configura el path."
            )
            self.lbl_br_status.setStyleSheet("color: #c0392b;")

    def _on_browser_detect(self) -> None:
        from core.browser import find_browser
        engine = self.cmb_browser.currentData() or "chromium"
        if engine == "chromium":
            QMessageBox.information(
                self, "Autodetectar",
                "Chromium viene incluido con Playwright, no requiere deteccion.",
            )
            return
        path = find_browser(engine)
        if path:
            self.in_browser_path.setText(path)
            self._refresh_browser_status()
            QMessageBox.information(
                self, f"{engine.title()} detectado",
                f"Encontrado en:\n{path}",
            )
        else:
            QMessageBox.warning(
                self, f"{engine.title()} no encontrado",
                f"No se pudo detectar {engine.title()} automaticamente.\n"
                f"Ingresa el path manualmente (ej: /usr/bin/brave).",
            )

    def _on_browser_test(self) -> None:
        from core.browser import launch_browser
        engine = self.cmb_browser.currentData() or "chromium"
        custom = self.in_browser_path.text().strip()
        self.btn_br_test.setEnabled(False)
        self.btn_br_test.setText("Probando...")
        QApplication.processEvents()
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = launch_browser(p, engine, custom, headless=True)
                ctx = browser.new_context()
                page = ctx.new_page()
                page.goto("about:blank", timeout=10_000)
                ua = page.evaluate("navigator.userAgent")
                browser.close()
            QMessageBox.information(
                self, "Test OK",
                f"Browser '{engine}' arranca correctamente.\n\n"
                f"User-Agent: {ua[:80]}...",
            )
        except Exception as e:
            QMessageBox.critical(
                self, "Test fallo",
                f"No se pudo arrancar el browser:\n\n{type(e).__name__}: {e}",
            )
        finally:
            self.btn_br_test.setEnabled(True)
            self.btn_br_test.setText("Test")

    def _on_browser_save(self) -> None:
        set_setting("browser_engine", self.cmb_browser.currentData() or "chromium")
        set_setting("browser_path", self.in_browser_path.text().strip())
        set_setting("browser_mode", self.cmb_browser_mode.currentData() or "ephemeral")
        set_setting(
            "browser_profile_dir",
            self.in_browser_profile.text().strip() or "data/browser_profile",
        )
        self._refresh_browser_status()
        QMessageBox.information(
            self, "Browser",
            f"Configuracion guardada:\n"
            f"  Motor: {self.cmb_browser.currentData()}\n"
            f"  Modo: {self.cmb_browser_mode.currentData()}\n"
            f"  Profile: {self.in_browser_profile.text() or 'data/browser_profile'}",
        )

    def _on_browser_open(self) -> None:
        """Abre el navegador con el perfil persistent, headed, para login manual."""
        engine = self.cmb_browser.currentData() or "chromium"
        custom = self.in_browser_path.text().strip()
        profile = self.in_browser_profile.text().strip() or "data/browser_profile"

        # Auto-guardar settings antes de abrir
        set_setting("browser_engine", engine)
        set_setting("browser_path", custom)
        set_setting("browser_mode", "persistent")
        set_setting("browser_profile_dir", profile)

        from core.browser import launch_persistent_browser
        self.btn_br_open.setEnabled(False)
        self.btn_br_open.setText("Abriendo navegador...")
        QApplication.processEvents()
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                ctx = launch_persistent_browser(
                    p, engine, custom,
                    profile_dir=profile, headed=True,
                )
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto("https://www.linkedin.com/login", timeout=30_000)
                # QMessageBox en otro thread, pero como Playwright es bloqueante
                # y este codigo corre en el main thread, mostramos al final.
                QMessageBox.information(
                    self, "Navegador abierto",
                    "Brave se abrio con tu perfil persistent.\n\n"
                    "Pasos:\n"
                    "1. Inicia sesion en LinkedIn (o cualquier otro sitio)\n"
                    "2. Resuelve cualquier CAPTCHA si aparece\n"
                    "3. Cuando estes logueado, CIERRA la ventana del navegador\n"
                    "4. Las cookies quedaran guardadas en:\n"
                    f"   {profile}\n\n"
                    "Las siguientes corridas del agente usaran esta sesion.",
                )
                # Mantener el contexto vivo hasta que el usuario cierre el browser
                # Cierra el contexto cuando el browser se cierre
                try:
                    page.wait_for_event("close", timeout=0)
                except Exception:
                    pass
                ctx.close()
            self._refresh_browser_status()
        except Exception as e:
            QMessageBox.critical(
                self, "Error abriendo navegador",
                f"{type(e).__name__}: {e}",
            )
        finally:
            self.btn_br_open.setEnabled(True)
            self.btn_br_open.setText("🌐 Abrir navegador (login manual)")
        try:
            if DEFAULT_COOKIES_PATH.exists():
                DEFAULT_COOKIES_PATH.unlink()
            QMessageBox.information(self, "Cookies borradas", "Cookies de LinkedIn eliminadas.")
            self._refresh_linkedin_status()
        except OSError as e:
            QMessageBox.critical(self, "Error", str(e))

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "Acerca de Job Sprout",
            "<b>Job Sprout</b><br>"
            "Agente scraper inteligente para portales de empleo en Colombia.<br><br>"
            "Stack: Python · Playwright · SQLite · PyQt6<br>"
            "Perfiles: Esposa - Gestión Humana, Jesús - Full Stack.<br><br>"
            "Doble clic en una vacante abre la URL en tu navegador.",
        )

    # ----- shutdown -----

    def closeEvent(self, ev) -> None:
        if self._worker is not None and self._worker.isRunning():
            r = QMessageBox.question(
                self, "Agente en ejecución",
                "El agente está corriendo. ¿Cancelar y salir?",
            )
            if r != QMessageBox.StandardButton.Yes:
                ev.ignore()
                return
            self._worker.request_cancel()
            self._worker.wait(3000)
        ev.accept()


def launch_gui() -> int:
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Job Sprout")
    app.setOrganizationName("vanjexdev")
    app_icon = QIcon("images/logo-cuadrado-app.png")
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
    win = MainWindow()
    win.show()
    return app.exec()
