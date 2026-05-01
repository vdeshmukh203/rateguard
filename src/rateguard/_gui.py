"""rateguard._gui — interactive visualizer for rate-limiting algorithms.

Launch from the command line::

    rateguard-gui

or from Python::

    from rateguard._gui import main
    main()

The visualizer provides two tabs — one for :class:`~rateguard.TokenBucket`
and one for :class:`~rateguard.SlidingWindow` — each with live canvas
animations, configurable parameters, manual and auto-acquire controls,
and a scrollable activity log.
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from rateguard import SlidingWindow, TokenBucket

# Refresh interval in milliseconds (≈ 20 fps).
_REFRESH_MS = 50


# ---------------------------------------------------------------------------
# Token Bucket panel
# ---------------------------------------------------------------------------


class _TokenBucketPanel(ttk.Frame):
    """Visualization panel for :class:`TokenBucket`."""

    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, padding=8)
        self._bucket: TokenBucket | None = None
        self._auto: bool = False
        self._log_q: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._n_acquires: int = 0
        self._total_wait: float = 0.0
        self._counter_lock = threading.Lock()
        self._build()
        self._refresh()

    # ------------------------------------------------------------------
    # UI construction

    def _build(self) -> None:
        # ── configuration ──────────────────────────────────────────────
        cfg = ttk.LabelFrame(self, text="Configuration", padding=6)
        cfg.grid(row=0, column=0, sticky="new", padx=4, pady=4)

        ttk.Label(cfg, text="Rate (tokens/s):").grid(row=0, column=0, sticky="w")
        self._rate_var = tk.StringVar(value="5.0")
        ttk.Entry(cfg, textvariable=self._rate_var, width=9).grid(
            row=0, column=1, padx=4, pady=2
        )

        ttk.Label(cfg, text="Burst:").grid(row=1, column=0, sticky="w")
        self._burst_var = tk.StringVar(value="10")
        ttk.Entry(cfg, textvariable=self._burst_var, width=9).grid(
            row=1, column=1, padx=4, pady=2
        )

        ttk.Button(cfg, text="Apply", command=self._apply).grid(
            row=2, column=0, columnspan=2, pady=6
        )

        # ── controls ───────────────────────────────────────────────────
        ctrl = ttk.LabelFrame(self, text="Controls", padding=6)
        ctrl.grid(row=1, column=0, sticky="new", padx=4, pady=4)

        ttk.Label(ctrl, text="Tokens:").grid(row=0, column=0, sticky="w")
        self._tokens_var = tk.StringVar(value="1")
        ttk.Entry(ctrl, textvariable=self._tokens_var, width=5).grid(
            row=0, column=1, padx=4, pady=2
        )

        ttk.Button(ctrl, text="Acquire", command=self._manual_acquire).grid(
            row=1, column=0, columnspan=2, pady=3
        )

        self._auto_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            ctrl,
            text="Auto-acquire (1 token/s)",
            variable=self._auto_var,
            command=self._toggle_auto,
        ).grid(row=2, column=0, columnspan=2, pady=2)

        ttk.Button(ctrl, text="Reset Bucket", command=self._reset).grid(
            row=3, column=0, columnspan=2, pady=3
        )

        # ── canvas ─────────────────────────────────────────────────────
        self._canvas = tk.Canvas(
            self, width=200, height=230, bg="white", relief="sunken", bd=1
        )
        self._canvas.grid(row=0, column=1, rowspan=3, padx=8, pady=4, sticky="n")

        # ── status label ───────────────────────────────────────────────
        self._status_var = tk.StringVar(value="Configure and click Apply to start.")
        ttk.Label(self, textvariable=self._status_var, foreground="#555555").grid(
            row=2, column=0, sticky="w", padx=6
        )

        # ── activity log ───────────────────────────────────────────────
        log_frame = ttk.LabelFrame(self, text="Activity Log", padding=4)
        log_frame.grid(row=3, column=0, columnspan=2, sticky="nsew", padx=4, pady=4)

        self._log_text = tk.Text(
            log_frame, height=7, width=56, state="disabled", wrap="none"
        )
        sb = ttk.Scrollbar(log_frame, command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=sb.set)
        self._log_text.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.columnconfigure(1, weight=1)
        self.rowconfigure(3, weight=1)

    # ------------------------------------------------------------------
    # Event handlers (main thread)

    def _apply(self) -> None:
        try:
            rate = float(self._rate_var.get())
            burst = int(self._burst_var.get())
            self._bucket = TokenBucket(rate=rate, burst=burst)
            with self._counter_lock:
                self._n_acquires = 0
                self._total_wait = 0.0
            self._enqueue_log(f"Created TokenBucket(rate={rate}, burst={burst})")
        except ValueError as exc:
            self._enqueue_log(f"Error: {exc}")

    def _manual_acquire(self) -> None:
        if self._bucket is None:
            self._enqueue_log("No bucket — click Apply first.")
            return
        try:
            tokens = int(self._tokens_var.get())
            wait = self._bucket.acquire(tokens)
            with self._counter_lock:
                self._n_acquires += 1
                self._total_wait += wait
            if wait:
                self._enqueue_log(f"acquire({tokens}) → wait {wait:.3f}s")
            else:
                self._enqueue_log(f"acquire({tokens}) → immediate")
        except ValueError as exc:
            self._enqueue_log(f"Error: {exc}")

    def _reset(self) -> None:
        if self._bucket is not None:
            self._bucket.reset()
            self._enqueue_log("Bucket reset to full capacity.")

    def _toggle_auto(self) -> None:
        if self._auto_var.get():
            self._auto = True
            threading.Thread(target=self._auto_worker, daemon=True).start()
        else:
            self._auto = False

    # ------------------------------------------------------------------
    # Background worker (non-tkinter thread)

    def _auto_worker(self) -> None:
        while self._auto:
            if self._bucket is not None:
                wait = self._bucket.acquire(1)
                with self._counter_lock:
                    self._n_acquires += 1
                    self._total_wait += wait
            time.sleep(1.0)

    # ------------------------------------------------------------------
    # Logging helpers

    def _enqueue_log(self, msg: str) -> None:
        self._log_q.put(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def _flush_log(self) -> None:
        self._log_text.configure(state="normal")
        try:
            while True:
                self._log_text.insert("end", self._log_q.get_nowait() + "\n")
        except queue.Empty:
            pass
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    # ------------------------------------------------------------------
    # Canvas rendering

    def _draw(self) -> None:
        c = self._canvas
        c.delete("all")
        W, H = 200, 230

        if self._bucket is None:
            c.create_text(
                W // 2,
                H // 2,
                text="Apply settings\nto visualize",
                justify="center",
                font=("Arial", 11),
                fill="#aaaaaa",
            )
            return

        avail = self._bucket.tokens_available
        burst = self._bucket.burst
        ratio = min(1.0, max(0.0, avail / burst))

        # Bucket body
        bx1, by1, bx2, by2 = 50, 20, 150, 160
        bh = by2 - by1

        if ratio >= 0.6:
            fill_col = "#27ae60"
        elif ratio >= 0.25:
            fill_col = "#f39c12"
        else:
            fill_col = "#e74c3c"

        fill_h = int(bh * ratio)
        fy1 = by2 - fill_h

        # Drop shadow
        c.create_rectangle(
            bx1 + 4, by1 + 4, bx2 + 4, by2 + 4, fill="#cccccc", outline=""
        )
        # Container
        c.create_rectangle(
            bx1, by1, bx2, by2, fill="#f8f9fa", outline="#2c3e50", width=2
        )
        # Fill
        if fill_h > 0:
            c.create_rectangle(
                bx1 + 2, fy1, bx2 - 2, by2 - 2, fill=fill_col, outline=""
            )

        # Percentage label
        c.create_text(
            (bx1 + bx2) // 2,
            (by1 + by2) // 2,
            text=f"{ratio * 100:.0f}%",
            font=("Arial", 20, "bold"),
            fill="#2c3e50",
        )

        # Token count
        c.create_text(
            W // 2,
            175,
            text=f"{avail:.2f} / {burst} tokens",
            font=("Arial", 10),
            fill="#2c3e50",
        )
        c.create_text(
            W // 2,
            193,
            text=f"rate: {self._bucket.rate:.1f} tok/s",
            font=("Arial", 9),
            fill="#7f8c8d",
        )
        c.create_text(
            W // 2,
            213,
            text="Token Bucket",
            font=("Arial", 9, "italic"),
            fill="#95a5a6",
        )

    # ------------------------------------------------------------------
    # Refresh loop (main thread, scheduled via after())

    def _refresh(self) -> None:
        self._draw()
        if self._bucket is not None:
            with self._counter_lock:
                n, tw = self._n_acquires, self._total_wait
            self._status_var.set(
                f"Acquires: {n}  |  Total wait: {tw:.2f}s  |  "
                f"Available: {self._bucket.tokens_available:.2f}/{self._bucket.burst}"
            )
        self._flush_log()
        self.after(_REFRESH_MS, self._refresh)


# ---------------------------------------------------------------------------
# Sliding Window panel
# ---------------------------------------------------------------------------


class _SlidingWindowPanel(ttk.Frame):
    """Visualization panel for :class:`SlidingWindow`."""

    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, padding=8)
        self._sw: SlidingWindow | None = None
        self._auto: bool = False
        self._log_q: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._n_calls: int = 0
        self._n_admitted: int = 0
        self._total_wait: float = 0.0
        self._counter_lock = threading.Lock()
        self._build()
        self._refresh()

    # ------------------------------------------------------------------
    # UI construction

    def _build(self) -> None:
        # ── configuration ──────────────────────────────────────────────
        cfg = ttk.LabelFrame(self, text="Configuration", padding=6)
        cfg.grid(row=0, column=0, sticky="new", padx=4, pady=4)

        ttk.Label(cfg, text="Max calls:").grid(row=0, column=0, sticky="w")
        self._max_calls_var = tk.StringVar(value="5")
        ttk.Entry(cfg, textvariable=self._max_calls_var, width=9).grid(
            row=0, column=1, padx=4, pady=2
        )

        ttk.Label(cfg, text="Window (s):").grid(row=1, column=0, sticky="w")
        self._window_s_var = tk.StringVar(value="10.0")
        ttk.Entry(cfg, textvariable=self._window_s_var, width=9).grid(
            row=1, column=1, padx=4, pady=2
        )

        ttk.Button(cfg, text="Apply", command=self._apply).grid(
            row=2, column=0, columnspan=2, pady=6
        )

        # ── controls ───────────────────────────────────────────────────
        ctrl = ttk.LabelFrame(self, text="Controls", padding=6)
        ctrl.grid(row=1, column=0, sticky="new", padx=4, pady=4)

        ttk.Button(ctrl, text="Make Call", command=self._manual_acquire).grid(
            row=0, column=0, columnspan=2, pady=3
        )

        self._auto_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            ctrl,
            text="Auto-call (2 calls/s)",
            variable=self._auto_var,
            command=self._toggle_auto,
        ).grid(row=1, column=0, columnspan=2, pady=2)

        ttk.Button(ctrl, text="Reset Window", command=self._reset).grid(
            row=2, column=0, columnspan=2, pady=3
        )

        # ── canvas ─────────────────────────────────────────────────────
        self._canvas = tk.Canvas(
            self, width=310, height=145, bg="white", relief="sunken", bd=1
        )
        self._canvas.grid(row=0, column=1, rowspan=2, padx=8, pady=4, sticky="n")

        # ── status label ───────────────────────────────────────────────
        self._status_var = tk.StringVar(value="Configure and click Apply to start.")
        ttk.Label(self, textvariable=self._status_var, foreground="#555555").grid(
            row=2, column=0, columnspan=2, sticky="w", padx=6
        )

        # ── activity log ───────────────────────────────────────────────
        log_frame = ttk.LabelFrame(self, text="Activity Log", padding=4)
        log_frame.grid(row=3, column=0, columnspan=2, sticky="nsew", padx=4, pady=4)

        self._log_text = tk.Text(
            log_frame, height=7, width=56, state="disabled", wrap="none"
        )
        sb = ttk.Scrollbar(log_frame, command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=sb.set)
        self._log_text.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.columnconfigure(1, weight=1)
        self.rowconfigure(3, weight=1)

    # ------------------------------------------------------------------
    # Event handlers (main thread)

    def _apply(self) -> None:
        try:
            mc = int(self._max_calls_var.get())
            ws = float(self._window_s_var.get())
            self._sw = SlidingWindow(max_calls=mc, window_seconds=ws)
            with self._counter_lock:
                self._n_calls = 0
                self._n_admitted = 0
                self._total_wait = 0.0
            self._enqueue_log(
                f"Created SlidingWindow(max_calls={mc}, window_seconds={ws})"
            )
        except ValueError as exc:
            self._enqueue_log(f"Error: {exc}")

    def _manual_acquire(self) -> None:
        if self._sw is None:
            self._enqueue_log("No window — click Apply first.")
            return
        wait = self._sw.acquire()
        with self._counter_lock:
            self._n_calls += 1
            if wait == 0.0:
                self._n_admitted += 1
            else:
                self._total_wait += wait
        if wait == 0.0:
            self._enqueue_log("acquire() → admitted")
        else:
            self._enqueue_log(f"acquire() → blocked, wait {wait:.3f}s")

    def _reset(self) -> None:
        if self._sw is not None:
            self._sw.reset()
            self._enqueue_log("Window cleared.")

    def _toggle_auto(self) -> None:
        if self._auto_var.get():
            self._auto = True
            threading.Thread(target=self._auto_worker, daemon=True).start()
        else:
            self._auto = False

    # ------------------------------------------------------------------
    # Background worker (non-tkinter thread)

    def _auto_worker(self) -> None:
        while self._auto:
            if self._sw is not None:
                wait = self._sw.acquire()
                with self._counter_lock:
                    self._n_calls += 1
                    if wait == 0.0:
                        self._n_admitted += 1
                    else:
                        self._total_wait += wait
            time.sleep(0.5)

    # ------------------------------------------------------------------
    # Logging helpers

    def _enqueue_log(self, msg: str) -> None:
        self._log_q.put(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def _flush_log(self) -> None:
        self._log_text.configure(state="normal")
        try:
            while True:
                self._log_text.insert("end", self._log_q.get_nowait() + "\n")
        except queue.Empty:
            pass
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    # ------------------------------------------------------------------
    # Canvas rendering

    def _draw(self) -> None:
        c = self._canvas
        c.delete("all")
        W, H = 310, 145

        if self._sw is None:
            c.create_text(
                W // 2,
                H // 2,
                text="Apply settings\nto visualize",
                justify="center",
                font=("Arial", 11),
                fill="#aaaaaa",
            )
            return

        ws = self._sw.window_seconds
        mc = self._sw.max_calls
        ages = self._sw.call_ages()
        in_win = len(ages)

        # ── timeline track ─────────────────────────────────────────────
        tx1, ty, tx2 = 20, 48, 290
        span = tx2 - tx1

        c.create_rectangle(
            tx1, ty - 10, tx2, ty + 10, fill="#ecf0f1", outline="#bdc3c7", width=1
        )

        # Axis labels
        c.create_text(
            tx1, ty + 22, text=f"−{ws:.0f}s", font=("Arial", 8), fill="#7f8c8d"
        )
        c.create_text(tx2, ty + 22, text="now", font=("Arial", 8), fill="#7f8c8d")
        c.create_text(
            (tx1 + tx2) // 2, 12, text="Time →", font=("Arial", 8), fill="#95a5a6"
        )

        # Calls as circles on the timeline
        for age in ages:
            frac = 1.0 - (age / ws)
            x = tx1 + int(frac * span)
            c.create_oval(
                x - 7, ty - 7, x + 7, ty + 7, fill="#3498db", outline="#2980b9"
            )

        # ── capacity fill bar ──────────────────────────────────────────
        bx1, by1, bx2, by2 = 20, 82, 290, 108
        fill_ratio = in_win / mc if mc else 0.0

        if fill_ratio >= 1.0:
            col = "#e74c3c"
        elif fill_ratio >= 0.7:
            col = "#f39c12"
        else:
            col = "#27ae60"

        c.create_rectangle(
            bx1, by1, bx2, by2, fill="#ecf0f1", outline="#bdc3c7", width=1
        )
        fill_x = bx1 + int((bx2 - bx1) * min(fill_ratio, 1.0))
        if fill_x > bx1:
            c.create_rectangle(bx1 + 1, by1 + 1, fill_x, by2 - 1, fill=col, outline="")

        c.create_text(
            (bx1 + bx2) // 2,
            (by1 + by2) // 2,
            text=f"{in_win} / {mc} calls in window",
            font=("Arial", 9, "bold"),
            fill="#2c3e50",
        )

        c.create_text(
            W // 2,
            130,
            text="Sliding Window",
            font=("Arial", 9, "italic"),
            fill="#95a5a6",
        )

    # ------------------------------------------------------------------
    # Refresh loop

    def _refresh(self) -> None:
        self._draw()
        if self._sw is not None:
            with self._counter_lock:
                nc, na, tw = self._n_calls, self._n_admitted, self._total_wait
            blocked = nc - na
            self._status_var.set(
                f"Calls: {nc}  |  Admitted: {na}  |  "
                f"Blocked: {blocked}  |  Total wait: {tw:.2f}s"
            )
        self._flush_log()
        self.after(_REFRESH_MS, self._refresh)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Launch the rateguard interactive rate-limiter visualizer."""
    root = tk.Tk()
    root.title("rateguard — Rate Limiter Visualizer")
    root.geometry("680x580")
    root.minsize(600, 500)
    root.resizable(True, True)

    # Header bar
    hdr = tk.Frame(root, bg="#2c3e50", height=48)
    hdr.pack(fill="x")
    hdr.pack_propagate(False)
    tk.Label(
        hdr,
        text="rateguard",
        fg="#ecf0f1",
        bg="#2c3e50",
        font=("Arial", 15, "bold"),
    ).pack(side="left", padx=14, pady=10)
    tk.Label(
        hdr,
        text="Rate Limiter Visualizer",
        fg="#95a5a6",
        bg="#2c3e50",
        font=("Arial", 11),
    ).pack(side="left", pady=10)
    tk.Label(
        hdr,
        text="v0.1.0",
        fg="#7f8c8d",
        bg="#2c3e50",
        font=("Arial", 9),
    ).pack(side="right", padx=14, pady=10)

    # Tabs
    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, padx=4, pady=4)

    tb_panel = _TokenBucketPanel(nb)
    sw_panel = _SlidingWindowPanel(nb)

    nb.add(tb_panel, text="  Token Bucket  ")
    nb.add(sw_panel, text="  Sliding Window  ")

    root.mainloop()


if __name__ == "__main__":
    main()
