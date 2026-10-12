"""
logger.py
Logging system and forensic snapshot capture for Oracle Academy automation.
"""
import os
import sys
import logging
from datetime import datetime

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS_DIR = os.path.join(PROJECT_ROOT, "logs")
SNAPSHOTS_DIR = os.path.join(LOGS_DIR, "snapshots")
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(SNAPSHOTS_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOGS_DIR, "automation.log")

_logger = logging.getLogger("OracleAutomation")
_logger.setLevel(logging.INFO)

if not _logger.handlers:
    _console_handler = logging.StreamHandler(sys.stdout)
    _console_handler.setLevel(logging.INFO)
    _console_formatter = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
    _console_handler.setFormatter(_console_formatter)

    _file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    _file_handler.setLevel(logging.DEBUG)
    _file_formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    _file_handler.setFormatter(_file_formatter)

    _logger.addHandler(_console_handler)
    _logger.addHandler(_file_handler)


def log_info(msg: str):
    _logger.info(f"  [INFO] {msg}")


def log_ok(msg: str):
    _logger.info(f"  [OK] {msg}")


def log_ai(msg: str):
    _logger.info(f"  [AI] {msg}")


def log_warn(msg: str):
    _logger.warning(f"  [WARN] {msg}")


def log_err(msg: str):
    _logger.error(f"  [ERROR] {msg}")


def save_diagnostic_snapshot(page, reason: str) -> dict:
    """
    Captures screenshot and HTML DOM snapshot when an unexpected state occurs.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_path = os.path.join(SNAPSHOTS_DIR, f"error_{timestamp}.png")
    html_path = os.path.join(SNAPSHOTS_DIR, f"error_{timestamp}.html")

    url = "Unknown"
    title = "Unknown"

    if page:
        try:
            url = page.url
        except Exception:
            pass
        try:
            title = page.title()
        except Exception:
            pass
        try:
            page.screenshot(path=screenshot_path, full_page=True)
        except Exception as exc:
            _logger.warning(f"  [WARN] Screenshot capture skipped: {exc}")
        try:
            html = page.content()
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
        except Exception as exc:
            _logger.warning(f"  [WARN] DOM capture skipped: {exc}")

    return {
        "timestamp": timestamp,
        "reason": reason,
        "url": url,
        "title": title
    }
