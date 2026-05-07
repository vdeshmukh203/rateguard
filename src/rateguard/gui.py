"""Interactive dashboard for monitoring and simulating rateguard limiters.

Launch from the command line::

    rateguard-dashboard
    # or
    python -m rateguard.gui

Or from Python::

    from rateguard.gui import run_dashboard
    run_dashboard()
"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import messagebox, ttk

from rateguard import CompositeRateLimiter, SlidingWindow, TokenBucket

__all__ = ["run_dashboard"]

_POLL_MS = 100   # UI refresh interval in milliseconds
_MAX_LOG = 80    # maximum history lines kept in the log widget


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_log(parent: tk.Widget) -> tk.Text:
    """Create a scrollable, read-only log widget inside *parent*."""
    lf = ttk.LabelFrame(parent, text="Call history")
    lf.pack(fill="both", expand=True, padx=8, pady=4)

    log = tk.Text(
        lf,
        height=9,
        state="disabled",
        wrap="none",
        font=("Courier", 9),
        bg="#1e1e1e",
        fg="#d4d4d4",
        insertbackground="#d4d4d4",
    )
    log.tag_configure("ok",   foreground="#4ec9b0")
    log.tag_configure("wait", foreground="#f48771")
    log.tag_configure("ts",   foreground="#858585")

    sb = ttk.Scrollbar(lf, orient="vertical", command=log.yview)
    log.configure(yscrollcommand=sb.set)
    log.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    return log


def _append_log(log: tk.Text, wait: float) -> None:
    """Append one line to *log* with colour coding."""
    tag = "ok" if wait == 0.0 else "wait"
    verdict = "admitted" if wait == 0.0 else f"wait {wait:.4f} s"
    ts = time.strftime("%H:%M:%S")

    log.configure(state="normal")
    log.insert("end", f"[{ts}] ", "ts")
    log.insert("end", f"{verdict}\n", tag)
    log.see("end")

    # trim oldest lines
    lines = int(log.index("end").split(".")[0])
    if lines > _MAX_LOG + 2:
        log.delete("1.0", "2.0")

    log.configure(state="disabled")


def _colored_label(parent: tk.Widget, **kw: object) -> tk.Label:
    """Return a plain tk.Label (supports foreground changes on all platforms)."""
    return tk.Label(parent, **kw)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Token Bucket tab
# ---------------------------------------------------------------------------

class _TokenBucketTab(ttk.Frame):
    def __init__(self, parent: ttk.Notebook) -> None:
        super().__init__(parent)
        self._bucket: TokenBucket | None = None
        self._build_ui()
        self._apply()
        self._schedule_poll()

    # ---- build ----

    def _build_ui(self) -> None:
        # -- Configuration --
        cfg = ttk.LabelFrame(self, text="Configuration")
        cfg.pack(fill="x", padx=8, pady=4)

        ttk.Label(cfg, text="Rate (tokens/s):").grid(
            row=0, column=0, sticky="w", padx=6, pady=4
        )
        self._rate_var = tk.StringVar(value="10.0")
        ttk.Entry(cfg, textvariable=self._rate_var, width=10).grid(
            row=0, column=1, padx=4, pady=4
        )

        ttk.Label(cfg, text="Burst:").grid(
            row=0, column=2, sticky="w", padx=6, pady=4
        )
        self._burst_var = tk.StringVar(value="20")
        ttk.Entry(cfg, textvariable=self._burst_var, width=8).grid(
            row=0, column=3, padx=4, pady=4
        )

        ttk.Button(cfg, text="Apply", command=self._apply).grid(
            row=0, column=4, padx=10, pady=4
        )
        ttk.Button(cfg, text="Reset bucket", command=self._reset).grid(
            row=0, column=5, padx=4, pady=4
        )

        # -- State --
        state_lf = ttk.LabelFrame(self, text="Current state")
        state_lf.pack(fill="x", padx=8, pady=4)

        self._fill_var = tk.DoubleVar()
        self._pb = ttk.Progressbar(
            state_lf, variable=self._fill_var, maximum=100, length=400
        )
        self._pb.pack(fill="x", padx=6, pady=4)

        self._state_lbl = ttk.Label(state_lf, text="—", font=("Courier", 10))
        self._state_lbl.pack(pady=2)

        # -- Simulate --
        sim = ttk.LabelFrame(self, text="Simulate an API call")
        sim.pack(fill="x", padx=8, pady=4)

        ttk.Label(sim, text="Tokens to acquire:").grid(
            row=0, column=0, sticky="w", padx=6, pady=4
        )
        self._tok_var = tk.StringVar(value="1")
        ttk.Entry(sim, textvariable=self._tok_var, width=6).grid(
            row=0, column=1, padx=4, pady=4
        )
        ttk.Button(sim, text="Acquire", command=self._acquire).grid(
            row=0, column=2, padx=10, pady=4
        )
        self._wait_lbl = _colored_label(sim, text="Last wait: —", width=22, anchor="w")
        self._wait_lbl.grid(row=0, column=3, padx=6)

        # -- Log --
        self._log = _make_log(self)

    # ---- actions ----

    def _apply(self) -> None:
        try:
            rate = float(self._rate_var.get())
            burst = int(self._burst_var.get())
            self._bucket = TokenBucket(rate=rate, burst=burst)
        except (ValueError, TypeError) as exc:
            messagebox.showerror("Configuration error", str(exc), parent=self)

    def _reset(self) -> None:
        if self._bucket is not None:
            self._bucket.reset()

    def _acquire(self) -> None:
        if self._bucket is None:
            return
        try:
            tokens = int(self._tok_var.get())
            wait = self._bucket.acquire(tokens)
        except (ValueError, TypeError) as exc:
            messagebox.showerror("Acquire error", str(exc), parent=self)
            return

        colour = "#4ec9b0" if wait == 0.0 else "#f48771"
        self._wait_lbl.configure(
            text=f"Last wait: {wait:.4f} s", fg=colour
        )
        _append_log(self._log, wait)

    # ---- polling ----

    def _poll(self) -> None:
        if self._bucket is not None:
            toks = self._bucket.tokens
            burst = self._bucket.burst
            pct = max(0.0, min(100.0, toks / burst * 100))
            self._fill_var.set(pct)
            self._state_lbl.configure(
                text=f"tokens: {toks:+.3f} / {burst}   ({pct:.1f} % full)"
            )
        self._schedule_poll()

    def _schedule_poll(self) -> None:
        self.after(_POLL_MS, self._poll)


# ---------------------------------------------------------------------------
# Sliding Window tab
# ---------------------------------------------------------------------------

class _SlidingWindowTab(ttk.Frame):
    def __init__(self, parent: ttk.Notebook) -> None:
        super().__init__(parent)
        self._sw: SlidingWindow | None = None
        self._build_ui()
        self._apply()
        self._schedule_poll()

    # ---- build ----

    def _build_ui(self) -> None:
        cfg = ttk.LabelFrame(self, text="Configuration")
        cfg.pack(fill="x", padx=8, pady=4)

        ttk.Label(cfg, text="Max calls:").grid(
            row=0, column=0, sticky="w", padx=6, pady=4
        )
        self._max_calls_var = tk.StringVar(value="60")
        ttk.Entry(cfg, textvariable=self._max_calls_var, width=8).grid(
            row=0, column=1, padx=4, pady=4
        )

        ttk.Label(cfg, text="Window (s):").grid(
            row=0, column=2, sticky="w", padx=6, pady=4
        )
        self._window_var = tk.StringVar(value="60.0")
        ttk.Entry(cfg, textvariable=self._window_var, width=10).grid(
            row=0, column=3, padx=4, pady=4
        )

        ttk.Button(cfg, text="Apply", command=self._apply).grid(
            row=0, column=4, padx=10, pady=4
        )
        ttk.Button(cfg, text="Reset window", command=self._reset).grid(
            row=0, column=5, padx=4, pady=4
        )

        state_lf = ttk.LabelFrame(self, text="Current state")
        state_lf.pack(fill="x", padx=8, pady=4)

        self._fill_var = tk.DoubleVar()
        self._pb = ttk.Progressbar(
            state_lf, variable=self._fill_var, maximum=100, length=400
        )
        self._pb.pack(fill="x", padx=6, pady=4)

        self._state_lbl = ttk.Label(state_lf, text="—", font=("Courier", 10))
        self._state_lbl.pack(pady=2)

        sim = ttk.LabelFrame(self, text="Simulate an API call")
        sim.pack(fill="x", padx=8, pady=4)

        ttk.Button(sim, text="Acquire", command=self._acquire).grid(
            row=0, column=0, padx=10, pady=4
        )
        self._wait_lbl = _colored_label(sim, text="Last wait: —", width=26, anchor="w")
        self._wait_lbl.grid(row=0, column=1, padx=6)

        self._log = _make_log(self)

    # ---- actions ----

    def _apply(self) -> None:
        try:
            max_calls = int(self._max_calls_var.get())
            window = float(self._window_var.get())
            self._sw = SlidingWindow(max_calls=max_calls, window_seconds=window)
        except (ValueError, TypeError) as exc:
            messagebox.showerror("Configuration error", str(exc), parent=self)

    def _reset(self) -> None:
        if self._sw is not None:
            self._sw.reset()

    def _acquire(self) -> None:
        if self._sw is None:
            return
        wait = self._sw.acquire()
        colour = "#4ec9b0" if wait == 0.0 else "#f48771"
        self._wait_lbl.configure(
            text=f"Last wait: {wait:.4f} s", fg=colour
        )
        _append_log(self._log, wait)

    # ---- polling ----

    def _poll(self) -> None:
        if self._sw is not None:
            calls = self._sw.current_calls
            mc = self._sw.max_calls
            pct = calls / mc * 100 if mc > 0 else 0.0
            self._fill_var.set(pct)
            self._state_lbl.configure(
                text=f"calls in window: {calls} / {mc}   ({pct:.1f} % full)"
            )
        self._schedule_poll()

    def _schedule_poll(self) -> None:
        self.after(_POLL_MS, self._poll)


# ---------------------------------------------------------------------------
# Composite tab
# ---------------------------------------------------------------------------

class _CompositeTab(ttk.Frame):
    def __init__(self, parent: ttk.Notebook) -> None:
        super().__init__(parent)
        self._comp: CompositeRateLimiter | None = None
        self._build_ui()
        self._apply()

    # ---- build ----

    def _build_ui(self) -> None:
        cfg = ttk.LabelFrame(self, text="Configuration  (TokenBucket + SlidingWindow)")
        cfg.pack(fill="x", padx=8, pady=4)

        # row 0: TokenBucket params
        ttk.Label(cfg, text="Rate (tokens/s):").grid(
            row=0, column=0, sticky="w", padx=6, pady=3
        )
        self._rate_var = tk.StringVar(value="10.0")
        ttk.Entry(cfg, textvariable=self._rate_var, width=8).grid(
            row=0, column=1, padx=4, pady=3
        )
        ttk.Label(cfg, text="Burst:").grid(
            row=0, column=2, sticky="w", padx=6, pady=3
        )
        self._burst_var = tk.StringVar(value="20")
        ttk.Entry(cfg, textvariable=self._burst_var, width=6).grid(
            row=0, column=3, padx=4, pady=3
        )

        # row 1: SlidingWindow params
        ttk.Label(cfg, text="Max calls:").grid(
            row=1, column=0, sticky="w", padx=6, pady=3
        )
        self._max_calls_var = tk.StringVar(value="60")
        ttk.Entry(cfg, textvariable=self._max_calls_var, width=8).grid(
            row=1, column=1, padx=4, pady=3
        )
        ttk.Label(cfg, text="Window (s):").grid(
            row=1, column=2, sticky="w", padx=6, pady=3
        )
        self._window_var = tk.StringVar(value="60.0")
        ttk.Entry(cfg, textvariable=self._window_var, width=10).grid(
            row=1, column=3, padx=4, pady=3
        )

        btn_frame = ttk.Frame(cfg)
        btn_frame.grid(row=0, column=4, rowspan=2, padx=10)
        ttk.Button(btn_frame, text="Apply", command=self._apply).pack(
            fill="x", pady=2
        )
        ttk.Button(btn_frame, text="Reset all", command=self._reset).pack(
            fill="x", pady=2
        )

        # Peek readout
        self._peek_lbl = ttk.Label(
            self, text="Peek: —", font=("Courier", 10)
        )
        self._peek_lbl.pack(pady=4)

        sim = ttk.LabelFrame(self, text="Simulate an API call")
        sim.pack(fill="x", padx=8, pady=4)

        ttk.Label(sim, text="Tokens:").grid(
            row=0, column=0, sticky="w", padx=6, pady=4
        )
        self._tok_var = tk.StringVar(value="1")
        ttk.Entry(sim, textvariable=self._tok_var, width=6).grid(
            row=0, column=1, padx=4, pady=4
        )
        ttk.Button(sim, text="Acquire", command=self._acquire).grid(
            row=0, column=2, padx=10, pady=4
        )
        self._wait_lbl = _colored_label(
            sim, text="Last wait: —", width=26, anchor="w"
        )
        self._wait_lbl.grid(row=0, column=3, padx=6)

        self._log = _make_log(self)

    # ---- actions ----

    def _apply(self) -> None:
        try:
            rate = float(self._rate_var.get())
            burst = int(self._burst_var.get())
            max_calls = int(self._max_calls_var.get())
            window = float(self._window_var.get())
            self._comp = CompositeRateLimiter(
                TokenBucket(rate=rate, burst=burst),
                SlidingWindow(max_calls=max_calls, window_seconds=window),
            )
            self._refresh_peek()
        except (ValueError, TypeError) as exc:
            messagebox.showerror("Configuration error", str(exc), parent=self)

    def _reset(self) -> None:
        if self._comp is not None:
            self._comp.reset()
            self._refresh_peek()

    def _acquire(self) -> None:
        if self._comp is None:
            return
        try:
            tokens = int(self._tok_var.get())
            wait = self._comp.acquire(tokens)
        except (ValueError, TypeError) as exc:
            messagebox.showerror("Acquire error", str(exc), parent=self)
            return

        colour = "#4ec9b0" if wait == 0.0 else "#f48771"
        self._wait_lbl.configure(
            text=f"Last wait: {wait:.4f} s", fg=colour
        )
        _append_log(self._log, wait)
        self._refresh_peek()

    def _refresh_peek(self) -> None:
        if self._comp is None:
            return
        try:
            tokens = int(self._tok_var.get())
        except (ValueError, TypeError):
            tokens = 1
        peek = self._comp.peek(tokens)
        colour = "#4ec9b0" if peek == 0.0 else "#f48771"
        self._peek_lbl.configure(
            text=f"Peek (no side effect): {peek:.4f} s",
            foreground=colour,
        )


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class RateGuardDashboard(tk.Tk):
    """Root window for the RateGuard interactive dashboard."""

    def __init__(self) -> None:
        super().__init__()
        self.title("RateGuard Dashboard")
        self.minsize(620, 540)
        self.resizable(True, True)

        # Apply a cleaner built-in theme where available
        style = ttk.Style(self)
        for preferred in ("clam", "alt", "default"):
            if preferred in style.theme_names():
                style.theme_use(preferred)
                break

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=4, pady=4)

        nb.add(_TokenBucketTab(nb),  text="  Token Bucket  ")
        nb.add(_SlidingWindowTab(nb), text="  Sliding Window  ")
        nb.add(_CompositeTab(nb),    text="  Composite  ")

        status = ttk.Label(
            self,
            text=(
                f"rateguard {_version()}  —  "
                "state refreshes every 100 ms  —  "
                "green = admitted, red = wait"
            ),
            relief="sunken",
            anchor="w",
        )
        status.pack(fill="x", side="bottom", ipady=2, padx=2)


def _version() -> str:
    try:
        from rateguard import __version__
        return __version__
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_dashboard() -> None:
    """Launch the RateGuard interactive dashboard."""
    app = RateGuardDashboard()
    app.mainloop()


if __name__ == "__main__":
    run_dashboard()
