"""PresentMon 子进程管理：实时读取真实帧事件（帧时间、GPU/CPU busy、Present 模式）。

关键点（2026-11-20 修复）：
反作弊保护下的游戏进程会让 PresentMon 的 Application 列变成 <unknown>，
原先只按进程名过滤会导致「先开游戏再开监控」完全采不到帧。这里增加
PID 兜底匹配（由 winproc.Toolhelp32 提供，不依赖进程句柄权限），
并在 ETW 会话启动失败时换 session 名重试、把错误暴露给界面。
"""
import csv
import ctypes
import os
import subprocess
import threading
import time
from collections import Counter


class PresentMonCollector:
    def __init__(self, exe_path, target_exe, on_frame, log_path=None,
                 pid_provider=None):
        self._exe = exe_path
        self._target = target_exe.lower()
        # 前缀匹配，兼容 r5apex.exe 与 r5apex_dx12.exe
        self._target_prefix = self._target.split(".")[0]
        self._on_frame = on_frame
        self._log_path = log_path
        self._pid_provider = pid_provider
        self._proc = None
        self._thread = None
        self._pid_thread = None
        self._alive = False
        self._target_pids = set()
        self.target_seen = False
        self.header_ok = False
        self.frame_count = 0
        self.session_name = ""
        self.last_error = ""
        self.unknown_apps = Counter()
        self._stale_cleaned = False

    def _log(self, text):
        if not self._log_path:
            return
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:
            pass

    @property
    def running(self):
        return self._alive and self._proc is not None and self._proc.poll() is None

    # ---------- 启动 ----------
    def _kill_stale_session(self):
        """清理早期版本留下的默认名会话（best-effort，失败无副作用）。"""
        logman = os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                              "System32", "logman.exe")
        if not os.path.exists(logman):
            return
        try:
            subprocess.run([logman, "stop", "PresentMon", "-ets"],
                           capture_output=True, timeout=6,
                           creationflags=getattr(subprocess,
                                                 "CREATE_NO_WINDOW", 0))
            self._log(f"[{time.strftime('%H:%M:%S')}] cleaned stale "
                      f"session 'PresentMon'")
        except Exception:
            pass

    def _spawn(self, session_name):
        args = [
            self._exe,
            "--output_stdout",
            "--no_console_stats",
            "--session_name", session_name,
            "--stop_existing_session",
        ]
        self._log(f"[{time.strftime('%H:%M:%S')}] starting: "
                  f"{' '.join(args)}")
        creationflags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags |= subprocess.CREATE_NO_WINDOW
        if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
        self._err_file = None
        stderr_target = subprocess.DEVNULL
        if self._log_path:
            try:
                self._err_file = open(self._log_path, "a", encoding="utf-8")
                stderr_target = self._err_file
            except Exception:
                self._err_file = None
        try:
            self._proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=stderr_target,
                bufsize=0,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
            )
        except Exception as exc:
            self.last_error = f"无法启动 PresentMon：{exc}"
            return False
        self.session_name = session_name
        return True

    def _wait_healthy(self, timeout=1.8):
        """等 header 到达或进程退出，判断本次会话是否真正建立成功。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._proc is None:
                return False
            if self._proc.poll() is not None:
                return False
            if self._thread is not None and self.header_ok:
                return True
            time.sleep(0.05)
        # header 未到但进程还活着：再给一次机会，认为可用
        return self._proc is not None and self._proc.poll() is None

    def _drop_proc(self):
        proc = self._proc
        self._proc = None
        self._alive = False
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=3)
            except Exception:
                pass
        if getattr(self, "_err_file", None):
            try:
                self._err_file.close()
            except Exception:
                pass
            self._err_file = None

    def start(self):
        if self.running:
            return
        if not self._stale_cleaned:
            self._stale_cleaned = True
            self._kill_stale_session()
        candidates = ["ApexPerfMonitor", "ApexPerfMon2", "ApexPerfMon3"]
        last_error = ""
        for i, name in enumerate(candidates):
            self.header_ok = False
            if not self._spawn(name):
                last_error = self.last_error
                self._drop_proc()
                continue
            self._alive = True
            if self._pid_provider is not None:
                self._pid_thread = threading.Thread(target=self._pid_loop,
                                                    daemon=True)
                self._pid_thread.start()
            self._thread = threading.Thread(target=self._reader, daemon=True)
            self._thread.start()
            if self._wait_healthy():
                self.last_error = ""
                self._log(f"[{time.strftime('%H:%M:%S')}] session ok: "
                          f"{name}")
                return
            code = self._proc.returncode if self._proc else None
            last_error = (self.last_error
                          or f"ETW 会话建立失败（exit={code}），"
                             f"常见原因是权限不足或反作弊拦截")
            self._log(f"[{time.strftime('%H:%M:%S')}] session failed "
                      f"'{name}': exit={code}")
            self._drop_proc()
            if i + 1 < len(candidates):
                time.sleep(0.5)
        self.last_error = last_error
        self._log(f"[{time.strftime('%H:%M:%S')}] {last_error}")

    # ---------- PID 兜底 ----------
    def _pid_loop(self):
        while self._alive:
            try:
                pids = self._pid_provider() if self._pid_provider else []
                self._target_pids = set(int(p) for p in pids)
            except Exception:
                pass
            for _ in range(8):
                if not self._alive:
                    return
                time.sleep(0.25)

    def _is_target(self, app, pid_raw):
        name = (app or "").strip().lower()
        if name.startswith(self._target_prefix):
            return True
        if pid_raw:
            try:
                pid = int(pid_raw)
            except (TypeError, ValueError):
                return False
            if pid in self._target_pids:
                return True
        return False

    # ---------- 读取 ----------
    def _reader(self):
        proc = self._proc
        try:
            reader = csv.reader(proc.stdout)
            header = next(reader, None)
            if not header:
                return
            # 列名大小写不敏感，兼容 1.x / 2.x 输出差异
            idx = {name.strip().lower(): i for i, name in enumerate(header)}
            self.header_ok = True

            def get(row, name):
                i = idx.get(name.lower())
                if i is None or i >= len(row):
                    return None
                return row[i]

            last_report = time.time()
            while self._alive:
                row = next(reader, None)
                if row is None:
                    break
                if not row:
                    continue
                app = (get(row, "Application") or "").lower()
                pid_raw = get(row, "ProcessID")
                if not self._is_target(app, pid_raw):
                    if app:
                        self.unknown_apps[app] += 1
                    continue
                self.target_seen = True
                frame = {
                    "process_id": pid_raw,
                    "time_ms": _to_float(get(row, "TimeInMs")),
                    "frame_ms": _to_float(get(row, "MsBetweenPresents")),
                    "display_delta_ms": _to_float(
                        get(row, "MsBetweenDisplayChange")),
                    "gpu_busy_ms": _to_float(get(row, "MsGPUBusy")),
                    "gpu_wait_ms": _to_float(get(row, "MsGPUWait")),
                    "cpu_busy_ms": _to_float(get(row, "MsCPUBusy")),
                    "present_mode": get(row, "PresentMode"),
                }
                self.frame_count += 1
                self._on_frame(frame)

                # 每 30 秒把见到过的进程名记一次，便于排查 <unknown>
                if time.time() - last_report > 30:
                    last_report = time.time()
                    top = [f"{n}({c})" for n, c in
                           self.unknown_apps.most_common(6)]
                    self._log(f"[{time.strftime('%H:%M:%S')}] frames="
                              f"{self.frame_count} pids={sorted(self._target_pids)} "
                              f"apps={top}")
        except Exception as exc:
            self._log(f"[{time.strftime('%H:%M:%S')}] reader error: {exc}")
        finally:
            self._alive = False

    def stop(self):
        self._alive = False
        if self._pid_thread and self._pid_thread.is_alive():
            self._pid_thread.join(timeout=1)
        self._pid_thread = None
        if self._proc:
            # 优先发 CTRL_BREAK，让 PresentMon 优雅退出并自行清理 ETW 会话
            try:
                ctypes.windll.kernel32.GenerateConsoleCtrlEvent(
                    1, self._proc.pid)  # CTRL_BREAK_EVENT = 1
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=5)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
            self._proc = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        if getattr(self, "_err_file", None):
            try:
                self._err_file.close()
            except Exception:
                pass
            self._err_file = None


def _to_float(v):
    if v is None or v == "NA":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def kill_stale_presentmon(wait=0.4):
    """清理上次异常退出遗留的 PresentMon 进程。

    程序被强杀时 aboutToQuit 不会触发，子进程会变成孤儿，既锁住安装目录的
    PresentMon.exe，也长期占用一个 ETW 会话。启动时先清一遍。
    """
    try:
        from winproc import find_pids
    except Exception:
        return 0
    try:
        pids = list(find_pids("PresentMon"))
    except Exception:
        return 0
    killed = 0
    for pid in pids:
        try:
            os.kill(pid, 9)  # Windows 上任意非 0 signal 都是 TerminateProcess
            killed += 1
        except Exception:
            try:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=5)
                killed += 1
            except Exception:
                pass
    if killed and wait:
        time.sleep(wait)
    return killed
