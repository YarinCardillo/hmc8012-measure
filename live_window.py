"""Live plot window: a native window (pywebview; WebView2 on Windows) in a child process.

The window runs in a process of its own, a second hmc.exe started with
``--live-window <url>``, so it stays open with the outcome after the capture
process has written result.txt and exited.
"""

import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Callable

LIVE_WINDOW_FLAG = "--live-window"
WINDOW_TITLE = "HMC8012 cattura"
# Close to a console window, so the live plot does not take the whole screen.
WINDOW_SIZE = (1000, 640)
MIN_WINDOW_SIZE = (600, 400)


def open_live_window(url: str, launch: Callable[..., object] = subprocess.Popen) -> None:
    """Start the window process for *url* without waiting for it.

    Raises:
        OSError: If the process cannot be started.
    """
    devnull = subprocess.DEVNULL
    launch(_window_command(url), stdin=devnull, stdout=devnull, stderr=devnull, **_detached_options())


def run_live_window(
    url: str,
    show: Callable[[str], None] | None = None,
    open_tab: Callable[[str], object] = webbrowser.open,
) -> None:
    """Window process entry: show *url* in a native window until the user closes it.

    Falls back to a browser tab when no web view is available, so the plot is
    never lost.
    """
    try:
        (show or _show_native_window)(url)
    except Exception as exc:
        print(f"[APP] No native window ({type(exc).__name__}: {exc}), opening a browser tab.", file=sys.stderr)
        open_tab(url)


def _window_command(url: str) -> list[str]:
    # Nuitka defines __compiled__ in compiled modules; sys.argv[0] is then hmc.exe itself.
    if "__compiled__" in globals():
        return [str(Path(sys.argv[0]).resolve()), LIVE_WINDOW_FLAG, url]
    return [sys.executable, str(Path(__file__).with_name("measure.py")), LIVE_WINDOW_FLAG, url]


def _detached_options() -> dict:
    if sys.platform == "win32":
        # No console window, and the window survives the capture process.
        return {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _show_native_window(url: str) -> None:
    import webview

    if sys.platform == "win32":
        from webview.platforms import winforms

        # Without WebView2, pywebview silently falls back to the Internet Explorer engine, where uPlot cannot run.
        if winforms.renderer != "edgechromium":
            raise RuntimeError(f"WebView2 runtime not found (pywebview renderer: {winforms.renderer})")
    width, height = WINDOW_SIZE
    webview.create_window(WINDOW_TITLE, url, width=width, height=height, min_size=MIN_WINDOW_SIZE)
    webview.start(private_mode=True)
