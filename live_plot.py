"""Live capture plot: a page on 127.0.0.1 that draws the readings while the capture runs.

The capture loop adds each reading; the page receives it as a server-sent event
as soon as it arrives and redraws at the display refresh rate.
"""

import logging
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from analyzer import AnalysisResult
from capture_plot import final_outcome, milliamps, render_live_page, script_json

logger = logging.getLogger(__name__)

# Loopback only: the page is never reachable from the network.
LOOPBACK_HOST = "127.0.0.1"
# How long closing waits for the page to receive the final outcome.
DEFAULT_FINAL_WAIT_S = 3.0
# How often the server loop checks for shutdown; bounds how long closing takes.
SHUTDOWN_POLL_S = 0.05


class LivePlot:
    """Serve the live plot page and stream the readings of one capture to it.

    Use as a context manager: the server starts on enter. On exit it waits up to
    *final_wait_s* for the page to receive the final outcome, then stops.
    """

    def __init__(self, final_wait_s: float = DEFAULT_FINAL_WAIT_S) -> None:
        self._final_wait_s = final_wait_s
        self._page = render_live_page().encode("utf-8")
        self._times: list[float] = []
        self._currents: list[float | None] = []
        self._values: list[float] = []
        self._final: dict | None = None
        self._is_closing = False
        self._changed = threading.Condition()
        self._final_delivered = threading.Event()
        self._server: ThreadingHTTPServer | None = None

    def __enter__(self) -> "LivePlot":
        return self.start()

    def __exit__(self, *exc_info) -> None:
        self.close()

    def start(self) -> "LivePlot":
        """Start serving on a free loopback port.

        Raises:
            OSError: If no port can be opened.
        """
        self._server = ThreadingHTTPServer((LOOPBACK_HOST, 0), _handler_for(self))
        threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": SHUTDOWN_POLL_S}, name="live-plot", daemon=True
        ).start()
        return self

    @property
    def url(self) -> str:
        """Address of the page, e.g. ``http://127.0.0.1:50123/``."""
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/"

    def add_sample(self, time_s: float, value: float) -> None:
        """Add one reading (amperes, NaN for a failed one); the page gets it at once."""
        with self._changed:
            self._times.append(float(time_s))
            self._currents.append(milliamps(value))
            self._values.append(value)
            self._changed.notify_all()

    def finish(
        self,
        captured_at: datetime,
        analysis: AnalysisResult | None = None,
        error: Exception | None = None,
    ) -> None:
        """Publish the outcome: the page marks the run and the window, or shows the error."""
        with self._changed:
            self._final = final_outcome(self._values, captured_at, analysis, error)
            self._changed.notify_all()

    def close(self) -> None:
        """Give the page up to final_wait_s to receive the outcome, then stop the server."""
        if self._server is None:
            return
        if self._final is None:
            self.finish(datetime.now(timezone.utc), error=RuntimeError("capture ended early, see result.txt"))
        self._final_delivered.wait(self._final_wait_s)
        with self._changed:
            self._is_closing = True
            self._changed.notify_all()
        self._server.shutdown()
        self._server.server_close()

    def _serve_page(self, handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(self._page)))
        handler.end_headers()
        handler.wfile.write(self._page)

    def _serve_events(self, handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        sent = _last_event_id(handler)
        while True:
            with self._changed:
                self._changed.wait_for(lambda: len(self._times) > sent or self._final is not None or self._is_closing)
                times, currents = self._times[sent:], self._currents[sent:]
                final, is_closing = self._final, self._is_closing
            if times:
                sent += len(times)
                _write_event(handler, "samples", {"time_s": times, "current_ma": currents}, event_id=sent)
            if final is not None:
                _write_event(handler, "final", final)
                self._final_delivered.set()
                return
            if is_closing:
                return


def _handler_for(live: LivePlot) -> type[BaseHTTPRequestHandler]:
    class LivePlotHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            try:
                if self.path == "/":
                    live._serve_page(self)
                elif self.path == "/events":
                    live._serve_events(self)
                else:
                    self.send_error(404)
            except (BrokenPipeError, ConnectionResetError):
                logger.debug("Live plot page disconnected")

        def log_message(self, format: str, *args) -> None:
            logger.debug("Live plot: " + format, *args)

    return LivePlotHandler


def _last_event_id(handler: BaseHTTPRequestHandler) -> int:
    """Readings the page already has: EventSource resends the last id when it reconnects."""
    text = handler.headers.get("Last-Event-ID", "0")
    return int(text) if text.isdigit() else 0


def _write_event(handler: BaseHTTPRequestHandler, name: str, payload: dict, event_id: int | None = None) -> None:
    lines = [f"event: {name}"]
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"data: {script_json(payload)}")
    handler.wfile.write(("\n".join(lines) + "\n\n").encode("utf-8"))
    handler.wfile.flush()
