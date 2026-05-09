"""Tkinter dashboard for visualising rateguard rate limiters in real time.

Run as a module::

    python -m rateguard.gui

or via the installed entry-point::

    rateguard-dashboard
"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import messagebox, ttk

from rateguard import SlidingWindow, TokenBucket, __version__

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------
_BG = "#1e1e2e"
_PANEL = "#2a2a3e"
_ACCENT = "#89b4fa"
_GREEN = "#a6e3a1"
_YELLOW = "#f9e2af"
_RED = "#f38ba8"
_TEXT = "#cdd6f4"
_SUBTEXT = "#a6adc8"
_ENTRY_BG = "#313244"
_BTN_BG = "#45475a"
_BTN_ACTIVE = "#585b70"


# ---------------------------------------------------------------------------
# Helper: coloured Canvas bar
# ---------------------------------------------------------------------------
class _Bar(tk.Canvas):
    """Horizontal fill bar (0-100 %)."""

    def __init__(self, parent: tk.Widget, **kwargs: object) -> None:
        super().__init__(parent, height=18, bg=_PANEL, highlightthickness=0, **kwargs)  # type: ignore[arg-type]
        self._fill = self.create_rectangle(0, 0, 0, 18, fill=_GREEN, outline="")
        self._pct: float = 100.0
        self.bind("<Configure>", self._redraw)

    def set(self, pct: float) -> None:
        self._pct = max(0.0, min(100.0, pct))
        self._redraw()

    def _redraw(self, _event: object = None) -> None:
        w = self.winfo_width()
        fill_w = int(w * self._pct / 100)
        color = _GREEN if self._pct > 40 else (_YELLOW if self._pct > 15 else _RED)
        self.coords(self._fill, 0, 0, fill_w, 18)
        self.itemconfigure(self._fill, fill=color)


# ---------------------------------------------------------------------------
# TokenBucket panel
# ---------------------------------------------------------------------------
class _TokenBucketPanel(ttk.Frame):
    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, style="Panel.TFrame")
        self._limiter: TokenBucket | None = None
        self._build()

    def _build(self) -> None:
        _heading(self, "Token Bucket").grid(row=0, column=0, columnspan=4,
                                             sticky="w", pady=(0, 8))

        # Config row
        _label(self, "rate (tok/s)").grid(row=1, column=0, sticky="w")
        self._rate = _entry(self, "10.0", width=8)
        self._rate.grid(row=1, column=1, padx=(4, 12))

        _label(self, "burst").grid(row=1, column=2, sticky="w")
        self._burst = _entry(self, "20", width=6)
        self._burst.grid(row=1, column=3, padx=(4, 0))

        _btn(self, "Create / Reconfigure", self._on_create).grid(
            row=2, column=0, columnspan=4, pady=(8, 0), sticky="ew"
        )

        # Status
        self._bar = _Bar(self)
        self._bar.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(10, 2))

        self._stat_var = tk.StringVar(value="—")
        _label(self, "", textvariable=self._stat_var, fg=_SUBTEXT).grid(
            row=4, column=0, columnspan=4, sticky="w"
        )

        # Acquire controls
        _label(self, "acquire N tokens:").grid(row=5, column=0, sticky="w",
                                                pady=(10, 0))
        self._n_tokens = _entry(self, "1", width=5)
        self._n_tokens.grid(row=5, column=1, padx=(4, 12), pady=(10, 0))
        _btn(self, "Acquire", self._on_acquire).grid(
            row=5, column=2, columnspan=2, pady=(10, 0), sticky="ew"
        )

        self._result_var = tk.StringVar(value="")
        self._result_lbl = _label(self, "", textvariable=self._result_var)
        self._result_lbl.grid(row=6, column=0, columnspan=4, sticky="w",
                               pady=(4, 0))

        self.columnconfigure(1, weight=1)
        self.columnconfigure(3, weight=1)

    def _on_create(self) -> None:
        try:
            rate = float(self._rate.get())
            burst = int(self._burst.get())
            self._limiter = TokenBucket(rate=rate, burst=burst)
            self._result_var.set("Created.")
            self._result_lbl.configure(fg=_GREEN)
        except (ValueError, TypeError) as exc:
            messagebox.showerror("Configuration error", str(exc))

    def _on_acquire(self) -> None:
        if self._limiter is None:
            messagebox.showinfo("Info", "Create a limiter first.")
            return
        try:
            n = int(self._n_tokens.get())
        except ValueError:
            messagebox.showerror("Error", "N must be an integer.")
            return
        try:
            wait = self._limiter.acquire(n)
        except ValueError as exc:
            messagebox.showerror("Error", str(exc))
            return
        if wait == 0.0:
            self._result_var.set("Admitted immediately.")
            self._result_lbl.configure(fg=_GREEN)
        else:
            self._result_var.set(f"Rate-limited — sleep {wait:.3f}s")
            self._result_lbl.configure(fg=_YELLOW)

    def refresh(self) -> None:
        if self._limiter is None:
            return
        s = self._limiter.status()
        avail: float = s["tokens_available"]
        burst: int = s["burst"]
        pct = (avail / burst) * 100 if burst else 0
        self._bar.set(pct)
        self._stat_var.set(
            f"tokens available: {avail:.2f} / {burst}  "
            f"({pct:.0f}%)  rate: {s['rate']} tok/s"
        )


# ---------------------------------------------------------------------------
# SlidingWindow panel
# ---------------------------------------------------------------------------
class _SlidingWindowPanel(ttk.Frame):
    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, style="Panel.TFrame")
        self._limiter: SlidingWindow | None = None
        self._build()

    def _build(self) -> None:
        _heading(self, "Sliding Window").grid(row=0, column=0, columnspan=4,
                                               sticky="w", pady=(0, 8))

        _label(self, "max calls").grid(row=1, column=0, sticky="w")
        self._max_calls = _entry(self, "60", width=6)
        self._max_calls.grid(row=1, column=1, padx=(4, 12))

        _label(self, "window (s)").grid(row=1, column=2, sticky="w")
        self._window = _entry(self, "60.0", width=8)
        self._window.grid(row=1, column=3, padx=(4, 0))

        _btn(self, "Create / Reconfigure", self._on_create).grid(
            row=2, column=0, columnspan=4, pady=(8, 0), sticky="ew"
        )

        self._bar = _Bar(self)
        self._bar.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(10, 2))

        self._stat_var = tk.StringVar(value="—")
        _label(self, "", textvariable=self._stat_var, fg=_SUBTEXT).grid(
            row=4, column=0, columnspan=4, sticky="w"
        )

        _btn(self, "Acquire slot", self._on_acquire).grid(
            row=5, column=0, columnspan=4, pady=(10, 0), sticky="ew"
        )

        self._result_var = tk.StringVar(value="")
        self._result_lbl = _label(self, "", textvariable=self._result_var)
        self._result_lbl.grid(row=6, column=0, columnspan=4, sticky="w",
                               pady=(4, 0))

        self.columnconfigure(1, weight=1)
        self.columnconfigure(3, weight=1)

    def _on_create(self) -> None:
        try:
            max_calls = int(self._max_calls.get())
            window_seconds = float(self._window.get())
            self._limiter = SlidingWindow(max_calls=max_calls,
                                          window_seconds=window_seconds)
            self._result_var.set("Created.")
            self._result_lbl.configure(fg=_GREEN)
        except (ValueError, TypeError) as exc:
            messagebox.showerror("Configuration error", str(exc))

    def _on_acquire(self) -> None:
        if self._limiter is None:
            messagebox.showinfo("Info", "Create a limiter first.")
            return
        wait = self._limiter.acquire()
        if wait == 0.0:
            self._result_var.set("Admitted immediately.")
            self._result_lbl.configure(fg=_GREEN)
        else:
            self._result_var.set(f"Rate-limited — sleep {wait:.3f}s")
            self._result_lbl.configure(fg=_YELLOW)

    def refresh(self) -> None:
        if self._limiter is None:
            return
        s = self._limiter.status()
        used: int = s["calls_in_window"]
        mx: int = s["max_calls"]
        free = mx - used
        pct = (free / mx) * 100 if mx else 0
        self._bar.set(pct)
        self._stat_var.set(
            f"calls in window: {used} / {mx}  "
            f"(free: {free})  window: {s['window_seconds']}s"
        )


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------
class _Log(ttk.Frame):
    def __init__(self, parent: tk.Widget, max_lines: int = 200) -> None:
        super().__init__(parent, style="Panel.TFrame")
        self._max = max_lines

        _heading(self, "Activity log").grid(row=0, column=0, sticky="w",
                                             pady=(0, 4))
        self._text = tk.Text(
            self,
            height=8,
            bg=_ENTRY_BG,
            fg=_TEXT,
            insertbackground=_TEXT,
            relief="flat",
            font=("Courier", 10),
            state="disabled",
            wrap="word",
        )
        self._text.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(self, command=self._text.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self._text.configure(yscrollcommand=sb.set)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

    def append(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self._text.configure(state="normal")
        self._text.insert("end", f"[{ts}] {msg}\n")
        lines = int(self._text.index("end-1c").split(".")[0])
        if lines > self._max:
            self._text.delete("1.0", f"{lines - self._max}.0")
        self._text.see("end")
        self._text.configure(state="disabled")


# ---------------------------------------------------------------------------
# Widget factories (keep styling DRY)
# ---------------------------------------------------------------------------
def _heading(parent: tk.Widget, text: str) -> tk.Label:
    return tk.Label(
        parent, text=text, bg=_PANEL, fg=_ACCENT,
        font=("Helvetica", 13, "bold"),
    )


def _label(
    parent: tk.Widget,
    text: str,
    textvariable: tk.StringVar | None = None,
    fg: str = _TEXT,
) -> tk.Label:
    kw = dict(bg=_PANEL, fg=fg, font=("Helvetica", 10))
    if textvariable is not None:
        return tk.Label(parent, textvariable=textvariable, **kw)  # type: ignore[arg-type]
    return tk.Label(parent, text=text, **kw)  # type: ignore[arg-type]


def _entry(parent: tk.Widget, default: str, width: int = 10) -> tk.Entry:
    e = tk.Entry(
        parent, width=width, bg=_ENTRY_BG, fg=_TEXT,
        insertbackground=_TEXT, relief="flat",
        font=("Helvetica", 10),
    )
    e.insert(0, default)
    return e


def _btn(parent: tk.Widget, text: str, cmd: object) -> tk.Button:
    return tk.Button(
        parent, text=text, command=cmd,  # type: ignore[arg-type]
        bg=_BTN_BG, fg=_TEXT, activebackground=_BTN_ACTIVE,
        activeforeground=_TEXT, relief="flat",
        font=("Helvetica", 10), padx=8, pady=4,
    )


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------
class Dashboard:
    """Main rateguard dashboard window.

    Args:
        root: Existing :class:`tk.Tk` instance.  When *None* a new one
            is created automatically.
        refresh_ms: Milliseconds between automatic status refreshes.
    """

    def __init__(
        self,
        root: tk.Tk | None = None,
        refresh_ms: int = 500,
    ) -> None:
        self._root = root or tk.Tk()
        self._refresh_ms = refresh_ms
        self._build()

    def _build(self) -> None:
        root = self._root
        root.title(f"rateguard dashboard  v{__version__}")
        root.configure(bg=_BG)
        root.resizable(True, True)
        root.minsize(540, 640)

        # ttk styles
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("Panel.TFrame", background=_PANEL)
        style.configure("TScrollbar", background=_BTN_BG, troughcolor=_ENTRY_BG)

        pad = dict(padx=14, pady=10, sticky="nsew")

        title = tk.Label(
            root,
            text=f"rateguard  {__version__}",
            bg=_BG, fg=_ACCENT,
            font=("Helvetica", 16, "bold"),
        )
        title.grid(row=0, column=0, columnspan=2, pady=(14, 4))

        subtitle = tk.Label(
            root,
            text="Local rate limiter for LLM API calls",
            bg=_BG, fg=_SUBTEXT,
            font=("Helvetica", 10),
        )
        subtitle.grid(row=1, column=0, columnspan=2, pady=(0, 10))

        sep = ttk.Separator(root, orient="horizontal")
        sep.grid(row=2, column=0, columnspan=2, sticky="ew", padx=14)

        self._tb_panel = _TokenBucketPanel(root)
        self._tb_panel.grid(row=3, column=0, **pad)

        self._sw_panel = _SlidingWindowPanel(root)
        self._sw_panel.grid(row=3, column=1, **pad)

        sep2 = ttk.Separator(root, orient="horizontal")
        sep2.grid(row=4, column=0, columnspan=2, sticky="ew", padx=14)

        self._log = _Log(root)
        self._log.grid(row=5, column=0, columnspan=2, **pad)

        # Patch acquire to log results
        self._patch_panels()

        root.columnconfigure(0, weight=1)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(5, weight=1)

        self._schedule_refresh()

    def _patch_panels(self) -> None:
        """Wrap panel acquire callbacks to write to the log."""
        log = self._log

        tb_orig = self._tb_panel._on_acquire  # noqa: SLF001

        def tb_acquire_logged() -> None:
            before = self._tb_panel._limiter
            tb_orig()
            if before is not None:
                s = before.status()
                avail = s["tokens_available"]
                log.append(
                    f"TokenBucket.acquire({self._tb_panel._n_tokens.get()}) → "
                    f"result shown above  |  tokens_available={avail:.2f}"
                )

        self._tb_panel._on_acquire = tb_acquire_logged  # type: ignore[method-assign]
        # re-bind button
        for child in self._tb_panel.winfo_children():
            if isinstance(child, tk.Button) and child["text"] == "Acquire":
                child.configure(command=tb_acquire_logged)

        sw_orig = self._sw_panel._on_acquire  # noqa: SLF001

        def sw_acquire_logged() -> None:
            sw_orig()
            if self._sw_panel._limiter is not None:
                s = self._sw_panel._limiter.status()
                log.append(
                    f"SlidingWindow.acquire() → result shown above  |  "
                    f"calls_in_window={s['calls_in_window']}/{s['max_calls']}"
                )

        self._sw_panel._on_acquire = sw_acquire_logged  # type: ignore[method-assign]
        for child in self._sw_panel.winfo_children():
            if isinstance(child, tk.Button) and child["text"] == "Acquire slot":
                child.configure(command=sw_acquire_logged)

    def _schedule_refresh(self) -> None:
        self._tb_panel.refresh()
        self._sw_panel.refresh()
        self._root.after(self._refresh_ms, self._schedule_refresh)

    def run(self) -> None:
        """Start the Tk main loop (blocks until the window is closed)."""
        self._root.mainloop()


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------
def main() -> None:
    """Launch the rateguard dashboard."""
    Dashboard().run()


if __name__ == "__main__":  # pragma: no cover
    main()
