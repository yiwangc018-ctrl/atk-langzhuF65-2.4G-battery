# -*- coding: utf-8 -*-
"""tray_app.py —— 三台设备电量托盘（纯 ctypes + tkinter，零第三方依赖）

托盘里三个数字图标：ATK 鼠标 / 狼蛛 F65 键盘 / 竹林鸟玄武耳机。
* 左键图标：立刻刷新
* 右键图标：菜单（刷新 / 打开面板 / 退出）
* 悬停：tooltip 显示设备名、电量、更新时间
* 每 60 秒自动刷新（SetTimer 驱动，空闲不占 CPU）

用法：
    pythonw tray_app.py          静默驻留
    python  tray_app.py --debug  前台运行并打印
    python  tray_app.py --once   只打印一次
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
import threading
import time
import traceback
from ctypes import wintypes
from typing import Optional

import devices
import win_icon
import win_shell

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "设备电量托盘"
MUTEX_NAME = "Local\\BatteryTray_ATK_AULA_ZHU"
WND_CLASS = "BatteryTrayWnd"
FROZEN = getattr(sys, "frozen", False)
LNK_NAME = "设备电量托盘.lnk"

USER32 = ctypes.WinDLL("user32", use_last_error=True)
KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
SHELL32 = ctypes.WinDLL("shell32", use_last_error=True)

WM_DESTROY = 0x0002
WM_TIMER = 0x0113
WM_COMMAND = 0x0111
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_APP = 0x8000
MSG_TRAY_CALLBACK = WM_APP + 1
MSG_REFRESH_DONE = WM_APP + 2

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04

MF_STRING, MF_SEPARATOR, MF_CHECKED = 0x0000, 0x0800, 0x0008
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100

REFRESH_INTERVAL_MS = 60_000


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint,
                             ctypes.c_size_t, ctypes.c_ssize_t)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT), ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int), ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p), ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p), ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", ctypes.c_void_p),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("hWnd", ctypes.c_void_p),
        ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT), ("hIcon", ctypes.c_void_p),
        ("szTip", ctypes.c_wchar * 128), ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD), ("szInfo", ctypes.c_wchar * 256),
        ("uVersion", wintypes.UINT), ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", wintypes.DWORD), ("guidItem", GUID),
        ("hBalloonIcon", ctypes.c_void_p),
    ]


class MSG(ctypes.Structure):
    _fields_ = [("hWnd", ctypes.c_void_p), ("message", wintypes.UINT),
                ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
                ("time", wintypes.DWORD), ("pt_x", wintypes.LONG),
                ("pt_y", wintypes.LONG)]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


USER32.RegisterClassExW.restype = wintypes.WORD
USER32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
USER32.CreateWindowExW.restype = ctypes.c_void_p
USER32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
USER32.DefWindowProcW.restype = ctypes.c_ssize_t
USER32.DefWindowProcW.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                  ctypes.c_size_t, ctypes.c_ssize_t]
USER32.DestroyWindow.argtypes = [ctypes.c_void_p]
USER32.SetTimer.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.UINT,
                            ctypes.c_void_p]
USER32.KillTimer.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
USER32.GetMessageW.argtypes = [ctypes.POINTER(MSG), ctypes.c_void_p,
                               wintypes.UINT, wintypes.UINT]
USER32.PostQuitMessage.argtypes = [ctypes.c_int]
USER32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
USER32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
USER32.CreatePopupMenu.restype = ctypes.c_void_p
USER32.AppendMenuW.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_size_t,
                               wintypes.LPCWSTR]
USER32.TrackPopupMenu.restype = ctypes.c_int
USER32.TrackPopupMenu.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                                  ctypes.c_void_p]
USER32.DestroyMenu.argtypes = [ctypes.c_void_p]
USER32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
USER32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
USER32.PostMessageW.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                ctypes.c_size_t, ctypes.c_ssize_t]
USER32.RegisterWindowMessageW.restype = wintypes.UINT
USER32.SetProcessDPIAware.restype = wintypes.BOOL

SHELL32.Shell_NotifyIconW.restype = wintypes.BOOL
SHELL32.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                      ctypes.POINTER(NOTIFYICONDATAW)]

KERNEL32.GetModuleHandleW.restype = ctypes.c_void_p
KERNEL32.CreateMutexW.restype = ctypes.c_void_p
KERNEL32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]

TRAY = [
    # (uID, key, 名称, 是否带底板)
    (1, "mouse", "ATK 鼠标", False),
    (2, "keyboard", "狼蛛 F65 键盘", True),
]


def instance_running() -> bool:
    """托盘是否已经在跑（用 OpenMutex，不会创建锁）。"""
    SYNCHRONIZE = 0x00100000
    KERNEL32.OpenMutexW.restype = wintypes.HANDLE
    KERNEL32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    h = KERNEL32.OpenMutexW(SYNCHRONIZE, False, MUTEX_NAME)
    if h:
        KERNEL32.CloseHandle(h)
        return True
    return False


def spawn_panel() -> None:
    """另起进程打开面板（打包后是同一个 exe 带 --panel）。"""
    import subprocess
    if FROZEN:
        cmd = [sys.executable, "--panel"]
    else:
        cmd = [sys.executable.replace("python.exe", "pythonw.exe"),
               os.path.join(BASE_DIR, "panel.py")]
    subprocess.Popen(cmd, cwd=BASE_DIR, creationflags=0x00000008)


class TrayApp:
    def __init__(self, debug: bool = False):
        self.debug = debug
        self.hwnd: Optional[int] = None
        self.wndproc_ref = None
        self.busy = False
        self.readings = {k: devices.Reading(note="正在读取…") for _, k, _, _ in TRAY}
        self.last_good = {}   # key -> (level, charging, voltage, 时间字符串)
        self.icons_added = False
        self.last_update = ""
        self.show_panel_on_start = False
        self.taskbar_msg = USER32.RegisterWindowMessageW("TaskbarCreated")

    def log(self, msg: str) -> None:
        if self.debug:
            print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)

    # ---------------- 图标 ----------------
    @staticmethod
    def _color(level: Optional[int]) -> tuple[int, int, int]:
        if level is None:
            return (150, 155, 160)
        if level >= 60:
            return (93, 220, 138)
        if level >= 30:
            return (245, 196, 81)
        return (255, 107, 107)

    def _nid(self, uid: int) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = uid
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = MSG_TRAY_CALLBACK
        return nid

    def _last(self, key: str):
        return self.last_good.get(key)

    def _tip(self, key: str, title: str, r: devices.Reading) -> str:
        if r.ok:
            bits = [f"{title}：{r.level}%"]
            if r.voltage_mv:
                bits.append(f"{r.voltage_mv}mV")
            if r.charging:
                bits.append("充电中")
            if self.last_update:
                bits.append(self.last_update)
            return "  ".join(bits)
        last = self._last(key)
        if last:
            return f"{title}：{last[0]}%（上次 {last[3]}；{r.note or '当前读不到'}）"
        return f"{title}：{r.note or '未知'}"

    def _icon_text(self, r: devices.Reading, key: str = "") -> str:
        if r.ok and r.level is not None:
            return str(r.level)
        last = self._last(key)
        if last:
            return str(last[0])
        return "?"

    def _apply(self, action: int, uid: int, key: str, title: str,
               plate: bool) -> bool:
        r = self.readings[key]
        nid = self._nid(uid)
        nid.hIcon = win_icon.make_hicon(self._icon_text(r, key),
                                        self._color(r.level if r.ok else
                                                    (self._last(key) or (None,))[0]),
                                        plate)
        nid.szTip = self._tip(key, title, r)[:127]
        ok = bool(SHELL32.Shell_NotifyIconW(action, ctypes.byref(nid)))
        self.log(f"icon {uid} {title}: level={r.level} ok={ok}")
        return ok

    def add_icons(self) -> None:
        for uid, key, title, plate in TRAY:
            self._apply(NIM_ADD, uid, key, title, plate)
        self.icons_added = True

    def update_icons(self) -> None:
        if not self.icons_added:
            self.add_icons()
            return
        for uid, key, title, plate in TRAY:
            self._apply(NIM_MODIFY, uid, key, title, plate)

    def remove_icons(self) -> None:
        for uid, _, _, _ in TRAY:
            nid = self._nid(uid)
            nid.uFlags = 0
            SHELL32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        self.icons_added = False

    # ---------------- 刷新 ----------------
    def refresh_now(self) -> None:
        if self.busy or not self.hwnd:
            return
        self.busy = True
        threading.Thread(target=self._worker, args=(self.hwnd,), daemon=True).start()

    def _worker(self, hwnd: int) -> None:
        try:
            self.readings = devices.read_all()
        except Exception:
            self.log("读取异常:\n" + traceback.format_exc())
        USER32.PostMessageW(hwnd, MSG_REFRESH_DONE, 0, 0)

    def on_refresh_done(self) -> None:
        self.busy = False
        self.last_update = time.strftime("%H:%M:%S")
        for key, r in self.readings.items():
            if r.ok and r.level is not None:
                self.last_good[key] = (r.level, r.charging, r.voltage_mv,
                                       self.last_update)
        parts = " ".join(f"{k}={r.level}/{r.note}" for k, r in self.readings.items())
        self.log(f"刷新完成 {parts}")
        self.update_icons()

    # ---------------- 菜单 ----------------
    def _lnk_path(self) -> str:
        return os.path.join(win_shell.startup_dir(), LNK_NAME)

    def autostart_enabled(self) -> bool:
        return win_shell.shortcut_exists(self._lnk_path())

    def set_autostart(self, enable: bool) -> bool:
        lnk = self._lnk_path()
        if not enable:
            return win_shell.remove_shortcut(lnk) if os.path.exists(lnk) else True
        if FROZEN:
            target = sys.executable
            args = ""
        else:
            target = sys.executable.replace("python.exe", "pythonw.exe")
            args = f'"{os.path.join(BASE_DIR, "tray_app.py")}"'
        return win_shell.create_shortcut(lnk, target, args, BASE_DIR,
                                         "设备电量托盘", target)

    def show_menu(self) -> None:
        menu = USER32.CreatePopupMenu()
        USER32.AppendMenuW(menu, MF_STRING, 1, "刷新")
        USER32.AppendMenuW(menu, MF_STRING, 2, "打开面板")
        USER32.AppendMenuW(menu, MF_STRING | (MF_CHECKED if self.autostart_enabled() else 0),
                           4, "开机自启")
        USER32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        USER32.AppendMenuW(menu, MF_STRING, 3, "退出")
        pt = POINT()
        USER32.GetCursorPos(ctypes.byref(pt))
        USER32.SetForegroundWindow(self.hwnd)
        cmd = USER32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                                    pt.x, pt.y, 0, self.hwnd, None)
        USER32.DestroyMenu(menu)
        if cmd == 1:
            self.refresh_now()
        elif cmd == 2:
            self.open_panel()
        elif cmd == 3:
            self.quit()
        elif cmd == 4:
            ok = self.set_autostart(not self.autostart_enabled())
            self.log(f"开机自启 -> {ok}")

    def open_panel(self) -> None:
        try:
            spawn_panel()
            self.log("已打开面板")
        except Exception as e:
            self.log(f"打开面板失败：{e}")

    def quit(self) -> None:
        self.log("退出")
        self.remove_icons()
        try:
            win_icon.destroy_all()
            win_icon.cleanup_files()
        except Exception:
            pass
        if self.hwnd:
            USER32.DestroyWindow(self.hwnd)

    # ---------------- 窗口过程 ----------------
    def wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == MSG_TRAY_CALLBACK:
            event = int(lparam) & 0xFFFF
            if event == WM_RBUTTONUP:
                self.show_menu()
            elif event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self.refresh_now()
            return 0
        if msg == MSG_REFRESH_DONE:
            self.on_refresh_done()
            return 0
        if msg == WM_TIMER:
            self.refresh_now()
            return 0
        if msg == self.taskbar_msg:
            self.add_icons()
            return 0
        if msg == WM_DESTROY:
            USER32.PostQuitMessage(0)
            return 0
        return USER32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # ---------------- 启动 ----------------
    def run(self) -> int:
        hinst = KERNEL32.GetModuleHandleW(None)
        self.wndproc_ref = WNDPROC(self.wnd_proc)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self.wndproc_ref
        wc.hInstance = hinst
        wc.lpszClassName = WND_CLASS
        if not USER32.RegisterClassExW(ctypes.byref(wc)):
            err = ctypes.get_last_error()
            if err not in (0, 1410):
                self.log(f"RegisterClassExW 失败 err={err}")
                return 1
        hwnd = USER32.CreateWindowExW(0, WND_CLASS, APP_NAME, 0, 0, 0, 0, 0,
                                      None, None, hinst, None)
        if not hwnd:
            self.log(f"CreateWindowExW 失败 err={ctypes.get_last_error()}")
            return 1
        self.hwnd = hwnd
        self.add_icons()
        USER32.SetTimer(hwnd, 1, REFRESH_INTERVAL_MS, None)
        self.refresh_now()
        if self.show_panel_on_start:
            try:
                spawn_panel()
            except Exception:
                self.log("打开面板失败\n" + traceback.format_exc())
        msg = MSG()
        while USER32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            USER32.TranslateMessage(ctypes.byref(msg))
            USER32.DispatchMessageW(ctypes.byref(msg))
        self.log("退出消息循环")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--debug", action="store_true", help="前台运行并输出")
    parser.add_argument("--once", action="store_true", help="只打印一次")
    parser.add_argument("--panel", action="store_true", help="只打开面板")
    parser.add_argument("--show", action="store_true",
                        help="启动托盘并打开面板；已经在跑就只打开面板")
    parser.add_argument("--autostart", choices=["on", "off", "status"],
                        help="设置/查看开机自启")
    args = parser.parse_args()

    if args.panel:
        import panel
        panel.main()
        return 0

    if args.autostart:
        app = TrayApp(debug=False)
        if args.autostart == "status":
            print("开机自启：" + ("开" if app.autostart_enabled() else "关"))
            print("快捷方式：" + app._lnk_path())
        else:
            ok = app.set_autostart(args.autostart == "on")
            print("设置成功" if ok else "设置失败")
        return 0

    if args.once:
        for key, r in devices.read_all().items():
            if r.ok:
                extra = f" {r.voltage_mv}mV" if r.voltage_mv else ""
                print(f"{key:9} {r.level:3d}%{extra}")
            else:
                print(f"{key:9} --    {r.note}")
        return 0

    show_panel = False
    if args.show:
        if instance_running():
            spawn_panel()
            return 0
        show_panel = True

    KERNEL32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.get_last_error() == 183:
        print("托盘已经在运行了（右下角那三个数字图标）")
        return 0

    try:
        USER32.SetProcessDPIAware()
    except Exception:
        pass

    app = TrayApp(debug=args.debug)
    app.show_panel_on_start = show_panel
    try:
        return app.run()
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
