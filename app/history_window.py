"""历史录制列表：列出 sessions 目录下所有录制场次及关键指标，双击打开回顾。"""
import os

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout)

from session_index import fmt_duration, fmt_mtime, load_index
from theme import C


class _NumItem(QTableWidgetItem):
    """按数值而不是字符串排序的单元格。"""

    def __init__(self, text, value):
        super().__init__(text)
        self._value = value
        self.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)

    def __lt__(self, other):
        try:
            return self._value < other._value
        except (AttributeError, TypeError):
            return super().__lt__(other)


class _IndexWorker(QThread):
    finished_list = Signal(list)
    progress = Signal(int, int, str)

    def __init__(self, sessions_dir, parent=None):
        super().__init__(parent)
        self._dir = sessions_dir

    def run(self):
        try:
            data = load_index(self._dir, self._on_progress)
        except Exception:
            data = []
        self.finished_list.emit(data)

    def _on_progress(self, i, n, stem):
        try:
            self.progress.emit(i, n, stem)
        except Exception:
            pass


COLS = [
    ("开始时间", 150, lambda e: fmt_mtime(e.get("mtime", 0)),
     lambda e: e.get("mtime", 0)),
    ("时长", 80, lambda e: fmt_duration(e.get("duration", 0)),
     lambda e: e.get("duration", 0)),
    ("帧数", 90, lambda e: f"{e.get('frame_count', 0):,}",
     lambda e: e.get("frame_count", 0)),
    ("平均帧", 85, lambda e: f"{e.get('avg_fps', 0):.1f}",
     lambda e: e.get("avg_fps", 0)),
    ("1% Low", 85, lambda e: f"{e.get('low1', 0):.1f}",
     lambda e: e.get("low1", 0)),
    ("0.1% Low", 90, lambda e: f"{e.get('low01', 0):.1f}",
     lambda e: e.get("low01", 0)),
    ("GPU 占用", 90, lambda e: (f"{e['gpu_util_avg']:.0f}%"
                               if e.get("gpu_util_avg") is not None else "—"),
     lambda e: e.get("gpu_util_avg") or 0),
    ("GPU 温度 均/峰", 120,
     lambda e: (f"{e['gpu_temp_avg']:.0f} / {e['gpu_temp_max']:.0f}℃"
                if e.get("gpu_temp_avg") is not None else "—"),
     lambda e: e.get("gpu_temp_max") or 0),
    ("CPU 温度 均/峰", 120,
     lambda e: (f"{e['cpu_temp_avg']:.0f} / {e['cpu_temp_max']:.0f}℃"
                if e.get("cpu_temp_avg") is not None else "—"),
     lambda e: e.get("cpu_temp_max") or 0),
    ("文件", 110, lambda e: f"{e.get('size', 0) / 1048576:.1f} MB",
     lambda e: e.get("size", 0)),
]


class HistoryWindow(QDialog):
    def __init__(self, sessions_dir, parent=None):
        super().__init__(parent)
        self.setWindowTitle("历史录制")
        self.resize(1180, 620)
        self.sessions_dir = sessions_dir
        self.selected = None
        self.entries = []
        self._worker = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        self.head = QLabel("历史录制")
        self.head.setStyleSheet(
            f"font-size:16px; font-weight:700; color:{C['accent']};"
            "background:transparent;")
        lay.addWidget(self.head)

        self.hint = QLabel("双击任意一行查看该场的曲线与统计")
        self.hint.setStyleSheet(
            f"color:{C['text_dim']}; background:transparent;")
        lay.addWidget(self.hint)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels([c[0] for c in COLS])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(True)
        for i, (_, width, _, _) in enumerate(COLS):
            self.table.setColumnWidth(i, width)
        self.table.doubleClicked.connect(self._accept_row)
        lay.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.count_label = QLabel("正在加载历史…")
        self.count_label.setStyleSheet(
            f"color:{C['text_dim']}; background:transparent;")
        row.addWidget(self.count_label)
        row.addStretch(1)
        open_btn = QPushButton("打开数据目录")
        open_btn.clicked.connect(self._open_dir)
        row.addWidget(open_btn)
        refresh_btn = QPushButton("重新索引")
        refresh_btn.clicked.connect(self._reload)
        row.addWidget(refresh_btn)
        view_btn = QPushButton("查看选中")
        view_btn.clicked.connect(self._accept_row)
        row.addWidget(view_btn)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        row.addWidget(close_btn)
        lay.addLayout(row)

        self._reload()

    # ---------- 数据 ----------
    def _reload(self):
        if self._worker is not None and self._worker.isRunning():
            return
        self.table.setRowCount(0)
        self.count_label.setText("正在加载历史…")
        self._worker = _IndexWorker(self.sessions_dir, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_list.connect(self._on_loaded)
        self._worker.start()

    def _on_progress(self, i, n, stem):
        self.count_label.setText(f"正在解析 {i}/{n} … {stem}")

    def _on_loaded(self, entries):
        self.entries = entries or []
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.entries))
        for r, e in enumerate(self.entries):
            for c, (_, _, text_fn, val_fn) in enumerate(COLS):
                try:
                    text = text_fn(e)
                    val = val_fn(e)
                except Exception:
                    text, val = "—", 0
                item = _NumItem(text, val)
                if c == 0:
                    item.setData(Qt.UserRole, r)
                self.table.setItem(r, c, item)
        self.table.setSortingEnabled(True)
        self.table.sortItems(0, Qt.DescendingOrder)
        self.count_label.setText(
            f"共 {len(self.entries)} 场录制　·　数据目录：{self.sessions_dir}")

    # ---------- 动作 ----------
    def _accept_row(self, *_):
        row = self.table.currentRow()
        if row < 0:
            return
        key_item = self.table.item(row, 0)
        if key_item is None:
            return
        idx = key_item.data(Qt.UserRole)
        if isinstance(idx, int) and 0 <= idx < len(self.entries):
            self.selected = self.entries[idx]
            self.accept()

    def _open_dir(self):
        if os.path.isdir(self.sessions_dir):
            os.startfile(self.sessions_dir)

    def closeEvent(self, event):
        if self._worker is not None and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait(1000)
        super().closeEvent(event)
