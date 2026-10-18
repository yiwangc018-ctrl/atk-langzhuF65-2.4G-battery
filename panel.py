# -*- coding: utf-8 -*-
"""panel.py —— 简洁电量面板（tkinter，标准库）"""
from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import ttk

import devices

BG = "#16181c"
CARD = "#20242b"
FG = "#e8eaed"
SUB = "#9aa0a6"

ITEMS = [
    ("mouse", "ATK 鼠标", "373B:101A · 1K 接收器"),
    ("keyboard", "狼蛛 F65 键盘", "3554:FA09 · 2.4G 接收器"),
    ("headset", "竹林鸟玄武耳机", "4C4A:4155 · ZhuAudio ZG2"),
]


def level_color(level):
    if level is None:
        return "#8a9099"
    if level >= 60:
        return "#5ddc8a"
    if level >= 30:
        return "#f5c451"
    return "#ff6b6b"


class Panel:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("设备电量")
        root.configure(bg=BG)
        root.geometry("440x380")
        root.resizable(False, False)

        self.readings = {}
        self.last_good = {}
        self.cards = {}

        header = tk.Frame(root, bg=BG)
        header.pack(fill="x", padx=18, pady=(16, 8))
        tk.Label(header, text="设备电量", bg=BG, fg=FG,
                 font=("Microsoft YaHei UI", 15, "bold")).pack(side="left")
        self.time_label = tk.Label(header, text="", bg=BG, fg=SUB,
                                   font=("Microsoft YaHei UI", 9))
        self.time_label.pack(side="right")

        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=4)
        for key, title, sub in ITEMS:
            self.cards[key] = self._card(body, key, title, sub)

        bar = tk.Frame(root, bg=BG)
        bar.pack(fill="x", padx=16, pady=12)
        self.refresh_btn = tk.Button(bar, text="刷新", command=self.refresh,
                                     bg="#2d333c", fg=FG, relief="flat",
                                     activebackground="#3a414c",
                                     activeforeground=FG, padx=18, pady=4,
                                     font=("Microsoft YaHei UI", 9))
        self.refresh_btn.pack(side="right")
        self.status = tk.Label(bar, text="", bg=BG, fg=SUB,
                               font=("Microsoft YaHei UI", 9))
        self.status.pack(side="left")

        self.busy = False
        self.refresh()
        self.root.after(60_000, self._tick)

    def _card(self, parent, key, title, sub):
        frame = tk.Frame(parent, bg=CARD)
        frame.pack(fill="x", pady=5)
        left = tk.Frame(frame, bg=CARD)
        left.pack(side="left", padx=14, pady=10)
        tk.Label(left, text=title, bg=CARD, fg=FG, anchor="w",
                 font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        tk.Label(left, text=sub, bg=CARD, fg=SUB, anchor="w",
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w")
        detail = tk.Label(left, text="", bg=CARD, fg=SUB, anchor="w",
                          font=("Microsoft YaHei UI", 8))
        detail.pack(anchor="w", pady=(4, 0))

        right = tk.Frame(frame, bg=CARD)
        right.pack(side="right", padx=14)
        big = tk.Label(right, text="--", bg=CARD, fg=SUB,
                       font=("Consolas", 22, "bold"))
        big.pack()
        bar_bg = tk.Frame(right, bg="#2c313a", width=110, height=5)
        bar_bg.pack(pady=(2, 0))
        bar_bg.pack_propagate(False)
        fill = tk.Frame(bar_bg, bg=SUB, width=0, height=5)
        fill.place(x=0, y=0)
        return {"big": big, "fill": fill, "bar_bg": bar_bg, "detail": detail}

    def refresh(self):
        if self.busy:
            return
        self.busy = True
        self.status.config(text="读取中…")
        self.refresh_btn.config(state="disabled")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        try:
            data = devices.read_all()
        except Exception as e:  # noqa: BLE001
            data = {k: devices.Reading(note=f"异常 {e}") for k, _, _ in ITEMS}
        self.root.after(0, lambda: self._apply(data))

    def _apply(self, data):
        self.busy = False
        self.refresh_btn.config(state="normal")
        self.readings = data
        for key, _, _ in ITEMS:
            r = data.get(key) or devices.Reading()
            card = self.cards[key]
            if r.ok and r.level is not None:
                self.last_good[key] = (r.level, time.strftime("%H:%M:%S"))
                shown = r.level
            else:
                last = self.last_good.get(key)
                shown = last[0] if last else None
            color = level_color(shown)
            card["big"].config(text=(f"{shown}%" if shown is not None else "--"),
                               fg=color if shown is not None else SUB)
            width = int(110 * (shown or 0) / 100)
            card["fill"].config(bg=color, width=width)
            if r.ok:
                bits = []
                if r.voltage_mv:
                    bits.append(f"{r.voltage_mv} mV")
                if r.charging:
                    bits.append("充电中")
                if r.note:
                    bits.append(r.note)
                card["detail"].config(text=" · ".join(bits) or "已连接")
            elif shown is not None:
                card["detail"].config(
                    text=f"上次 {self.last_good[key][1]} · {r.note or '读不到'}")
            else:
                card["detail"].config(text=r.note or "未连接")
        self.time_label.config(text=time.strftime("%H:%M:%S 更新"))
        self.status.config(text="")

    def _tick(self):
        self.refresh()
        self.root.after(60_000, self._tick)


def main():
    root = tk.Tk()
    try:
        root.call("tk", "scaling", 1.3)
    except tk.TclError:
        pass
    Panel(root)
    root.mainloop()


if __name__ == "__main__":
    main()
