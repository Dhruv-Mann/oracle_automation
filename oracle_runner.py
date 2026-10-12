"""
oracle_runner.py
Master Autonomous Automation Engine for Oracle Academy.
Orchestrates course walking, slide progression, quiz/exam solving with Gemini AI,
and multi-course completion.
"""
import sys
import os

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from oracle_core.engine import start_oracle_automation

if __name__ == "__main__":
    try:
        start_oracle_automation()
    except KeyboardInterrupt:
        print("\n[!] Automation cancelled by user.")
    except Exception as e:
        print(f"\n[ERROR] {e}")
    finally:
        if getattr(sys, "frozen", False):
            print("\n" + "=" * 70)
            print("  Process finished. Press Enter to exit.")
            print("=" * 70)
            try:
                input()
            except Exception:
                pass
