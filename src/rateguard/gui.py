"""rateguard graphical dashboard.

Launches an interactive Tkinter window for visualising and experimenting
with :class:`~rateguard.TokenBucket` and :class:`~rateguard.SlidingWindow`
rate limiters in real time.

Run directly::

    python -m rateguard.gui

Or via the installed entry-point::

    rateguard-gui
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from typing import Any, Optional

from rateguard import SlidingWindow, TokenBucket

# ---------------------------------------------------------------------------
# Colour palette (Catppuccin Mocha)
# ---------------------------------------------------------------------------

_C = {
    "base": "#1e1e2e",
    "surface0": "#313244",
    "surface1": "#45475a",
    "overlay0": "#6c7086",
    "text": "#cdd6f4",
    "subtext": "#a6adc8",
    "blue": "#89b4fa",
    "sky": "#89dceb",
    "teal": "#94e2d5",
    "green": "#a6e3a1",
    "yellow": "#f9e2af",
    "peach": "#fab387",
    "red": "#f38ba8",
    "mauve": "#cba6f7",
}

_REFRESH_MS = 150  # GUI refresh interval in milliseconds


# ---------------------------------------------------------------------------
# Token Bucket panel
# ---------------------------------------------------------------------------


class _TokenBucketPanel(ttk.Frame):
    """Interactive panel for a :class:`TokenBucket`."""

    def __init__(self, master: tk.Widget) -> None:
        super().__init__(master, padding=10)
        self._bucket: Optional[TokenBucket] = None
        self._build()
        self._schedule_refresh()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        # Configuration
        cfg = ttk.LabelFrame(self, text="Configuration", padding=8)
        cfg.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))

        ttk.Label(cfg, text="Rate (tokens/sec):").grid(
            row=0, column=0, sticky="w", pady=2
        )
        self._rate_var = tk.DoubleVar(value=5.0)
        ttk.Spinbox(
            cfg, from_=0.1, to=10000.0, increment=0.5,
            textvariable=self._rate_var, width=9, format="%.1f",
        ).grid(row=0, column=1, padx=(4, 0), pady=2)

        ttk.Label(cfg, text="Burst capacity:").grid(
            row=1, column=0, sticky="w", pady=2
        )
        self._burst_var = tk.IntVar(value=10)
        ttk.Spinbox(
            cfg, from_=1, to=10000, increment=1,
            textvariable=self._burst_var, width=9,
        ).grid(row=1, column=1, padx=(4, 0), pady=2)

        ttk.Label(cfg, text="Tokens to acquire:").grid(
            row=2, column=0, sticky="w", pady=2
        )
        self._tokens_var = tk.IntVar(value=1)
        ttk.Spinbox(
            cfg, from_=1, to=10000, increment=1,
            textvariable=self._tokens_var, width=9,
        ).grid(row=2, column=1, padx=(4, 0), pady=2)

        ttk.Button(
            cfg, text="Create / Reset Bucket", command=self._create
        ).grid(row=3, column=0, columnspan=2, pady=(10, 0), sticky="ew")

        # Bucket canvas
        self._canvas = tk.Canvas(
            self, width=200, height=240,
            bg=_C["base"], highlightthickness=1,
            highlightbackground=_C["surface1"],
        )
        self._canvas.grid(row=0, column=1, rowspan=2, padx=8)

        # Controls + status
        ctrl = ttk.LabelFrame(self, text="Controls", padding=8)
        ctrl.grid(row=0, column=2, sticky="nsew", padx=(8, 0), pady=(0, 8))
        ctrl.columnconfigure(0, weight=1)

        ttk.Button(ctrl, text="Acquire (1×)", command=self._acquire_one).grid(
            row=0, column=0, sticky="ew", pady=2
        )
        ttk.Button(ctrl, text="Acquire (5×)", command=self._acquire_five).grid(
            row=1, column=0, sticky="ew", pady=2
        )
        ttk.Button(ctrl, text="Reset Bucket", command=self._reset).grid(
            row=2, column=0, sticky="ew", pady=2
        )

        ttk.Separator(ctrl, orient="horizontal").grid(
            row=3, column=0, sticky="ew", pady=8
        )

        self._status_var = tk.StringVar(value="No bucket.\nCreate one above.")
        ttk.Label(
            ctrl, textvariable=self._status_var,
            wraplength=150, justify="left",
        ).grid(row=4, column=0, sticky="nw")

        # Log
        log_lf = ttk.LabelFrame(self, text="Acquire Log", padding=4)
        log_lf.grid(
            row=1, column=0, columnspan=3, sticky="nsew",
            padx=0, pady=(0, 0)
        )
        log_lf.columnconfigure(0, weight=1)
        log_lf.rowconfigure(0, weight=1)

        self._log = tk.Text(
            log_lf, height=7, state="disabled",
            bg=_C["surface0"], fg=_C["text"],
            font=("Courier", 9), relief="flat", borderwidth=0,
        )
        sb = ttk.Scrollbar(log_lf, command=self._log.yview)
        self._log.configure(yscrollcommand=sb.set)
        self._log.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")

        self._draw_bucket(fill=1.0, tokens=0.0, burst=0)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _create(self) -> None:
        try:
            self._bucket = TokenBucket(
                rate=self._rate_var.get(),
                burst=self._burst_var.get(),
            )
            self._log_msg(
                f"[CREATE] TokenBucket(rate={self._bucket.rate}, "
                f"burst={self._bucket.burst})"
            )
        except ValueError as exc:
            messagebox.showerror("Invalid parameters", str(exc))

    def _acquire_one(self) -> None:
        self._do_acquire(self._tokens_var.get())

    def _acquire_five(self) -> None:
        for _ in range(5):
            self._do_acquire(self._tokens_var.get())

    def _do_acquire(self, tokens: int) -> None:
        if self._bucket is None:
            messagebox.showinfo("No bucket", "Create a bucket first.")
            return
        try:
            wait = self._bucket.acquire(tokens)
            if wait == 0.0:
                self._log_msg(f"acquire({tokens}) → 0.000 s  ✓ immediate")
            else:
                self._log_msg(f"acquire({tokens}) → {wait:.3f} s  ⏳ wait")
        except (TypeError, ValueError) as exc:
            messagebox.showerror("Acquire error", str(exc))

    def _reset(self) -> None:
        if self._bucket is None:
            messagebox.showinfo("No bucket", "Create a bucket first.")
            return
        self._bucket.reset()
        self._log_msg("[RESET] bucket refilled to burst capacity")

    # ------------------------------------------------------------------
    # Refresh / drawing
    # ------------------------------------------------------------------

    def _schedule_refresh(self) -> None:
        self._refresh()
        self.after(_REFRESH_MS, self._schedule_refresh)

    def _refresh(self) -> None:
        if self._bucket is None:
            return
        st = self._bucket.status()
        fill = st["tokens"] / st["burst"]
        self._draw_bucket(fill=fill, tokens=st["tokens"], burst=st["burst"])
        pct = fill * 100
        color = _C["green"] if pct >= 50 else (_C["yellow"] if pct >= 20 else _C["red"])
        self._status_var.set(
            f"Tokens:  {st['tokens']:.2f} / {st['burst']}\n"
            f"Rate:    {st['rate']:.1f} /s\n"
            f"Fill:    {pct:.0f}%"
        )

    def _draw_bucket(self, fill: float, tokens: float, burst: int) -> None:
        c = self._canvas
        c.delete("all")
        W, H = 200, 240

        # Geometry of the trapezoid bucket
        tx1, tx2 = 30, 170      # top-left / top-right x
        ty1, ty2 = 30, 185      # top / bottom y  (trapezoid)
        bw = 16                  # inset at bottom on each side
        bh = ty2 - ty1

        # Title
        c.create_text(
            W // 2, 14, text="Token Bucket",
            fill=_C["text"], font=("Helvetica", 10, "bold"),
        )

        # Liquid fill (clip to trapezoidal shape)
        fill = max(0.0, min(1.0, fill))
        fill_px = int(bh * fill)
        if fill_px > 0:
            fy = ty2 - fill_px
            frac = fill_px / bh
            lx = (tx1 + bw) - frac * bw
            rx = (tx2 - bw) + frac * bw
            liq_color = _C["sky"] if fill >= 0.3 else _C["peach"]
            c.create_polygon(
                tx1 + bw, ty2,
                tx2 - bw, ty2,
                rx, fy,
                lx, fy,
                fill=liq_color, outline="",
            )

        # Bucket outline (drawn on top of liquid)
        c.create_polygon(
            tx1, ty1, tx2, ty1,
            tx2 - bw, ty2, tx1 + bw, ty2,
            outline=_C["blue"], fill="", width=2,
        )

        # Percentage text inside bucket
        pct = int(fill * 100)
        text_col = _C["base"] if fill > 0.45 else _C["text"]
        c.create_text(
            W // 2, (ty1 + ty2) // 2,
            text=f"{pct}%",
            fill=text_col, font=("Helvetica", 18, "bold"),
        )

        # Tokens label below bucket
        tok_str = f"{tokens:.1f} / {burst}" if burst else "—"
        c.create_text(
            W // 2, ty2 + 18, text=f"{tok_str} tokens",
            fill=_C["subtext"], font=("Helvetica", 9),
        )

        # Fill-level bar (horizontal, underneath label)
        bx1, bx2 = 30, 170
        by = ty2 + 38
        bh2 = 10
        c.create_rectangle(bx1, by, bx2, by + bh2,
                            outline=_C["surface1"], fill=_C["surface0"])
        bar_w = int((bx2 - bx1) * fill)
        if bar_w:
            bar_col = _C["green"] if fill >= 0.5 else (_C["yellow"] if fill >= 0.2 else _C["red"])
            c.create_rectangle(bx1, by, bx1 + bar_w, by + bh2,
                                fill=bar_col, outline="")

    def _log_msg(self, msg: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", msg + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")


# ---------------------------------------------------------------------------
# Sliding Window panel
# ---------------------------------------------------------------------------


class _SlidingWindowPanel(ttk.Frame):
    """Interactive panel for a :class:`SlidingWindow`."""

    def __init__(self, master: tk.Widget) -> None:
        super().__init__(master, padding=10)
        self._window: Optional[SlidingWindow] = None
        self._build()
        self._schedule_refresh()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        # Configuration
        cfg = ttk.LabelFrame(self, text="Configuration", padding=8)
        cfg.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))

        ttk.Label(cfg, text="Max calls:").grid(
            row=0, column=0, sticky="w", pady=2
        )
        self._max_calls_var = tk.IntVar(value=5)
        ttk.Spinbox(
            cfg, from_=1, to=10000, increment=1,
            textvariable=self._max_calls_var, width=9,
        ).grid(row=0, column=1, padx=(4, 0), pady=2)

        ttk.Label(cfg, text="Window (seconds):").grid(
            row=1, column=0, sticky="w", pady=2
        )
        self._window_secs_var = tk.DoubleVar(value=10.0)
        ttk.Spinbox(
            cfg, from_=0.1, to=3600.0, increment=1.0,
            textvariable=self._window_secs_var, width=9, format="%.1f",
        ).grid(row=1, column=1, padx=(4, 0), pady=2)

        ttk.Button(
            cfg, text="Create / Reset Window", command=self._create
        ).grid(row=2, column=0, columnspan=2, pady=(10, 0), sticky="ew")

        # Timeline canvas
        self._canvas = tk.Canvas(
            self, width=340, height=240,
            bg=_C["base"], highlightthickness=1,
            highlightbackground=_C["surface1"],
        )
        self._canvas.grid(row=0, column=1, rowspan=2, padx=8)

        # Controls + status
        ctrl = ttk.LabelFrame(self, text="Controls", padding=8)
        ctrl.grid(row=0, column=2, sticky="nsew", padx=(8, 0), pady=(0, 8))
        ctrl.columnconfigure(0, weight=1)

        ttk.Button(ctrl, text="Acquire (1×)", command=self._acquire_one).grid(
            row=0, column=0, sticky="ew", pady=2
        )
        ttk.Button(ctrl, text="Acquire (5×)", command=self._acquire_five).grid(
            row=1, column=0, sticky="ew", pady=2
        )
        ttk.Button(ctrl, text="Reset Window", command=self._reset).grid(
            row=2, column=0, sticky="ew", pady=2
        )

        ttk.Separator(ctrl, orient="horizontal").grid(
            row=3, column=0, sticky="ew", pady=8
        )

        self._status_var = tk.StringVar(value="No window.\nCreate one above.")
        ttk.Label(
            ctrl, textvariable=self._status_var,
            wraplength=150, justify="left",
        ).grid(row=4, column=0, sticky="nw")

        # Log
        log_lf = ttk.LabelFrame(self, text="Acquire Log", padding=4)
        log_lf.grid(
            row=1, column=0, columnspan=3, sticky="nsew",
            padx=0, pady=(0, 0)
        )
        log_lf.columnconfigure(0, weight=1)
        log_lf.rowconfigure(0, weight=1)

        self._log = tk.Text(
            log_lf, height=7, state="disabled",
            bg=_C["surface0"], fg=_C["text"],
            font=("Courier", 9), relief="flat", borderwidth=0,
        )
        sb = ttk.Scrollbar(log_lf, command=self._log.yview)
        self._log.configure(yscrollcommand=sb.set)
        self._log.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")

        self._draw_window(current=0, max_calls=5, window_secs=10.0, ages=[])

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _create(self) -> None:
        try:
            self._window = SlidingWindow(
                max_calls=self._max_calls_var.get(),
                window_seconds=self._window_secs_var.get(),
            )
            self._log_msg(
                f"[CREATE] SlidingWindow(max_calls={self._window.max_calls}, "
                f"window_seconds={self._window.window_seconds})"
            )
        except ValueError as exc:
            messagebox.showerror("Invalid parameters", str(exc))

    def _acquire_one(self) -> None:
        self._do_acquire()

    def _acquire_five(self) -> None:
        for _ in range(5):
            self._do_acquire()

    def _do_acquire(self) -> None:
        if self._window is None:
            messagebox.showinfo("No window", "Create a window first.")
            return
        wait = self._window.acquire()
        if wait == 0.0:
            self._log_msg("acquire() → 0.000 s  ✓ immediate")
        else:
            self._log_msg(f"acquire() → {wait:.3f} s  ⏳ wait (retry after sleeping)")

    def _reset(self) -> None:
        if self._window is None:
            messagebox.showinfo("No window", "Create a window first.")
            return
        self._window.reset()
        self._log_msg("[RESET] window cleared")

    # ------------------------------------------------------------------
    # Refresh / drawing
    # ------------------------------------------------------------------

    def _schedule_refresh(self) -> None:
        self._refresh()
        self.after(_REFRESH_MS, self._schedule_refresh)

    def _refresh(self) -> None:
        if self._window is None:
            return
        st = self._window.status()
        self._draw_window(
            current=st["current_calls"],
            max_calls=st["max_calls"],
            window_secs=st["window_seconds"],
            ages=st["call_ages"],
        )
        pct = st["current_calls"] / st["max_calls"] * 100
        self._status_var.set(
            f"Calls:   {st['current_calls']} / {st['max_calls']}\n"
            f"Window:  {st['window_seconds']:.1f} s\n"
            f"Load:    {pct:.0f}%"
        )

    def _draw_window(
        self,
        current: int,
        max_calls: int,
        window_secs: float,
        ages: list[float],
    ) -> None:
        c = self._canvas
        c.delete("all")
        W, H = 340, 240

        # Title
        c.create_text(
            W // 2, 14, text="Sliding Window Timeline",
            fill=_C["text"], font=("Helvetica", 10, "bold"),
        )

        # -- Timeline bar --
        bx1, bx2 = 20, 320
        bw = bx2 - bx1
        bar_y, bar_h = 50, 28
        c.create_rectangle(
            bx1, bar_y, bx2, bar_y + bar_h,
            outline=_C["surface1"], fill=_C["surface0"],
        )

        # Axis labels
        c.create_text(
            bx1, bar_y - 10, text=f"−{window_secs:.0f}s",
            fill=_C["overlay0"], font=("Helvetica", 8), anchor="w",
        )
        c.create_text(
            bx2, bar_y - 10, text="now",
            fill=_C["overlay0"], font=("Helvetica", 8), anchor="e",
        )

        # Draw a dot for each active call on the timeline
        for age in ages:
            frac = 1.0 - (age / window_secs) if window_secs > 0 else 1.0
            x = bx1 + int(frac * bw)
            x = max(bx1 + 6, min(bx2 - 6, x))
            cy = bar_y + bar_h // 2
            c.create_oval(
                x - 5, cy - 5, x + 5, cy + 5,
                fill=_C["blue"], outline=_C["sky"], width=1,
            )

        # -- Capacity bar --
        cap_y = bar_y + bar_h + 32
        cap_h = 20
        c.create_rectangle(
            bx1, cap_y, bx2, cap_y + cap_h,
            outline=_C["surface1"], fill=_C["surface0"],
        )
        ratio = current / max(max_calls, 1)
        fill_px = int(bw * ratio)
        if fill_px:
            bar_col = (
                _C["green"] if ratio < 0.7
                else (_C["yellow"] if ratio < 1.0 else _C["red"])
            )
            c.create_rectangle(
                bx1, cap_y, bx1 + fill_px, cap_y + cap_h,
                fill=bar_col, outline="",
            )
        cap_label = f"{current} / {max_calls} calls in window"
        text_col = _C["base"] if fill_px > bw // 2 else _C["text"]
        c.create_text(
            W // 2, cap_y + cap_h // 2, text=cap_label,
            fill=text_col, font=("Helvetica", 9, "bold"),
        )

        # -- Slots grid (circles for each slot) --
        grid_y = cap_y + cap_h + 22
        c.create_text(
            bx1, grid_y - 14, text="Slot usage:",
            fill=_C["subtext"], font=("Helvetica", 8), anchor="w",
        )
        max_show = min(max_calls, 30)
        slot_r = 8
        slot_gap = slot_r * 2 + 4
        total_w = max_show * slot_gap - 4
        sx0 = (W - total_w) // 2
        for i in range(max_show):
            sx = sx0 + i * slot_gap
            sy = grid_y
            used = i < current
            fill_c = _C["blue"] if used else _C["surface0"]
            out_c = _C["blue"] if used else _C["surface1"]
            c.create_oval(
                sx, sy, sx + slot_r * 2, sy + slot_r * 2,
                fill=fill_c, outline=out_c,
            )
        if max_calls > max_show:
            c.create_text(
                W // 2, grid_y + slot_r * 2 + 14,
                text=f"(showing {max_show} of {max_calls} slots)",
                fill=_C["overlay0"], font=("Helvetica", 8),
            )

    def _log_msg(self, msg: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", msg + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")


# ---------------------------------------------------------------------------
# Application entry point
# ---------------------------------------------------------------------------


def _apply_theme(root: tk.Tk) -> None:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=_C["base"], foreground=_C["text"])
    style.configure("TFrame", background=_C["base"])
    style.configure(
        "TLabelframe",
        background=_C["base"], foreground=_C["text"],
        bordercolor=_C["surface1"], relief="solid",
    )
    style.configure(
        "TLabelframe.Label",
        background=_C["base"], foreground=_C["mauve"],
        font=("Helvetica", 9, "bold"),
    )
    style.configure("TLabel", background=_C["base"], foreground=_C["text"])
    style.configure(
        "TButton",
        background=_C["surface0"], foreground=_C["text"],
        focusthickness=0, borderwidth=0, relief="flat",
        padding=(6, 4),
    )
    style.map(
        "TButton",
        background=[("active", _C["surface1"]), ("pressed", _C["overlay0"])],
        foreground=[("active", _C["text"])],
    )
    style.configure(
        "TSpinbox",
        fieldbackground=_C["surface0"], foreground=_C["text"],
        selectbackground=_C["surface1"], selectforeground=_C["text"],
        arrowcolor=_C["text"], bordercolor=_C["surface1"],
    )
    style.configure(
        "TScrollbar",
        background=_C["surface0"], troughcolor=_C["base"],
        arrowcolor=_C["text"], bordercolor=_C["base"],
    )
    style.configure(
        "TSeparator",
        background=_C["surface1"],
    )
    style.configure(
        "TNotebook",
        background=_C["base"], tabmargins=[2, 5, 2, 0],
        bordercolor=_C["surface1"],
    )
    style.configure(
        "TNotebook.Tab",
        background=_C["surface0"], foreground=_C["subtext"],
        padding=[14, 5], font=("Helvetica", 9),
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", _C["surface1"])],
        foreground=[("selected", _C["text"])],
    )


def main() -> None:
    """Launch the rateguard dashboard."""
    root = tk.Tk()
    root.title("rateguard — Rate Limiter Dashboard")
    root.configure(bg=_C["base"])
    root.resizable(True, True)
    root.minsize(720, 480)

    _apply_theme(root)

    # Header
    hdr = tk.Frame(root, bg=_C["surface0"], pady=8)
    hdr.pack(fill="x")
    tk.Label(
        hdr,
        text="rateguard  ·  Rate Limiter Dashboard",
        bg=_C["surface0"], fg=_C["mauve"],
        font=("Helvetica", 13, "bold"),
    ).pack()
    tk.Label(
        hdr,
        text="Visualise and experiment with TokenBucket and SlidingWindow in real time",
        bg=_C["surface0"], fg=_C["subtext"],
        font=("Helvetica", 9),
    ).pack()

    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, padx=12, pady=12)

    tb_panel = _TokenBucketPanel(nb)
    nb.add(tb_panel, text="  Token Bucket  ")

    sw_panel = _SlidingWindowPanel(nb)
    nb.add(sw_panel, text="  Sliding Window  ")

    root.mainloop()


if __name__ == "__main__":
    main()
