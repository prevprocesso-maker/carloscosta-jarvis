"""
JARVIS terminal quiet mode.

Loaded automatically by Python and imported explicitly by main.py. It blocks
third-party warning spam (mainly sounddevice/NumPy/DDG) while preserving real
exceptions and JARVIS logs.
"""
from __future__ import annotations

import os
import sys
import warnings

os.environ.setdefault("PYTHONWARNINGS", "ignore")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
os.environ.setdefault("GLOG_minloglevel", "3")

warnings.simplefilter("ignore")
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*duckduckgo_search.*")
warnings.filterwarnings("ignore", message=".*Setting the shape on a NumPy array.*")
warnings.filterwarnings("ignore", message=".*non-data parts in the response.*")

_NOISY_PATTERNS = (
    "sounddevice.py:2794",
    "setting the shape on a numpy array has been deprecated",
    "as an alternative, you can create a new view using np.reshape",
    "data.shape = -1, channels",
    "duckduckgo_search",
    "has been renamed to `ddgs`",
    "non-data parts in the response",
    "returning concatenated data result from data parts",
)


def _is_noisy(text: str) -> bool:
    low = str(text or "").lower()
    return any(pattern in low for pattern in _NOISY_PATTERNS)


_ORIGINAL_WARN = warnings.warn
_ORIGINAL_SHOWWARNING = warnings.showwarning


def _quiet_warn(message, category=None, stacklevel=1, source=None, **kwargs):
    if _is_noisy(str(message)):
        return None
    # Keep all other warnings suppressed by default, because this is a user UI terminal.
    return None


def _quiet_showwarning(message, category, filename, lineno, file=None, line=None):
    combined = f"{filename}:{lineno}: {getattr(category, '__name__', category)}: {message}\n{line or ''}"
    if _is_noisy(combined):
        return None
    return None


warnings.warn = _quiet_warn
warnings.showwarning = _quiet_showwarning


class _FilteredStream:
    """Line-based stream filter for libraries that write warning text directly."""

    def __init__(self, wrapped):
        self._wrapped = wrapped
        self._buffer = ""

    def write(self, data):
        text = str(data or "")
        if not text:
            return 0
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if not _is_noisy(line):
                self._safe_write(line + "\n")
        # Drop noisy partial chunks early. Keep normal partial terminal output intact.
        if _is_noisy(self._buffer):
            self._buffer = ""
        elif len(self._buffer) > 600:
            self._safe_write(self._buffer)
            self._buffer = ""
        return len(text)

    def flush(self):
        if self._buffer:
            if not _is_noisy(self._buffer):
                self._safe_write(self._buffer)
            self._buffer = ""
        if self._wrapped is not None:
            return self._wrapped.flush()
        return None

    def _safe_write(self, text):
        if self._wrapped is None:
            return len(text)
        try:
            return self._wrapped.write(text)
        except UnicodeEncodeError:
            encoding = getattr(self._wrapped, "encoding", None) or "utf-8"
            safe = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
            return self._wrapped.write(safe)

    def isatty(self):
        return getattr(self._wrapped, "isatty", lambda: False)()

    def fileno(self):
        if self._wrapped is None:
            raise OSError("stream sem descritor")
        return self._wrapped.fileno()

    @property
    def encoding(self):
        return getattr(self._wrapped, "encoding", "utf-8")

    @property
    def errors(self):
        return getattr(self._wrapped, "errors", "replace")

    def __getattr__(self, name):
        return getattr(self._wrapped, name)


if not getattr(sys.stderr, "_mark_xlviii_filtered", False):
    filtered_err = _FilteredStream(sys.stderr)
    filtered_err._mark_xlviii_filtered = True
    sys.stderr = filtered_err

if not getattr(sys.stdout, "_mark_xlviii_filtered", False):
    filtered_out = _FilteredStream(sys.stdout)
    filtered_out._mark_xlviii_filtered = True
    sys.stdout = filtered_out
