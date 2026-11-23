"""曲线悬停读数：十字线 + 数值浮标，主界面与录制回顾共用。"""
import bisect

import pyqtgraph as pg
from PySide6.QtCore import Qt

from theme import C


class Crosshair:
    """鼠标在曲线上移动时，垂直虚线定位最近数据点并弹出该点各序列的数值。

    specs 为 [{"item": PlotDataItem, "name": str, "color": str, "fmt": str}]。
    """

    def __init__(self, plot, specs, x_format="{:.1f} s"):
        self.plot = plot
        self.specs = specs
        self.x_format = x_format
        self.vline = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen(C["text_dim"], width=1, style=Qt.DashLine))
        self.vline.setZValue(20)
        self.vline.hide()
        plot.addItem(self.vline, ignoreBounds=True)

        self.text = pg.TextItem(anchor=(0, 0), fill=pg.mkBrush(10, 13, 19, 240),
                                border=pg.mkPen(C["border"]))
        self.text.setZValue(21)
        self.text.hide()
        plot.addItem(self.text, ignoreBounds=True)
        plot.scene().sigMouseMoved.connect(self._on_move)

    def _on_move(self, pos):
        try:
            vb = self.plot.getPlotItem().vb
            if not self.plot.sceneBoundingRect().contains(pos):
                self.vline.hide()
                self.text.hide()
                return
            xv = vb.mapSceneToView(pos).x()

            idx, xs = None, None
            for sp in self.specs:
                data = sp["item"].xData
                if data is None or len(data) < 1:
                    continue
                i = bisect.bisect_left(data, xv)
                i = max(0, min(len(data) - 1, i))
                if i > 0 and abs(data[i - 1] - xv) < abs(data[i] - xv):
                    i -= 1
                idx, xs = i, data
                break
            if idx is None:
                self.vline.hide()
                self.text.hide()
                return

            real_x = float(xs[idx])
            self.vline.setPos(real_x)
            self.vline.show()

            rows = [f'<span style="color:{C["text_dim"]}">'
                    f'{self.x_format.format(real_x)}</span>']
            for sp in self.specs:
                yd = sp["item"].yData
                if yd is None or idx >= len(yd) or yd[idx] is None:
                    continue
                try:
                    val = float(yd[idx])
                except (TypeError, ValueError):
                    continue
                fmt = sp.get("fmt", ".1f")
                try:
                    txt = format(val, fmt)
                except (ValueError, TypeError):
                    txt = f"{val:.1f}"
                rows.append(
                    f'<span style="color:{sp["color"]}">■</span> '
                    f'{sp["name"]} <b>{txt}</b>')
            if len(rows) == 1:
                self.vline.hide()
                self.text.hide()
                return
            self.text.setHtml("<br>".join(rows))

            yr = vb.viewRange()[1]
            # 靠右时把浮标翻到左侧，避免读数被窗口边缘截断
            xr = vb.viewRange()[0]
            frac = (real_x - xr[0]) / max(1e-9, xr[1] - xr[0])
            self.text.setAnchor((1.0, 0.0) if frac > 0.62 else (0.0, 0.0))
            self.text.setPos(real_x, yr[1])
            self.text.show()
        except Exception:
            self.vline.hide()
            self.text.hide()
