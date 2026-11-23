"""进程枚举：走 Toolhelp32 快照，不打开进程句柄。

反作弊（EAC/BattlEye）会拒绝 OpenProcess 读取受保护进程的信息，导致
PresentMon 把 Application 列写成 <unknown>、psutil 也拿不到进程名。
Toolhelp32 快照由内核填充，不需要句柄权限，能稳定读到可执行文件名。
"""
import ctypes
from ctypes import wintypes

TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
MAX_PATH = 260


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * MAX_PATH),
    ]


_kernel32 = None


def _k32():
    global _kernel32
    if _kernel32 is None:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k.Process32FirstW.restype = wintypes.BOOL
        k.Process32FirstW.argtypes = [wintypes.HANDLE,
                                      ctypes.POINTER(PROCESSENTRY32W)]
        k.Process32NextW.restype = wintypes.BOOL
        k.Process32NextW.argtypes = [wintypes.HANDLE,
                                     ctypes.POINTER(PROCESSENTRY32W)]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        _kernel32 = k
    return _kernel32


def iter_processes():
    """返回 [(pid, exe_name)]；枚举失败时返回空列表。"""
    k = _k32()
    snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return []
    out = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out.append((int(entry.th32ProcessID), entry.szExeFile or ""))
            ok = k.Process32NextW(snap, ctypes.byref(entry))
    finally:
        try:
            k.CloseHandle(snap)
        except Exception:
            pass
    return out


def find_pids(prefix):
    """按可执行文件名前缀（不含 .exe）返回 PID 列表，兼容 r5apex/r5apex_dx12。"""
    low = prefix.lower()
    return [pid for pid, name in iter_processes()
            if name and name.lower().startswith(low)]


def describe_processes(prefix, limit=8):
    """返回命中的 (pid, name) 明细，供日志与状态显示。"""
    low = prefix.lower()
    hits = [(pid, name) for pid, name in iter_processes()
            if name and name.lower().startswith(low)]
    return hits[:limit]


# ---------- 窗口相关：用于「选择要监控的程序」 ----------
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                  wintypes.LPARAM)


def window_pids():
    """返回所有拥有可见顶层窗口的 PID 集合；失败时返回空集合。"""
    pids = set()
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]

        def _cb(hwnd, _lparam):
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value:
                    pids.add(int(pid.value))
            except Exception:
                pass
            return True

        user32.EnumWindows(_WNDENUMPROC(_cb), 0)
    except Exception:
        return set()
    return pids


def foreground_pid():
    """当前前台窗口所属进程 PID；失败返回 0。"""
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return 0
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        return 0


def list_candidates():
    """列出可监控的进程：[(pid, exe_name, has_window)]。

    有可见窗口的排前面（更可能是用户想监控的程序），其余按名字排序。
    过滤掉系统进程（PID 0/4）和快照里读不到名字的项。
    """
    wins = window_pids()
    skip = {"system", "[system process]", "registry", "memory compression",
            "secure system"}
    items = []
    for pid, name in iter_processes():
        if not name or pid <= 4 or name.lower() in skip:
            continue
        items.append((pid, name, pid in wins))
    items.sort(key=lambda x: (not x[2], x[1].lower(), x[0]))
    return items
