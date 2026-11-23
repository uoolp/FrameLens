"""在进程启动最早期把主线程切换到用户当前的输入桌面。

沙箱运行时会把进程放进隔离桌面，导致 GUI 窗口在用户屏幕上不可见。
在创建任何窗口 / 消息队列之前调用 attach_to_input_desktop() 即可让窗口
出现在用户实际看到的桌面上。必须在 QApplication 创建之前执行。
"""
import ctypes
from ctypes import wintypes

_DESKTOP_ALL = 0x000F01FF


def attach_to_input_desktop():
    """把当前线程绑定到当前接收用户输入的桌面。成功返回 True。"""
    user32 = ctypes.windll.user32
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    desktop = user32.OpenInputDesktop(0, False, _DESKTOP_ALL)
    if not desktop:
        return False
    return bool(user32.SetThreadDesktop(desktop))
