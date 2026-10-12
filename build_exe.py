r"""
build_exe.py
Packages oracle_runner.py and all oracle_core modules into a standalone
Windows .exe using PyInstaller.

Usage:
    python build_exe.py

Output:
    dist\OracleAuto.exe  (drag and drop to run)
"""

import os
import sys
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENTRY_POINT = os.path.join(SCRIPT_DIR, "oracle_runner.py")
DIST_DIR = os.path.join(SCRIPT_DIR, "dist")
BUILD_DIR = os.path.join(SCRIPT_DIR, "build")
OUTPUT_NAME = "OracleAuto"

PYINSTALLER_ARGS = [
    sys.executable, "-m", "PyInstaller",
    "--onefile",                      # Single .exe bundle
    "--console",                      # Keep console window for progress output
    "--noconfirm",                    # Overwrite existing build without asking
    f"--name={OUTPUT_NAME}",
    f"--distpath={DIST_DIR}",
    f"--workpath={BUILD_DIR}",
    "--add-data", f"{os.path.join(SCRIPT_DIR, 'oracle_core')};oracle_core",
    "--collect-all=playwright",
    "--hidden-import=playwright",
    "--hidden-import=playwright.sync_api",
    "--hidden-import=playwright.async_api",
    "--hidden-import=requests",
    "--hidden-import=oracle_core.engine",
    "--hidden-import=oracle_core.dom_interactor",
    "--hidden-import=oracle_core.session_keeper",
    "--hidden-import=oracle_core.quiz_feedback",
    "--hidden-import=oracle_core.backoff_policy",
    "--hidden-import=oracle_core.logger",
    "--hidden-import=oracle_core.ai_solver",
    "--hidden-import=oracle_core.sql_kb",
    ENTRY_POINT,
]

print("=" * 60)
print(f"  Building Oracle Academy Automation EXE")
print(f"  Entry: {ENTRY_POINT}")
print(f"  Output: {DIST_DIR}\\{OUTPUT_NAME}.exe")
print("=" * 60)
print()

# Install PyInstaller if not present
try:
    import PyInstaller
    print("  PyInstaller already installed.")
except ImportError:
    print("  Installing PyInstaller...")
    subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)

print("\n  Building EXE (this may take a few minutes)...")
result = subprocess.run(PYINSTALLER_ARGS)

if result.returncode == 0:
    exe_path = os.path.join(DIST_DIR, f"{OUTPUT_NAME}.exe")
    print("\n" + "=" * 60)
    print(f"  SUCCESS! EXE built at:")
    print(f"  {exe_path}")
    print()
    print("  NOTE: The .exe requires Chrome to be installed on the target")
    print("  machine with remote debugging port 9222 open.")
    print("  Use RUN_ORACLE_AUTO.bat to launch Chrome + the EXE together.")
    print("=" * 60)
else:
    print("\n  BUILD FAILED. Check output above for errors.")
    sys.exit(1)
