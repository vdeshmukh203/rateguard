"""rateguard interactive dashboard (tkinter).

Launch with::

    python -m rateguard
    # or, after installation:
    rateguard-gui
"""

from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Optional, Tuple

from rateguard import BucketStats, SlidingWindow, TokenBucket, WindowStats

# ── Catppuccin Mocha colour palette ──────────────────────────────────────────
_BG      = "#1e1e2e"
_BASE    = "#181825"
_SURFACE = "#313244"
_OVERLAY = "#6c7086"
_TEXT    = "#cdd6f4"
_SUBTEXT = "#a6adc8"
_GREEN   = "#a6e3a1"
_YELLOW  = "#f9e2af"
_RED     = "#f38ba8"
_BLUE    = "#89b4fa"
_LAVEND  = "#b4befe"
_TEAL    = "#94e2d5"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _spinbox(
    parent: tk.Widget,
    var: tk.Variable,
    from_: float,
    to: float,
    increment: float = 1,
    width: int = 8,
) -> ttk.Spinbox:
    return ttk.Spinbox(
        parent, textvariable=var, from_=from_, to=to,
        increment=increment, width=width, justify="right",
    )


class _Log(ttk.Frame):
    """Scrollable fixed-font message log."""

    def __init__(self, parent: tk.Widget, **kw: object) -> None:
        super().__init__(parent, **kw)
        sb = ttk.Scrollbar(self, orient="vertical")
        self._t = tk.Text(
            self, height=4, state="disabled",
            font=("Courier", 9),
            bg=_BASE, fg=_TEXT, insertbackground=_TEXT,
            relief="flat", borderwidth=0,
            yscrollcommand=sb.set,
        )
        sb.config(command=self._t.yview)
        sb.pack(side="right", fill="y")
        self._t.pack(side="left", fill="both", expand=True)

    def append(self, msg: str) -> None:
        self._t.config(state="normal")
        self._t.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        self._t.see("end")
        self._t.config(state="disabled")


class _Stat:
    """A label / value pair for a statistics grid."""

    def __init__(
        self, parent: tk.Widget, label: str, row: int, col: int = 0
    ) -> None:
        ttk.Label(parent, text=label, foreground=_SUBTEXT).grid(
            row=row, column=col, sticky="w", padx=(0, 10), pady=2
        )
        self.var = tk.StringVar(value="—")
        ttk.Label(
            parent, textvariable=self.var,
            font=("Courier", 10, "bold"), foreground=_TEAL,
        ).grid(row=row, column=col + 1, sticky="e", pady=2)


# ── Token Bucket tab ──────────────────────────────────────────────────────────

class _TokenBucketTab(ttk.Frame):
    _TICK_MS = 80

    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, padding=12)
        self._bucket: Optional[TokenBucket] = None
        self._guard = threading.Lock()
        self._after_id: Optional[str] = None
        self._build()

    # layout ──────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        self.columnconfigure(0, weight=0, minsize=185)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        # ── configuration ────────────────────────────────────────────────────
        cf = ttk.LabelFrame(self, text="  Configuration  ", padding=8)
        cf.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))

        ttk.Label(cf, text="Rate (tok/s):").grid(row=0, column=0, sticky="w")
        self._rate = tk.DoubleVar(value=2.0)
        _spinbox(cf, self._rate, 0.1, 100_000.0, 0.5).grid(
            row=0, column=1, padx=(4, 20)
        )

        ttk.Label(cf, text="Burst:").grid(row=0, column=2, sticky="w")
        self._burst = tk.IntVar(value=10)
        _spinbox(cf, self._burst, 1, 100_000).grid(row=0, column=3, padx=(4, 20))

        ttk.Button(cf, text="Apply", command=self._apply).grid(
            row=0, column=4, padx=(8, 0)
        )

        # ── canvas (token level visualisation) ───────────────────────────────
        vf = ttk.LabelFrame(self, text="  Token Level  ", padding=4)
        vf.grid(row=1, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))
        vf.rowconfigure(0, weight=1)
        vf.columnconfigure(0, weight=1)
        self._cv = tk.Canvas(vf, bg=_BG, width=185, highlightthickness=0)
        self._cv.grid(row=0, column=0, sticky="nsew")

        # ── right panel ───────────────────────────────────────────────────────
        rp = ttk.Frame(self)
        rp.grid(row=1, column=1, sticky="nsew", pady=(0, 8))
        rp.columnconfigure(0, weight=1)
        rp.rowconfigure(2, weight=1)

        # stats
        sf = ttk.LabelFrame(rp, text="  Statistics  ", padding=8)
        sf.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        sf.columnconfigure(1, weight=1)
        self._s_tok  = _Stat(sf, "Current tokens:", 0)
        self._s_acq  = _Stat(sf, "Total acquired:", 1)
        self._s_wait = _Stat(sf, "Times waited:",   2)
        self._s_wt   = _Stat(sf, "Wait time (s):",  3)

        # controls
        ctl = ttk.LabelFrame(rp, text="  Controls  ", padding=8)
        ctl.grid(row=1, column=0, sticky="ew", pady=(0, 8))

        ttk.Label(ctl, text="Tokens:").grid(row=0, column=0, sticky="w")
        self._n_tok = tk.IntVar(value=1)
        _spinbox(ctl, self._n_tok, 1, 100_000, width=7).grid(
            row=0, column=1, padx=(4, 16)
        )
        self._auto = tk.BooleanVar(value=True)
        ttk.Checkbutton(ctl, text="Auto-sleep", variable=self._auto).grid(
            row=0, column=2, padx=(0, 12)
        )
        ttk.Button(ctl, text="Acquire", command=self._acquire).grid(
            row=0, column=3, padx=(0, 8)
        )
        ttk.Button(ctl, text="Reset", command=self._reset).grid(row=0, column=4)

        # log
        lf = ttk.LabelFrame(rp, text="  Log  ", padding=4)
        lf.grid(row=2, column=0, sticky="nsew")
        lf.rowconfigure(0, weight=1)
        lf.columnconfigure(0, weight=1)
        self._log = _Log(lf)
        self._log.grid(row=0, column=0, sticky="nsew")

    # actions ─────────────────────────────────────────────────────────────────

    def _apply(self) -> None:
        try:
            b = TokenBucket(
                rate=float(self._rate.get()), burst=int(self._burst.get())
            )
        except ValueError as exc:
            self._log.append(f"Error: {exc}")
            return
        with self._guard:
            self._bucket = b
        self._log.append(f"Created {b!r}")
        if self._after_id is None:
            self._tick()

    def _acquire(self) -> None:
        with self._guard:
            b = self._bucket
        if b is None:
            self._log.append("Click Apply first.")
            return
        n = self._n_tok.get()
        try:
            wait = b.acquire(n)
        except ValueError as exc:
            self._log.append(f"Error: {exc}")
            return
        if wait > 0:
            if self._auto.get():
                self._log.append(f"Acquired {n} tok — sleeping {wait:.3f}s …")
                self.after(
                    int(wait * 1000 + 0.5),
                    lambda: self._log.append("Ready."),
                )
            else:
                self._log.append(
                    f"Acquired {n} tok — would wait {wait:.3f}s (auto-sleep off)"
                )
        else:
            self._log.append(f"Acquired {n} tok immediately.")

    def _reset(self) -> None:
        with self._guard:
            b = self._bucket
        if b:
            b.reset()
            self._log.append("Bucket reset to full.")

    # animation ───────────────────────────────────────────────────────────────

    def _tick(self) -> None:
        self._redraw()
        self._after_id = self.after(self._TICK_MS, self._tick)

    def _redraw(self) -> None:
        with self._guard:
            b = self._bucket
        if b is None:
            return
        s = b.stats()
        self._s_tok.var.set(f"{max(0.0, s.current_tokens):.2f}")
        self._s_acq.var.set(str(s.total_acquired))
        self._s_wait.var.set(str(s.total_waited))
        self._s_wt.var.set(f"{s.total_wait_time:.3f}")
        self._paint(s, b.burst, b.rate)

    def _paint(self, s: BucketStats, burst: int, rate: float) -> None:
        cv = self._cv
        cv.delete("all")
        W = cv.winfo_width() or 185
        H = cv.winfo_height() or 320

        tok = max(0.0, s.current_tokens)
        frac = min(1.0, tok / burst)

        pad = 22
        bw = max(60, min(90, W - 2 * pad))
        bh = H - 110
        bx = (W - bw) // 2
        by = 48

        # Background tank body
        cv.create_rectangle(
            bx, by, bx + bw, by + bh,
            fill=_SURFACE, outline=_OVERLAY, width=2,
        )

        # Liquid fill
        fh = int(frac * (bh - 4))
        if fh > 1:
            col = _GREEN if frac > 0.6 else (_YELLOW if frac > 0.25 else _RED)
            cv.create_rectangle(
                bx + 2, by + bh - 2 - fh,
                bx + bw - 2, by + bh - 2,
                fill=col, outline="",
            )

        # Percentage label (centred inside bar)
        cv.create_text(
            bx + bw // 2, by + bh // 2,
            text=f"{frac * 100:.0f}%",
            fill=_BG if fh > bh // 3 else _TEXT,
            font=("Helvetica", 14, "bold"),
        )

        # Max-capacity dashed line at top
        cv.create_line(
            bx - 6, by + 2, bx + bw + 6, by + 2,
            fill=_BLUE, width=1, dash=(4, 3),
        )

        # 25 / 50 / 75 % tick marks on the left
        for pct in (0.25, 0.5, 0.75):
            ty = by + int((1 - pct) * bh)
            cv.create_line(bx - 5, ty, bx, ty, fill=_OVERLAY, width=1)
            cv.create_text(
                bx - 7, ty, anchor="e",
                text=f"{int(pct * 100)}%",
                fill=_OVERLAY, font=("Helvetica", 7),
            )

        # Tokens label above bar
        cv.create_text(
            W // 2, by - 28, text="Tokens",
            fill=_SUBTEXT, font=("Helvetica", 9),
        )
        cv.create_text(
            W // 2, by - 12,
            text=f"{tok:.1f} / {burst}",
            fill=_TEXT, font=("Helvetica", 11, "bold"),
        )

        # Rate label below bar
        cv.create_text(
            W // 2, by + bh + 16,
            text=f"refill rate: {rate} tok/s",
            fill=_LAVEND, font=("Helvetica", 9),
        )

    def destroy(self) -> None:
        if self._after_id:
            self.after_cancel(self._after_id)
        super().destroy()


# ── Sliding Window tab ────────────────────────────────────────────────────────

class _SlidingWindowTab(ttk.Frame):
    _TICK_MS = 80

    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, padding=12)
        self._sw: Optional[SlidingWindow] = None
        self._guard = threading.Lock()
        self._after_id: Optional[str] = None
        self._build()

    # layout ──────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        # ── configuration ────────────────────────────────────────────────────
        cf = ttk.LabelFrame(self, text="  Configuration  ", padding=8)
        cf.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        ttk.Label(cf, text="Max calls:").grid(row=0, column=0, sticky="w")
        self._mc = tk.IntVar(value=5)
        _spinbox(cf, self._mc, 1, 1_000_000).grid(row=0, column=1, padx=(4, 20))

        ttk.Label(cf, text="Window (s):").grid(row=0, column=2, sticky="w")
        self._ws = tk.DoubleVar(value=10.0)
        _spinbox(cf, self._ws, 0.1, 86_400.0, 0.5).grid(
            row=0, column=3, padx=(4, 20)
        )
        ttk.Button(cf, text="Apply", command=self._apply).grid(
            row=0, column=4, padx=(8, 0)
        )

        # ── controls + inline stats ───────────────────────────────────────────
        ctl = ttk.LabelFrame(self, text="  Controls & Statistics  ", padding=8)
        ctl.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ctl.columnconfigure(8, weight=1)

        self._auto = tk.BooleanVar(value=True)
        ttk.Checkbutton(ctl, text="Auto-sleep", variable=self._auto).grid(
            row=0, column=0, padx=(0, 12)
        )
        ttk.Button(ctl, text="Acquire", command=self._acquire).grid(
            row=0, column=1, padx=(0, 8)
        )
        ttk.Button(ctl, text="Reset", command=self._reset).grid(
            row=0, column=2, padx=(0, 24)
        )

        self._s_cur = _Stat(ctl, "In window:", 0, col=3)
        self._s_adm = _Stat(ctl, "Admitted:",  0, col=5)
        self._s_blk = _Stat(ctl, "Blocked:",   0, col=7)

        # ── canvas (timeline visualisation) ──────────────────────────────────
        vf = ttk.LabelFrame(self, text="  Window Timeline  ", padding=4)
        vf.grid(row=2, column=0, sticky="nsew", pady=(0, 8))
        vf.rowconfigure(0, weight=1)
        vf.columnconfigure(0, weight=1)
        self._cv = tk.Canvas(vf, bg=_BG, highlightthickness=0)
        self._cv.grid(row=0, column=0, sticky="nsew")

        # ── log ───────────────────────────────────────────────────────────────
        lf = ttk.LabelFrame(self, text="  Log  ", padding=4)
        lf.grid(row=3, column=0, sticky="ew")
        self._log = _Log(lf)
        self._log.pack(fill="both", expand=True)

    # actions ─────────────────────────────────────────────────────────────────

    def _apply(self) -> None:
        try:
            sw = SlidingWindow(
                max_calls=int(self._mc.get()),
                window_seconds=float(self._ws.get()),
            )
        except ValueError as exc:
            self._log.append(f"Error: {exc}")
            return
        with self._guard:
            self._sw = sw
        self._log.append(f"Created {sw!r}")
        if self._after_id is None:
            self._tick()

    def _acquire(self) -> None:
        with self._guard:
            sw = self._sw
        if sw is None:
            self._log.append("Click Apply first.")
            return
        wait = sw.acquire()
        if wait > 0:
            if self._auto.get():
                self._log.append(f"Blocked — sleeping {wait:.3f}s …")
                self.after(
                    int(wait * 1000 + 0.5),
                    lambda: self._log.append("Retry now."),
                )
            else:
                self._log.append(f"Blocked — wait {wait:.3f}s (auto-sleep off)")
        else:
            self._log.append("Admitted immediately.")

    def _reset(self) -> None:
        with self._guard:
            sw = self._sw
        if sw:
            sw.reset()
            self._log.append("Window cleared.")

    # animation ───────────────────────────────────────────────────────────────

    def _tick(self) -> None:
        self._redraw()
        self._after_id = self.after(self._TICK_MS, self._tick)

    def _redraw(self) -> None:
        with self._guard:
            sw = self._sw
        if sw is None:
            return
        s = sw.stats()
        self._s_cur.var.set(str(s.current_calls))
        self._s_adm.var.set(str(s.total_admitted))
        self._s_blk.var.set(str(s.total_blocked))
        self._paint(s, sw.max_calls, sw.window_seconds)

    def _paint(
        self, s: WindowStats, max_calls: int, window_s: float
    ) -> None:
        cv = self._cv
        cv.delete("all")
        W = cv.winfo_width() or 600
        H = cv.winfo_height() or 220

        now = time.monotonic()
        px, py = 52, 40
        tw = W - 2 * px
        cy = H // 2
        th = 20  # track height

        frac = min(1.0, s.current_calls / max_calls) if max_calls > 0 else 0.0
        col = _GREEN if frac < 0.7 else (_YELLOW if frac < 1.0 else _RED)

        # ── track background ──────────────────────────────────────────────────
        cv.create_rectangle(
            px, cy - th // 2, px + tw, cy + th // 2,
            fill=_SURFACE, outline=_OVERLAY, width=1,
        )

        # ── capacity fill bar ─────────────────────────────────────────────────
        if frac > 0:
            cv.create_rectangle(
                px + 1, cy - th // 2 + 1,
                px + 1 + int(frac * (tw - 2)), cy + th // 2 - 1,
                fill=col, outline="",
            )

        # ── call dots ─────────────────────────────────────────────────────────
        for t in s.call_times:
            age = max(0.0, now - t)
            x_frac = 1.0 - age / window_s
            if 0.0 <= x_frac <= 1.0:
                x = px + int(x_frac * tw)
                r = 6
                cv.create_oval(
                    x - r, cy - r, x + r, cy + r,
                    fill=_BLUE, outline=_BG, width=1,
                )

        # ── axis labels ───────────────────────────────────────────────────────
        cv.create_text(
            px, cy - th // 2 - 10,
            text=f"−{window_s}s", anchor="w",
            fill=_OVERLAY, font=("Helvetica", 8),
        )
        cv.create_text(
            px + tw, cy - th // 2 - 10,
            text="now", anchor="e",
            fill=_OVERLAY, font=("Helvetica", 8),
        )

        # ── 'now' vertical marker ─────────────────────────────────────────────
        cv.create_line(
            px + tw, cy - th // 2 - 6,
            px + tw, cy + th // 2 + 6,
            fill=_LAVEND, width=2,
        )

        # ── summary text ──────────────────────────────────────────────────────
        cv.create_text(
            W // 2, cy + th // 2 + 22,
            text=f"{s.current_calls} / {max_calls} calls in the last {window_s}s",
            fill=col, font=("Helvetica", 11, "bold"),
        )
        cv.create_text(
            W // 2, cy + th // 2 + 42,
            text=f"{frac * 100:.0f}% of capacity used",
            fill=_TEXT, font=("Helvetica", 9),
        )

        # ── time-axis tick marks ──────────────────────────────────────────────
        n_ticks = 4
        for i in range(1, n_ticks):
            tx = px + int(tw * i / n_ticks)
            age_label = window_s * (1 - i / n_ticks)
            cv.create_line(tx, cy + th // 2, tx, cy + th // 2 + 4,
                           fill=_OVERLAY, width=1)
            cv.create_text(
                tx, cy + th // 2 + 12,
                text=f"−{age_label:.1f}s",
                fill=_OVERLAY, font=("Helvetica", 7),
            )

    def destroy(self) -> None:
        if self._after_id:
            self.after_cancel(self._after_id)
        super().destroy()


# ── Dashboard window ──────────────────────────────────────────────────────────

def _apply_style(root: tk.Tk) -> None:
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass
    st.configure(".",
                 background=_BG, foreground=_TEXT, fieldbackground=_SURFACE)
    st.configure("TFrame", background=_BG)
    st.configure("TLabelframe", background=_BG)
    st.configure("TLabelframe.Label",
                 background=_BG, foreground=_LAVEND,
                 font=("Helvetica", 9, "bold"))
    st.configure("TLabel", background=_BG, foreground=_TEXT)
    st.configure("TButton",
                 background=_SURFACE, foreground=_TEXT, padding=(6, 3))
    st.map("TButton",
           background=[("active", _OVERLAY), ("pressed", _OVERLAY)],
           foreground=[("active", _TEXT)])
    st.configure("TCheckbutton", background=_BG, foreground=_TEXT)
    st.map("TCheckbutton", background=[("active", _BG)])
    st.configure("TSpinbox",
                 fieldbackground=_SURFACE, foreground=_TEXT,
                 background=_SURFACE, arrowcolor=_TEXT)
    st.configure("TNotebook", background=_BG, tabposition="nw")
    st.configure("TNotebook.Tab",
                 background=_SURFACE, foreground=_SUBTEXT, padding=(12, 5))
    st.map("TNotebook.Tab",
           background=[("selected", _OVERLAY)],
           foreground=[("selected", _TEXT)])
    st.configure("TScrollbar",
                 background=_SURFACE, troughcolor=_BG, arrowcolor=_OVERLAY)


class _Dashboard(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("rateguard — Rate Limiter Dashboard")
        self.geometry("800x600")
        self.minsize(660, 500)
        self.configure(bg=_BG)
        _apply_style(self)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        self._tb = _TokenBucketTab(nb)
        self._sw = _SlidingWindowTab(nb)
        nb.add(self._tb, text="  Token Bucket  ")
        nb.add(self._sw, text="  Sliding Window  ")


def main() -> None:
    """Launch the rateguard interactive dashboard."""
    app = _Dashboard()
    app.mainloop()
