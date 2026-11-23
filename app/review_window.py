"""录制回顾窗口：查看任意历史场次的温度、帧率、帧时间与占用，支持曲线悬停读数。"""
import csv
import math
import os

import pyqtgraph as pg
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QTabWidget,
    QVBoxLayout, QWidget)

from chart_hover import Crosshair
from session_index import fmt_duration, fmt_mtime, load_index
from theme import C

pg.setConfigOptions(antialias=True)


def _num(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _cols(header):
    names = [c.strip().lower().replace("\ufeff", "") for c in header.split(",")]
    return {n: i for i, n in enumerate(names)}


# CSV 里的 fps_target_cpu 实际存的是游戏进程 CPU 占用
_ALIAS = {"fps_target_cpu": "target_cpu"}
_KEEP = ("gpu_util", "gpu_temp", "gpu_hotspot", "gpu_vram_temp", "cpu_temp",
         "cpu_total", "cpu_core_max", "mem_percent", "vram_ratio",
         "gpu_vram_used", "target_cpu")


# ---------- 数据加载 ----------
def load_system_csv(path):
    """系统样本 1 Hz CSV → [{wall, key: value}]。"""
    if not path or not os.path.exists(path):
        return []
    rows = []
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            header = fh.readline()
            idx = _cols(header)
            for line in fh:
                parts = line.split(",")
                w = None
                rec = {}
                for name, i in idx.items():
                    if i >= len(parts):
                        continue
                    v = _num(parts[i])
                    key = _ALIAS.get(name, name)
                    if key == "wall":
                        w = v
                    elif key in _KEEP:
                        rec[key] = v
                if w is None:
                    continue
                rec["wall"] = w
                rows.append(rec)
    except OSError:
        return []
    return rows


def load_frame_buckets(path):
    """帧 CSV 按秒聚合，返回逐秒序列与全局统计。"""
    if not path or not os.path.exists(path):
        return None
    buckets = {}
    all_ms = []
    w0 = None
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            header = fh.readline()
            idx = _cols(header)
            wi = idx.get("wall")
            fi = idx.get("frame_ms")
            gi = idx.get("gpu_busy_ms")
            if wi is None or fi is None:
                return None
            for line in fh:
                parts = line.split(",")
                need = max(wi, fi)
                if len(parts) <= need:
                    continue
                try:
                    w = float(parts[wi])
                    m = float(parts[fi])
                except ValueError:
                    continue
                if m <= 0:
                    continue
                if w0 is None:
                    w0 = w
                all_ms.append(m)
                sec = int(w - w0)
                b = buckets.setdefault(sec, {"n": 0, "sum": 0.0, "max": 0.0,
                                             "ms": [], "gpu": []})
                b["n"] += 1
                b["sum"] += m
                if m > b["max"]:
                    b["max"] = m
                b["ms"].append(m)
                if gi is not None and len(parts) > gi:
                    g = _num(parts[gi])
                    if g is not None:
                        b["gpu"].append(g)
    except OSError:
        return None
    if not all_ms:
        return None

    secs, avg, low, p50, p99, gpu = [], [], [], [], [], []
    for sec in sorted(buckets):
        b = buckets[sec]
        if b["n"] < 1:
            continue
        secs.append(sec)
        avg.append(1000.0 * b["n"] / b["sum"])
        low.append(1000.0 / b["max"])
        srt = sorted(b["ms"])
        p50.append(srt[len(srt) // 2])
        p99.append(srt[min(len(srt) - 1, int(len(srt) * 0.99))])
        gpu.append(sum(b["gpu"]) / len(b["gpu"]) if b["gpu"] else None)

    srt_all = sorted(all_ms)
    n = len(srt_all)
    stats = {
        "frame_count": n,
        "duration": (max(buckets) + 1) if buckets else 0,
        "avg_fps": 1000.0 * n / sum(all_ms),
        "p50": srt_all[n // 2],
        "p99": srt_all[min(n - 1, int(n * 0.99))],
        "p999": srt_all[min(n - 1, int(n * 0.999))],
    }
    stats["low1"] = 1000.0 / stats["p99"] if stats["p99"] > 0 else 0.0
    stats["low01"] = 1000.0 / stats["p999"] if stats["p999"] > 0 else 0.0
    return {"secs": secs, "avg": avg, "low": low, "p50": p50, "p99": p99,
            "gpu": gpu, "stats": stats, "origin": w0}


def load_session(frames_path, system_path):
    """一场完整数据：系统样本 + 帧聚合 + 统计。"""
    samples = load_system_csv(system_path)
    fb = load_frame_buckets(frames_path)
    origin = fb["origin"] if fb else (
        samples[0]["wall"] if samples else 0.0)

    stats = dict(fb["stats"]) if fb else {}
    temps = [s["gpu_temp"] for s in samples if s.get("gpu_temp") is not None]
    ctemps = [s["cpu_temp"] for s in samples if s.get("cpu_temp") is not None]
    if temps:
        stats["avg_gpu_temp"] = sum(temps) / len(temps)
        stats["max_gpu_temp"] = max(temps)
    if ctemps:
        stats["avg_cpu_temp"] = sum(ctemps) / len(ctemps)
        stats["max_cpu_temp"] = max(ctemps)
    if samples:
        gu = [s["gpu_util"] for s in samples if s.get("gpu_util") is not None]
        if gu:
            stats["avg_gpu_util"] = sum(gu) / len(gu)
        ct = [s["cpu_total"] for s in samples if s.get("cpu_total") is not None]
        if ct:
            stats["avg_cpu_total"] = sum(ct) / len(ct)
        stats["duration"] = max(stats.get("duration") or 0,
                                samples[-1]["wall"] - origin)
    return {"samples": samples, "frames": fb, "origin": origin, "stats": stats}


class _Loader(QThread):
    loaded = Signal(dict)

    def __init__(self, frames_path, system_path, parent=None):
        super().__init__(parent)
        self._f = frames_path
        self._s = system_path

    def run(self):
        try:
            data = load_session(self._f, self._s)
        except Exception:
            data = {"samples": [], "frames": None, "origin": 0.0, "stats": {}}
        self.loaded.emit(data)


# ---------- 绘图辅助 ----------
class _MinuteAxis(pg.AxisItem):
    """X 轴用「分:秒」，长录制更好读。"""

    def tickStrings(self, values, scale, spacing):
        out = []
        for v in values:
            if v < 0:
                out.append("")
                continue
            m = int(v // 60)
            s = int(round(v)) % 60
            out.append(f"{m}:{s:02d}" if m else f"{s}s")
        return out


def _style_plot(plot, y_label=""):
    plot.setBackground(None)
    plot.setMenuEnabled(False)
    plot.setMouseEnabled(x=True, y=False)
    plot.hideButtons()
    for side in ("left", "right", "bottom"):
        ax = plot.getAxis(side)
        ax.setPen(C["border"])
        ax.setTextPen(C["text_dim"])
        ax.setStyle(tickLength=0)
    plot.showAxis("right", False)
    plot.setLabel("left", y_label, color=C["text_dim"], size=9)
    plot.setLabel("bottom", "", color=C["text_dim"], size=9)


def _series(samples, origin, key):
    xs, ys = [], []
    for s in samples:
        v = s.get(key)
        if v is None:
            continue
        xs.append(s["wall"] - origin)
        ys.append(float(v))
    return xs, ys


def _legend_row(items):
    row = QHBoxLayout()
    row.setSpacing(16)
    row.setContentsMargins(6, 0, 6, 0)
    for name, color in items:
        wrap = QWidget()
        w = QHBoxLayout(wrap)
        w.setContentsMargins(0, 0, 0, 0)
        w.setSpacing(5)
        dot = QLabel()
        dot.setFixedSize(9, 9)
        dot.setStyleSheet(f"background:{color}; border-radius:4px;")
        txt = QLabel(name)
        txt.setStyleSheet(
            f"color:{C['text_dim']}; font-size:11px; background:transparent;")
        w.addWidget(dot)
        w.addWidget(txt)
        row.addWidget(wrap)
    row.addStretch(1)
    return row


class ReviewWindow(QDialog):
    def __init__(self, summary=None, samples=None, frame_path=None,
                 sys_path=None, origin_wall=None, sessions_dir=None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("录制回顾")
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint)
        self.resize(1120, 820)
        self.sessions_dir = sessions_dir
        self.entries = []
        self.data = None
        # 必须持有引用：pyqtgraph 用 weakref 保存 sigMouseMoved 的槽，
        # 实例一旦被回收，悬停读数就不再响应。
        self._crosshairs = []
        self.summary = summary or {}
        self._qc = samples or []
        self._frame_path = frame_path
        self._sys_path = sys_path
        self._origin = origin_wall
        self._loader = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        top = QHBoxLayout()
        self.session_box = QComboBox()
        self.session_box.setMinimumWidth(330)
        if sessions_dir:
            try:
                self.entries = load_index(sessions_dir)
            except Exception:
                self.entries = []
            for e in self.entries:
                self.session_box.addItem(
                    f"{fmt_mtime(e.get('mtime', 0))}　·　"
                    f"{fmt_duration(e.get('duration', 0))}　·　"
                    f"{e.get('avg_fps', 0):.0f} FPS")
            if frame_path:
                for i, e in enumerate(self.entries):
                    if os.path.abspath(e.get("frames", "")) == \
                            os.path.abspath(frame_path):
                        self.session_box.setCurrentIndex(i)
                        break
            self.session_box.currentIndexChanged.connect(self._on_pick)
        else:
            self.session_box.addItem("当前录制")
            self.session_box.setEnabled(False)
        top.addWidget(QLabel("场次"))
        top.addWidget(self.session_box, 1)
        lay.addLayout(top)

        self.head = QLabel("加载中…")
        self.head.setStyleSheet(
            f"font-size:15px; font-weight:700; color:{C['accent']};"
            "background:transparent;")
        lay.addWidget(self.head)

        self.kpi = QLabel("")
        self.kpi.setWordWrap(True)
        self.kpi.setStyleSheet(
            f"color:{C['text']}; background:{C['card_alt']};"
            f"border:1px solid {C['border']}; border-radius:8px;"
            "padding:10px 14px;")
        lay.addWidget(self.kpi)

        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        shot_btn = QPushButton("保存截图")
        shot_btn.clicked.connect(self._save_shot)
        row.addWidget(shot_btn)
        open_btn = QPushButton("打开数据目录")
        open_btn.clicked.connect(self._open_dir)
        row.addWidget(open_btn)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        lay.addLayout(row)

        # 若未指定路径，默认用最新一场
        if not frame_path and not sys_path and self.entries:
            first = self.entries[0]
            frame_path, sys_path = first.get("frames"), first.get("system")
        self._start_load(frame_path, sys_path)

    # ---------- 加载 ----------
    def _on_pick(self, index):
        if index < 0 or index >= len(self.entries):
            return
        e = self.entries[index]
        # 换场次后不能再沿用上一场的内存样本与摘要
        self._qc = []
        self.summary = {}
        self._start_load(e.get("frames"), e.get("system"))

    def _start_load(self, frames_path, sys_path):
        if self._loader is not None and self._loader.isRunning():
            return
        self._frame_path, self._sys_path = frames_path, sys_path
        self.head.setText("正在解析录制数据…")
        self.tabs.clear()
        self._crosshairs.clear()
        self._loader = _Loader(frames_path, sys_path, self)
        self._loader.loaded.connect(self._on_loaded)
        self._loader.start()

    def _on_loaded(self, data):
        self.data = data
        samples = data["samples"]
        fb = data["frames"]
        # 内存样本优先（刚录完时更完整）；切换历史场次时会清空
        if self._qc:
            samples = self._qc
        self._samples = samples
        self._fb = fb
        self._origin = data["origin"] or self._origin or 0.0
        self._stats = self.summary if self.summary else data["stats"]

        dur = self._stats.get("duration") or (
            samples[-1]["wall"] - self._origin if len(samples) >= 2 else 0)
        self.head.setText(
            f"录制回顾　·　时长 {fmt_duration(dur)}　·　"
            f"帧 {self._stats.get('frame_count', 0):,} 个　·　"
            f"系统样本 {len(samples)} 个")
        self.kpi.setText(self._kpi_text())

        self.tabs.addTab(self._temp_tab(), "温度")
        self.tabs.addTab(self._fps_tab(), "帧率")
        self.tabs.addTab(self._frametime_tab(), "帧时间")
        self.tabs.addTab(self._util_tab(), "占用")

    # ---------- 摘要 ----------
    def _kpi_text(self):
        s = self._stats or {}
        bits = []
        if s.get("avg_fps"):
            bits.append(f"平均 <b>{s['avg_fps']:.1f}</b> FPS")
        if s.get("low1"):
            bits.append(f"1% Low <b>{s['low1']:.1f}</b>")
        if s.get("low01"):
            bits.append(f"0.1% Low <b>{s['low01']:.1f}</b>")
        if s.get("p99"):
            bits.append(f"P99 帧时间 <b>{s['p99']:.2f}</b> ms")
        if s.get("avg_gpu_util") is not None:
            bits.append(f"GPU <b>{s['avg_gpu_util']:.0f}%</b>")
        if s.get("avg_cpu_total") is not None:
            bits.append(f"CPU <b>{s['avg_cpu_total']:.0f}%</b>")
        if s.get("avg_gpu_temp") is not None:
            bits.append(f"GPU 温 <b>{s['avg_gpu_temp']:.0f}</b>/"
                        f"{s['max_gpu_temp']:.0f}℃")
        if s.get("avg_cpu_temp") is not None:
            bits.append(f"CPU 温 <b>{s['avg_cpu_temp']:.0f}</b>/"
                        f"{s['max_cpu_temp']:.0f}℃")
        return "　·　".join(bits) if bits else "无统计数据"

    # ---------- 各页 ----------
    def _temp_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.addLayout(_legend_row([
            ("GPU 边缘", C["temp"]), ("GPU 热点", C["warn"]),
            ("GPU 显存", C["gpu"]), ("CPU", C["cpu_temp"])]))

        plot = pg.PlotWidget(axisItems={"bottom": _MinuteAxis("bottom")})
        _style_plot(plot, "温度 ℃")
        items, values = [], []
        for key, name, color, width in (
                ("gpu_temp", "GPU 边缘", C["temp"], 2.4),
                ("gpu_hotspot", "GPU 热点", C["warn"], 2.0),
                ("gpu_vram_temp", "GPU 显存", C["gpu"], 2.0),
                ("cpu_temp", "CPU", C["cpu_temp"], 2.4)):
            xs, ys = _series(self._samples, self._origin, key)
            if len(ys) < 2:
                continue
            it = plot.plot(xs, ys, pen=pg.mkPen(color, width=width),
                           connect="finite")
            items.append({"item": it, "name": name, "color": color, "fmt": ".1f"})
            values.extend(ys)

        for level, color in ((85, C["warn"]), (95, C["danger"])):
            plot.addItem(pg.InfiniteLine(
                pos=level, angle=0,
                pen=pg.mkPen(color, width=1, style=Qt.DashLine),
                label=f"{level}°", labelOpts={"position": 0.97,
                                              "color": color, "movable": False}))

        if items and values:
            lo = max(0, math.floor(min(values) - 8))
            hi = math.ceil(max(values) + 8)
            plot.setYRange(lo, max(hi, lo + 12))
            plot.setXRange(0, self._x_span(), padding=0)
            self._crosshairs.append(
                Crosshair(plot, items, x_format="{:.0f} s"))
        else:
            plot.addItem(pg.TextItem(
                "本次录制没有温度数据\n（需 AMD 显卡驱动 ADL 可用）",
                color=C["text_dim"], anchor=(0.5, 0.5)))
        lay.addWidget(plot)
        return page

    def _fps_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.addLayout(_legend_row([
            ("每秒平均帧率", C["fps"]), ("该秒最差帧", C["frametime"]),
            ("1% Low 参考线", C["warn"])]))

        plot = pg.PlotWidget(axisItems={"bottom": _MinuteAxis("bottom")})
        _style_plot(plot, "FPS")
        fb = self._fb
        if fb and fb["secs"]:
            it_avg = plot.plot(fb["secs"], fb["avg"],
                               pen=pg.mkPen(C["fps"], width=2.2))
            it_low = plot.plot(fb["secs"], fb["low"],
                               pen=pg.mkPen(C["frametime"], width=1.5))
            low1 = (self._stats or {}).get("low1")
            if low1:
                plot.addItem(pg.InfiniteLine(
                    pos=low1, angle=0,
                    pen=pg.mkPen(C["warn"], width=1, style=Qt.DashLine),
                    label=f"1% Low {low1:.0f}", labelOpts={
                        "position": 0.03, "color": C["warn"], "movable": False}))
            top = max(60, math.ceil(max(fb["avg"]) / 30) * 30 + 15)
            plot.setYRange(0, top)
            plot.setXRange(0, self._x_span(), padding=0)
            self._crosshairs.append(Crosshair(plot, [
                {"item": it_avg, "name": "平均帧", "color": C["fps"], "fmt": ".1f"},
                {"item": it_low, "name": "最差帧", "color": C["frametime"],
                 "fmt": ".1f"},
            ], x_format="{:.0f} s"))
        else:
            plot.addItem(pg.TextItem("本次没有可用的帧数据",
                                     color=C["text_dim"], anchor=(0.5, 0.5)))
        lay.addWidget(plot, 1)
        return page

    def _frametime_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.addLayout(_legend_row([
            ("每秒 P50 帧时间", C["frametime"]),
            ("每秒 P99 帧时间", C["fps"])]))

        plot = pg.PlotWidget(axisItems={"bottom": _MinuteAxis("bottom")})
        _style_plot(plot, "帧时间 ms")
        fb = self._fb
        if fb and fb["secs"]:
            it50 = plot.plot(fb["secs"], fb["p50"],
                             pen=pg.mkPen(C["frametime"], width=2.0))
            it99 = plot.plot(fb["secs"], fb["p99"],
                             pen=pg.mkPen(C["fps"], width=1.8))
            # 上限压到 30ms 以内，开局的加载尖峰会被截断但主体细节展开
            top = max(12.0, min(30.0, max(fb["p99"]) * 1.2))
            plot.setYRange(0, top)
            plot.setXRange(0, self._x_span(), padding=0)
            # 160Hz 每帧预算参考线
            plot.addItem(pg.InfiniteLine(
                pos=6.25, angle=0,
                pen=pg.mkPen(C["text_dim"], width=1, style=Qt.DotLine),
                label="160Hz 6.25ms",
                labelOpts={"position": 0.02, "color": C["text_dim"],
                           "movable": False}))
            self._crosshairs.append(Crosshair(plot, [
                {"item": it50, "name": "P50", "color": C["frametime"], "fmt": ".2f"},
                {"item": it99, "name": "P99", "color": C["fps"], "fmt": ".2f"},
            ], x_format="{:.0f} s"))
        else:
            plot.addItem(pg.TextItem("本次没有可用的帧数据",
                                     color=C["text_dim"], anchor=(0.5, 0.5)))
        lay.addWidget(plot, 1)
        return page

    def _util_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.addLayout(_legend_row([
            ("GPU 占用", C["gpu"]), ("CPU 占用", C["cpu"]),
            ("内存占用", C["mem"]), ("游戏进程（整机）", C["fps"])]))

        plot = pg.PlotWidget(axisItems={"bottom": _MinuteAxis("bottom")})
        _style_plot(plot, "占用 %")
        items = []
        for key, name, color, width, fmt in (
                ("gpu_util", "GPU 占用", C["gpu"], 2.2, ".0f"),
                ("cpu_total", "CPU 占用", C["cpu"], 2.2, ".0f"),
                ("mem_percent", "内存占用", C["mem"], 1.8, ".1f")):
            xs, ys = _series(self._samples, self._origin, key)
            if len(ys) >= 2:
                it = plot.plot(xs, ys, pen=pg.mkPen(color, width=width),
                               connect="finite")
                items.append({"item": it, "name": name, "color": color,
                              "fmt": fmt})
        # 目标进程 CPU 是跨核合计（可超过 100%），折算成整机占比才能和上面的
        # GPU/CPU/内存 同轴显示，否则会直接飞出 0-100 的可视区。
        xs, ys = _series(self._samples, self._origin, "target_cpu")
        if len(ys) >= 2:
            ncpu = os.cpu_count() or 1
            ys = [v / ncpu for v in ys]
            it = plot.plot(xs, ys, pen=pg.mkPen(C["fps"], width=1.6),
                           connect="finite")
            items.append({"item": it, "name": "游戏进程（整机占比）",
                          "color": C["fps"], "fmt": ".1f"})
        plot.setYRange(0, 100)
        plot.setXRange(0, self._x_span(), padding=0)
        if items:
            self._crosshairs.append(
                Crosshair(plot, items, x_format="{:.0f} s"))
        lay.addWidget(plot, 1)
        return page

    # ---------- 工具 ----------
    def _x_span(self):
        fb = self._fb if hasattr(self, "_fb") else None
        if fb and fb["secs"]:
            return max(1.0, fb["secs"][-1] + 1)
        if len(self._samples) >= 2:
            return max(1.0, self._samples[-1]["wall"] - self._origin)
        return 1.0

    def _open_dir(self):
        folder = os.path.dirname(self._frame_path or self._sys_path or "")
        if folder and os.path.isdir(folder):
            os.startfile(folder)

    def _save_shot(self):
        if not self._frame_path:
            return
        base = os.path.splitext(os.path.basename(self._frame_path))[0]
        out = os.path.join(os.path.dirname(self._frame_path), f"{base}_回顾.png")
        try:
            self.grab().save(out)
            self.head.setText(f"截图已保存：{out}")
        except Exception as exc:
            self.head.setText(f"截图保存失败：{exc}")

    def closeEvent(self, event):
        if self._loader is not None and self._loader.isRunning():
            self._loader.wait(1500)
        super().closeEvent(event)
