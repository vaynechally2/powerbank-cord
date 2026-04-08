# Brave Claim V4

Desktop GUI automation helper for Brave with:
- profile+URL lock
- domain whitelist
- selector/text claim detection
- optional image fallback
- cooldown + schedules + clock offset
- TXT/CSV logging + screenshots
- crash recovery + global hotkeys

## Run locally

```bash
py -m pip install playwright pynput psutil pygetwindow pyautogui opencv-python pillow
py brave_claim_v4.py
```

## Build EXE

```bash
py -m pip install pyinstaller
py -m PyInstaller --onefile --noconsole brave_claim_v4.py
```

Output: `dist/brave_claim_v4.exe`

## Build installer

Compile `BraveClaimBot_v4.iss` in Inno Setup.
