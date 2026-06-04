"""
Modelo de tabla para QTableView que refleja la tabla `vacantes` de SQLite.

Columnas:
  0  __selected__  -> checkbox por fila (estado en memoria, no se persiste)
  1  id
  2  portal
  3  perfil_nombre
  4  titulo
  5  empresa
  6  ubicacion
  7  salario
  8  bilingue
  9  url        -> Aplicar (clickable, link azul, abre la URL en el navegador)
  10 created_at

Ademas expone ResultsViewProxy (search + filtros + paginacion) y metodos para
gestionar la seleccion por id (no por row index, que cambia con paginacion).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from PyQt6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QBrush, QColor

from core.database import list_vacantes


COLUMNS: list[tuple[str, str]] = [
    ("__selected__", "☑"),
    ("id", "ID"),
    ("portal", "Portal"),
    ("perfil_nombre", "Perfil"),
    ("titulo", "Título"),
    ("empresa", "Empresa"),
    ("ubicacion", "Ubicación"),
    ("salario", "Salario"),
    ("bilingue", "Bilingüe"),
    ("url", "Aplicar"),
    ("created_at", "Guardada"),
]

SELECTED_COL = 0
ID_COL = next(i for i, (key, _) in enumerate(COLUMNS) if key == "id")
URL_COL = next(i for i, (key, _) in enumerate(COLUMNS) if key == "url")
URL_KEY = "url"
ID_KEY = "id"


def _format_dt(iso: str) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


def _format_bilingue(v: Any) -> str:
    if v in (1, True, "1", "true"):
        return "Sí"
    return "No"


class VacantesTableModel(QAbstractTableModel):
    """Modelo editable en lectura. Reconsulta la DB al hacer refresh()."""

    countChanged = pyqtSignal(int)
    selectionChanged = pyqtSignal(int)

    def __init__(self, parent=None, *, only_approved: bool = True) -> None:
        super().__init__(parent)
        self._rows: list[dict[str, Any]] = []
        self._only_approved = only_approved
        self._selected_ids: set[int] = set()
        self.refresh()

    def refresh(self) -> int:
        self.beginResetModel()
        self._rows = list_vacantes(
            aprobado=True if self._only_approved else None,
            limit=5000,
        )
        valid_ids = {int(r.get("id", 0)) for r in self._rows}
        self._selected_ids &= valid_ids
        self.endResetModel()
        self.countChanged.emit(len(self._rows))
        self.selectionChanged.emit(len(self._selected_ids))
        return len(self._rows)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(COLUMNS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole and 0 <= section < len(COLUMNS):
                return COLUMNS[section][1]
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = super().flags(index)
        if not index.isValid():
            return base
        col_key = COLUMNS[index.column()][0]
        if col_key == "__selected__":
            return base | Qt.ItemFlag.ItemIsUserCheckable
        return base

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None

        row = self._rows[index.row()]
        col_key = COLUMNS[index.column()][0]
        raw = row.get(col_key, "")

        if col_key == "__selected__":
            if role in (Qt.ItemDataRole.CheckStateRole, Qt.ItemDataRole.DisplayRole):
                vid = int(row.get(ID_KEY, 0) or 0)
                return Qt.CheckState.Checked if vid in self._selected_ids else Qt.CheckState.Unchecked
            return None

        if col_key == "url":
            if role == Qt.ItemDataRole.DisplayRole:
                return "🔗 Aplicar"
            if role == Qt.ItemDataRole.ToolTipRole:
                return str(raw or "")
            if role == Qt.ItemDataRole.ForegroundRole:
                return QBrush(QColor("#0a66c2"))
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return int(Qt.AlignmentFlag.AlignCenter)
            if role == Qt.ItemDataRole.UserRole:
                return raw
            return None

        if role == Qt.ItemDataRole.DisplayRole:
            if col_key == "bilingue":
                return _format_bilingue(raw)
            if col_key == "created_at":
                return _format_dt(raw)
            return "" if raw is None else str(raw)

        if role == Qt.ItemDataRole.ToolTipRole:
            if col_key == "titulo":
                return str(row.get("descripcion", ""))[:600]
            if col_key == "id":
                return f"ID interno: {raw}"

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col_key == "id":
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if col_key == "bilingue":
                return int(Qt.AlignmentFlag.AlignCenter)

        if role == Qt.ItemDataRole.ForegroundRole and col_key == "bilingue":
            if raw in (1, True, "1"):
                return QBrush(QColor("#1b8a3a"))
            return QBrush(QColor("#888888"))

        if role == Qt.ItemDataRole.UserRole:
            return raw

        return None

    def setData(
        self,
        index: QModelIndex,
        value: Any,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return False
        col_key = COLUMNS[index.column()][0]
        if col_key == "__selected__" and role in (
            Qt.ItemDataRole.CheckStateRole, Qt.ItemDataRole.EditRole
        ):
            vid = int(self._rows[index.row()].get(ID_KEY, 0) or 0)
            checked = (
                value == Qt.CheckState.Checked.value
                or value == Qt.CheckState.Checked
                or value == 2
            )
            if checked:
                self._selected_ids.add(vid)
            else:
                self._selected_ids.discard(vid)
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
            self.selectionChanged.emit(len(self._selected_ids))
            return True
        return False

    def url_at(self, index: QModelIndex) -> str:
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return ""
        return str(self._rows[index.row()].get(URL_KEY, ""))

    def vacante_id_at(self, index: QModelIndex) -> int:
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return 0
        return int(self._rows[index.row()].get(ID_KEY, 0) or 0)

    def row_dict(self, index: QModelIndex) -> dict[str, Any] | None:
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        return dict(self._rows[index.row()])

    def append_row(self, row: dict[str, Any]) -> None:
        self.beginInsertRows(QModelIndex(), 0, 0)
        self._rows.insert(0, row)
        self.endInsertRows()
        self.countChanged.emit(len(self._rows))

    def remove_ids(self, ids: Iterable[int]) -> int:
        """Quita de la vista las filas con esos ids (sin tocar la DB)."""
        idset = {int(i) for i in ids}
        if not idset:
            return 0
        removed = 0
        for r in range(len(self._rows) - 1, -1, -1):
            if int(self._rows[r].get(ID_KEY, 0) or 0) in idset:
                self.beginRemoveRows(QModelIndex(), r, r)
                del self._rows[r]
                self.endRemoveRows()
                removed += 1
        self._selected_ids -= idset
        self.countChanged.emit(len(self._rows))
        self.selectionChanged.emit(len(self._selected_ids))
        return removed

    def selected_ids(self) -> list[int]:
        return sorted(self._selected_ids)

    def set_selected(self, ids: Iterable[int], selected: bool = True) -> None:
        """Marca/desmarca varios ids y emite dataChanged para refrescar UI."""
        target = {int(i) for i in ids}
        if not target:
            return
        if selected:
            self._selected_ids |= target
        else:
            self._selected_ids -= target
        for r, row in enumerate(self._rows):
            if int(row.get(ID_KEY, 0) or 0) in target:
                idx = self.index(r, SELECTED_COL)
                self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.CheckStateRole])
        self.selectionChanged.emit(len(self._selected_ids))

    def clear_selection(self) -> None:
        if not self._selected_ids:
            return
        old = self._selected_ids
        self._selected_ids = set()
        for r, row in enumerate(self._rows):
            if int(row.get(ID_KEY, 0) or 0) in old:
                idx = self.index(r, SELECTED_COL)
                self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.CheckStateRole])
        self.selectionChanged.emit(0)


class ResultsViewProxy(QSortFilterProxyModel):
    """Proxy que combina: busqueda + filtros (bilingue, perfil) + paginacion.

    Trabaja sobre un VacantesTableModel. La paginacion opera sobre el set
    YA filtrado: cambiar de pagina no reaplica el filtro.
    """

    pageInfoChanged = pyqtSignal(int, int)
    """Emitido al cambiar: (pagina_actual, total_paginas)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._search = ""
        self._only_bilingual = False
        self._perfil_filter = ""
        self._page = 0
        self._page_size = 50
        self.setDynamicSortFilter(False)

    # ---- filtros ----

    def set_search(self, text: str) -> None:
        self._search = (text or "").lower().strip()
        self._page = 0
        self._refresh()

    def set_only_bilingual(self, on: bool) -> None:
        self._only_bilingual = bool(on)
        self._page = 0
        self._refresh()

    def set_perfil_filter(self, name: str) -> None:
        self._perfil_filter = name or ""
        self._page = 0
        self._refresh()

    def _refresh(self) -> None:
        self.beginResetModel()
        self.endResetModel()
        self.pageInfoChanged.emit(self._page, self.page_count())

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        src = self.sourceModel()
        if not isinstance(src, VacantesTableModel):
            return True
        rows = src._rows
        if not (0 <= source_row < len(rows)):
            return False
        row = rows[source_row]
        if self._only_bilingual and not row.get("bilingue"):
            return False
        if self._perfil_filter and row.get("perfil_nombre") != self._perfil_filter:
            return False
        if self._search:
            haystack = " ".join(
                str(row.get(k, "") or "")
                for k in ("titulo", "empresa", "ubicacion", "portal", "perfil_nombre")
            ).lower()
            if self._search not in haystack:
                return False
        return True

    def _filtered_count(self) -> int:
        src = self.sourceModel()
        if not src:
            return 0
        total = src.rowCount()
        n = 0
        for i in range(total):
            if self.filterAcceptsRow(i, QModelIndex()):
                n += 1
        return n

    # ---- paginacion ----

    def page_size(self) -> int:
        return self._page_size

    def set_page_size(self, n: int) -> None:
        n = max(1, int(n))
        if n == self._page_size:
            return
        self._page_size = n
        self._page = 0
        self._refresh()

    def page(self) -> int:
        return self._page

    def set_page(self, p: int) -> None:
        p = max(0, min(int(p), max(0, self.page_count() - 1)))
        if p == self._page:
            return
        self._page = p
        self._refresh()

    def page_count(self) -> int:
        total = self._filtered_count()
        if total <= 0:
            return 1
        return (total + self._page_size - 1) // self._page_size

    # ---- overrides proxy ----

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid() or not self.sourceModel():
            return 0
        total = self._filtered_count()
        start = self._page * self._page_size
        end = min(start + self._page_size, total)
        return max(0, end - start)

    def mapToSource(self, proxy_index: QModelIndex) -> QModelIndex:
        if not proxy_index.isValid():
            return QModelIndex()
        src = self.sourceModel()
        if not src:
            return QModelIndex()
        proxy_row = proxy_index.row()
        target_seen = self._page * self._page_size + proxy_row
        seen = -1
        for i in range(src.rowCount()):
            if self.filterAcceptsRow(i, QModelIndex()):
                seen += 1
                if seen == target_seen:
                    return src.index(i, proxy_index.column())
        return QModelIndex()

    def mapFromSource(self, source_index: QModelIndex) -> QModelIndex:
        if not source_index.isValid():
            return QModelIndex()
        src = self.sourceModel()
        if not src:
            return QModelIndex()
        if not self.filterAcceptsRow(source_index.row(), QModelIndex()):
            return QModelIndex()
        seen = -1
        for i in range(source_index.row() + 1):
            if self.filterAcceptsRow(i, QModelIndex()):
                seen += 1
        if seen < 0:
            return QModelIndex()
        page_start = self._page * self._page_size
        if seen < page_start or seen >= page_start + self._page_size:
            return QModelIndex()
        return self.index(seen - page_start, source_index.column())
