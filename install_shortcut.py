#!/usr/bin/env python3
"""install_shortcut.py - add Controller Bridge to the ChromeOS app launcher.

Crostini shows Linux apps in the launcher when it finds a .desktop file in
~/.local/share/applications. This writes one that starts gui.py from this folder.
Afterwards, right-click the icon in the launcher and choose "Pin to shelf" if you like.

Run:   python3 install_shortcut.py           (add or update the shortcut)
       python3 install_shortcut.py --remove  (remove it)
"""
import argparse
import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
WINDOW_CLASS = "Controllerbridge"  # must match gui.WINDOW_CLASS (tested)
FILE_NAME = "controller-bridge.desktop"


def applications_dir():
    data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(data) / "applications"


def quote(path):
    r"""Quote a path for the Exec line of a .desktop file.

    The spec applies two layers: inside double quotes, \ " ` $ need a backslash, and
    then every backslash is doubled again by the general string escaping. So in the
    file, one literal backslash becomes \\\\ and a double quote becomes \\".
    """
    out = str(path).replace("\\", "\\\\")      # quoting layer: \ -> \\
    for ch in '"`$':
        out = out.replace(ch, "\\" + ch)
    out = out.replace("\\", "\\\\")           # string layer: every \ doubled
    return f'"{out}"'


def desktop_entry():
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=Controller Bridge",
        "Comment=Use a Switch Pro Controller as keyboard and mouse in Linux apps",
        f"Exec={quote(sys.executable)} {quote(PROJECT_DIR / 'gui.py')}",
        f"Path={PROJECT_DIR}",
        f"Icon={PROJECT_DIR / 'icon.png'}",
        "Terminal=false",
        "Categories=Game;",
        f"StartupWMClass={WINDOW_CLASS}",
        "",
    ])


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--remove", action="store_true", help="remove the shortcut")
    args = parser.parse_args()
    target = applications_dir() / FILE_NAME
    if args.remove:
        if target.exists():
            target.unlink()
            print(f"Removed {target}")
        else:
            print("No shortcut installed.")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(desktop_entry())
    print(f"Wrote {target}")
    print("Controller Bridge should appear in the ChromeOS launcher within a few seconds.")


if __name__ == "__main__":
    main()
