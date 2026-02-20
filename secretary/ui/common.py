"""
UI 公共工具函数

提取 dashboard 和 report_viewer 中的公共代码，便于复用。
"""
import sys
from typing import Optional

# Windows 和 Unix 的键盘输入处理
if sys.platform == "win32":
    try:
        import msvcrt
    except ImportError:
        msvcrt = None
else:
    try:
        import select
        import termios
        import tty
    except ImportError:
        select = None
        termios = None
        tty = None


def setup_keyboard_input():
    """
    设置键盘输入（Unix 系统）
    
    Returns:
        (original_settings, success): 原始终端设置和是否成功
    """
    if sys.platform != "win32" and termios and tty:
        try:
            original_settings = termios.tcgetattr(sys.stdin)
            tty.setraw(sys.stdin.fileno())
            return (original_settings, True)
        except Exception:
            return (None, False)
    return (None, True)  # Windows 不需要设置


def restore_keyboard_input(original_settings):
    """恢复键盘输入设置（Unix 系统）"""
    if sys.platform != "win32" and termios and original_settings:
        try:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, original_settings)
        except Exception:
            pass


def read_key(timeout: float = 0.1) -> Optional[str]:
    """
    非阻塞读取单个按键
    
    Args:
        timeout: 超时时间（秒）
    
    Returns:
        读取到的字符，如果没有输入则返回 None
    """
    if sys.platform == "win32":
        if msvcrt:
            if msvcrt.kbhit():
                try:
                    key = msvcrt.getch()
                    if isinstance(key, bytes):
                        return key.decode("utf-8", errors="ignore")
                    return key
                except Exception:
                    pass
        return None
    else:
        if select and select.select([sys.stdin], [], [], timeout)[0]:
            try:
                return sys.stdin.read(1)
            except Exception:
                pass
        return None

