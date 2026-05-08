"""rateguard.gui - interactive Tkinter explorer for TokenBucket and SlidingWindow.

Launch with::

    python -m rateguard.gui

or, after installation::

    rateguard-gui
"""

from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import List, Optional

from rateguard import SlidingWindow, TokenBucket, __version__

# ---------------------------------------------------------------------------
# Palette (Catppuccin Mocha-inspired, works on light and dark desktop themes)
# ---------------------------------------------------------------------------
_BG = "#1e1e2e"
_FG = "#cdd6f4"
_GREEN = "#a6e3a1"
_YELLOW = "#f9e2af"
_RED = "#f38ba8"
_BLUE = "#89b4fa"
_SURFACE = "#313244"
_OVERLAY = "#585b70"
_FONT_MONO = ("Courier", 10)
_FONT_BOLD = ("Helvetica", 11, "bold")


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def _color_for_fill(fill: float, low_is_good: bool = True) -> str:
    """Return a traffic-light colour for a 0–1 fill fraction."""
    if low_is_good:
        # green when low (few calls), red when high (near limit)
        if fill < 0.7:
            return _BLUE
        if fill < 0.9:
            return _YELLOW
        return _RED
    else:
        # green when high (full bucket), red when low (nearly empty)
        if fill > 0.3:
            return _GREEN
        if fill > 0.1:
            return _YELLOW
        return _RED


# ---------------------------------------------------------------------------
# Reusable canvas gauge
# ---------------------------------------------------------------------------


class _FillGauge(tk.Canvas):
    """Horizontal fill bar with centred text label."""

    _HEIGHT = 56
    _WIDTH = 440

    def __init__(self, parent: tk.Widget, **kw: object) -> None:
        super().__init__(
            parent,
            width=self._WIDTH,
            height=self._HEIGHT,
            bg=_BG,
            highlightthickness=1,
            highlightbackground=_OVERLAY,
            **kw,
        )
        self._bar = self.create_rectangle(0, 0, 0, self._HEIGHT, fill=_GREEN, outline="")
        self._label = self.create_text(
            self._WIDTH // 2,
            self._HEIGHT // 2,
            text="—",
            fill=_FG,
            font=_FONT_BOLD,
        )

    def update_fill(self, fill: float, text: str, color: str) -> None:
        w = self.winfo_width() or self._WIDTH
        self.coords(self._bar, 0, 0, int(w * fill), self._HEIGHT)
        self.itemconfig(self._bar, fill=color)
        self.itemconfig(self._label, text=text)


# ---------------------------------------------------------------------------
# Shared log widget
# ---------------------------------------------------------------------------


class _LogBox(tk.Frame):
    def __init__(self, parent: tk.Widget, height: int = 10) -> None:
        super().__init__(parent)
        self._text = tk.Text(
            self,
            height=height,
            state="disabled",
            wrap="word",
            bg=_BG,
            fg=_FG,
            insertbackground=_FG,
            font=_FONT_MONO,
        )
        sb = ttk.Scrollbar(self, orient="vertical", command=self._text.yview)
        self._text["yscrollcommand"] = sb.set
        sb.pack(side="right", fill="y")
        self._text.pack(fill="both", expand=True)

    def append(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self._text.configure(state="normal")
        self._text.insert("end", f"[{ts}] {msg}\n")
        self._text.see("end")
        self._text.configure(state="disabled")

    def clear(self) -> None:
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.configure(state="disabled")


# ---------------------------------------------------------------------------
# Token Bucket tab
# ---------------------------------------------------------------------------


class _TokenBucketTab(ttk.Frame):
    _PAD = 8

    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, padding=self._PAD)
        self._bucket: Optional[TokenBucket] = None
        self._auto_job: Optional[str] = None
        self._build_ui()
        self._refresh()

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        P = self._PAD

        # ── configuration ──────────────────────────────────────────────
        cfg = ttk.LabelFrame(self, text="Configuration", padding=P)
        cfg.grid(row=0, column=0, sticky="ew", padx=P, pady=(P, 0))

        ttk.Label(cfg, text="Rate (tokens / sec):").grid(row=0, column=0, sticky="w")
        self._rate_var = tk.StringVar(value="5.0")
        ttk.Entry(cfg, textvariable=self._rate_var, width=10).grid(
            row=0, column=1, padx=4, sticky="w"
        )

        ttk.Label(cfg, text="Burst (max tokens):").grid(row=1, column=0, sticky="w")
        self._burst_var = tk.StringVar(value="10")
        ttk.Entry(cfg, textvariable=self._burst_var, width=10).grid(
            row=1, column=1, padx=4, sticky="w"
        )

        ttk.Button(cfg, text="Create / Reset", command=self._create).grid(
            row=0, column=2, rowspan=2, padx=12
        )

        # ── gauge ──────────────────────────────────────────────────────
        vis = ttk.LabelFrame(self, text="Token level", padding=P)
        vis.grid(row=1, column=0, sticky="ew", padx=P, pady=(P, 0))
        self._gauge = _FillGauge(vis)
        self._gauge.pack(fill="x", expand=True)

        # ── controls ───────────────────────────────────────────────────
        ctrl = ttk.Frame(self, padding=(P, 4))
        ctrl.grid(row=2, column=0, sticky="ew", padx=P)

        ttk.Label(ctrl, text="Tokens:").pack(side="left")
        self._tokens_var = tk.IntVar(value=1)
        ttk.Spinbox(ctrl, from_=1, to=9999, textvariable=self._tokens_var, width=6).pack(
            side="left", padx=4
        )
        ttk.Button(ctrl, text="Acquire", command=self._acquire).pack(side="left", padx=6)
        ttk.Button(ctrl, text="Reset bucket", command=self._reset_bucket).pack(
            side="left", padx=2
        )

        ttk.Separator(ctrl, orient="vertical").pack(side="left", fill="y", padx=8)

        self._auto_var = tk.BooleanVar(value=False)
        self._auto_interval_var = tk.StringVar(value="1.0")
        ttk.Checkbutton(ctrl, text="Auto every", variable=self._auto_var,
                         command=self._toggle_auto).pack(side="left")
        ttk.Entry(ctrl, textvariable=self._auto_interval_var, width=5).pack(
            side="left", padx=2
        )
        ttk.Label(ctrl, text="s").pack(side="left")

        # ── log ────────────────────────────────────────────────────────
        log_frame = ttk.LabelFrame(self, text="Acquire log", padding=P)
        log_frame.grid(row=3, column=0, sticky="nsew", padx=P, pady=P)
        self._log = _LogBox(log_frame, height=10)
        self._log.pack(fill="both", expand=True)

        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

    # ------------------------------------------------------------------

    def _create(self) -> None:
        try:
            rate = float(self._rate_var.get())
            burst = int(self._burst_var.get())
            self._bucket = TokenBucket(rate=rate, burst=burst)
            self._log.append(f"Created TokenBucket(rate={rate}, burst={burst})")
        except ValueError as exc:
            messagebox.showerror("Invalid parameters", str(exc), parent=self)

    def _reset_bucket(self) -> None:
        if self._bucket is None:
            messagebox.showinfo("No bucket", "Click 'Create / Reset' first.", parent=self)
            return
        self._bucket.reset()
        self._log.append("Bucket reset to full burst capacity.")

    def _acquire(self) -> None:
        if self._bucket is None:
            messagebox.showinfo("No bucket", "Click 'Create / Reset' first.", parent=self)
            return
        n = self._tokens_var.get()
        try:
            wait = self._bucket.acquire(n)
        except ValueError as exc:
            messagebox.showerror("Acquire error", str(exc), parent=self)
            return
        if wait == 0.0:
            self._log.append(f"acquire({n}) → 0.000 s  [immediate ✓]")
        else:
            self._log.append(f"acquire({n}) → {wait:.3f} s  [sleeping…]")
            threading.Thread(
                target=self._background_sleep,
                args=(wait,),
                daemon=True,
            ).start()

    def _background_sleep(self, wait: float) -> None:
        time.sleep(wait)
        self.after(0, lambda: self._log.append(f"  ↳ done sleeping {wait:.3f} s  [✓]"))

    def _toggle_auto(self) -> None:
        if self._auto_var.get():
            self._schedule_auto()
        else:
            if self._auto_job is not None:
                self.after_cancel(self._auto_job)
                self._auto_job = None

    def _schedule_auto(self) -> None:
        if not self._auto_var.get():
            return
        if self._bucket is not None:
            wait = self._bucket.acquire(1)
            if wait == 0.0:
                self._log.append("auto acquire(1) → immediate ✓")
            else:
                self._log.append(f"auto acquire(1) → {wait:.3f} s wait")
        try:
            interval_ms = int(float(self._auto_interval_var.get()) * 1000)
        except ValueError:
            interval_ms = 1000
        self._auto_job = self.after(interval_ms, self._schedule_auto)

    def _refresh(self) -> None:
        if self._bucket is not None:
            s = self._bucket.status()
            fill = _clamp(s.tokens / s.burst, 0.0, 1.0)
            color = _color_for_fill(fill, low_is_good=False)
            self._gauge.update_fill(
                fill,
                f"{s.tokens:.2f} / {s.burst} tokens  ({fill * 100:.0f}%)",
                color,
            )
        self.after(80, self._refresh)


# ---------------------------------------------------------------------------
# Sliding Window tab
# ---------------------------------------------------------------------------


class _SlidingWindowTab(ttk.Frame):
    _PAD = 8

    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, padding=self._PAD)
        self._sw: Optional[SlidingWindow] = None
        self._auto_job: Optional[str] = None
        self._build_ui()
        self._refresh()

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        P = self._PAD

        # ── configuration ──────────────────────────────────────────────
        cfg = ttk.LabelFrame(self, text="Configuration", padding=P)
        cfg.grid(row=0, column=0, sticky="ew", padx=P, pady=(P, 0))

        ttk.Label(cfg, text="Max calls:").grid(row=0, column=0, sticky="w")
        self._max_var = tk.StringVar(value="5")
        ttk.Entry(cfg, textvariable=self._max_var, width=10).grid(
            row=0, column=1, padx=4, sticky="w"
        )

        ttk.Label(cfg, text="Window (sec):").grid(row=1, column=0, sticky="w")
        self._win_var = tk.StringVar(value="10.0")
        ttk.Entry(cfg, textvariable=self._win_var, width=10).grid(
            row=1, column=1, padx=4, sticky="w"
        )

        ttk.Button(cfg, text="Create / Reset", command=self._create).grid(
            row=0, column=2, rowspan=2, padx=12
        )

        # ── gauge ──────────────────────────────────────────────────────
        vis = ttk.LabelFrame(self, text="Calls in window", padding=P)
        vis.grid(row=1, column=0, sticky="ew", padx=P, pady=(P, 0))
        self._gauge = _FillGauge(vis)
        self._gauge.pack(fill="x", expand=True)

        # ── controls ───────────────────────────────────────────────────
        ctrl = ttk.Frame(self, padding=(P, 4))
        ctrl.grid(row=2, column=0, sticky="ew", padx=P)

        ttk.Button(ctrl, text="Acquire", command=self._acquire).pack(side="left", padx=6)
        ttk.Button(ctrl, text="Reset window", command=self._reset_window).pack(
            side="left", padx=2
        )

        ttk.Separator(ctrl, orient="vertical").pack(side="left", fill="y", padx=8)

        self._auto_var = tk.BooleanVar(value=False)
        self._auto_interval_var = tk.StringVar(value="1.0")
        ttk.Checkbutton(ctrl, text="Auto every", variable=self._auto_var,
                         command=self._toggle_auto).pack(side="left")
        ttk.Entry(ctrl, textvariable=self._auto_interval_var, width=5).pack(
            side="left", padx=2
        )
        ttk.Label(ctrl, text="s").pack(side="left")

        # ── log ────────────────────────────────────────────────────────
        log_frame = ttk.LabelFrame(self, text="Acquire log", padding=P)
        log_frame.grid(row=3, column=0, sticky="nsew", padx=P, pady=P)
        self._log = _LogBox(log_frame, height=10)
        self._log.pack(fill="both", expand=True)

        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

    # ------------------------------------------------------------------

    def _create(self) -> None:
        try:
            max_calls = int(self._max_var.get())
            window_seconds = float(self._win_var.get())
            self._sw = SlidingWindow(max_calls=max_calls, window_seconds=window_seconds)
            self._log.append(
                f"Created SlidingWindow(max_calls={max_calls},"
                f" window_seconds={window_seconds})"
            )
        except ValueError as exc:
            messagebox.showerror("Invalid parameters", str(exc), parent=self)

    def _reset_window(self) -> None:
        if self._sw is None:
            messagebox.showinfo("No window", "Click 'Create / Reset' first.", parent=self)
            return
        self._sw.reset()
        self._log.append("Window cleared.")

    def _acquire(self) -> None:
        if self._sw is None:
            messagebox.showinfo("No window", "Click 'Create / Reset' first.", parent=self)
            return
        wait = self._sw.acquire()
        if wait == 0.0:
            self._log.append("acquire() → 0.000 s  [admitted ✓]")
        else:
            self._log.append(
                f"acquire() → {wait:.3f} s  [blocked — retry after sleep]"
            )

    def _toggle_auto(self) -> None:
        if self._auto_var.get():
            self._schedule_auto()
        else:
            if self._auto_job is not None:
                self.after_cancel(self._auto_job)
                self._auto_job = None

    def _schedule_auto(self) -> None:
        if not self._auto_var.get():
            return
        if self._sw is not None:
            wait = self._sw.acquire()
            if wait == 0.0:
                self._log.append("auto acquire() → admitted ✓")
            else:
                self._log.append(f"auto acquire() → blocked ({wait:.3f} s)")
        try:
            interval_ms = int(float(self._auto_interval_var.get()) * 1000)
        except ValueError:
            interval_ms = 1000
        self._auto_job = self.after(interval_ms, self._schedule_auto)

    def _refresh(self) -> None:
        if self._sw is not None:
            s = self._sw.status()
            fill = _clamp(
                s.calls_in_window / s.max_calls if s.max_calls else 0.0, 0.0, 1.0
            )
            color = _color_for_fill(fill, low_is_good=True)
            self._gauge.update_fill(
                fill,
                f"{s.calls_in_window} / {s.max_calls} calls  ({fill * 100:.0f}%)",
                color,
            )
        self.after(80, self._refresh)


# ---------------------------------------------------------------------------
# About tab
# ---------------------------------------------------------------------------


class _AboutTab(ttk.Frame):
    _PAD = 12

    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, padding=self._PAD)
        self._build_ui()

    def _build_ui(self) -> None:
        P = self._PAD
        ttk.Label(
            self,
            text=f"rateguard  v{__version__}",
            font=("Helvetica", 18, "bold"),
        ).pack(pady=(P, 4))
        ttk.Label(
            self,
            text="Local rate limiter for LLM API calls",
            font=("Helvetica", 11),
        ).pack(pady=(0, P))

        info = (
            "rateguard provides two thread-safe rate-limiting primitives:\n\n"
            "• TokenBucket — refills at a constant rate up to a burst capacity.\n"
            "  Maps directly onto tokens-per-minute (TPM) limits.\n\n"
            "• SlidingWindow — tracks call timestamps in a rolling window.\n"
            "  Maps directly onto requests-per-minute (RPM) limits.\n\n"
            "Both primitives are pure standard library; no external dependencies.\n\n"
            "Usage pattern:\n"
            "  wait = limiter.acquire(...)\n"
            "  if wait > 0:\n"
            "      time.sleep(wait)\n"
            "  # proceed with the API call"
        )
        txt = tk.Text(
            self,
            height=16,
            wrap="word",
            bg=_BG,
            fg=_FG,
            font=_FONT_MONO,
            relief="flat",
            state="disabled",
        )
        txt.configure(state="normal")
        txt.insert("end", info)
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True, pady=P)

        ttk.Label(
            self,
            text="https://github.com/vdeshmukh203/rateguard",
            foreground="#89b4fa",
        ).pack(pady=(0, P))


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------


class _App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"rateguard {__version__} — rate-limiter explorer")
        self.minsize(560, 540)
        self.configure(bg=_BG)
        self._build_ui()

    def _build_ui(self) -> None:
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        nb.add(_TokenBucketTab(nb), text="  Token Bucket  ")
        nb.add(_SlidingWindowTab(nb), text="  Sliding Window  ")
        nb.add(_AboutTab(nb), text="  About  ")

        ttk.Label(
            self,
            text=f"rateguard v{__version__}  |  MIT licence",
            anchor="e",
            foreground="gray",
        ).pack(side="bottom", fill="x", padx=8, pady=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Launch the rateguard GUI explorer."""
    app = _App()
    app.mainloop()


if __name__ == "__main__":
    main()
