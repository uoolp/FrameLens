"""用户配置持久化。

配置放在 %LOCALAPPDATA%\\FrameLens\\config.json，
首次启动时自动创建；录制数据放在「文档」目录下，卸载重装不丢数据。
"""
import json
import os

APP_NAME = "FrameLens"
APP_TITLE = "FrameLens 帧镜"
APP_DIR_LEGACY = "ApexPerfMonitor"

DEFAULTS = {
    "target": "",            # 目标进程 exe 名，例如 "r5apex.exe"
    "target_title": "",      # 显示用的友好名
    "sessions_dir": "",      # 留空则用默认文档目录
    "chart_seconds": 120,    # 实时曲线时间窗（秒）
    "gpu_index": -1,         # 留空/负数表示自动选显存最大的独显
    "accent": "",            # 主题强调色（留空用默认）
}


def config_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, APP_NAME)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def config_path():
    return os.path.join(config_dir(), "config.json")


def default_sessions_dir():
    doc = os.path.join(os.path.expanduser("~"), "Documents", APP_NAME)
    return os.path.join(doc, "sessions")


def legacy_sessions_dir():
    doc = os.path.join(os.path.expanduser("~"), "Documents", APP_DIR_LEGACY)
    return os.path.join(doc, "sessions")


def load():
    """读取配置；文件不存在或损坏时返回默认值。"""
    cfg = dict(DEFAULTS)
    try:
        with open(config_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            for k, v in data.items():
                if k in DEFAULTS or k.startswith("_"):
                    cfg[k] = v
    except (OSError, ValueError):
        pass
    if not cfg.get("sessions_dir"):
        cfg["sessions_dir"] = default_sessions_dir()
    return cfg


def save(cfg):
    """写回配置；返回是否成功。"""
    try:
        cur = load()
        cur.update(cfg or {})
        with open(config_path(), "w", encoding="utf-8") as fh:
            json.dump(cur, fh, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


def ensure_sessions_dir(cfg):
    """确保录制目录存在；首次运行时把旧版（ApexPerfMonitor）的数据搬过来。

    只做复制不删除，避免迁移失败把历史录制弄丢。
    """
    d = cfg.get("sessions_dir") or default_sessions_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return d
    legacy = legacy_sessions_dir()
    if os.path.isdir(legacy) and os.path.abspath(legacy) != os.path.abspath(d):
        try:
            existing = {n for n in os.listdir(d) if n.endswith(".csv")}
            for name in os.listdir(legacy):
                if not name.endswith(".csv") or name in existing:
                    continue
                src = os.path.join(legacy, name)
                dst = os.path.join(d, name)
                try:
                    with open(src, "rb") as a, open(dst, "wb") as b:
                        b.write(a.read())
                except OSError:
                    pass
        except OSError:
            pass
    return d
