"""Interactive dashboard for exploring rateguard rate limiters.

Launch with::

    python -m rateguard
    rateguard-gui

The window shows two tabs:

* **Configuration** — set algorithm parameters, fire manual calls, and
  toggle an automatic call stream.
* **Monitor** — live state gauge, rolling call timeline, session statistics,
  and a colour-coded activity log.
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from collections import deque
from tkinter import font as tkfont
from tkinter import ttk
from typing import Deque, List, NamedTuple, Optional, Union

from rateguard import SlidingWindow, TokenBucket

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_HISTORY = 300  # maximum events kept in the rolling buffer

# Colour palette (Catppuccin Mocha-inspired)
_BG = "#1e1e2e"
_PANEL = "#2a2a3e"
_TEXT = "#cdd6f4"
_MUTED = "#6c7086"
_ACCENT = "#89b4fa"
_GREEN = "#a6e3a1"
_YELLOW = "#f9e2af"
_RED = "#f38ba8"
_SURFACE = "#313244"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class _Event(NamedTuple):
    ts: float       # wall-clock time (time.time())
    wait: float     # seconds the caller must sleep
    admitted: bool  # True when wait == 0


class _LogEntry(NamedTuple):
    message: str
    tag: str        # tkinter text tag: "admitted", "throttled", or "info"


class _Metrics:
    """Thread-safe rolling buffer of recent call events."""

    def __init__(self, maxlen: int = _MAX_HISTORY) -> None:
        self._lock = threading.Lock()
        self._events: Deque[_Event] = deque(maxlen=maxlen)

    def record(self, wait: float) -> None:
        with self._lock:
            self._events.append(_Event(
                ts=time.time(),
                wait=wait,
                admitted=(wait == 0.0),
            ))

    def snapshot(self) -> List[_Event]:
        with self._lock:
            return list(self._events)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------

class RateGuardApp(tk.Tk):
    """Main tkinter window for the rateguard visualizer."""

    def __init__(self) -> None:
        super().__init__()
        self.title("rateguard — Rate Limiter Visualizer")
        self.configure(bg=_BG)
        self.minsize(680, 720)
        self.resizable(True, True)

        self._metrics = _Metrics()
        self._limiter: Optional[Union[TokenBucket, SlidingWindow]] = None
        self._auto_running = False
        self._auto_after_id: Optional[str] = None
        self._gauge_fill = 0.0  # 0.0 – 1.0
        self._log_queue: queue.Queue[_LogEntry] = queue.Queue()
        self._limiter_type = tk.StringVar(value="token_bucket")

        self._build_ui()
        self._apply_settings()
        self._schedule_refresh()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = tk.Frame(self, bg=_BG, padx=14, pady=12)
        outer.pack(fill="both", expand=True)

        # Header
        hf = tk.Frame(outer, bg=_BG)
        hf.pack(fill="x", pady=(0, 6))
        title_font = tkfont.Font(family="Helvetica", size=17, weight="bold")
        tk.Label(hf, text="rateguard", font=title_font, bg=_BG, fg=_ACCENT).pack(side="left")
        tk.Label(hf, text="  rate limiter visualizer", bg=_BG, fg=_MUTED,
                 font=("Helvetica", 11)).pack(side="left", pady=2)

        sep = ttk.Separator(outer, orient="horizontal")
        sep.pack(fill="x", pady=(0, 8))

        # Notebook
        style = ttk.Style(self)
        style.configure("TNotebook", background=_BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=_SURFACE, foreground=_TEXT,
                        padding=[12, 4])
        style.map("TNotebook.Tab",
                  background=[("selected", _ACCENT)],
                  foreground=[("selected", _BG)])

        nb = ttk.Notebook(outer)
        nb.pack(fill="both", expand=True)

        tab_cfg = tk.Frame(nb, bg=_PANEL, padx=14, pady=12)
        tab_mon = tk.Frame(nb, bg=_PANEL)
        nb.add(tab_cfg, text="  Configuration  ")
        nb.add(tab_mon, text="  Monitor  ")

        self._build_config_tab(tab_cfg)
        self._build_monitor_tab(tab_mon)

    # --- Configuration tab -----------------------------------------------

    def _build_config_tab(self, parent: tk.Frame) -> None:
        # Limiter type selector
        self._section(parent, "Limiter Type", row=0)
        type_frame = tk.Frame(parent, bg=_PANEL)
        type_frame.grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 4))
        for label, value in [("Token Bucket", "token_bucket"),
                              ("Sliding Window", "sliding_window")]:
            tk.Radiobutton(
                type_frame, text=label, variable=self._limiter_type, value=value,
                bg=_PANEL, fg=_TEXT, selectcolor=_BG,
                activebackground=_PANEL, activeforeground=_ACCENT,
                command=self._on_type_change,
                font=("Helvetica", 10),
            ).pack(side="left", padx=(0, 24))

        ttk.Separator(parent, orient="horizontal").grid(
            row=2, column=0, columnspan=3, sticky="ew", pady=8)

        # Token Bucket params
        self._tb_frame = tk.Frame(parent, bg=_PANEL)
        self._tb_frame.grid(row=3, column=0, columnspan=3, sticky="ew")
        self._section(self._tb_frame, "Token Bucket Parameters", row=0)

        self._tb_rate_var = tk.DoubleVar(value=5.0)
        self._tb_burst_var = tk.IntVar(value=10)
        self._param_row(self._tb_frame, "Rate (tokens / sec):",
                        self._tb_rate_var, from_=0.5, to=50.0,
                        resolution=0.5, row=1, fmt=".1f")
        self._param_row(self._tb_frame, "Burst (max tokens):",
                        self._tb_burst_var, from_=1, to=200,
                        resolution=1, row=2, fmt="d")

        # Sliding Window params (hidden initially)
        self._sw_frame = tk.Frame(parent, bg=_PANEL)
        self._sw_frame.grid(row=3, column=0, columnspan=3, sticky="ew")
        self._section(self._sw_frame, "Sliding Window Parameters", row=0)

        self._sw_calls_var = tk.IntVar(value=10)
        self._sw_window_var = tk.DoubleVar(value=5.0)
        self._param_row(self._sw_frame, "Max calls:",
                        self._sw_calls_var, from_=1, to=200,
                        resolution=1, row=1, fmt="d")
        self._param_row(self._sw_frame, "Window (seconds):",
                        self._sw_window_var, from_=1.0, to=120.0,
                        resolution=1.0, row=2, fmt=".0f")
        self._sw_frame.grid_remove()

        # Apply / Reset
        ttk.Separator(parent, orient="horizontal").grid(
            row=4, column=0, columnspan=3, sticky="ew", pady=8)
        btn_row = tk.Frame(parent, bg=_PANEL)
        btn_row.grid(row=5, column=0, columnspan=3, sticky="w")
        self._btn(btn_row, "Apply Settings", self._apply_settings,
                  bg=_ACCENT, fg=_BG).pack(side="left", padx=(0, 8))
        self._btn(btn_row, "Reset", self._reset,
                  bg=_MUTED, fg=_TEXT).pack(side="left")

        # Fire controls
        ttk.Separator(parent, orient="horizontal").grid(
            row=6, column=0, columnspan=3, sticky="ew", pady=8)
        self._section(parent, "Fire Calls", row=7)

        fire_row = tk.Frame(parent, bg=_PANEL)
        fire_row.grid(row=8, column=0, columnspan=3, sticky="w", pady=(0, 6))
        for n in (1, 5, 20, 50):
            self._btn(fire_row, f"Fire {n}",
                      lambda c=n: self._fire_calls(c),
                      bg=_SURFACE, fg=_TEXT).pack(side="left", padx=(0, 6))

        # Auto-fire row
        auto_row = tk.Frame(parent, bg=_PANEL)
        auto_row.grid(row=9, column=0, columnspan=3, sticky="w")
        tk.Label(auto_row, text="Auto-fire at", bg=_PANEL, fg=_TEXT,
                 font=("Helvetica", 10)).pack(side="left")
        self._auto_rate_var = tk.DoubleVar(value=3.0)
        tk.Scale(
            auto_row, variable=self._auto_rate_var,
            from_=0.5, to=30.0, resolution=0.5,
            orient="horizontal", length=140,
            bg=_PANEL, fg=_TEXT, troughcolor=_BG,
            highlightthickness=0, showvalue=True,
        ).pack(side="left", padx=6)
        tk.Label(auto_row, text="calls / sec", bg=_PANEL, fg=_TEXT,
                 font=("Helvetica", 10)).pack(side="left")
        self._auto_btn = self._btn(
            auto_row, "Start Auto", self._toggle_auto,
            bg=_SURFACE, fg=_TEXT,
        )
        self._auto_btn.pack(side="left", padx=10)

    # --- Monitor tab -------------------------------------------------------

    def _build_monitor_tab(self, parent: tk.Frame) -> None:
        # Current state
        state_frame = tk.Frame(parent, bg=_PANEL, padx=14, pady=10)
        state_frame.pack(fill="x")
        tk.Label(state_frame, text="Current State", bg=_PANEL, fg=_ACCENT,
                 font=("Helvetica", 10, "bold")).pack(anchor="w")
        self._state_label = tk.Label(state_frame, text="—", bg=_PANEL, fg=_TEXT,
                                     font=("Helvetica", 11))
        self._state_label.pack(anchor="w", pady=(2, 6))
        self._gauge_canvas = tk.Canvas(state_frame, height=28, bg=_PANEL,
                                       highlightthickness=0)
        self._gauge_canvas.pack(fill="x")
        self._gauge_canvas.bind("<Configure>", lambda _: self._draw_gauge())

        ttk.Separator(parent, orient="horizontal").pack(fill="x")

        # Session stats
        stats_frame = tk.Frame(parent, bg=_PANEL, padx=14, pady=8)
        stats_frame.pack(fill="x")
        tk.Label(stats_frame, text="Session Statistics", bg=_PANEL, fg=_ACCENT,
                 font=("Helvetica", 10, "bold")).pack(anchor="w")
        self._stats_label = tk.Label(
            stats_frame,
            text="Calls: 0  |  Admitted: 0  |  Throttled: 0  |  Avg wait: 0.000 s",
            bg=_PANEL, fg=_TEXT, font=("Helvetica", 10),
        )
        self._stats_label.pack(anchor="w", pady=2)

        ttk.Separator(parent, orient="horizontal").pack(fill="x")

        # Timeline
        tl_frame = tk.Frame(parent, bg=_PANEL, padx=14, pady=8)
        tl_frame.pack(fill="x")
        tk.Label(tl_frame, text="Call Timeline   ● admitted   ○ throttled",
                 bg=_PANEL, fg=_ACCENT, font=("Helvetica", 10, "bold")).pack(anchor="w")
        self._timeline_canvas = tk.Canvas(
            tl_frame, height=44, bg=_BG,
            highlightthickness=1, highlightbackground=_MUTED,
        )
        self._timeline_canvas.pack(fill="x", pady=4)
        self._timeline_canvas.bind("<Configure>", lambda _: self._draw_timeline())

        ttk.Separator(parent, orient="horizontal").pack(fill="x")

        # Activity log
        log_frame = tk.Frame(parent, bg=_PANEL, padx=14, pady=8)
        log_frame.pack(fill="both", expand=True)
        log_hdr = tk.Frame(log_frame, bg=_PANEL)
        log_hdr.pack(fill="x")
        tk.Label(log_hdr, text="Activity Log", bg=_PANEL, fg=_ACCENT,
                 font=("Helvetica", 10, "bold")).pack(side="left")
        self._btn(log_hdr, "Clear", self._clear_log,
                  bg=_SURFACE, fg=_MUTED).pack(side="right")

        log_inner = tk.Frame(log_frame, bg=_PANEL)
        log_inner.pack(fill="both", expand=True, pady=(4, 0))
        self._log_text = tk.Text(
            log_inner, state="disabled", height=10,
            bg=_BG, fg=_TEXT, font=("Courier", 9), relief="flat",
            insertbackground=_TEXT, wrap="none",
        )
        sb_y = ttk.Scrollbar(log_inner, orient="vertical",
                              command=self._log_text.yview)
        sb_x = ttk.Scrollbar(log_inner, orient="horizontal",
                              command=self._log_text.xview)
        self._log_text.configure(yscrollcommand=sb_y.set,
                                  xscrollcommand=sb_x.set)
        sb_y.pack(side="right", fill="y")
        sb_x.pack(side="bottom", fill="x")
        self._log_text.pack(fill="both", expand=True)
        self._log_text.tag_config("admitted", foreground=_GREEN)
        self._log_text.tag_config("throttled", foreground=_RED)
        self._log_text.tag_config("info", foreground=_MUTED)
        self._log_text.tag_config("ts", foreground=_MUTED)

    # ------------------------------------------------------------------
    # Helper widget constructors
    # ------------------------------------------------------------------

    @staticmethod
    def _section(parent: tk.Frame, text: str, row: int) -> None:
        tk.Label(parent, text=text, bg=_PANEL, fg=_ACCENT,
                 font=("Helvetica", 10, "bold")).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(6, 2))

    @staticmethod
    def _param_row(
        parent: tk.Frame,
        label: str,
        var: tk.Variable,
        from_: float,
        to: float,
        resolution: float,
        row: int,
        fmt: str = ".1f",
    ) -> None:
        tk.Label(parent, text=label, bg=_PANEL, fg=_TEXT,
                 font=("Helvetica", 10), width=22, anchor="w").grid(
            row=row, column=0, sticky="w", pady=3)
        tk.Scale(
            parent, variable=var, from_=from_, to=to,
            resolution=resolution, orient="horizontal", length=300,
            bg=_PANEL, fg=_TEXT, troughcolor=_BG,
            highlightthickness=0, showvalue=False,
        ).grid(row=row, column=1, padx=8)
        # Value display
        val_label = tk.Label(parent, bg=_PANEL, fg=_TEXT,
                             font=("Courier", 10), width=7, anchor="e")
        val_label.grid(row=row, column=2, sticky="e")

        def _update(*_: object) -> None:
            raw = var.get()
            val_label.configure(text=(f"{raw:{fmt}}" if fmt != "d"
                                      else f"{int(raw):d}"))

        var.trace_add("write", _update)
        _update()

    @staticmethod
    def _btn(parent: tk.Widget, text: str, command: object,
             bg: str = _SURFACE, fg: str = _TEXT) -> tk.Button:
        return tk.Button(
            parent, text=text, command=command,
            bg=bg, fg=fg, relief="flat", padx=10, pady=4,
            cursor="hand2", activebackground=_ACCENT, activeforeground=_BG,
            font=("Helvetica", 10),
        )

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_type_change(self) -> None:
        if self._limiter_type.get() == "token_bucket":
            self._sw_frame.grid_remove()
            self._tb_frame.grid()
        else:
            self._tb_frame.grid_remove()
            self._sw_frame.grid()

    def _apply_settings(self) -> None:
        self._stop_auto()
        self._metrics.clear()
        while not self._log_queue.empty():
            try:
                self._log_queue.get_nowait()
            except queue.Empty:
                break

        if self._limiter_type.get() == "token_bucket":
            rate = float(self._tb_rate_var.get())
            burst = int(self._tb_burst_var.get())
            self._limiter = TokenBucket(rate=rate, burst=burst)
        else:
            max_calls = int(self._sw_calls_var.get())
            window = float(self._sw_window_var.get())
            self._limiter = SlidingWindow(max_calls=max_calls, window_seconds=window)

        self._log_queue.put(_LogEntry(
            f"Applied: {self._limiter!r}", "info"))
        self._clear_log_widget()
        self._update_status()

    def _reset(self) -> None:
        self._apply_settings()

    def _fire_calls(self, count: int = 1) -> None:
        if self._limiter is None:
            return

        def _worker() -> None:
            for _ in range(count):
                if isinstance(self._limiter, TokenBucket):
                    wait = self._limiter.acquire(1)
                else:
                    assert isinstance(self._limiter, SlidingWindow)
                    wait = self._limiter.acquire()
                self._metrics.record(wait)
                tag = "admitted" if wait == 0.0 else "throttled"
                label = "ADMITTED " if wait == 0.0 else "THROTTLED"
                self._log_queue.put(_LogEntry(
                    f"{label}  wait={wait:.4f} s", tag))

        threading.Thread(target=_worker, daemon=True).start()

    def _toggle_auto(self) -> None:
        if self._auto_running:
            self._stop_auto()
        else:
            self._start_auto()

    def _start_auto(self) -> None:
        if self._limiter is None:
            return
        self._auto_running = True
        self._auto_btn.configure(text="Stop Auto", bg=_RED, fg=_BG)
        self._schedule_auto_fire()

    def _stop_auto(self) -> None:
        self._auto_running = False
        if hasattr(self, "_auto_btn"):
            self._auto_btn.configure(text="Start Auto", bg=_SURFACE, fg=_TEXT)
        if self._auto_after_id is not None:
            try:
                self.after_cancel(self._auto_after_id)
            except Exception:
                pass
            self._auto_after_id = None

    def _schedule_auto_fire(self) -> None:
        if not self._auto_running:
            return
        rate = float(self._auto_rate_var.get())
        interval_ms = max(50, int(1000.0 / rate))
        self._fire_calls(1)
        self._auto_after_id = self.after(interval_ms, self._schedule_auto_fire)

    # ------------------------------------------------------------------
    # Periodic refresh (runs on main thread via after())
    # ------------------------------------------------------------------

    def _schedule_refresh(self) -> None:
        self._flush_log_queue()
        self._update_status()
        self._draw_timeline()
        self.after(100, self._schedule_refresh)

    def _update_status(self) -> None:
        if self._limiter is None:
            self._state_label.configure(text="No limiter configured.")
            self._gauge_fill = 0.0
            self._stats_label.configure(
                text="Calls: 0  |  Admitted: 0  |  Throttled: 0  |  Avg wait: 0.000 s")
            return

        if isinstance(self._limiter, TokenBucket):
            avail = self._limiter.tokens_available
            burst = self._limiter.burst
            self._state_label.configure(
                text=f"Tokens available:  {avail:.2f} / {burst}"
                     f"   (rate = {self._limiter.rate:.1f} tok/s)")
            # Gauge shows how *full* the bucket is
            self._gauge_fill = avail / burst
        else:
            assert isinstance(self._limiter, SlidingWindow)
            in_win = self._limiter.calls_in_window
            max_c = self._limiter.max_calls
            self._state_label.configure(
                text=f"Calls in window:  {in_win} / {max_c}"
                     f"   (window = {self._limiter.window_seconds:.0f} s)")
            # Gauge shows how *occupied* the window is
            self._gauge_fill = in_win / max_c if max_c else 0.0

        events = self._metrics.snapshot()
        total = len(events)
        admitted = sum(1 for e in events if e.admitted)
        throttled = total - admitted
        avg_wait = sum(e.wait for e in events) / total if total else 0.0
        self._stats_label.configure(
            text=(f"Calls: {total}  |  Admitted: {admitted}  |  "
                  f"Throttled: {throttled}  |  Avg wait: {avg_wait:.3f} s"))

        self._draw_gauge()

    # ------------------------------------------------------------------
    # Canvas drawing
    # ------------------------------------------------------------------

    def _draw_gauge(self) -> None:
        c = self._gauge_canvas
        w, h = c.winfo_width(), c.winfo_height()
        if w <= 1:
            return
        c.delete("all")

        # Track
        pad = 2
        c.create_rectangle(pad, pad, w - pad, h - pad,
                            fill=_BG, outline=_MUTED)

        # Fill colour: green → yellow → red
        fill = max(0.0, min(1.0, self._gauge_fill))
        fill_w = int((w - 2 * pad) * fill)
        if fill_w > 0:
            color = _GREEN if fill < 0.5 else (_YELLOW if fill < 0.8 else _RED)
            c.create_rectangle(pad, pad, pad + fill_w, h - pad,
                                fill=color, outline="")

        pct_text = f"{int(fill * 100)} %"
        c.create_text(w // 2, h // 2, text=pct_text,
                      fill=_TEXT, font=("Helvetica", 9, "bold"))

    def _draw_timeline(self) -> None:
        c = self._timeline_canvas
        w, h = c.winfo_width(), c.winfo_height()
        if w <= 1:
            return
        c.delete("all")

        events = self._metrics.snapshot()
        if not events:
            c.create_text(w // 2, h // 2, text="No calls yet", fill=_MUTED,
                          font=("Helvetica", 9))
            return

        r = 5
        spacing = 13
        cy = h // 2
        max_visible = max(1, (w - 10) // spacing)
        visible = events[-max_visible:]

        x = 8
        for ev in visible:
            color = _GREEN if ev.admitted else _RED
            if ev.admitted:
                c.create_oval(x - r, cy - r, x + r, cy + r,
                              fill=color, outline="")
            else:
                c.create_oval(x - r, cy - r, x + r, cy + r,
                              fill="", outline=color, width=2)
            x += spacing

    # ------------------------------------------------------------------
    # Log helpers
    # ------------------------------------------------------------------

    def _flush_log_queue(self) -> None:
        try:
            while True:
                entry = self._log_queue.get_nowait()
                self._write_log(entry)
        except queue.Empty:
            pass

    def _write_log(self, entry: _LogEntry) -> None:
        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:12]
        self._log_text.configure(state="normal")
        self._log_text.insert("end", f"{ts}  ", "ts")
        self._log_text.insert("end", f"{entry.message}\n", entry.tag)
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    def _clear_log_widget(self) -> None:
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self._metrics.clear()
        self._clear_log_widget()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Launch the rateguard interactive dashboard."""
    app = RateGuardApp()
    app.mainloop()


if __name__ == "__main__":
    main()
