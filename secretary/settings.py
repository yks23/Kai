"""
持久化用户配置 — 按平台存储:
  Windows: %APPDATA%\\kai\\settings.json
  macOS/Linux: ~/.config/kai/settings.json (XDG 约定)

支持的配置项:
  base_dir  — 工作区目录 (kai base <path>)
  cli_name  — CLI 命令名 (kai name <name>)
"""
import json
import os
import shutil
import sys
from pathlib import Path


def _config_dir() -> Path:
    """按平台返回配置目录。Windows: APPDATA/kai；macOS/Linux: ~/.config/kai"""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "kai"
        # 回退到用户目录下 .config/kai
        return Path.home() / ".config" / "kai"
    return Path.home() / ".config" / "kai"


# 配置文件路径（模块加载时确定，保证后续读写一致）
_CONFIG_DIR = _config_dir()
_SETTINGS_FILE = _CONFIG_DIR / "settings.json"

# 默认值
_DEFAULTS = {
    "base_dir": "",       # 空 = 使用 CWD
    "cli_name": "kai",    # 默认命令名
    "model": "Auto",      # 默认模型
    "language": "zh",    # 输出语言 en | zh，也可用环境变量 SECRETARY_LANGUAGE
}


def _ensure_config_dir():
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_settings() -> dict:
    """加载持久化配置，不存在则返回默认值"""
    if _SETTINGS_FILE.exists():
        try:
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 合并默认值 (兼容旧版配置缺少新字段)
            merged = {**_DEFAULTS, **data}
            return merged
        except (json.JSONDecodeError, OSError):
            return dict(_DEFAULTS)
    return dict(_DEFAULTS)


def save_settings(settings: dict):
    """保存配置到磁盘"""
    _ensure_config_dir()
    with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


# ============ 便捷接口 ============

def get_base_dir() -> str:
    """获取已保存的 base_dir (空字符串 = 未设置，使用 CWD)"""
    return load_settings().get("base_dir", "")


def set_base_dir(path: str):
    """设置并持久化 base_dir"""
    s = load_settings()
    s["base_dir"] = path
    save_settings(s)


def get_cli_name() -> str:
    """获取当前 CLI 命令名"""
    return load_settings().get("cli_name", "kai")


def get_model() -> str:
    """获取已保存的模型设置"""
    return load_settings().get("model", "Auto")


def set_model(model: str):
    """设置并持久化模型"""
    s = load_settings()
    s["model"] = model
    save_settings(s)


def get_language() -> str:
    """获取当前输出语言: en | zh。优先级: 环境变量 SECRETARY_LANGUAGE > 持久化配置 > 默认 zh"""
    raw = os.environ.get("SECRETARY_LANGUAGE", "").strip().lower()
    if raw in ("en", "zh"):
        return raw
    return load_settings().get("language", "zh") or "zh"


def set_language(lang: str):
    """设置并持久化输出语言 (en | zh)"""
    lang = lang.strip().lower()
    if lang not in ("en", "zh"):
        raise ValueError("language must be 'en' or 'zh'")
    s = load_settings()
    s["language"] = lang
    save_settings(s)


def set_cli_name(new_name: str):
    """
    重命名 CLI 命令:
      1. 在 bin 目录创建符号链接 new_name → 原始入口
      2. 保存到配置
    """
    old_name = get_cli_name()

    # 找到当前可执行文件所在的 bin 目录
    bin_dir = _find_bin_dir()
    if not bin_dir:
        print("   ⚠️ 无法定位 bin 目录，请手动创建别名:")
        print(f"      alias {new_name}='kai'")
        s = load_settings()
        s["cli_name"] = new_name
        save_settings(s)
        return False

    # 源: kai 的实际入口脚本（Windows 下可能为 kai.exe / secretary.exe）
    def _resolve_exe(directory: Path, name: str) -> Path | None:
        for candidate in (directory / name, directory / f"{name}.exe"):
            if candidate.exists():
                return candidate
        return None

    src = _resolve_exe(bin_dir, "kai") or _resolve_exe(bin_dir, "secretary") or _resolve_exe(bin_dir, old_name)
    if src is None:
        src = bin_dir / "kai"  # 占位，下面 src.exists() 为 False 会走 _create_wrapper_script
    # Windows Scripts 目录下通常使用 .exe，新建链接/副本时保持一致便于 PATH 解析
    if sys.platform == "win32" and str(src).lower().endswith(".exe"):
        dest = bin_dir / f"{new_name}.exe"
    else:
        dest = bin_dir / new_name

    if dest.exists() or dest.is_symlink():
        dest.unlink()

    try:
        if src.exists():
            # 创建符号链接: lily → kai (或复制脚本)
            os.symlink(src, dest)
            print(f"   ✅ 已创建: {dest} → {src.name}")
        else:
            # 如果找不到源，直接创建一个小 wrapper
            _create_wrapper_script(dest)
            print(f"   ✅ 已创建入口脚本: {dest}")
    except OSError as e:
        print(f"   ⚠️ 创建链接失败: {e}")
        print(f"   💡 请手动执行: alias {new_name}='kai'")

    s = load_settings()
    s["cli_name"] = new_name
    save_settings(s)
    return True


def _has_kai_or_secretary(directory: Path) -> bool:
    """检查目录下是否存在 kai 或 secretary 可执行文件（含 .exe）"""
    for base in ("kai", "secretary"):
        if (directory / base).exists() or (directory / f"{base}.exe").exists():
            return True
    return False


def _find_bin_dir() -> Path | None:
    """找到 pip 安装脚本的 bin 目录（含 Windows 用户级 Scripts）"""
    # 方法1: 从 sys.executable 推断
    # venv: .../venv/bin/python → .../venv/bin/ (Unix) 或 .../venv/Scripts/ (Windows)
    # system: /usr/bin/python → ~/.local/bin/ (user install, Unix)
    venv_bin = Path(sys.executable).parent
    if _has_kai_or_secretary(venv_bin):
        return venv_bin

    # 方法2: Unix 常见的 user bin
    user_bin = Path.home() / ".local" / "bin"
    if user_bin.exists() and _has_kai_or_secretary(user_bin):
        return user_bin

    # 方法3: Windows 用户级 pip 安装目录 (%APPDATA%\Python\Python3x\Scripts)
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            python_appdata = Path(appdata) / "Python"
            if python_appdata.exists():
                for sub in python_appdata.iterdir():
                    if sub.is_dir() and sub.name.startswith("Python"):
                        scripts = sub / "Scripts"
                        if scripts.exists() and _has_kai_or_secretary(scripts):
                            return scripts

    # 方法4: 从 PATH 搜索
    for name in ["kai", "secretary"]:
        found = shutil.which(name)
        if found:
            return Path(found).parent

    return venv_bin if venv_bin.exists() else None


def _create_wrapper_script(dest: Path):
    """创建一个 Python wrapper 脚本"""
    script = """#!/usr/bin/env python3
# Auto-generated by kai name command
from secretary.cli import main
main()
"""
    dest.write_text(script, encoding="utf-8")
    dest.chmod(0o755)

