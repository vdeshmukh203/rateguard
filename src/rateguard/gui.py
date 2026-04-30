"""rateguard GUI dashboard.

A Tkinter-based interactive dashboard for visualising and experimenting
with the TokenBucket and SlidingWindow rate limiters in real time.

Run directly::

    python -m rateguard

Or via the installed entry point::

    rateguard-gui
"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from rateguard import SlidingWindow, TokenBucket

# ---------------------------------------------------------------------------
# Colour palette (Catppuccin Mocha)
# ---------------------------------------------------------------------------
_BG = "#1e1e2e"
_SURFACE = "#313244"
_OVERLAY = "#45475a"
_TEXT = "#cdd6f4"
_SUBTEXT = "#a6adc8"
_GREEN = "#a6e3a1"
_RED = "#f38ba8"
_BLUE = "#89b4fa"
_TEAL = "#89dceb"
_YELLOW = "#f9e2af"
_MAUVE = "#cba6f7"

_FONT = ("Helvetica", 10)
_MONO = ("Courier", 9)


# ---------------------------------------------------------------------------
# Helper: rounded-rectangle canvas item
# ---------------------------------------------------------------------------

def _round_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float,
                r: int = 8, **kw) -> int:
    points = [
        x1 + r, y1, x2 - r, y1,
        x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r,
        x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kw)


# ---------------------------------------------------------------------------
# Token Bucket panel
# ---------------------------------------------------------------------------

class _TokenBucketPanel(ttk.Frame):
    _W = 360  # canvas width
    _H = 80   # canvas height
    _BAR_PAD = 8

    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, padding=12)
        self._bucket: Optional[TokenBucket] = None
        self._log_lines: list[str] = []
        self._build()
        self._refresh()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)

        # --- Config ---
        cfg = ttk.LabelFrame(self, text=" Configuration ", padding=8)
        cfg.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        cfg.columnconfigure(1, weight=1)

        ttk.Label(cfg, text="Rate (tokens/sec):", font=_FONT).grid(
            row=0, column=0, sticky="w", padx=(0, 6))
        self._rate_var = tk.StringVar(value="10.0")
        ttk.Entry(cfg, textvariable=self._rate_var, width=10).grid(
            row=0, column=1, sticky="w")

        ttk.Label(cfg, text="Burst capacity:", font=_FONT).grid(
            row=1, column=0, sticky="w", padx=(0, 6), pady=(4, 0))
        self._burst_var = tk.StringVar(value="20")
        ttk.Entry(cfg, textvariable=self._burst_var, width=10).grid(
            row=1, column=1, sticky="w", pady=(4, 0))

        ttk.Button(cfg, text="Create / Reset", command=self._on_create).grid(
            row=2, column=0, columnspan=2, pady=(8, 0))

        # --- Visualisation ---
        viz = ttk.LabelFrame(self, text=" Token Level ", padding=8)
        viz.grid(row=1, column=0, sticky="ew", pady=(0, 8))

        self._canvas = tk.Canvas(
            viz, width=self._W, height=self._H, bg=_BG,
            highlightthickness=0)
        self._canvas.pack()

        # Background track
        _round_rect(self._canvas,
                    self._BAR_PAD, 20,
                    self._W - self._BAR_PAD, self._H - 20,
                    r=6, fill=_SURFACE, outline=_OVERLAY, width=1)

        # Foreground fill (starts empty)
        self._bar_fill = _round_rect(self._canvas,
                                     self._BAR_PAD, 20,
                                     self._BAR_PAD, self._H - 20,
                                     r=6, fill=_TEAL, outline="")

        # Percentage label
        self._bar_label = self._canvas.create_text(
            self._W // 2, self._H // 2,
            text="— / —", fill=_TEXT, font=_MONO)

        # --- Simulate ---
        sim = ttk.LabelFrame(self, text=" Simulate ", padding=8)
        sim.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        sim.columnconfigure(2, weight=1)

        ttk.Label(sim, text="Tokens:", font=_FONT).grid(row=0, column=0, sticky="w")
        self._acq_var = tk.StringVar(value="1")
        ttk.Entry(sim, textvariable=self._acq_var, width=6).grid(
            row=0, column=1, padx=6)
        ttk.Button(sim, text="Acquire", command=self._on_acquire).grid(
            row=0, column=2, sticky="w")

        self._last_var = tk.StringVar(value="")
        ttk.Label(sim, textvariable=self._last_var,
                  font=_MONO, foreground=_GREEN).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # --- Activity log ---
        log_frame = ttk.LabelFrame(self, text=" Activity Log ", padding=8)
        log_frame.grid(row=3, column=0, sticky="ew")

        self._log_box = tk.Text(
            log_frame, height=6, width=46, bg=_SURFACE, fg=_TEXT,
            font=_MONO, state="disabled", relief="flat",
            insertbackground=_TEXT)
        self._log_box.pack(fill="both", expand=True)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_create(self) -> None:
        try:
            rate = float(self._rate_var.get())
            burst = int(self._burst_var.get())
            self._bucket = TokenBucket(rate=rate, burst=burst)
            self._last_var.set(f"Created TokenBucket(rate={rate}, burst={burst})")
            self._append_log(f"[create] rate={rate}, burst={burst}")
        except ValueError as exc:
            messagebox.showerror("Invalid configuration", str(exc))

    def _on_acquire(self) -> None:
        if self._bucket is None:
            messagebox.showinfo("No bucket", "Create a bucket first.")
            return
        try:
            tokens = int(self._acq_var.get())
        except ValueError:
            messagebox.showerror("Invalid input", "Tokens must be an integer.")
            return
        try:
            wait = self._bucket.acquire(tokens)
        except ValueError as exc:
            messagebox.showerror("Invalid tokens", str(exc))
            return
        if wait == 0.0:
            msg = f"[acquire {tokens}] admitted immediately"
            self._last_var.set(f"Acquired {tokens} token(s) — no wait.")
        else:
            msg = f"[acquire {tokens}] wait {wait:.4f}s"
            self._last_var.set(f"Must wait {wait:.4f}s for {tokens} token(s).")
        self._append_log(msg)

    # ------------------------------------------------------------------
    # Live refresh (every 100 ms)
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        if self._bucket is not None:
            avail = self._bucket.tokens_available
            burst = self._bucket.burst
            pct = avail / burst
            bar_w = max(0, (self._W - 2 * self._BAR_PAD) * pct)
            x2 = self._BAR_PAD + bar_w

            # Update fill rectangle by recreating (smooth polygon coords)
            self._canvas.delete(self._bar_fill)
            fill_color = _GREEN if pct > 0.3 else _RED
            self._bar_fill = _round_rect(
                self._canvas,
                self._BAR_PAD, 20, max(self._BAR_PAD + 1, x2), self._H - 20,
                r=6, fill=fill_color, outline="")
            # Keep label on top
            self._canvas.tag_raise(self._bar_label)
            self._canvas.itemconfig(
                self._bar_label,
                text=f"{avail:.1f} / {burst}  ({pct*100:.0f}%)",
                fill=_TEXT)
        self.after(100, self._refresh)

    # ------------------------------------------------------------------
    # Log helper
    # ------------------------------------------------------------------

    def _append_log(self, line: str) -> None:
        self._log_lines.append(line)
        if len(self._log_lines) > 200:
            self._log_lines.pop(0)
        self._log_box.configure(state="normal")
        self._log_box.insert("end", line + "\n")
        self._log_box.see("end")
        self._log_box.configure(state="disabled")


# ---------------------------------------------------------------------------
# Sliding Window panel
# ---------------------------------------------------------------------------

class _SlidingWindowPanel(ttk.Frame):
    _W = 360
    _H = 80
    _BAR_PAD = 8
    _MAX_DOTS = 30  # visual cap for dot display

    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, padding=12)
        self._window: Optional[SlidingWindow] = None
        self._log_lines: list[str] = []
        self._build()
        self._refresh()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)

        # --- Config ---
        cfg = ttk.LabelFrame(self, text=" Configuration ", padding=8)
        cfg.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        cfg.columnconfigure(1, weight=1)

        ttk.Label(cfg, text="Max calls:", font=_FONT).grid(
            row=0, column=0, sticky="w", padx=(0, 6))
        self._max_calls_var = tk.StringVar(value="10")
        ttk.Entry(cfg, textvariable=self._max_calls_var, width=10).grid(
            row=0, column=1, sticky="w")

        ttk.Label(cfg, text="Window (seconds):", font=_FONT).grid(
            row=1, column=0, sticky="w", padx=(0, 6), pady=(4, 0))
        self._window_var = tk.StringVar(value="5.0")
        ttk.Entry(cfg, textvariable=self._window_var, width=10).grid(
            row=1, column=1, sticky="w", pady=(4, 0))

        ttk.Button(cfg, text="Create / Reset", command=self._on_create).grid(
            row=2, column=0, columnspan=2, pady=(8, 0))

        # --- Visualisation ---
        viz = ttk.LabelFrame(self, text=" Calls in Window ", padding=8)
        viz.grid(row=1, column=0, sticky="ew", pady=(0, 8))

        self._canvas = tk.Canvas(
            viz, width=self._W, height=self._H, bg=_BG,
            highlightthickness=0)
        self._canvas.pack()

        # Background track
        _round_rect(self._canvas,
                    self._BAR_PAD, 20,
                    self._W - self._BAR_PAD, self._H - 20,
                    r=6, fill=_SURFACE, outline=_OVERLAY, width=1)

        self._bar_fill = _round_rect(self._canvas,
                                     self._BAR_PAD, 20,
                                     self._BAR_PAD, self._H - 20,
                                     r=6, fill=_BLUE, outline="")

        self._bar_label = self._canvas.create_text(
            self._W // 2, self._H // 2,
            text="— / —", fill=_TEXT, font=_MONO)

        # --- Simulate ---
        sim = ttk.LabelFrame(self, text=" Simulate ", padding=8)
        sim.grid(row=2, column=0, sticky="ew", pady=(0, 8))

        ttk.Button(sim, text="Acquire (1 call)", command=self._on_acquire).pack(
            side="left")

        self._last_var = tk.StringVar(value="")
        ttk.Label(sim, textvariable=self._last_var,
                  font=_MONO, foreground=_GREEN).pack(
            side="left", padx=10)

        # --- Activity log ---
        log_frame = ttk.LabelFrame(self, text=" Activity Log ", padding=8)
        log_frame.grid(row=3, column=0, sticky="ew")

        self._log_box = tk.Text(
            log_frame, height=6, width=46, bg=_SURFACE, fg=_TEXT,
            font=_MONO, state="disabled", relief="flat",
            insertbackground=_TEXT)
        self._log_box.pack(fill="both", expand=True)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_create(self) -> None:
        try:
            max_calls = int(self._max_calls_var.get())
            window_secs = float(self._window_var.get())
            self._window = SlidingWindow(max_calls=max_calls,
                                         window_seconds=window_secs)
            self._last_var.set(
                f"Created SlidingWindow(max_calls={max_calls}, "
                f"window={window_secs}s)")
            self._append_log(
                f"[create] max_calls={max_calls}, window={window_secs}s")
        except ValueError as exc:
            messagebox.showerror("Invalid configuration", str(exc))

    def _on_acquire(self) -> None:
        if self._window is None:
            messagebox.showinfo("No window", "Create a window first.")
            return
        wait = self._window.acquire()
        if wait == 0.0:
            msg = "[acquire] admitted immediately"
            self._last_var.set("Admitted immediately.")
        else:
            msg = f"[acquire] wait {wait:.4f}s"
            self._last_var.set(f"Must wait {wait:.4f}s — retry after sleeping.")
        self._append_log(msg)

    # ------------------------------------------------------------------
    # Live refresh
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        if self._window is not None:
            in_win = self._window.calls_in_window
            max_c = self._window.max_calls
            pct = in_win / max_c if max_c > 0 else 0.0
            bar_w = max(0, (self._W - 2 * self._BAR_PAD) * pct)
            x2 = self._BAR_PAD + bar_w

            self._canvas.delete(self._bar_fill)
            fill_color = _GREEN if pct < 0.8 else _RED
            self._bar_fill = _round_rect(
                self._canvas,
                self._BAR_PAD, 20, max(self._BAR_PAD + 1, x2), self._H - 20,
                r=6, fill=fill_color, outline="")
            self._canvas.tag_raise(self._bar_label)
            self._canvas.itemconfig(
                self._bar_label,
                text=f"{in_win} / {max_c}  ({pct*100:.0f}%)",
                fill=_TEXT)
        self.after(100, self._refresh)

    # ------------------------------------------------------------------
    # Log helper
    # ------------------------------------------------------------------

    def _append_log(self, line: str) -> None:
        self._log_lines.append(line)
        if len(self._log_lines) > 200:
            self._log_lines.pop(0)
        self._log_box.configure(state="normal")
        self._log_box.insert("end", line + "\n")
        self._log_box.see("end")
        self._log_box.configure(state="disabled")


# ---------------------------------------------------------------------------
# About panel
# ---------------------------------------------------------------------------

class _AboutPanel(ttk.Frame):
    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, padding=20)
        from rateguard import __version__
        text = (
            f"rateguard  v{__version__}\n\n"
            "Thread-safe rate limiting primitives for Python LLM clients.\n\n"
            "TokenBucket\n"
            "  Refills at a constant rate up to a burst capacity.\n"
            "  acquire(n) returns the wait in seconds; n tokens are reserved.\n\n"
            "SlidingWindow\n"
            "  Tracks call timestamps within a rolling time window.\n"
            "  acquire() returns the wait until a slot opens; no reservation.\n\n"
            "Usage\n"
            "  1. Select a tab above.\n"
            "  2. Set parameters and press Create / Reset.\n"
            "  3. Click Acquire to simulate API calls.\n"
            "  4. Watch the live level bar update in real time.\n\n"
            "Source: https://github.com/vdeshmukh203/rateguard\n"
            "License: MIT"
        )
        ttk.Label(self, text=text, justify="left",
                  font=_FONT, foreground=_TEXT).pack(anchor="w")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Launch the rateguard GUI dashboard."""
    root = tk.Tk()
    root.title("rateguard — Rate Limiter Dashboard")
    root.configure(bg=_BG)
    root.resizable(False, False)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass  # fall back to whatever is available

    # Dark-ish styling for ttk widgets
    style.configure(".", background=_BG, foreground=_TEXT,
                    fieldbackground=_SURFACE, font=_FONT)
    style.configure("TLabelframe", background=_BG, foreground=_SUBTEXT)
    style.configure("TLabelframe.Label", background=_BG, foreground=_MAUVE,
                    font=("Helvetica", 9, "bold"))
    style.configure("TEntry", fieldbackground=_SURFACE, foreground=_TEXT,
                    insertcolor=_TEXT)
    style.configure("TButton", background=_SURFACE, foreground=_TEXT,
                    relief="flat", padding=4)
    style.map("TButton",
              background=[("active", _OVERLAY)],
              foreground=[("active", _TEXT)])
    style.configure("TNotebook", background=_BG, tabmargins=[2, 2, 2, 0])
    style.configure("TNotebook.Tab", background=_SURFACE, foreground=_SUBTEXT,
                    padding=[10, 4])
    style.map("TNotebook.Tab",
              background=[("selected", _BG)],
              foreground=[("selected", _MAUVE)])

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True, padx=10, pady=10)

    notebook.add(_TokenBucketPanel(notebook), text="Token Bucket")
    notebook.add(_SlidingWindowPanel(notebook), text="Sliding Window")
    notebook.add(_AboutPanel(notebook), text="About")

    root.mainloop()


if __name__ == "__main__":
    main()
