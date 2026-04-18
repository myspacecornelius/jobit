"""Job Application AI Agent.

Loads `.env` at import time so every module that calls `get_config()` sees
the user's overrides. Searches from CWD upward, then falls back to the
package's project root.
"""

from pathlib import Path

try:
    from dotenv import load_dotenv

    _project_root = Path(__file__).resolve().parent.parent
    _candidates = [
        Path.cwd() / ".env",
        _project_root / ".env",
    ]
    for _env_path in _candidates:
        if _env_path.is_file():
            load_dotenv(_env_path, override=False)
            break
except ImportError:
    pass

__version__ = "0.2.0"
