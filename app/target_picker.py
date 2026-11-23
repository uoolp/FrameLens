"""目标进程选择：列出运行中的程序，支持搜索与手动输入。"""
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout)

from theme import C
from winproc import foreground_pid, list_candidates


class TargetPicker(QDialog):
    """返回选中的可执行文件名（如 "game.exe"），取消时 selected 为 None。"""

    def __init__(self, current="", parent=None, title="选择要监控的程序"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(740, 700)
        self.setMinimumSize(680, 620)
        self.selected = None
        self._rows = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        head = QLabel(title)
        head.setStyleSheet(
            f"font-size:16px; font-weight:700; color:{C['accent']};"
            "background:transparent;")
        lay.addWidget(head)

        tip = QLabel(
            "选中后软件会持续采集该程序的帧率、GPU/CPU 占用与温度。\n"
            "游戏需要以「先开监控、再启动游戏」的顺序运行，否则部分反作弊会拒绝采集。")
        tip.setStyleSheet(f"color:{C['text_dim']}; background:transparent;")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索进程名…（例如 apex、wow、cs2）")
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["进程", "PID", "窗口"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setColumnWidth(0, 330)
        self.table.setColumnWidth(1, 90)
        self.table.setColumnWidth(2, 60)
        self.table.cellDoubleClicked.connect(lambda *_: self._accept_table())
        lay.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.manual = QLineEdit()
        self.manual.setPlaceholderText("或直接输入进程名，例如 game.exe")
        self.manual.returnPressed.connect(self._accept_manual)
        row.addWidget(self.manual, 1)
        use_btn = QPushButton("使用该名称")
        use_btn.clicked.connect(self._accept_manual)
        row.addWidget(use_btn)
        lay.addLayout(row)

        btns = QHBoxLayout()
        self.count_label = QLabel("")
        self.count_label.setStyleSheet(
            f"color:{C['text_dim']}; background:transparent;")
        btns.addWidget(self.count_label)
        btns.addStretch(1)
        refresh = QPushButton("刷新列表")
        refresh.clicked.connect(self._reload)
        btns.addWidget(refresh)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        ok = QPushButton("开始监控")
        ok.setObjectName("RecordBtn")
        ok.clicked.connect(self._accept_table)
        btns.addWidget(ok)
        lay.addLayout(btns)

        self._current = (current or "").lower()
        self._reload()

    # ---------- 列表 ----------
    def _reload(self):
        try:
            items = list_candidates()
        except Exception:
            items = []
        fg = 0
        try:
            fg = foreground_pid()
        except Exception:
            pass

        # 同名进程合并成一行的同时保留 PID 信息
        by_name = {}
        for pid, name, has_win in items:
            key = name.lower()
            rec = by_name.setdefault(key, {"name": name, "pids": [],
                                           "win": False, "fg": False})
            rec["pids"].append(pid)
            rec["win"] = rec["win"] or has_win
            rec["fg"] = rec["fg"] or (pid == fg)

        self._rows = sorted(by_name.values(),
                            key=lambda r: (not r["fg"], not r["win"],
                                           not r["name"].lower().startswith(
                                               self._current.split(".")[0]),
                                           r["name"].lower()))
        self.table.setRowCount(len(self._rows))
        default_row = -1
        for i, rec in enumerate(self._rows):
            name = rec["name"]
            if len(rec["pids"]) > 1:
                name = f"{name}  （{len(rec['pids'])} 个实例）"
            it0 = QTableWidgetItem(name)
            it0.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            if rec["fg"]:
                it0.setText(f"▶ {name}　（当前前台）")
            it1 = QTableWidgetItem(str(rec["pids"][0]))
            it1.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            it2 = QTableWidgetItem("●" if rec["win"] else "")
            it2.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            it2.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(i, 0, it0)
            self.table.setItem(i, 1, it1)
            self.table.setItem(i, 2, it2)
            if self._current and rec["name"].lower() == self._current:
                default_row = i
        if default_row >= 0:
            self.table.selectRow(default_row)
            self.table.scrollToItem(self.table.item(default_row, 0))
        self.count_label.setText(f"共 {len(self._rows)} 个进程")
        self._filter(self.search.text())

    def _filter(self, text):
        q = (text or "").strip().lower()
        shown = 0
        for i, rec in enumerate(self._rows):
            hit = (not q) or q in rec["name"].lower()
            self.table.setRowHidden(i, not hit)
            if hit:
                shown += 1
        if q:
            self.count_label.setText(f"匹配 {shown} 个进程")

    # ---------- 选择 ----------
    def _accept_table(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            if len(self._rows) == 1:
                self.selected = self._rows[0]["name"]
                self.accept()
            return
        self.selected = self._rows[row]["name"]
        self.accept()

    def _accept_manual(self):
        name = (self.manual.text() or "").strip()
        if not name:
            return
        if not name.lower().endswith(".exe"):
            name += ".exe"
        self.selected = name
        self.accept()
