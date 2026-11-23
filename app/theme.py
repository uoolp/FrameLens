"""深色现代主题：配色、全局 QSS、pyqtgraph 全局配置。"""
from PySide6.QtGui import QColor

C = {
    "bg": "#0A0D13",
    "card": "#12161F",
    "card_alt": "#171D29",
    "border": "#242C3B",
    "text": "#E9EEF6",
    "text_dim": "#7E8AA0",
    "accent": "#3DD6F5",
    "fps": "#2DD4BF",
    "frametime": "#FB923C",
    "gpu": "#A78BFA",
    "cpu": "#60A5FA",
    "mem": "#34D399",
    "vram": "#F472B6",
    "temp": "#FF7043",
    "cpu_temp": "#E879F9",
    "warn": "#FBBF24",
    "danger": "#FB7185",
    "idle": "#64748B",
}

QSS = f"""
* {{
    font-family: "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
    outline: none;
}}
QWidget {{
    color: {C['text']};
    background: {C['bg']};
}}
QMainWindow, QDialog {{ background: {C['bg']}; }}

QTableWidget {{
    background: {C['card']};
    alternate-background-color: {C['card_alt']};
    border: 1px solid {C['border']};
    border-radius: 10px;
    gridline-color: {C['border']};
    selection-background-color: #1E2C42;
    selection-color: {C['text']};
}}
QTableWidget::item {{ padding: 5px 8px; }}
QHeaderView::section {{
    background: {C['card_alt']};
    color: {C['text_dim']};
    border: none;
    border-bottom: 1px solid {C['border']};
    padding: 7px 8px;
    font-weight: 600;
}}
QTableCornerButton::section {{
    background: {C['card_alt']};
    border: none;
}}

QComboBox {{
    background: {C['card_alt']};
    border: 1px solid {C['border']};
    border-radius: 8px;
    padding: 5px 10px;
    min-height: 22px;
}}
QComboBox:hover {{ border-color: {C['accent']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {C['card_alt']};
    border: 1px solid {C['border']};
    outline: none;
    selection-background-color: #1E2C42;
}}
QTabWidget::pane {{
    border: none;
    background: transparent;
}}
QTabBar::tab {{
    background: {C['card_alt']};
    color: {C['text_dim']};
    border: 1px solid {C['border']};
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 6px 16px;
    margin-right: 3px;
}}
QTabBar::tab:selected {{
    color: {C['accent']};
    background: {C['card']};
    font-weight: 600;
}}

QFrame#Card {{
    background: {C['card']};
    border: 1px solid {C['border']};
    border-radius: 12px;
}}
QFrame#TopBar {{
    background: {C['card']};
    border: 1px solid {C['border']};
    border-radius: 14px;
}}
QLabel#CardTitle {{
    color: {C['text_dim']};
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 1px;
}}
QLabel#KpiValue {{
    font-size: 30px;
    font-weight: 700;
    background: transparent;
}}
QLabel#KpiUnit {{
    color: {C['text_dim']};
    font-size: 13px;
    background: transparent;
}}
QLabel#AppTitle {{
    font-size: 18px;
    font-weight: 700;
    background: transparent;
}}
QLabel#BottleneckLabel {{
    font-size: 22px;
    font-weight: 800;
    background: transparent;
}}
QLabel#BottleneckReason {{
    color: {C['text_dim']};
    font-size: 13px;
    background: transparent;
}}

QPushButton {{
    background: {C['card_alt']};
    border: 1px solid {C['border']};
    border-radius: 9px;
    padding: 9px 18px;
    font-size: 13px;
    font-weight: 600;
}}
QPushButton:hover {{
    border-color: {C['accent']};
    color: {C['accent']};
}}
QPushButton:pressed {{ background: {C['border']}; }}
QPushButton#RecordBtn {{
    background: rgba(251,113,133,0.14);
    border: 1px solid {C['danger']};
    color: {C['danger']};
    padding: 10px 22px;
}}
QPushButton#RecordBtn:hover {{ background: rgba(251,113,133,0.25); }}
QPushButton#RecordBtn[recording="true"] {{
    background: {C['danger']};
    color: #1A0D12;
}}
QPushButton#ExportBtn {{
    border-color: {C['fps']};
    color: {C['fps']};
}}

QProgressBar {{
    background: {C['card_alt']};
    border: none;
    border-radius: 4px;
    height: 8px;
    text-align: center;
}}
QProgressBar::chunk {{
    border-radius: 4px;
    background: {C['accent']};
}}
QToolTip {{
    background: {C['card_alt']};
    color: {C['text']};
    border: 1px solid {C['border']};
    padding: 6px;
    border-radius: 6px;
}}
"""

BOTTLENECK_META = {
    "GPU": ("GPU 瓶颈", C["gpu"]),
    "CPU": ("CPU 瓶颈", C["cpu"]),
    "VRAM": ("显存瓶颈", C["vram"]),
    "CAPPED": ("帧率封顶", C["warn"]),
    "BALANCED": ("负载均衡", C["mem"]),
    "IDLE": ("等待游戏", C["idle"]),
}


def qcolor(name):
    return QColor(C[name])


def hex_to_rgba(hex_color, alpha):
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (r, g, b, alpha)
