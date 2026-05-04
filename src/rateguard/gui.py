"""Interactive visualizer for rateguard rate limiters.

Launch::

    python -m rateguard.gui
    # or, if installed:
    rateguard-gui

The GUI shows live token-bucket and sliding-window state and lets you
simulate acquire calls to explore the behaviour of each primitive.
"""

from __future__ import annotations

import time
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from typing import List, Optional, Tuple

from rateguard import TokenBucket, SlidingWindow, __version__

# ── colour palette (works on light and dark OS themes) ──────────────────────
_BG = "#1e1e2e"
_SURFACE = "#2a2a3e"
_ACCENT_GREEN = "#a6e3a1"
_ACCENT_ORANGE = "#fab387"
_ACCENT_RED = "#f38ba8"
_ACCENT_BLUE = "#89b4fa"
_TEXT = "#cdd6f4"
_SUBTEXT = "#a6adc8"
_OUTLINE = "#45475a"

_FONT_MONO = ("Courier", 9)
_FONT_LABEL = ("TkDefaultFont", 9)
_FONT_BOLD = ("TkDefaultFont", 10, "bold")
_FONT_BIG = ("TkDefaultFont", 18, "bold")


def _fill_color(ratio: float) -> str:
    """Return a hex colour representing a fill level 0..1."""
    if ratio > 0.55:
        return _ACCENT_GREEN
    if ratio > 0.25:
        return _ACCENT_ORANGE
    return _ACCENT_RED


# ── Token Bucket panel ───────────────────────────────────────────────────────

class _TokenBucketPanel(ttk.Frame):
    """Visualizer panel for a single :class:`TokenBucket` instance."""

    _TANK_W = 120
    _TANK_H = 220
    _UPDATE_MS = 80  # redraw every ~80 ms → ~12 fps

    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent)
        self._bucket: Optional[TokenBucket] = None
        self._log_lines: List[str] = []
        self._build_ui()
        self._apply_config()
        self._schedule_update()

    # ------------------------------------------------------------------ build

    def _build_ui(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        # ── left column ───────────────────────────────────────────────
        left = ttk.Frame(self, padding=10)
        left.grid(row=0, column=0, sticky="ns")

        # configuration
        cfg = ttk.LabelFrame(left, text="Configuration", padding=8)
        cfg.pack(fill="x", pady=(0, 10))

        ttk.Label(cfg, text="Rate (tok/s):").grid(row=0, column=0, sticky="w", pady=3)
        self._rate_var = tk.StringVar(value="5.0")
        ttk.Entry(cfg, textvariable=self._rate_var, width=10).grid(
            row=0, column=1, padx=(6, 0), pady=3
        )

        ttk.Label(cfg, text="Burst (max):").grid(row=1, column=0, sticky="w", pady=3)
        self._burst_var = tk.StringVar(value="10")
        ttk.Entry(cfg, textvariable=self._burst_var, width=10).grid(
            row=1, column=1, padx=(6, 0), pady=3
        )

        ttk.Button(cfg, text="Apply", command=self._apply_config).grid(
            row=2, column=0, columnspan=2, pady=(6, 0)
        )

        # acquire controls
        acq = ttk.LabelFrame(left, text="Acquire", padding=8)
        acq.pack(fill="x", pady=(0, 10))

        ttk.Label(acq, text="Tokens:").grid(row=0, column=0, sticky="w", pady=3)
        self._acq_var = tk.IntVar(value=1)
        self._spin = ttk.Spinbox(
            acq, from_=1, to=9999, textvariable=self._acq_var, width=8
        )
        self._spin.grid(row=0, column=1, padx=(6, 0), pady=3)

        ttk.Button(acq, text="Acquire Tokens", command=self._do_acquire).grid(
            row=1, column=0, columnspan=2, pady=(6, 0)
        )

        ttk.Button(left, text="Reset Bucket", command=self._do_reset).pack(
            fill="x", pady=(0, 6)
        )

        # status label
        self._status_var = tk.StringVar(value="—")
        ttk.Label(left, textvariable=self._status_var, wraplength=160,
                  font=_FONT_LABEL).pack(pady=4)

        # ── right column ──────────────────────────────────────────────
        right = ttk.Frame(self, padding=10)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        # canvas + info
        viz = ttk.LabelFrame(right, text="Token Level", padding=6)
        viz.grid(row=0, column=0, sticky="ew")
        viz.columnconfigure(0, weight=1)

        self._canvas = tk.Canvas(
            viz, width=self._TANK_W + 60, height=self._TANK_H + 40,
            bg=_BG, highlightthickness=0
        )
        self._canvas.pack()

        self._info_var = tk.StringVar(value="")
        ttk.Label(right, textvariable=self._info_var, font=_FONT_LABEL,
                  foreground=_SUBTEXT).grid(row=1, column=0, pady=(4, 0))

        # log
        log_frame = ttk.LabelFrame(right, text="Activity Log", padding=4)
        log_frame.grid(row=2, column=0, sticky="ew", pady=(10, 0))

        self._log_text = tk.Text(
            log_frame, height=7, width=44, state="disabled",
            font=_FONT_MONO, bg=_SURFACE, fg=_TEXT,
            relief="flat", highlightthickness=0
        )
        sb = ttk.Scrollbar(log_frame, command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=sb.set)
        self._log_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    # ----------------------------------------------------------------- actions

    def _apply_config(self) -> None:
        try:
            rate = float(self._rate_var.get())
            burst = int(self._burst_var.get())
            self._bucket = TokenBucket(rate=rate, burst=burst)
            # clamp spinbox max
            self._spin.configure(to=burst)
            if self._acq_var.get() > burst:
                self._acq_var.set(burst)
            self._status_var.set(f"Bucket ready  rate={rate} burst={burst}")
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror("Configuration error", str(exc), parent=self)

    def _do_acquire(self) -> None:
        if self._bucket is None:
            return
        try:
            tokens = int(self._acq_var.get())
            wait = self._bucket.acquire(tokens)
            ts = time.strftime("%H:%M:%S")
            if wait == 0.0:
                self._status_var.set(f"Admitted — {tokens} tok immediate")
                self._append_log(f"[{ts}] ✓ acquired {tokens:>4} tok  wait=0.000s")
            else:
                self._status_var.set(f"Reserved — wait {wait:.3f}s")
                self._append_log(
                    f"[{ts}] ↻ reserved {tokens:>4} tok  wait={wait:.3f}s"
                )
        except ValueError as exc:
            messagebox.showerror("Acquire error", str(exc), parent=self)

    def _do_reset(self) -> None:
        if self._bucket is not None:
            self._bucket.reset()
            self._status_var.set("Bucket reset to full")
            self._append_log(f"[{time.strftime('%H:%M:%S')}] ⟳ reset")

    def _append_log(self, line: str) -> None:
        self._log_text.configure(state="normal")
        self._log_text.insert("end", line + "\n")
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    # ----------------------------------------------------------------- drawing

    def _schedule_update(self) -> None:
        self._draw()
        self.after(self._UPDATE_MS, self._schedule_update)

    def _draw(self) -> None:
        if self._bucket is None:
            return

        c = self._canvas
        c.delete("all")

        avail = self._bucket.available_tokens
        burst = self._bucket.burst
        ratio = avail / burst if burst else 0.0

        cw = int(c["width"])
        ch = int(c["height"])

        # tank geometry
        tx1 = (cw - self._TANK_W) // 2
        ty1 = 20
        tx2 = tx1 + self._TANK_W
        ty2 = ty1 + self._TANK_H

        # background of tank
        c.create_rectangle(tx1, ty1, tx2, ty2, fill=_SURFACE, outline=_OUTLINE, width=2)

        # fill liquid
        fill_h = int(self._TANK_H * ratio)
        if fill_h > 0:
            color = _fill_color(ratio)
            c.create_rectangle(
                tx1 + 2, ty2 - fill_h,
                tx2 - 2, ty2 - 2,
                fill=color, outline=""
            )
            # shimmer line
            c.create_line(
                tx1 + 2, ty2 - fill_h,
                tx2 - 2, ty2 - fill_h,
                fill="white", width=1
            )

        # percentage ticks on right side
        for pct in range(0, 101, 25):
            y = ty2 - int(self._TANK_H * pct / 100)
            c.create_line(tx2, y, tx2 + 8, y, fill=_OUTLINE)
            c.create_text(
                tx2 + 22, y, text=f"{pct}%", fill=_SUBTEXT,
                font=("TkDefaultFont", 7), anchor="center"
            )

        # overlay text: token count
        mid_x = (tx1 + tx2) // 2
        mid_y = (ty1 + ty2) // 2
        c.create_text(mid_x, mid_y - 12, text=f"{avail:.1f}", fill="white",
                      font=_FONT_BIG)
        c.create_text(mid_x, mid_y + 16, text=f"/ {burst} tokens",
                      fill=_SUBTEXT, font=("TkDefaultFont", 9))

        self._info_var.set(
            f"rate {self._bucket.rate:.1f} tok/s  ·  "
            f"fill {ratio * 100:.0f}%"
        )


# ── Sliding Window panel ─────────────────────────────────────────────────────

class _SlidingWindowPanel(ttk.Frame):
    """Visualizer panel for a single :class:`SlidingWindow` instance."""

    _TIMELINE_W = 480
    _TIMELINE_H = 60
    _UPDATE_MS = 100

    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent)
        self._window: Optional[SlidingWindow] = None
        # local mirror of admitted timestamps for the timeline canvas
        self._ts_lock = threading.Lock()
        self._admitted: List[float] = []
        self._build_ui()
        self._apply_config()
        self._schedule_update()

    # ------------------------------------------------------------------ build

    def _build_ui(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        # ── left column ───────────────────────────────────────────────
        left = ttk.Frame(self, padding=10)
        left.grid(row=0, column=0, sticky="ns")

        cfg = ttk.LabelFrame(left, text="Configuration", padding=8)
        cfg.pack(fill="x", pady=(0, 10))

        ttk.Label(cfg, text="Max calls:").grid(row=0, column=0, sticky="w", pady=3)
        self._max_var = tk.StringVar(value="5")
        ttk.Entry(cfg, textvariable=self._max_var, width=10).grid(
            row=0, column=1, padx=(6, 0), pady=3
        )

        ttk.Label(cfg, text="Window (s):").grid(row=1, column=0, sticky="w", pady=3)
        self._win_var = tk.StringVar(value="10.0")
        ttk.Entry(cfg, textvariable=self._win_var, width=10).grid(
            row=1, column=1, padx=(6, 0), pady=3
        )

        ttk.Button(cfg, text="Apply", command=self._apply_config).grid(
            row=2, column=0, columnspan=2, pady=(6, 0)
        )

        ttk.Button(left, text="Acquire Call", command=self._do_acquire).pack(
            fill="x", pady=(0, 6)
        )
        ttk.Button(left, text="Reset Window", command=self._do_reset).pack(
            fill="x", pady=(0, 10)
        )

        self._status_var = tk.StringVar(value="—")
        ttk.Label(left, textvariable=self._status_var, wraplength=160,
                  font=_FONT_LABEL).pack(pady=4)

        # ── right column ──────────────────────────────────────────────
        right = ttk.Frame(self, padding=10)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        viz = ttk.LabelFrame(right, text="Call Timeline", padding=6)
        viz.grid(row=0, column=0, sticky="ew")

        self._canvas = tk.Canvas(
            viz,
            width=self._TIMELINE_W,
            height=self._TIMELINE_H + 60,
            bg=_BG, highlightthickness=0
        )
        self._canvas.pack()

        self._info_var = tk.StringVar(value="")
        ttk.Label(right, textvariable=self._info_var, font=_FONT_LABEL,
                  foreground=_SUBTEXT).grid(row=1, column=0, pady=(4, 0))

        log_frame = ttk.LabelFrame(right, text="Activity Log", padding=4)
        log_frame.grid(row=2, column=0, sticky="ew", pady=(10, 0))

        self._log_text = tk.Text(
            log_frame, height=7, width=44, state="disabled",
            font=_FONT_MONO, bg=_SURFACE, fg=_TEXT,
            relief="flat", highlightthickness=0
        )
        sb = ttk.Scrollbar(log_frame, command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=sb.set)
        self._log_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    # ----------------------------------------------------------------- actions

    def _apply_config(self) -> None:
        try:
            max_calls = int(self._max_var.get())
            win_secs = float(self._win_var.get())
            self._window = SlidingWindow(max_calls=max_calls,
                                         window_seconds=win_secs)
            with self._ts_lock:
                self._admitted.clear()
            self._status_var.set(
                f"Window ready  max={max_calls}  window={win_secs}s"
            )
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror("Configuration error", str(exc), parent=self)

    def _do_acquire(self) -> None:
        if self._window is None:
            return
        wait = self._window.acquire()
        ts_now = time.monotonic()
        ts_str = time.strftime("%H:%M:%S")
        if wait == 0.0:
            with self._ts_lock:
                self._admitted.append(ts_now)
            self._status_var.set("Admitted — immediate")
            self._append_log(f"[{ts_str}] ✓ admitted  wait=0.000s")
        else:
            self._status_var.set(f"Blocked — retry in {wait:.3f}s")
            self._append_log(f"[{ts_str}] ✗ blocked   retry in {wait:.3f}s")

    def _do_reset(self) -> None:
        if self._window is not None:
            self._window.reset()
            with self._ts_lock:
                self._admitted.clear()
            self._status_var.set("Window cleared")
            self._append_log(f"[{time.strftime('%H:%M:%S')}] ⟳ reset")

    def _append_log(self, line: str) -> None:
        self._log_text.configure(state="normal")
        self._log_text.insert("end", line + "\n")
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    # ----------------------------------------------------------------- drawing

    def _schedule_update(self) -> None:
        self._draw()
        self.after(self._UPDATE_MS, self._schedule_update)

    def _draw(self) -> None:
        if self._window is None:
            return

        c = self._canvas
        c.delete("all")

        now = time.monotonic()
        win = self._window.window_seconds
        max_calls = self._window.max_calls
        used = self._window.used_calls
        avail = max_calls - used
        ratio = used / max_calls if max_calls else 0.0

        cw = int(c["width"])
        # ── timeline bar ──────────────────────────────────────────────
        bar_x1 = 20
        bar_x2 = cw - 20
        bar_y = 30
        bar_h = self._TIMELINE_H
        bar_w = bar_x2 - bar_x1

        # track background
        c.create_rectangle(bar_x1, bar_y, bar_x2, bar_y + bar_h,
                           fill=_SURFACE, outline=_OUTLINE, width=1)

        # prune local mirror
        with self._ts_lock:
            cutoff = now - win
            self._admitted = [t for t in self._admitted if t > cutoff]
            timestamps = list(self._admitted)

        # draw call markers
        for ts in timestamps:
            age = now - ts           # 0 = just happened, win = about to expire
            x = bar_x1 + bar_w * (1.0 - age / win)
            dot_r = 6
            dot_color = _fill_color(1.0 - ratio)
            c.create_oval(
                x - dot_r, bar_y + bar_h // 2 - dot_r,
                x + dot_r, bar_y + bar_h // 2 + dot_r,
                fill=dot_color, outline=""
            )

        # time labels
        c.create_text(bar_x1, bar_y + bar_h + 10, text=f"−{win:.0f}s",
                      fill=_SUBTEXT, font=("TkDefaultFont", 8), anchor="w")
        c.create_text(bar_x2, bar_y + bar_h + 10, text="now",
                      fill=_SUBTEXT, font=("TkDefaultFont", 8), anchor="e")

        # ── usage gauge row ───────────────────────────────────────────
        gauge_y = bar_y + bar_h + 30
        gauge_h = 16
        gauge_x1 = bar_x1
        gauge_x2 = bar_x2

        c.create_rectangle(gauge_x1, gauge_y, gauge_x2, gauge_y + gauge_h,
                           fill=_SURFACE, outline=_OUTLINE)
        if ratio > 0:
            fill_w = int((gauge_x2 - gauge_x1) * ratio)
            c.create_rectangle(
                gauge_x1, gauge_y,
                gauge_x1 + fill_w, gauge_y + gauge_h,
                fill=_fill_color(ratio), outline=""
            )
        c.create_text(
            (gauge_x1 + gauge_x2) // 2, gauge_y + gauge_h // 2,
            text=f"{used} / {max_calls} calls used",
            fill="white", font=("TkDefaultFont", 8)
        )

        self._info_var.set(
            f"{avail} slot{'s' if avail != 1 else ''} remaining  ·  "
            f"window {win:.1f}s"
        )


# ── Main application window ──────────────────────────────────────────────────

class RateGuardApp:
    """Top-level Tk application window."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(f"RateGuard {__version__} — Rate Limiter Visualizer")
        self.root.resizable(True, True)
        self._build()

    def _build(self) -> None:
        # menu bar
        menubar = tk.Menu(self.root)
        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_menu)
        self.root.configure(menu=menubar)

        # notebook
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        tb_panel = _TokenBucketPanel(nb)
        tb_panel.pack(fill="both", expand=True)
        nb.add(tb_panel, text="  Token Bucket  ")

        sw_panel = _SlidingWindowPanel(nb)
        sw_panel.pack(fill="both", expand=True)
        nb.add(sw_panel, text="  Sliding Window  ")

    def _show_about(self) -> None:
        messagebox.showinfo(
            "About RateGuard",
            f"rateguard {__version__}\n\n"
            "Thread-safe rate-limiting primitives\n"
            "for LLM API calls.\n\n"
            "Token Bucket — burst-aware, reserves tokens.\n"
            "Sliding Window — fixed-count, retry-on-block.\n\n"
            "https://github.com/vdeshmukh203/rateguard",
            parent=self.root,
        )

    def run(self) -> None:
        """Enter the Tk main loop."""
        self.root.mainloop()


# ── entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    """Launch the RateGuard visualizer."""
    app = RateGuardApp()
    app.run()


if __name__ == "__main__":
    main()
