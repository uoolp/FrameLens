"""主窗口：KPI 卡片、瓶颈横幅、实时曲线仪表盘。"""
import math
import os
import statistics
import time

import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget, QDialog, QMessageBox)

from chart_hover import Crosshair as _Crosshair
from data_store import foreground_frames
from review_window import ReviewWindow
from theme import BOTTLENECK_META, C, hex_to_rgba

pg.setConfigOptions(antialias=True)


def style_plot(plot, y_max=None, y_label="", x_span=120):
    plot.setBackground(None)
    plot.setMenuEnabled(False)
    plot.setMouseEnabled(x=False, y=False)
    plot.hideButtons()
    plot.showGrid(x=True, y=True, alpha=0.10)
    plot.setDownsampling(auto=True, mode="peak")
    plot.setClipToView(True)

    bottom = plot.getAxis("bottom")
    bottom.setStyle(showValues=True, tickLength=0, tickTextOffset=4)
    bottom.setPen(C["border"])
    bottom.setTextPen(C["text_dim"])
    bottom.setHeight(20)

    for side in ("left", "right"):
        ax = plot.getAxis(side)
        ax.setPen(C["border"])
        ax.setTextPen(C["text_dim"])
        ax.setStyle(tickLength=0)
    plot.showAxis("right", False)
    plot.getAxis("left").setWidth(44)
    plot.setLabel("left", y_label, color=C["text_dim"], size=9)
    if y_max is not None:
        plot.setYRange(0, y_max)
    plot.setXRange(-x_span, 0, padding=0)


def Crosshair(plot, specs):
    """主界面十字线：X 轴显示相对当前时刻的秒数（-120.0 ~ 0.0）。"""
    return _Crosshair(plot, specs, x_format="{:+.1f} s")


class ChartCard(QFrame):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(6)
        head = QHBoxLayout()
        t = QLabel(title)
        t.setObjectName("CardTitle")
        head.addWidget(t)
        head.addStretch()
        self.value_label = QLabel("")
        self.value_label.setObjectName("CardTitle")
        head.addWidget(self.value_label)
        lay.addLayout(head)

        self._legend_wrap = QWidget()
        self._legend_lay = QHBoxLayout(self._legend_wrap)
        self._legend_lay.setContentsMargins(0, 0, 0, 0)
        self._legend_lay.setSpacing(14)
        self._legend_wrap.hide()
        lay.addWidget(self._legend_wrap)

        self.plot = pg.PlotWidget()
        self.plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay.addWidget(self.plot)
        self._placeholder = None

    def set_head_value(self, text):
        self.value_label.setText(text)

    def set_legend(self, items):
        while self._legend_lay.count():
            it = self._legend_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        for name, color in items:
            wrap = QWidget()
            h = QHBoxLayout(wrap)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(5)
            dot = QLabel()
            dot.setFixedSize(8, 8)
            dot.setStyleSheet(f"background:{color}; border-radius:4px;")
            txt = QLabel(name)
            txt.setStyleSheet(
                f"color:{C['text_dim']}; font-size:11px; background:transparent;")
            h.addWidget(dot)
            h.addWidget(txt)
            self._legend_lay.addWidget(wrap)
        self._legend_lay.addStretch(1)
        self._legend_wrap.show()

    def set_placeholder(self, text):
        self._placeholder = pg.TextItem(text, color=C["text_dim"],
                                        anchor=(0.5, 0.5))
        self.plot.addItem(self._placeholder)

    def clear_placeholder(self):
        if self._placeholder is not None:
            self._placeholder.hide()


class KpiCard(QFrame):
    def __init__(self, title, color, unit, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(4)
        t = QLabel(title)
        t.setObjectName("CardTitle")
        lay.addWidget(t)

        row = QHBoxLayout()
        self.value = QLabel("—")
        self.value.setObjectName("KpiValue")
        self.value.setStyleSheet(f"color: {color};")
        row.addWidget(self.value, alignment=Qt.AlignBottom)
        u = QLabel(unit)
        u.setObjectName("KpiUnit")
        row.addWidget(u, alignment=Qt.AlignBottom)
        row.addStretch()
        lay.addLayout(row)

        self.spark = pg.PlotWidget()
        self.spark.setFixedHeight(38)
        self.spark.setBackground(None)
        self.spark.setMenuEnabled(False)
        self.spark.setMouseEnabled(False, False)
        self.spark.hideButtons()
        self.spark.hideAxis("left")
        self.spark.hideAxis("bottom")
        self.spark_curve = self.spark.plot(
            pen=pg.mkPen(color, width=1.6),
            fillLevel=0, brush=pg.mkBrush(*hex_to_rgba(color, 34)))
        self.spark.setMouseEnabled(False, False)
        lay.addWidget(self.spark)

    def set_value(self, text):
        self.value.setText(text)

    def set_spark(self, y):
        self.spark_curve.setData(list(range(len(y))), y)


class MainWindow(QWidget):
    def __init__(self, store, sessions_dir, target_name, collector=None,
                 on_change_target=None, controller=None):
        super().__init__()
        self.store = store
        self.sessions_dir = sessions_dir
        self.target_name = target_name
        # 新装配用 controller=（CollectorController），旧调用仍可传 collector=
        self.collector = controller if controller is not None else collector
        self.on_change_target = on_change_target
        self._app_start = time.time()
        self.setWindowTitle("性能监控台")
        # 按屏幕可用区域自适应，避免小屏或高缩放下窗口超出屏幕
        avail = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1360, max(1180, avail.width() - 60)),
                    min(980, max(820, avail.height() - 60)))
        self.setMinimumSize(1180, 800)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(14)

        root.addWidget(self._build_topbar())
        root.addLayout(self._build_kpis())
        root.addWidget(self._build_bottleneck())
        root.addLayout(self._build_charts(), stretch=1)
        self._apply_title()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(250)

    # ---------- 目标名 ----------
    def _apply_title(self):
        shown = os.path.splitext(self.target_name or "")[0] or "未选择目标"
        self.setWindowTitle(f"{shown} 性能监控台")
        if hasattr(self, "title_label"):
            self.title_label.setText(f"{shown} 性能监控台")

    def set_target_name(self, name):
        """切换目标后更新标题与状态文案。"""
        self.target_name = name or ""
        self._apply_title()
        try:
            self._update_status()
        except Exception:
            pass

    def _pick_target(self):
        if not callable(self.on_change_target):
            return
        new_target = self.on_change_target()
        if new_target:
            self.set_target_name(new_target)

    # ---------- 构建 ----------
    def _build_topbar(self):
        bar = QFrame()
        bar.setObjectName("TopBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 12, 20, 12)
        title = QLabel("性能监控台")
        title.setObjectName("AppTitle")
        self.title_label = title
        lay.addWidget(title)
        self.target_status = QLabel("● 等待目标进程")
        self.target_status.setStyleSheet(f"color: {C['idle']}; background: transparent;")
        lay.addWidget(self.target_status)
        lay.addStretch()

        self.record_timer = QLabel("00:00")
        self.record_timer.setStyleSheet("background: transparent; font-weight: 700;")
        lay.addWidget(self.record_timer)
        self.record_btn = QPushButton("开始录制")
        self.record_btn.setObjectName("RecordBtn")
        self.record_btn.setProperty("recording", False)
        self.record_btn.clicked.connect(self._toggle_record)
        lay.addWidget(self.record_btn)
        self.export_btn = QPushButton("导出当前数据")
        self.export_btn.setObjectName("ExportBtn")
        self.export_btn.clicked.connect(self._export_all)
        lay.addWidget(self.export_btn)

        self.history_btn = QPushButton("历史记录")
        self.history_btn.setObjectName("ExportBtn")
        self.history_btn.clicked.connect(self._open_history)
        lay.addWidget(self.history_btn)

        self.change_btn = QPushButton("更换目标")
        self.change_btn.setObjectName("ExportBtn")
        self.change_btn.clicked.connect(self._pick_target)
        lay.addWidget(self.change_btn)
        return bar

    def _build_kpis(self):
        self.kpi_fps = KpiCard("当前帧率", C["fps"], "FPS")
        self.kpi_ft = KpiCard("帧时间", C["frametime"], "ms")
        self.kpi_gpu = KpiCard("GPU 占用", C["gpu"], "%")
        self.kpi_temp = KpiCard("GPU 温度", C["temp"], "°C")
        self.kpi_cpu = KpiCard("CPU 占用", C["cpu"], "%")
        self.kpi_cputemp = KpiCard("CPU 温度", C["cpu_temp"], "°C")
        grid = QGridLayout()
        grid.setSpacing(12)
        cards = (self.kpi_fps, self.kpi_ft, self.kpi_gpu,
                 self.kpi_temp, self.kpi_cpu, self.kpi_cputemp)
        for i, card in enumerate(cards):
            grid.addWidget(card, 0, i)
        return grid

    def _build_bottleneck(self):
        bar = QFrame()
        bar.setObjectName("Card")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 14, 20, 14)
        lay.setSpacing(24)

        left = QVBoxLayout()
        left.setSpacing(2)
        self.bn_label = QLabel("等待游戏")
        self.bn_label.setObjectName("BottleneckLabel")
        left.addWidget(self.bn_label)
        self.bn_reason = QLabel("启动 Apex Legends 后开始分析")
        self.bn_reason.setObjectName("BottleneckReason")
        left.addWidget(self.bn_reason)
        lay.addLayout(left)
        lay.addStretch()

        self.share_bars = {}
        right = QGridLayout()
        right.setHorizontalSpacing(16)
        right.setVerticalSpacing(6)
        for i, key in enumerate(("GPU", "CPU", "VRAM", "CAPPED", "BALANCED")):
            name, color = BOTTLENECK_META[key]
            lab = QLabel(name)
            lab.setStyleSheet(f"color:{color}; background:transparent; font-size:12px;")
            bar_w = QProgressBar()
            bar_w.setFixedWidth(130)
            bar_w.setTextVisible(False)
            bar_w.setStyleSheet(
                f"QProgressBar{{background:{C['card_alt']}; border:none; border-radius:4px; height:8px;}}"
                f"QProgressBar::chunk{{background:{color}; border-radius:4px;}}")
            bar_w.setValue(0)
            self.share_bars[key] = bar_w
            right.addWidget(lab, i // 3, (i % 3) * 2)
            right.addWidget(bar_w, i // 3, (i % 3) * 2 + 1)
        lay.addLayout(right)
        return bar

    def _build_charts(self):
        self.card_fps = ChartCard("帧率曲线 FPS")
        self.card_ft = ChartCard("帧时间曲线")
        self.card_gpu = ChartCard("GPU 利用率 / GPU Busy")
        self.card_vram = ChartCard("显存占用（独显）")
        self.card_cpu = ChartCard("CPU 总占用 / 每核")
        self.card_mem = ChartCard("内存占用")
        self.card_temp = ChartCard("温度曲线 GPU / CPU")

        # 帧率：面积填充 + 均值虚线
        self.c_fps = self.card_fps.plot.plot(
            pen=pg.mkPen(C["fps"], width=2.2),
            fillLevel=0, brush=pg.mkBrush(*hex_to_rgba(C["fps"], 26)))
        self.c_fps_avg = self.card_fps.plot.plot(
            pen=pg.mkPen(C["warn"], width=1.4, style=Qt.DashLine))
        style_plot(self.card_fps.plot, y_label="FPS", x_span=6)
        self.card_fps.plot.setYRange(0, 200)
        self.card_fps.set_legend([("瞬时帧率", C["fps"]), ("均值", C["warn"])])

        # 帧时间
        self.c_ft = self.card_ft.plot.plot(
            pen=pg.mkPen(C["frametime"], width=2.2),
            fillLevel=0, brush=pg.mkBrush(*hex_to_rgba(C["frametime"], 24)))
        style_plot(self.card_ft.plot, y_label="ms", x_span=6)
        self.card_ft.plot.setYRange(0, 25)
        self.card_ft.set_legend([("帧时间", C["frametime"]),
                                 ("16.7ms = 60FPS", C["text_dim"])])

        # GPU 利用率 + Busy
        self.c_gpu = self.card_gpu.plot.plot(
            pen=pg.mkPen(C["gpu"], width=2.2),
            fillLevel=0, brush=pg.mkBrush(*hex_to_rgba(C["gpu"], 24)))
        self.c_busy = self.card_gpu.plot.plot(
            pen=pg.mkPen(C["accent"], width=1.6, style=Qt.DashLine))
        style_plot(self.card_gpu.plot, y_max=100, y_label="%")
        self.card_gpu.set_legend([("GPU 利用率", C["gpu"]), ("GPU Busy", C["accent"])])

        # 显存
        self.c_vram = self.card_vram.plot.plot(
            pen=pg.mkPen(C["vram"], width=2.2),
            fillLevel=0, brush=pg.mkBrush(*hex_to_rgba(C["vram"], 20)))
        style_plot(self.card_vram.plot, y_label="MB")
        self.card_vram.set_legend([("显存占用", C["vram"]), ("显存上限", C["danger"])])

        # CPU 总占用 + 每核淡线
        self.c_cpu = self.card_cpu.plot.plot(
            pen=pg.mkPen(C["cpu"], width=2.4),
            fillLevel=0, brush=pg.mkBrush(*hex_to_rgba(C["cpu"], 24)))
        self.c_core_curves = [
            self.card_cpu.plot.plot(pen=pg.mkPen(C["cpu"], width=1, alpha=55))
            for _ in range(16)]
        style_plot(self.card_cpu.plot, y_max=100, y_label="%")
        self.card_cpu.set_legend([("CPU 总占用", C["cpu"]), ("各逻辑核心", C["text_dim"])])

        # 内存
        self.c_mem = self.card_mem.plot.plot(
            pen=pg.mkPen(C["mem"], width=2.2),
            fillLevel=0, brush=pg.mkBrush(*hex_to_rgba(C["mem"], 24)))
        style_plot(self.card_mem.plot, y_max=100, y_label="%")
        self.card_mem.set_legend([("内存占用", C["mem"])])

        # 温度：GPU / CPU 双线 + 警戒线 + 本次峰值线
        self.c_temp = self.card_temp.plot.plot(
            pen=pg.mkPen(C["temp"], width=2.4))
        self.c_cputemp = self.card_temp.plot.plot(
            pen=pg.mkPen(C["cpu_temp"], width=2.4))
        style_plot(self.card_temp.plot, y_label="°C")
        self.card_temp.plot.setYRange(30, 92)
        self.temp_warn = self.card_temp.plot.addLine(
            y=85, pen=pg.mkPen(C["warn"], width=1, style=Qt.DashLine))
        self.temp_danger = self.card_temp.plot.addLine(
            y=95, pen=pg.mkPen(C["danger"], width=1, style=Qt.DashLine))
        self.temp_peak = self.card_temp.plot.addLine(
            y=30, pen=pg.mkPen(C["text_dim"], width=1, style=Qt.DotLine))
        self.card_temp.set_legend([("GPU 温度", C["temp"]),
                                   ("CPU 温度", C["cpu_temp"]),
                                   ("85℃ 警戒", C["warn"]),
                                   ("峰值", C["text_dim"])])

        self.c_vram_limit = self.card_vram.plot.addLine(
            y=self.store.gpu_vram / 1024**2,
            pen=pg.mkPen(C["danger"], width=1, style=Qt.DotLine))

        # 悬停十字线
        self.cross = {
            "fps": Crosshair(self.card_fps.plot, [
                {"name": "FPS", "color": C["fps"], "item": self.c_fps, "fmt": "0"}]),
            "ft": Crosshair(self.card_ft.plot, [
                {"name": "帧时间 ms", "color": C["frametime"],
                 "item": self.c_ft, "fmt": "1"}]),
            "gpu": Crosshair(self.card_gpu.plot, [
                {"name": "GPU %", "color": C["gpu"], "item": self.c_gpu, "fmt": "0"},
                {"name": "Busy %", "color": C["accent"], "item": self.c_busy, "fmt": "0"}]),
            "vram": Crosshair(self.card_vram.plot, [
                {"name": "显存 MB", "color": C["vram"], "item": self.c_vram, "fmt": "0"}]),
            "cpu": Crosshair(self.card_cpu.plot, [
                {"name": "CPU %", "color": C["cpu"], "item": self.c_cpu, "fmt": "0"}]),
            "mem": Crosshair(self.card_mem.plot, [
                {"name": "内存 %", "color": C["mem"], "item": self.c_mem, "fmt": "0"}]),
            "temp": Crosshair(self.card_temp.plot, [
                {"name": "GPU ℃", "color": C["temp"], "item": self.c_temp, "fmt": "0"},
                {"name": "CPU ℃", "color": C["cpu_temp"], "item": self.c_cputemp, "fmt": "0"}]),
        }

        for card, txt in ((self.card_fps, "等待帧数据…"),
                          (self.card_ft, "等待帧数据…"),
                          (self.card_gpu, "等待系统采样…"),
                          (self.card_vram, "等待系统采样…"),
                          (self.card_cpu, "等待系统采样…"),
                          (self.card_mem, "等待系统采样…"),
                          (self.card_temp, "等待温度数据…")):
            card.set_placeholder(txt)

        grid = QGridLayout()
        grid.setSpacing(12)
        cards = [self.card_fps, self.card_ft, self.card_gpu,
                 self.card_vram, self.card_cpu, self.card_mem]
        for i, card in enumerate(cards):
            grid.addWidget(card, i // 3, i % 3)
        grid.addWidget(self.card_temp, 2, 0, 1, 3)
        return grid

    # ---------- 刷新 ----------
    def _update_status(self, sys_latest=None):
        """区分四种状态：目标未运行、运行中、采集引擎异常、跑着却采不到帧。"""
        alive = bool(sys_latest and sys_latest.get("target_alive"))
        col = self.collector
        # collector 可能是 CollectorController（内含 PresentMon），也可能是旧的直接实例
        pm = getattr(col, "pm", None) or col
        frames = getattr(pm, "frame_count", 0) if pm is not None else 0
        err = (getattr(pm, "last_error", "") or "") if pm is not None else ""
        uptime = time.time() - self._app_start

        if err and frames == 0:
            self.target_status.setText("● 采集被反作弊拦截")
            self.target_status.setStyleSheet(
                f"color:{C['danger']}; background:transparent;")
            self.target_status.setToolTip(
                "目标程序的反作弊可能阻止监控在程序启动后再创建 ETW 采集会话。\n"
                "退出该程序后监控会自动恢复，随后重新进入即可正常采集。\n"
                "长期方案：开机时先启动本监控（或加入开机自启），再开程序。")
            return
        if alive and frames == 0 and uptime > 4:
            self.target_status.setText("● 目标在运行，但采不到帧")
            self.target_status.setStyleSheet(
                f"color:{C['warn']}; background:transparent;")
            self.target_status.setToolTip(
                "目标程序先于监控启动时，反作弊会阻止新建采集会话。\n"
                "处理：退出该程序 → 等状态恢复 → 再启动。")
            return
        if alive or frames > 0:
            self.target_status.setText(f"● {self.target_name} 运行中")
            self.target_status.setStyleSheet(
                f"color:{C['mem']}; background:transparent;")
            self.target_status.setToolTip("")
            return
        self.target_status.setText("● 未检测到目标进程")
        self.target_status.setStyleSheet(
            f"color:{C['idle']}; background:transparent;")
        self.target_status.setToolTip("")

    def _refresh(self):
        snap = self.store.snapshot()
        frames = snap["frames"][-400:]
        samples = snap["sys_samples"][-120:]
        sys_latest = snap["latest_sys"]

        # 目标状态与采集状态
        self._update_status(sys_latest)

        # KPI
        self.kpi_fps.set_value(f"{snap['fps']:.0f}")
        if frames and frames[-1].get("frame_ms"):
            self.kpi_ft.set_value(f"{frames[-1]['frame_ms']:.1f}")
        if sys_latest:
            self.kpi_gpu.set_value(f"{sys_latest['gpu_util']:.0f}")
            self.kpi_cpu.set_value(f"{sys_latest['cpu_total']:.0f}")

        # 帧曲线
        if frames:
            t0 = frames[-1]["wall"]
            fxx = [f["wall"] - t0 for f in frames if f.get("frame_ms")]
            fy = [1000.0 / f["frame_ms"] for f in frames if f.get("frame_ms")]
            ftm = [f["frame_ms"] for f in frames if f.get("frame_ms")]
            self.c_fps.setData(fxx, fy, skipFiniteCheck=True)
            self.c_ft.setData(fxx, ftm, skipFiniteCheck=True)
            if fy:
                self.card_fps.clear_placeholder()
                self.card_ft.clear_placeholder()
                span = max(1.5, min(8.0, -fxx[0] + 0.25)) if fxx else 6.0
                self.card_fps.plot.setXRange(-span, 0, padding=0)
                self.card_ft.plot.setXRange(-span, 0, padding=0)
                eff = foreground_frames(frames)
                eff_fps = [1000.0 / f["frame_ms"] for f in eff if f.get("frame_ms")]
                avg = statistics.mean(eff_fps) if eff_fps else 0.0
                dropped = len(frames) - len(eff)
                self.c_fps_avg.setData([fxx[0], 0], [avg, avg])
                if dropped > 10:
                    self.card_fps.set_head_value(
                        f"均值 {avg:.0f} · 已剔后台 {dropped} 帧")
                else:
                    self.card_fps.set_head_value(f"均值 {avg:.0f}")
                top = max(60, math.ceil(max(fy) / 30) * 30 + 15)
                self.card_fps.plot.setYRange(0, top)
                srt = sorted(ftm)
                p99 = srt[min(len(srt) - 1, int(len(srt) * 0.99))]
                self.card_ft.plot.setYRange(0, max(20.0, min(60.0, p99 * 1.25)))
            self.kpi_fps.set_spark(fy[-60:])
            self.kpi_ft.set_spark(ftm[-60:])

        # 系统曲线
        if samples:
            t0 = samples[-1]["wall"]
            sx = [s["wall"] - t0 for s in samples]
            if sx:
                span = max(45.0, min(120.0, -sx[0] + 3))
                if abs(span - getattr(self, "_sys_span", 0.0)) > 4:
                    self._sys_span = span
                    for card in (self.card_gpu, self.card_vram,
                                 self.card_cpu, self.card_mem,
                                 self.card_temp):
                        card.plot.setXRange(-span, 0, padding=0)
            self.card_gpu.clear_placeholder()
            self.card_vram.clear_placeholder()
            self.card_cpu.clear_placeholder()
            self.card_mem.clear_placeholder()
            gpu_vals = [s["gpu_util"] for s in samples]
            self.c_gpu.setData(sx, gpu_vals)
            busy_x = [s["wall"] - t0 for s in samples
                      if s.get("gpu_busy_ratio") is not None]
            busy_y = [s["gpu_busy_ratio"] * 100 for s in samples
                      if s.get("gpu_busy_ratio") is not None]
            self.c_busy.setData(busy_x, busy_y)
            self.c_vram.setData(sx, [s["gpu_vram_used"] / 1024**2
                                     for s in samples])
            self.card_vram.set_head_value(
                f"{samples[-1]['gpu_vram_used'] / 1024**2:.0f} MB")
            self.c_cpu.setData(sx, [s["cpu_total"] for s in samples])
            self.c_mem.setData(sx, [s["mem_percent"] for s in samples])
            self.card_mem.set_head_value(f"{samples[-1]['mem_percent']:.0f}%")
            self.card_gpu.set_head_value(f"{gpu_vals[-1]:.0f}%")

            # 每核曲线
            n_core = len(samples[-1]["cpu_per_core"])
            for ci, curve in enumerate(self.c_core_curves):
                if ci < n_core:
                    curve.setData(sx, [s["cpu_per_core"][ci] for s in samples])
                    curve.show()
                else:
                    curve.hide()

            self.kpi_gpu.set_spark(gpu_vals)
            self.kpi_cpu.set_spark([s["cpu_total"] for s in samples])

            # 温度曲线（GPU 橙 / CPU 品红）
            temp_x = [s["wall"] - t0 for s in samples
                      if s.get("gpu_temp") is not None]
            temp_y = [s["gpu_temp"] for s in samples
                      if s.get("gpu_temp") is not None]
            ctemp_x = [s["wall"] - t0 for s in samples
                       if s.get("cpu_temp") is not None]
            ctemp_y = [s["cpu_temp"] for s in samples
                       if s.get("cpu_temp") is not None]
            self.c_temp.setData(temp_x, temp_y)
            self.c_cputemp.setData(ctemp_x, ctemp_y)

            all_temps = temp_y + ctemp_y
            if temp_y:
                self.kpi_temp.set_value(f"{temp_y[-1]:.0f}")
                self.kpi_temp.set_spark(temp_y[-60:])
            else:
                self.kpi_temp.set_value("—")
            if ctemp_y:
                self.kpi_cputemp.set_value(f"{ctemp_y[-1]:.0f}")
                self.kpi_cputemp.set_spark(ctemp_y[-60:])
            else:
                self.kpi_cputemp.set_value("—")

            if all_temps:
                self.card_temp.clear_placeholder()
                seg = []
                if temp_y:
                    seg.append(f"GPU {temp_y[-1]:.0f}°C")
                if ctemp_y:
                    seg.append(f"CPU {ctemp_y[-1]:.0f}°C")
                seg.append(f"峰值 GPU {max(temp_y) if temp_y else 0:.0f}"
                           f" / CPU {max(ctemp_y) if ctemp_y else 0:.0f}")
                self.card_temp.set_head_value("　".join(seg))
                lo = max(20, min(all_temps) - 10)
                hi = max(75.0, math.ceil(max(all_temps) / 10) * 10 + 5)
                self.card_temp.plot.setYRange(lo, hi)
                self.temp_peak.setValue(max(all_temps))
            else:
                self.card_temp.set_head_value(
                    "无温度数据（需 AMD 显卡驱动 ADL 可用）")

            # 瓶颈横幅
            if sys_latest is None:
                label, reason = "IDLE", "等待系统采样…"
            else:
                label = sys_latest["bottleneck"]
                reason = sys_latest["bottleneck_reason"]
            name, color = BOTTLENECK_META.get(label, BOTTLENECK_META["IDLE"])
            self.bn_label.setText(name)
            self.bn_label.setStyleSheet(f"color:{color};")
            self.bn_reason.setText(reason)

            share = {}
            for s in samples:
                share[s["bottleneck"]] = share.get(s["bottleneck"], 0) + 1
            total = sum(share.values()) or 1
            for key, bar in self.share_bars.items():
                bar.setValue(int(100 * share.get(key, 0) / total))

        if snap["recording"]:
            el = int(snap["record_elapsed"])
            self.record_timer.setText(f"{el // 60:02d}:{el % 60:02d}")

    # ---------- 录制 / 导出 ----------
    def _toggle_record(self):
        if self.store.recording:
            start = self.store.stop_recording()
            self.record_btn.setText("开始录制")
            self.record_btn.setProperty("recording", False)
            self.record_btn.style().unpolish(self.record_btn)
            self.record_btn.style().polish(self.record_btn)
            try:
                fp, sp, summary = self.store.export(self.sessions_dir, start)
                self._open_review(summary, fp, sp, start)
            except Exception as e:
                QMessageBox.warning(self, "导出失败", str(e))
        else:
            self.store.start_recording()
            self.record_btn.setText("停止并生成报告")
            self.record_btn.setProperty("recording", True)
            self.record_btn.style().unpolish(self.record_btn)
            self.record_btn.style().polish(self.record_btn)

    def _export_all(self):
        try:
            fp, sp, summary = self.store.export(self.sessions_dir, None)
            self._open_review(summary, fp, sp, None)
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))

    def _open_review(self, summary, frame_path, sys_path, start):
        """打开录制回顾窗口：直接看录制期间的温度与帧率曲线。"""
        samples = self.store.snapshot()["sys_samples"]
        if start is not None:
            samples = [s for s in samples if s["wall"] >= start]
        origin = start if start is not None else (
            samples[0]["wall"] if samples else time.time())
        ReviewWindow(summary, samples, frame_path, sys_path, origin,
                     self.sessions_dir, self).exec()

    def _open_history(self):
        """打开历史录制列表，选中后直接进回顾窗口。"""
        from history_window import HistoryWindow
        dlg = HistoryWindow(self.sessions_dir, self)
        if dlg.exec() == QDialog.Accepted and dlg.selected:
            entry = dlg.selected
            ReviewWindow(None, [], entry.get("frames"), entry.get("system"),
                         None, self.sessions_dir, self).exec()
