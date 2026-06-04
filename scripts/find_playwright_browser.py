"""Print path to Playwright's installed Chromium browser directory."""
from __future__ import annotations

import os
import sys


def main() -> None:
    # Use Playwright API to find the installed Chromium binary path
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        exe = p.chromium.executable_path
        if not exe or not os.path.isfile(exe):
            print("", end="")
            sys.exit(1)

        # Navigate from the executable to the chromium-REVISION dir
        #   Linux:   .../chromium-REVISION/chrome-linux/chrome
        #   Windows: .../chromium-REVISION/chrome-win/chrome.exe
        #   macOS:   .../chromium-REVISION/chrome-mac/Chromium.app/...
        for sub in ("chrome-linux64", "chrome-linux", "chrome-win", "chrome-mac"):
            idx = exe.find(sub)
            if idx != -1:
                browser_dir = exe[:idx].rstrip("/\\")
                if os.path.isdir(browser_dir):
                    print(browser_dir)
                    return

        # Fallback: parent of parent
        parent = os.path.dirname(os.path.dirname(exe))
        if os.path.isdir(parent):
            print(parent)
            return

    print("", end="")
    sys.exit(1)


if __name__ == "__main__":
    main()
