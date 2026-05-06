"""rateguard.gui - interactive terminal dashboard for rateguard.

Launch with::

    python -m rateguard.gui

or, after installation with the ``gui`` extra::

    rateguard-gui

The dashboard shows two panels side by side.  The left panel controls a
:class:`~rateguard.TokenBucket`; the right controls a
:class:`~rateguard.SlidingWindow`.  Each panel has live configuration inputs,
a visual gauge, acquire/reset buttons, an auto-fire toggle, and a status line.
A shared log at the bottom records every acquire event.
"""
from __future__ import annotations

import time
from typing import Optional

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.timer import Timer
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    Log,
    Rule,
    Static,
    Switch,
)
from textual import on

from rateguard import TokenBucket, SlidingWindow, __version__


# ── shared helpers ────────────────────────────────────────────────────────────

def _bar(filled: int, total: int, width: int = 26) -> str:
    """Return a Rich-marked up ASCII progress bar."""
    n = max(0, min(width, round(filled / total * width))) if total else 0
    return f"[cyan]{'█' * n}{'░' * (width - n)}[/cyan]"


# ── Token Bucket panel ────────────────────────────────────────────────────────

class TokenBucketPanel(Vertical):
    """Left panel: live TokenBucket controls and gauge."""

    DEFAULT_CSS = """
    TokenBucketPanel {
        width: 1fr;
        border: round $primary;
        padding: 1 2;
        margin: 0 1 0 0;
        height: 100%;
    }
    TokenBucketPanel .row {
        height: 3;
        margin-bottom: 0;
    }
    TokenBucketPanel .lbl {
        width: 16;
        padding: 1 0;
    }
    TokenBucketPanel #tb-gauge {
        height: 3;
        content-align: center middle;
        padding: 0 1;
        margin: 1 0;
        border: solid $primary-darken-2;
    }
    TokenBucketPanel .panel-title {
        text-align: center;
        text-style: bold;
        color: $primary;
        height: 2;
    }
    TokenBucketPanel #tb-btn-row {
        height: 3;
        margin-top: 1;
    }
    TokenBucketPanel #tb-auto-row {
        height: 3;
    }
    """

    _bucket: TokenBucket
    _auto_timer: Optional[Timer] = None

    def compose(self) -> ComposeResult:
        yield Label("⬡ Token Bucket", classes="panel-title")
        yield Rule()
        with Horizontal(classes="row"):
            yield Label("Rate (tok/s)", classes="lbl")
            yield Input("10.0", id="tb-rate")
        with Horizontal(classes="row"):
            yield Label("Burst       ", classes="lbl")
            yield Input("20", id="tb-burst")
        yield Static("", id="tb-gauge")
        yield Rule()
        with Horizontal(id="tb-btn-row"):
            yield Button("Acquire 1", id="tb-acq1", variant="primary")
            yield Button("Acquire 5", id="tb-acq5")
            yield Button("Reset", id="tb-reset", variant="warning")
        with Horizontal(id="tb-auto-row"):
            yield Label("Auto-fire (0.5 s): ")
            yield Switch(animate=False, id="tb-auto")
        yield Label("Status: Ready", id="tb-status")
        yield Label("Last wait: —", id="tb-wait")

    def on_mount(self) -> None:
        self._bucket = TokenBucket(rate=10.0, burst=20)
        self.set_interval(0.1, self._refresh_gauge)

    # ── gauge refresh ─────────────────────────────────────────────────

    def _refresh_gauge(self) -> None:
        s = self._bucket.stats()
        tokens, burst = s["tokens"], s["burst"]
        gauge = self.query_one("#tb-gauge", Static)
        bar = _bar(round(tokens), burst)
        pct = s["fill_ratio"] * 100
        gauge.update(f"{bar} {tokens:.1f}/{burst} ({pct:.0f}%)")

    # ── config helpers ────────────────────────────────────────────────

    def _parse_config(self) -> Optional[tuple[float, int]]:
        try:
            rate = float(self.query_one("#tb-rate", Input).value)
            burst = int(self.query_one("#tb-burst", Input).value)
            if rate <= 0 or burst < 1:
                raise ValueError
            return rate, burst
        except (ValueError, TypeError):
            return None

    def _rebuild(self) -> None:
        cfg = self._parse_config()
        if cfg:
            rate, burst = cfg
            self._bucket = TokenBucket(rate=rate, burst=burst)

    @on(Input.Submitted)
    def _on_input_submitted(self, _: Input.Submitted) -> None:
        self._rebuild()

    # ── button handlers ───────────────────────────────────────────────

    @on(Button.Pressed, "#tb-acq1")
    def _acq1(self, _: Button.Pressed) -> None:
        self._do_acquire(1)

    @on(Button.Pressed, "#tb-acq5")
    def _acq5(self, _: Button.Pressed) -> None:
        cfg = self._parse_config()
        n = min(5, cfg[1] if cfg else 1)
        self._do_acquire(n)

    @on(Button.Pressed, "#tb-reset")
    def _reset(self, _: Button.Pressed) -> None:
        self._bucket.reset()
        self.query_one("#tb-status", Label).update("Status: [green]Reset ✓[/green]")
        self.app.log_event("TokenBucket.reset()")

    @on(Switch.Changed, "#tb-auto")
    def _toggle_auto(self, event: Switch.Changed) -> None:
        if event.value:
            self._auto_timer = self.set_interval(0.5, lambda: self._do_acquire(1))
        else:
            if self._auto_timer is not None:
                self._auto_timer.stop()
                self._auto_timer = None

    # ── core acquire ──────────────────────────────────────────────────

    def _do_acquire(self, n: int) -> None:
        try:
            wait = self._bucket.acquire(n)
        except ValueError as exc:
            self.query_one("#tb-status", Label).update(f"Status: [red]{exc}[/red]")
            return
        if wait == 0.0:
            msg = "[green]Admitted ✓[/green]"
        else:
            msg = f"[yellow]Wait {wait:.3f} s[/yellow]"
        self.query_one("#tb-status", Label).update(f"Status: {msg}")
        self.query_one("#tb-wait", Label).update(f"Last wait: {wait:.4f} s")
        self.app.log_event(f"TokenBucket.acquire({n}) → wait={wait:.4f} s")


# ── Sliding Window panel ──────────────────────────────────────────────────────

class SlidingWindowPanel(Vertical):
    """Right panel: live SlidingWindow controls and call counter."""

    DEFAULT_CSS = """
    SlidingWindowPanel {
        width: 1fr;
        border: round $accent;
        padding: 1 2;
        height: 100%;
    }
    SlidingWindowPanel .row {
        height: 3;
        margin-bottom: 0;
    }
    SlidingWindowPanel .lbl {
        width: 16;
        padding: 1 0;
    }
    SlidingWindowPanel #sw-gauge {
        height: 3;
        content-align: center middle;
        padding: 0 1;
        margin: 1 0;
        border: solid $accent-darken-2;
    }
    SlidingWindowPanel .panel-title {
        text-align: center;
        text-style: bold;
        color: $accent;
        height: 2;
    }
    SlidingWindowPanel #sw-btn-row {
        height: 3;
        margin-top: 1;
    }
    SlidingWindowPanel #sw-auto-row {
        height: 3;
    }
    """

    _window: SlidingWindow
    _auto_timer: Optional[Timer] = None

    def compose(self) -> ComposeResult:
        yield Label("⧖ Sliding Window", classes="panel-title")
        yield Rule()
        with Horizontal(classes="row"):
            yield Label("Max calls   ", classes="lbl")
            yield Input("10", id="sw-max")
        with Horizontal(classes="row"):
            yield Label("Window (s)  ", classes="lbl")
            yield Input("60.0", id="sw-window")
        yield Static("", id="sw-gauge")
        yield Rule()
        with Horizontal(id="sw-btn-row"):
            yield Button("Acquire", id="sw-acq", variant="primary")
            yield Button("Reset", id="sw-reset", variant="warning")
        with Horizontal(id="sw-auto-row"):
            yield Label("Auto-fire (0.5 s): ")
            yield Switch(animate=False, id="sw-auto")
        yield Label("Status: Ready", id="sw-status")
        yield Label("Last wait: —", id="sw-wait")

    def on_mount(self) -> None:
        self._window = SlidingWindow(max_calls=10, window_seconds=60.0)
        self.set_interval(0.1, self._refresh_gauge)

    # ── gauge refresh ─────────────────────────────────────────────────

    def _refresh_gauge(self) -> None:
        s = self._window.stats()
        count = s["calls_in_window"]
        total = s["max_calls"]
        gauge = self.query_one("#sw-gauge", Static)
        bar = _bar(count, total)
        avail = s["available"]
        gauge.update(f"{bar} {count}/{total} used  ({avail} free)")

    # ── config helpers ────────────────────────────────────────────────

    def _parse_config(self) -> Optional[tuple[int, float]]:
        try:
            max_calls = int(self.query_one("#sw-max", Input).value)
            window = float(self.query_one("#sw-window", Input).value)
            if max_calls < 1 or window <= 0:
                raise ValueError
            return max_calls, window
        except (ValueError, TypeError):
            return None

    def _rebuild(self) -> None:
        cfg = self._parse_config()
        if cfg:
            max_calls, window = cfg
            self._window = SlidingWindow(max_calls=max_calls, window_seconds=window)

    @on(Input.Submitted)
    def _on_input_submitted(self, _: Input.Submitted) -> None:
        self._rebuild()

    # ── button handlers ───────────────────────────────────────────────

    @on(Button.Pressed, "#sw-acq")
    def _acq(self, _: Button.Pressed) -> None:
        self._do_acquire()

    @on(Button.Pressed, "#sw-reset")
    def _reset(self, _: Button.Pressed) -> None:
        self._window.reset()
        self.query_one("#sw-status", Label).update("Status: [green]Reset ✓[/green]")
        self.app.log_event("SlidingWindow.reset()")

    @on(Switch.Changed, "#sw-auto")
    def _toggle_auto(self, event: Switch.Changed) -> None:
        if event.value:
            self._auto_timer = self.set_interval(0.5, self._do_acquire)
        else:
            if self._auto_timer is not None:
                self._auto_timer.stop()
                self._auto_timer = None

    # ── core acquire ──────────────────────────────────────────────────

    def _do_acquire(self) -> None:
        wait = self._window.acquire()
        if wait == 0.0:
            msg = "[green]Admitted ✓[/green]"
        else:
            msg = f"[yellow]Wait {wait:.3f} s[/yellow]"
        self.query_one("#sw-status", Label).update(f"Status: {msg}")
        self.query_one("#sw-wait", Label).update(f"Last wait: {wait:.4f} s")
        self.app.log_event(f"SlidingWindow.acquire() → wait={wait:.4f} s")


# ── Main application ──────────────────────────────────────────────────────────

class RateGuardApp(App[None]):
    """Interactive terminal dashboard for rateguard.

    The two panels operate independently; each holds its own limiter instance.
    Editing a config field and pressing **Enter** rebuilds the limiter.
    """

    TITLE = f"rateguard  v{__version__}"
    SUB_TITLE = "local rate-limiter dashboard"

    CSS = """
    Screen {
        layout: vertical;
    }
    #panels {
        layout: horizontal;
        height: 1fr;
        min-height: 22;
    }
    #log-section {
        height: 8;
        border: round $surface;
        padding: 0 1;
    }
    #log-title {
        text-style: bold;
        color: $text-muted;
        height: 1;
    }
    Log {
        height: 6;
        scrollbar-gutter: stable;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("ctrl+l", "clear_log", "Clear log"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="panels"):
            yield TokenBucketPanel()
            yield SlidingWindowPanel()
        with Vertical(id="log-section"):
            yield Label("Event log", id="log-title")
            yield Log(id="event-log", max_lines=200)
        yield Footer()

    def log_event(self, message: str) -> None:
        """Append a timestamped message to the event log."""
        ts = time.strftime("%H:%M:%S")
        self.query_one("#event-log", Log).write_line(f"{ts}  {message}")

    def action_clear_log(self) -> None:
        self.query_one("#event-log", Log).clear()


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    """Launch the rateguard terminal dashboard."""
    RateGuardApp().run()


if __name__ == "__main__":
    main()
