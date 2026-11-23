"""入口：管理员提权、路径解析、组件装配。

监控目标由用户在启动时选择（或从配置读回），不是写死的单一游戏。
"""
import ctypes
import os
import sys


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def elevate():
    if getattr(sys, "frozen", False):
        params = " ".join(f'"{a}"' for a in sys.argv[1:])
        executable = sys.executable
    else:
        params = f'"{os.path.abspath(__file__)}" ' + \
                 " ".join(f'"{a}"' for a in sys.argv[1:])
        executable = sys.executable
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas",
                                             executable, params, None, 1)
    return rc > 32


if not is_admin():
    ok = elevate()
    if not ok:
        input("需要管理员权限才能采集 ETW 帧数据，取消提权将退出。按回车关闭。")
    sys.exit(0)


if __name__ == "__main__":
    import threading

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QDialog

    if getattr(sys, "frozen", False):
        APP_DIR = os.path.dirname(sys.executable)
        BUNDLE_DIR = sys._MEIPASS
        for p in (BUNDLE_DIR, os.path.join(BUNDLE_DIR, "app")):
            if p not in sys.path:
                sys.path.insert(0, p)
    else:
        APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        BUNDLE_DIR = APP_DIR
        sys.path.insert(0, os.path.join(BUNDLE_DIR, "app"))

    import config
    from controller import CollectorController
    from data_store import DataStore
    from gpu_enum import enumerate_adapters
    from main_window import MainWindow
    from presentmon import kill_stale_presentmon
    from target_picker import TargetPicker
    from theme import QSS

    PRESENTMON = os.path.join(BUNDLE_DIR, "bin", "PresentMon.exe")

    cfg = config.load()
    SESSIONS = config.ensure_sessions_dir(cfg)

    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)
    icon_path = os.path.join(BUNDLE_DIR, "bin", "icon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    # 选择独显：排除 WARP(0x1414)，取显存最大的物理适配器
    physical = [a for a in enumerate_adapters() if a["vendor_id"] != 0x1414]
    discrete = max(physical, key=lambda a: a["vram_bytes"])

    # 首次运行（或配置里没有目标）先让用户选一个程序
    target = (cfg.get("target") or "").strip()
    if not target:
        picker = TargetPicker("")
        if picker.exec() != QDialog.Accepted or not picker.selected:
            sys.exit(0)
        target = picker.selected
        config.save({"target": target})

    store = DataStore(discrete["luid_low"], discrete["vram_bytes"], SESSIONS)

    # 清掉上次异常退出留下的孤儿 PresentMon，避免它锁住文件、占住 ETW 会话
    try:
        kill_stale_presentmon()
    except Exception:
        pass

    ctrl = CollectorController(store, SESSIONS, PRESENTMON, discrete["name"])

    def change_target():
        """弹出选择器并切换到新目标，返回新目标名或 None。"""
        picker = TargetPicker(ctrl.target, window, title="更换监控目标")
        if picker.exec() != QDialog.Accepted or not picker.selected:
            return None
        new_target = picker.selected
        ctrl.start(new_target)
        config.save({"target": new_target})
        return new_target

    window = MainWindow(store, SESSIONS, target, controller=ctrl,
                        on_change_target=change_target)
    window.show()

    ctrl.start(target)
    threading.Thread(target=ctrl.watchdog, daemon=True).start()

    def cleanup():
        ctrl.stop()
        # 双保险：CTRL_BREAK / terminate 偶发失败时，确保不留孤儿进程
        try:
            kill_stale_presentmon(0.1)
        except Exception:
            pass

    app.aboutToQuit.connect(cleanup)
    sys.exit(app.exec())
