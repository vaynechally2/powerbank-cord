# Brave Claim Bot V4

This repository contains:

- `brave_claim_v4.py` — main source file.
- `profiles.json` — starter profile template.
- `BraveClaimBot_v4.iss` — Inno Setup script for installer creation.

## Build EXE (Windows)

```powershell
py -m pip install playwright pynput psutil pygetwindow pyautogui opencv-python pillow pyinstaller
py -m PyInstaller --onefile --noconsole .\brave_claim_v4.py
```

Generated EXE:

- `dist\brave_claim_v4.exe`

## Build Installer (Windows)

1. Install Inno Setup.
2. Open `BraveClaimBot_v4.iss` in Inno Setup.
3. Build.

