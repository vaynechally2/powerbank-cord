import os
import csv
import json
import time
import random
import threading
import subprocess
from datetime import datetime, timedelta
from urllib.parse import urlparse
import tkinter as tk
from tkinter import ttk, messagebox

import psutil
import pygetwindow as gw
import pyautogui
from pynput import keyboard as pynput_keyboard
from pynput import mouse as pynput_mouse
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

APP_NAME = "Brave Claim V4"
PROFILES_FILE = "profiles.json"

pyautogui.FAILSAFE = True


def now_corrected(offset_sec: float) -> datetime:
    return datetime.now() - timedelta(seconds=offset_sec)


def ts(offset_sec: float) -> str:
    return now_corrected(offset_sec).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def booly(s: str) -> bool:
    return str(s).strip().lower() in ("1", "true", "yes", "y", "on")


def split_lines(s: str):
    return [x.strip() for x in (s or "").splitlines() if x.strip()]


def in_time_window(start_hhmm: str, end_hhmm: str, offset_sec: float) -> bool:
    now = now_corrected(offset_sec).strftime("%H:%M")
    if start_hhmm <= end_hhmm:
        return start_hhmm <= now < end_hhmm
    return now >= start_hhmm or now < end_hhmm


def is_weekend(offset_sec: float) -> bool:
    return now_corrected(offset_sec).weekday() >= 5


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().strip()
    except Exception:
        return ""


def domain_allowed(url: str, whitelist_csv: str) -> bool:
    host = host_of(url)
    allow = [d.strip().lower() for d in whitelist_csv.split(",") if d.strip()]
    if not allow:
        return False
    for domain in allow:
        if host == domain or host.endswith("." + domain):
            return True
    return False


def is_brave_running() -> bool:
    for proc in psutil.process_iter(["name"]):
        name = (proc.info.get("name") or "").lower()
        if name == "brave.exe":
            return True
    return False


def launch_brave_if_needed(brave_exe, profile_dir, target_url):
    if not is_brave_running():
        subprocess.Popen([brave_exe, f"--profile-directory={profile_dir}", target_url])
        time.sleep(2.5)


def find_any_brave_hwnd():
    wins = gw.getWindowsWithTitle("Brave")
    for win in wins:
        if win.width > 0 and win.height > 0:
            return win._hWnd
    return None


def hwnd_exists(hwnd: int) -> bool:
    try:
        return any(getattr(win, "_hWnd", None) == hwnd for win in gw.getAllWindows())
    except Exception:
        return False


def get_window_by_hwnd(hwnd: int):
    try:
        for win in gw.getAllWindows():
            if getattr(win, "_hWnd", None) == hwnd:
                return win
    except Exception:
        pass
    return None


class Bot:
    def __init__(self, ui):
        self.ui = ui
        self.running = False
        self.paused = False
        self.stop_event = threading.Event()
        self.thread = None
        self.last_click = 0.0
        self.last_user_activity = 0.0
        self.bound_hwnd = None
        self.k_listener = None
        self.m_listener = None

    def log_txt(self, cfg, msg):
        line = f"[{ts(cfg['clock_offset_sec'])}] {msg}\n"
        with open(cfg["log_txt_path"], "a", encoding="utf-8") as file:
            file.write(line)
        self.ui.set_status(msg)

    def log_csv(self, cfg, event, detail=""):
        first = not os.path.exists(cfg["log_csv_path"])
        with open(cfg["log_csv_path"], "a", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            if first:
                writer.writerow(["timestamp", "event", "detail"])
            writer.writerow([ts(cfg["clock_offset_sec"]), event, detail])

    def touch_activity(self, *args):
        self.last_user_activity = time.time()

    def start_activity_monitors(self):
        self.k_listener = pynput_keyboard.Listener(on_press=self.touch_activity)
        self.m_listener = pynput_mouse.Listener(
            on_move=self.touch_activity,
            on_click=self.touch_activity,
            on_scroll=self.touch_activity,
        )
        self.k_listener.start()
        self.m_listener.start()

    def stop_activity_monitors(self):
        if self.k_listener:
            self.k_listener.stop()
            self.k_listener = None
        if self.m_listener:
            self.m_listener.stop()
            self.m_listener = None

    def user_active_recently(self, idle_required_sec: float) -> bool:
        return (time.time() - self.last_user_activity) < idle_required_sec

    def in_allowed_schedule(self, cfg) -> bool:
        if cfg["use_weekday_weekend_schedule"]:
            if is_weekend(cfg["clock_offset_sec"]):
                return in_time_window(cfg["weekend_start"], cfg["weekend_end"], cfg["clock_offset_sec"])
            return in_time_window(cfg["weekday_start"], cfg["weekday_end"], cfg["clock_offset_sec"])
        return in_time_window(cfg["allowed_start"], cfg["allowed_end"], cfg["clock_offset_sec"])

    def try_selector_or_text_click(self, page, cfg):
        if time.time() - self.last_click < cfg["click_cooldown_sec"]:
            return False, "cooldown"

        for selector in split_lines(cfg["selectors_multiline"]):
            try:
                loc = page.locator(selector).first
                if loc.count() > 0 and loc.is_visible():
                    loc.click(timeout=2000)
                    self.last_click = time.time()
                    return True, f"selector:{selector}"
            except Exception:
                pass

        for text in split_lines(cfg["fallback_texts_multiline"]):
            try:
                loc = page.get_by_text(text, exact=False).first
                if loc.count() > 0 and loc.is_visible():
                    loc.click(timeout=2000)
                    self.last_click = time.time()
                    return True, f"text:{text}"
            except Exception:
                pass

        return False, "not_found"

    def try_image_click_fallback(self, cfg):
        if not cfg["use_image_fallback"]:
            return False, "image_fallback_disabled"

        image_path = cfg["reference_image_path"]
        if not image_path or not os.path.exists(image_path):
            return False, "image_missing"

        win = get_window_by_hwnd(self.bound_hwnd) if self.bound_hwnd else None
        if not win:
            return False, "hwnd_window_missing"

        region = (win.left, win.top, win.width, win.height)
        try:
            loc = pyautogui.locateOnScreen(image_path, confidence=cfg["image_confidence"], region=region)
            if loc:
                x, y = pyautogui.center(loc)
                pyautogui.moveTo(x, y, duration=random.uniform(0.15, 0.35))
                time.sleep(random.uniform(0.08, 0.18))
                pyautogui.click()
                self.last_click = time.time()
                return True, f"image:{image_path}"
            return False, "image_not_found"
        except Exception as exc:
            return False, f"image_error:{exc}"

    def screenshot(self, page, cfg):
        if not cfg["screenshot_on_click"]:
            return ""
        os.makedirs(cfg["screenshot_dir"], exist_ok=True)
        path = os.path.join(
            cfg["screenshot_dir"],
            now_corrected(cfg["clock_offset_sec"]).strftime("click_%Y%m%d_%H%M%S_%f")[:-3] + ".png",
        )
        page.screenshot(path=path, full_page=False)
        return path

    def run(self, cfg):
        self.running = True
        self.paused = False
        self.stop_event.clear()
        self.last_click = 0.0
        self.last_user_activity = 0.0

        self.start_activity_monitors()
        self.log_txt(cfg, "START")
        self.log_csv(cfg, "START", "")

        launch_brave_if_needed(cfg["brave_exe_path"], cfg["brave_profile_dir"], cfg["target_url"])

        self.bound_hwnd = find_any_brave_hwnd()
        if not self.bound_hwnd:
            self.log_txt(cfg, "ERROR: could not bind Brave HWND")
            self.log_csv(cfg, "ERROR", "no_hwnd")
            self.running = False
            self.stop_activity_monitors()
            return

        refresh = max(cfg["refresh_min_sec"], min(cfg["refresh_interval_sec"], cfg["refresh_max_sec"]))
        next_refresh = time.time() + refresh

        crashes = 0
        while not self.stop_event.is_set():
            try:
                with sync_playwright() as p:
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=cfg["brave_user_data_dir"],
                        executable_path=cfg["brave_exe_path"],
                        headless=False,
                        args=[f"--profile-directory={cfg['brave_profile_dir']}"]
                    )

                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto(cfg["target_url"], wait_until="domcontentloaded", timeout=45000)

                    while not self.stop_event.is_set():
                        if self.paused:
                            time.sleep(0.2)
                            continue

                        if cfg["pause_on_user_activity"] and self.user_active_recently(cfg["user_idle_required_sec"]):
                            self.ui.set_status("Paused: user activity")
                            time.sleep(0.2)
                            continue

                        if not self.in_allowed_schedule(cfg):
                            self.ui.set_status("Outside allowed schedule")
                            time.sleep(0.3)
                            continue

                        if not hwnd_exists(self.bound_hwnd):
                            self.log_txt(cfg, "HWND lock failed; waiting...")
                            self.log_csv(cfg, "HWND_FAIL", "")
                            time.sleep(0.5)
                            continue

                        cur = page.url or ""
                        if cfg["strict_url_match"]:
                            url_ok = (cur == cfg["target_url"])
                        else:
                            url_ok = cur.startswith(cfg["target_url_prefix"])

                        if not url_ok:
                            self.log_txt(cfg, f"URL_BLOCK: {cur}")
                            self.log_csv(cfg, "URL_BLOCK", cur)
                            time.sleep(0.5)
                            continue

                        if not domain_allowed(cur, cfg["domain_whitelist_csv"]):
                            self.log_txt(cfg, f"DOMAIN_BLOCK: {cur}")
                            self.log_csv(cfg, "DOMAIN_BLOCK", cur)
                            time.sleep(0.5)
                            continue

                        now = time.time()
                        if now >= next_refresh:
                            try:
                                page.reload(wait_until="domcontentloaded", timeout=30000)
                                self.log_txt(cfg, "REFRESH")
                                self.log_csv(cfg, "REFRESH", "")
                            except PWTimeoutError:
                                self.log_txt(cfg, "REFRESH_TIMEOUT")
                                self.log_csv(cfg, "REFRESH_TIMEOUT", "")
                            except Exception as exc:
                                self.log_txt(cfg, f"REFRESH_ERR: {exc}")
                                self.log_csv(cfg, "REFRESH_ERR", str(exc))

                            jitter = random.uniform(-cfg["refresh_jitter_sec"], cfg["refresh_jitter_sec"])
                            next_refresh = time.time() + max(0.5, refresh + jitter)

                        clicked, reason = self.try_selector_or_text_click(page, cfg)
                        if not clicked:
                            clicked, reason = self.try_image_click_fallback(cfg)

                        if clicked:
                            shot = self.screenshot(page, cfg)
                            self.log_txt(cfg, f"CLICK: {reason} shot={shot}")
                            self.log_csv(cfg, "CLICK", f"{reason}|{shot}")
                            time.sleep(0.5)

                        time.sleep(cfg["loop_sleep_sec"])

                    context.close()
                    break

            except Exception as exc:
                crashes += 1
                wait_s = min(60, 2 ** crashes)
                self.log_txt(cfg, f"CRASH_RECOVERY #{crashes}: {exc} (retry in {wait_s}s)")
                self.log_csv(cfg, "CRASH_RECOVERY", f"{exc}|wait={wait_s}")
                if crashes >= cfg["max_crash_retries"]:
                    self.log_txt(cfg, "Max crash retries reached; stopping")
                    self.log_csv(cfg, "STOP", "max_crash_retries")
                    break
                time.sleep(wait_s)

        self.stop_activity_monitors()
        self.running = False
        self.paused = False
        self.log_txt(cfg, "STOP")
        self.log_csv(cfg, "STOP", "")
        self.ui.set_status("Stopped")

    def start(self, cfg):
        if self.running:
            return
        self.thread = threading.Thread(target=self.run, args=(cfg,), daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def toggle_pause(self):
        if self.running:
            self.paused = not self.paused
            self.ui.set_status("Paused" if self.paused else "Running")

    def test_selector_once(self, cfg):
        try:
            launch_brave_if_needed(cfg["brave_exe_path"], cfg["brave_profile_dir"], cfg["target_url"])
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=cfg["brave_user_data_dir"],
                    executable_path=cfg["brave_exe_path"],
                    headless=False,
                    args=[f"--profile-directory={cfg['brave_profile_dir']}"]
                )
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(cfg["target_url"], wait_until="domcontentloaded", timeout=45000)

                cur = page.url or ""
                if not domain_allowed(cur, cfg["domain_whitelist_csv"]):
                    messagebox.showwarning(APP_NAME, f"Domain not allowed:\n{cur}")
                else:
                    clicked, reason = self.try_selector_or_text_click(page, cfg)
                    if not clicked:
                        clicked, reason = self.try_image_click_fallback(cfg)
                    if clicked:
                        shot = self.screenshot(page, cfg)
                        messagebox.showinfo(APP_NAME, f"Test click success: {reason}\n{shot}")
                    else:
                        messagebox.showinfo(APP_NAME, f"No click: {reason}")

                context.close()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Test selector failed:\n{exc}")


class App:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.bot = Bot(self)
        self.status_var = tk.StringVar(value="Idle")
        self.profile_var = tk.StringVar(value="default")
        self.e = {}
        self.build()
        self.setup_hotkeys()

    def set_status(self, status):
        self.root.after(0, lambda: self.status_var.set(status))

    def add_entry(self, parent, label, key, default="", width=58):
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=6, pady=2)
        ttk.Label(row, text=label, width=36).pack(side="left")
        ent = ttk.Entry(row, width=width)
        ent.pack(side="left", fill="x", expand=True)
        ent.insert(0, default)
        self.e[key] = ent

    def add_text(self, parent, label, key, default=""):
        box = ttk.LabelFrame(parent, text=label)
        box.pack(fill="both", expand=False, padx=6, pady=4)
        text = tk.Text(box, height=4)
        text.pack(fill="both", expand=True)
        text.insert("1.0", default)
        self.e[key] = text

    def get(self, key):
        widget = self.e[key]
        if isinstance(widget, tk.Text):
            return widget.get("1.0", "end").strip()
        return widget.get().strip()

    def setup_hotkeys(self):
        def _start():
            self.root.after(0, self.on_start)

        def _stop():
            self.root.after(0, self.on_stop)

        def _pause():
            self.root.after(0, self.on_pause)

        self.hk = pynput_keyboard.GlobalHotKeys({
            "<f8>": _start,
            "<f9>": _stop,
            "<f10>": _pause,
        })
        self.hk.start()

    def cfg(self):
        cfg = {
            "brave_exe_path": self.get("brave_exe_path"),
            "brave_user_data_dir": self.get("brave_user_data_dir"),
            "brave_profile_dir": self.get("brave_profile_dir"),
            "target_url": self.get("target_url"),
            "target_url_prefix": self.get("target_url_prefix"),
            "strict_url_match": booly(self.get("strict_url_match")),
            "domain_whitelist_csv": self.get("domain_whitelist_csv"),
            "refresh_interval_sec": float(self.get("refresh_interval_sec")),
            "refresh_jitter_sec": float(self.get("refresh_jitter_sec")),
            "refresh_min_sec": float(self.get("refresh_min_sec")),
            "refresh_max_sec": float(self.get("refresh_max_sec")),
            "loop_sleep_sec": float(self.get("loop_sleep_sec")),
            "click_cooldown_sec": float(self.get("click_cooldown_sec")),
            "allowed_start": self.get("allowed_start"),
            "allowed_end": self.get("allowed_end"),
            "use_weekday_weekend_schedule": booly(self.get("use_weekday_weekend_schedule")),
            "weekday_start": self.get("weekday_start"),
            "weekday_end": self.get("weekday_end"),
            "weekend_start": self.get("weekend_start"),
            "weekend_end": self.get("weekend_end"),
            "clock_offset_sec": float(self.get("clock_offset_sec")),
            "pause_on_user_activity": booly(self.get("pause_on_user_activity")),
            "user_idle_required_sec": float(self.get("user_idle_required_sec")),
            "screenshot_on_click": booly(self.get("screenshot_on_click")),
            "screenshot_dir": self.get("screenshot_dir"),
            "log_txt_path": self.get("log_txt_path"),
            "log_csv_path": self.get("log_csv_path"),
            "selectors_multiline": self.get("selectors_multiline"),
            "fallback_texts_multiline": self.get("fallback_texts_multiline"),
            "use_image_fallback": booly(self.get("use_image_fallback")),
            "reference_image_path": self.get("reference_image_path"),
            "image_confidence": float(self.get("image_confidence")),
            "max_crash_retries": int(self.get("max_crash_retries")),
        }
        if not os.path.exists(cfg["brave_exe_path"]):
            raise ValueError("Brave EXE path not found")
        if not os.path.exists(cfg["brave_user_data_dir"]):
            raise ValueError("Brave user data dir not found")
        return cfg

    def on_start(self):
        try:
            self.bot.start(self.cfg())
            self.set_status("Starting...")
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def on_stop(self):
        self.bot.stop()
        self.set_status("Stopping...")

    def on_pause(self):
        self.bot.toggle_pause()

    def on_test(self):
        try:
            cfg = self.cfg()
            threading.Thread(target=self.bot.test_selector_once, args=(cfg,), daemon=True).start()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def save_profile(self):
        name = self.profile_var.get().strip()
        if not name:
            messagebox.showwarning(APP_NAME, "Enter profile name")
            return
        data = {}
        if os.path.exists(PROFILES_FILE):
            with open(PROFILES_FILE, "r", encoding="utf-8") as file:
                data = json.load(file)
        data[name] = {k: self.get(k) for k in self.e.keys()}
        with open(PROFILES_FILE, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
        messagebox.showinfo(APP_NAME, f"Saved profile: {name}")

    def load_profile(self):
        name = self.profile_var.get().strip()
        if not os.path.exists(PROFILES_FILE):
            messagebox.showwarning(APP_NAME, "profiles.json not found")
            return
        with open(PROFILES_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        if name not in data:
            messagebox.showwarning(APP_NAME, f"Profile '{name}' not found")
            return
        cfg = data[name]
        for key, widget in self.e.items():
            if key in cfg:
                if isinstance(widget, tk.Text):
                    widget.delete("1.0", "end")
                    widget.insert("1.0", cfg[key])
                else:
                    widget.delete(0, tk.END)
                    widget.insert(0, cfg[key])
        messagebox.showinfo(APP_NAME, f"Loaded profile: {name}")

    def list_profiles(self):
        self.profile_list.delete("1.0", "end")
        if not os.path.exists(PROFILES_FILE):
            self.profile_list.insert("1.0", "No profiles yet")
            return
        with open(PROFILES_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        for key in data.keys():
            self.profile_list.insert("end", f"- {key}\n")

    def build(self):
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True)
        tab_main = ttk.Frame(notebook)
        tab_detection = ttk.Frame(notebook)
        tab_profiles = ttk.Frame(notebook)
        notebook.add(tab_main, text="Main")
        notebook.add(tab_detection, text="Claim Detection")
        notebook.add(tab_profiles, text="Profiles")

        self.add_entry(tab_main, "Brave EXE path", "brave_exe_path", r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe")
        self.add_entry(tab_main, "Brave user data dir", "brave_user_data_dir", os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data"))
        self.add_entry(tab_main, "Brave profile directory", "brave_profile_dir", "Default")
        self.add_entry(tab_main, "Target URL", "target_url", "https://example.com/path")
        self.add_entry(tab_main, "Target URL prefix", "target_url_prefix", "https://example.com/")
        self.add_entry(tab_main, "Strict URL match (true/false)", "strict_url_match", "false")
        self.add_entry(tab_main, "Domain whitelist CSV", "domain_whitelist_csv", "example.com")
        self.add_entry(tab_main, "Refresh interval sec", "refresh_interval_sec", "10")
        self.add_entry(tab_main, "Refresh jitter +/- sec", "refresh_jitter_sec", "0.5")
        self.add_entry(tab_main, "Refresh min sec", "refresh_min_sec", "2")
        self.add_entry(tab_main, "Refresh max sec", "refresh_max_sec", "300")
        self.add_entry(tab_main, "Loop sleep sec", "loop_sleep_sec", "0.25")
        self.add_entry(tab_main, "Click cooldown sec", "click_cooldown_sec", "25")
        self.add_entry(tab_main, "Allowed start HH:MM", "allowed_start", "08:00")
        self.add_entry(tab_main, "Allowed end HH:MM", "allowed_end", "23:00")
        self.add_entry(tab_main, "Use weekday/weekend schedule", "use_weekday_weekend_schedule", "true")
        self.add_entry(tab_main, "Weekday start HH:MM", "weekday_start", "08:00")
        self.add_entry(tab_main, "Weekday end HH:MM", "weekday_end", "23:00")
        self.add_entry(tab_main, "Weekend start HH:MM", "weekend_start", "09:00")
        self.add_entry(tab_main, "Weekend end HH:MM", "weekend_end", "22:00")
        self.add_entry(tab_main, "Clock offset sec (+ahead -behind)", "clock_offset_sec", "0.0")
        self.add_entry(tab_main, "Pause on user activity", "pause_on_user_activity", "true")
        self.add_entry(tab_main, "Idle required sec", "user_idle_required_sec", "3")
        self.add_entry(tab_main, "Screenshot on click", "screenshot_on_click", "true")
        self.add_entry(tab_main, "Screenshot directory", "screenshot_dir", "screenshots")
        self.add_entry(tab_main, "TXT log path", "log_txt_path", "bot.log")
        self.add_entry(tab_main, "CSV log path", "log_csv_path", "bot.csv")
        self.add_entry(tab_main, "Max crash retries", "max_crash_retries", "8")

        row = ttk.Frame(tab_main)
        row.pack(fill="x", padx=6, pady=8)
        ttk.Button(row, text="Start (F8)", command=self.on_start).pack(side="left", padx=3)
        ttk.Button(row, text="Stop (F9)", command=self.on_stop).pack(side="left", padx=3)
        ttk.Button(row, text="Pause/Resume (F10)", command=self.on_pause).pack(side="left", padx=3)
        ttk.Button(row, text="Test Claim Once", command=self.on_test).pack(side="left", padx=8)
        ttk.Label(tab_main, textvariable=self.status_var, foreground="blue").pack(anchor="w", padx=10, pady=2)

        self.add_text(tab_detection, "CSS selectors (one per line)", "selectors_multiline", "button[data-test='claim']\nbutton:has-text('Claim')")
        self.add_text(tab_detection, "Fallback texts (one per line)", "fallback_texts_multiline", "Claim\nCollect\nGet reward")
        self.add_entry(tab_detection, "Use image fallback", "use_image_fallback", "false")
        self.add_entry(tab_detection, "Reference image path (optional)", "reference_image_path", "")
        self.add_entry(tab_detection, "Image confidence 0.1-1.0", "image_confidence", "0.82")

        row2 = ttk.Frame(tab_profiles)
        row2.pack(fill="x", padx=6, pady=6)
        ttk.Label(row2, text="Profile name").pack(side="left")
        ttk.Entry(row2, textvariable=self.profile_var, width=24).pack(side="left", padx=6)
        ttk.Button(row2, text="Save", command=self.save_profile).pack(side="left", padx=3)
        ttk.Button(row2, text="Load", command=self.load_profile).pack(side="left", padx=3)
        ttk.Button(row2, text="List", command=self.list_profiles).pack(side="left", padx=3)

        self.profile_list = tk.Text(tab_profiles, height=20)
        self.profile_list.pack(fill="both", expand=True, padx=6, pady=6)


def main():
    root = tk.Tk()
    root.geometry("980x900")
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
