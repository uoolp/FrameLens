"""采集控制器：管理 PresentMon 与系统采样器，支持运行时切换监控目标。"""
import os
import threading
import time

from presentmon import PresentMonCollector
from system_metrics import SystemCollector
from winproc import find_pids


class CollectorController:
    """把「采集器实例」和「目标进程」绑在一起，换目标时整组重建。

    PresentMon 与 SystemCollector 构造时就固定了目标名，所以切换目标必须
    重建实例；这里统一封装，界面只需调用 start(target_exe)。
    """

    def __init__(self, store, sessions_dir, presentmon_path, gpu_name=""):
        self.store = store
        self.sessions_dir = sessions_dir
        self.presentmon_path = presentmon_path
        self.gpu_name = gpu_name
        self.target = ""
        self.pm = None
        self.sc = None
        self._lock = threading.Lock()

    # ---------- 状态 ----------
    @property
    def prefix(self):
        return (self.target or "").split(".")[0].lower()

    @property
    def running(self):
        return bool(self.pm is not None and self.pm.running)

    def target_alive(self):
        try:
            return bool(find_pids(self.prefix)) if self.prefix else False
        except Exception:
            return False

    def describe(self):
        """当前目标的命中进程，用于状态栏。"""
        try:
            return find_pids(self.prefix)[:3]
        except Exception:
            return []

    # ---------- 生命周期 ----------
    def start(self, target_exe):
        """停止旧采集器，按新目标重建并启动。"""
        with self._lock:
            self._stop_locked()
            self.target = (target_exe or "").strip()
            if not self.target:
                return
            log_path = os.path.join(self.sessions_dir, "presentmon_engine.log")
            self.pm = PresentMonCollector(
                self.presentmon_path, self.target, self.store.push_frame,
                log_path=log_path,
                pid_provider=lambda: find_pids(self.prefix))
            self.sc = SystemCollector(self.target, self.store.push_sys,
                                  self.gpu_name)
            self.pm.start()
            self.sc.start()

    def _stop_locked(self):
        if self.pm is not None:
            try:
                self.pm.stop()
            except Exception:
                pass
            self.pm = None
        if self.sc is not None:
            try:
                self.sc.stop()
            except Exception:
                pass
            self.sc = None

    def stop(self):
        with self._lock:
            self._stop_locked()

    # ---------- 看门狗 ----------
    def watchdog(self, interval=2.5):
        """目标消失后自动重建会话；被反作弊拦下时降频重试。

        反作弊在游戏运行期间会拒绝新建 ETW 采集会话，此时重试无意义，
        降低频率等游戏退出；一旦目标进程消失就立刻重建，这样
        「忘了先开监控 → 进游戏 → 退出游戏」后能自动恢复采集。
        """
        while True:
            time.sleep(interval)
            try:
                pm = self.pm
                if pm is None or pm.running:
                    continue
                if self.target_alive() and pm.last_error:
                    time.sleep(25)
                    continue
                with self._lock:
                    if self.pm is pm and not pm.running:
                        pm.start()
            except Exception:
                time.sleep(5)
