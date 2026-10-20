import tkinter as tk
from tkinter import colorchooser
import customtkinter as ctk
import subprocess
import json
import threading
import asyncio
import colorsys
import socket
import time
import datetime
import random
import shutil
import re
import sys
import os

# Попытка импорта Pillow для функции Ambilight (захват цвета экрана)
try:
    from PIL import ImageGrab
    HAS_IMAGEGRAB = True
except ImportError:
    HAS_IMAGEGRAB = False

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tapo_config.json")


def try_import_kasa():
    """
    Импортирует python-kasa напрямую (включая изолированную установку через pipx в ~/.local/bin/kasa).
    """
    try:
        import kasa
        return kasa
    except ImportError:
        pass

    kasa_bin = os.path.expanduser("~/.local/bin/kasa")
    if os.path.exists(kasa_bin):
        try:
            with open(kasa_bin, "r", encoding="utf-8", errors="ignore") as f:
                first_line = f.readline().strip()
            if first_line.startswith("#!"):
                py_exec = first_line[2:].strip().split()[0]
                venv_root = os.path.dirname(os.path.dirname(py_exec))
                lib_dir = os.path.join(venv_root, "lib")
                if os.path.isdir(lib_dir):
                    for entry in os.listdir(lib_dir):
                        sp = os.path.join(lib_dir, entry, "site-packages")
                        if os.path.isdir(sp) and sp not in sys.path:
                            sys.path.append(sp)
                    import kasa
                    return kasa
        except Exception:
            pass
    return None


KASA_LIB = try_import_kasa()

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

# Палитра современного студийного интерфейса
THEME = {
    "bg_main": "#0b0f19",
    "bg_header": "#111827",
    "bg_card": "#1e293b",
    "bg_card_inner": "#0f172a",
    "border": "#334155",
    "accent": "#3b82f6",
    "accent_hover": "#2563eb",
    "purple": "#8b5cf6",
    "purple_hover": "#7c3aed",
    "success": "#10b981",
    "success_hover": "#059669",
    "danger": "#ef4444",
    "danger_hover": "#dc2626",
    "warning": "#f59e0b",
    "text_main": "#f8fafc",
    "text_muted": "#94a3b8",
}

DEFAULT_PRESETS = [
    ("Ярко-белый", 0, 0, 100, "#ffffff"),
    ("Теплый уют", 32, 75, 85, "#ffaa44"),
    ("Кибер-неон", 285, 100, 100, "#cc00ff"),
    ("Ледяной циан", 185, 100, 100, "#00eaff"),
    ("Малиновый", 335, 95, 100, "#ff0d73"),
    ("Изумруд", 145, 95, 90, "#0be666"),
    ("Глубокий синий", 230, 100, 100, "#1a40ff"),
    ("Алый закат", 10, 100, 100, "#ff2a00"),
]

BUILT_IN_SCENES = [
    {
        "name": " Киберпанк 2077",
        "desc": "Неоновый пурпур сверху + ледяной циан снизу",
        "top": (290, 100, 100),
        "bottom": (185, 100, 100),
    },
    {
        "name": " Калифорнийский закат",
        "desc": "Мягкий фиолетовый верх + теплый оранжевый низ",
        "top": (275, 85, 85),
        "bottom": (20, 100, 95),
    },
    {
        "name": " Глубокий фокус (Учеба)",
        "desc": "Чистый дневной свет сверху + спокойный синий снизу",
        "top": (200, 12, 100),
        "bottom": (215, 75, 65),
    },
    {
        "name": " Кинозал (Анти-блик)",
        "desc": "Приглушенное янтарное свечение без бликов на мониторе",
        "top": (28, 85, 15),
        "bottom": (22, 95, 28),
    },
    {
        "name": " Северное сияние",
        "desc": "Изумрудное свечение сверху + глубокий индиго под столом",
        "top": (150, 95, 85),
        "bottom": (250, 95, 85),
    },
    {
        "name": " Лава и Лёд",
        "desc": "Огненно-красный контраст сверху + арктический синий снизу",
        "top": (8, 100, 100),
        "bottom": (200, 100, 90),
    },
    {
        "name": " Ночной режим (5%)",
        "desc": "Минимальная теплая подсветка для глаз ночью",
        "top": (25, 100, 5),
        "bottom": (20, 100, 5),
    },
]


class TapoBackend:
    """
    Асинхронный движок управления и сетевого обнаружения лент Tapo.
    Поддерживает постоянные сессии python-kasa, защиту от наслоения команд и сканер сети.
    """
    def __init__(self, status_callback):
        self.status_callback = status_callback
        self.kasa_bin = self._find_kasa_bin()
        self.loop = asyncio.new_event_loop()
        self.devices_cache = {}
        self.ip_locks = {}
        self.pending_per_ip = {}

        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def _find_kasa_bin(self):
        local_bin = os.path.expanduser("~/.local/bin/kasa")
        if os.path.exists(local_bin):
            return local_bin
        return shutil.which("kasa") or local_bin

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def clear_cache(self):
        async def _close_all():
            for dev in list(self.devices_cache.values()):
                try:
                    await dev.disconnect()
                except Exception:
                    pass
            self.devices_cache.clear()
        asyncio.run_coroutine_threadsafe(_close_all(), self.loop)

    @staticmethod
    def get_local_subnet_prefix():
        """Определяет префикс локальной подсети ПК (например, '192.168.0.')."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            parts = local_ip.split(".")
            if len(parts) == 4:
                return f"{parts[0]}.{parts[1]}.{parts[2]}."
        except Exception:
            pass
        return "192.168.0."

    @staticmethod
    def check_ip_online(ip, timeout=0.9):
        """Быстрая проверка доступности ленты (TCP порт 80 / 9999 или ping)."""
        for port in (80, 9999):
            try:
                with socket.create_connection((ip, port), timeout=timeout):
                    return True
            except OSError:
                pass
        try:
            res = subprocess.run(
                ["ping", "-c", "1", "-W", "1", ip],
                capture_output=True,
                timeout=1.3,
            )
            return res.returncode == 0
        except Exception:
            return False

    def discover_network_devices(self, email, password, known_ips, callback):
        """
        Комбинированный поиск лент по локальной сети:
        1) Протокол Kasa/Tapo Broadcast Discover (получает имя, модель и IP).
        2) Быстрое параллельное TCP-сканирование всех 254 адресов подсети на порт 80 (HTTP Tapo/KLAP).
        """
        async def _scan():
            self.status_callback(" Сканирование локальной сети на наличие лент Tapo...", THEME["warning"])
            found = {}

            # Шаг 1: Официальный протокол обнаружения python-kasa
            if KASA_LIB is not None:
                try:
                    if hasattr(KASA_LIB, "Credentials") and email and password:
                        creds = KASA_LIB.Credentials(username=email, password=password)
                        discovered = await KASA_LIB.Discover.discover(credentials=creds, timeout=4)
                    else:
                        discovered = await KASA_LIB.Discover.discover(timeout=4)

                    for ip, dev in discovered.items():
                        try:
                            await dev.update()
                        except Exception:
                            pass
                        alias = getattr(dev, "alias", None) or "Устройство Tapo"
                        model = getattr(dev, "model", None) or "Smart Strip"
                        mac = getattr(dev, "mac", "")
                        found[ip] = {
                            "ip": ip,
                            "alias": alias,
                            "model": model,
                            "mac": mac,
                            "source": "Tapo Broadcast",
                        }
                except Exception:
                    pass

            # Шаг 2: Быстрое асинхронное сканирование подсети (на случай блокировки UDP broadcast)
            prefixes = {self.get_local_subnet_prefix()}
            for kip in known_ips:
                parts = kip.split(".")
                if len(parts) == 4:
                    prefixes.add(f"{parts[0]}.{parts[1]}.{parts[2]}.")

            sem = asyncio.Semaphore(64)

            async def _probe_ip(ip_addr):
                if ip_addr in found:
                    return
                async with sem:
                    try:
                        conn = asyncio.open_connection(ip_addr, 80)
                        reader, writer = await asyncio.wait_for(conn, timeout=0.55)
                        writer.close()
                        try:
                            await writer.wait_closed()
                        except Exception:
                            pass
                    except Exception:
                        return

                    # Если порт 80 открыт, пробуем опросить через Kasa Discover Single
                    if KASA_LIB is not None:
                        try:
                            if hasattr(KASA_LIB, "Credentials") and email and password:
                                creds = KASA_LIB.Credentials(username=email, password=password)
                                dev = await KASA_LIB.Discover.discover_single(ip_addr, credentials=creds, timeout=2.5)
                            else:
                                dev = await KASA_LIB.Discover.discover_single(ip_addr, timeout=2.5)
                            if dev:
                                try:
                                    await dev.update()
                                except Exception:
                                    pass
                                found[ip_addr] = {
                                    "ip": ip_addr,
                                    "alias": getattr(dev, "alias", None) or "Tapo LED",
                                    "model": getattr(dev, "model", None) or "Tapo Strip",
                                    "mac": getattr(dev, "mac", ""),
                                    "source": "Direct Probe",
                                }
                                return
                        except Exception:
                            pass

            tasks = []
            for pref in prefixes:
                for i in range(1, 255):
                    tasks.append(_probe_ip(f"{pref}{i}"))
            await asyncio.gather(*tasks)

            # Шаг 3: Если ничего не найдено через библиотеку, пробуем CLI `kasa discover`
            if not found:
                try:
                    cli_out = await asyncio.to_thread(
                        subprocess.run,
                        [self.kasa_bin, "discover"],
                        capture_output=True,
                        text=True,
                        timeout=7,
                    )
                    text = (cli_out.stdout or "") + "\n" + (cli_out.stderr or "")
                    current_alias = "Tapo Device"
                    for line in text.splitlines():
                        m_host = re.search(r"==\s*(.+?)\s*-\s*([^\s=]+)\s*==", line)
                        if m_host:
                            current_alias = m_host.group(1).strip()
                        m_ip = re.search(r"Host:\s*(\d+\.\d+\.\d+\.\d+)", line)
                        if m_ip:
                            ip_v = m_ip.group(1)
                            found[ip_v] = {
                                "ip": ip_v,
                                "alias": current_alias,
                                "model": "Tapo Smart",
                                "mac": "",
                                "source": "CLI Discover",
                            }
                except Exception:
                    pass

            sorted_list = sorted(found.values(), key=lambda x: [int(p) for p in x["ip"].split(".")])
            callback(sorted_list)

        asyncio.run_coroutine_threadsafe(_scan(), self.loop)

    async def _get_device(self, ip, email, password):
        key = f"{ip}:{email}"
        dev = self.devices_cache.get(key)
        if dev is not None:
            return dev

        if hasattr(KASA_LIB, "Credentials"):
            creds = KASA_LIB.Credentials(username=email, password=password)
            dev = await KASA_LIB.Discover.discover_single(ip, credentials=creds, timeout=5)
        else:
            dev = await KASA_LIB.Discover.discover_single(ip, username=email, password=password, timeout=5)

        await dev.update()
        self.devices_cache[key] = dev
        return dev

    @staticmethod
    def _find_light_module(dev):
        if hasattr(dev, "modules") and isinstance(dev.modules, dict):
            if hasattr(KASA_LIB, "Module") and hasattr(KASA_LIB.Module, "Light"):
                mod = dev.modules.get(KASA_LIB.Module.Light)
                if mod is not None:
                    return mod
            for k, mod in dev.modules.items():
                if "light" in str(k).lower() or hasattr(mod, "set_hsv"):
                    return mod
        if hasattr(dev, "light") and dev.light is not None:
            return dev.light
        if hasattr(dev, "set_hsv") or hasattr(dev, "set_brightness"):
            return dev
        return None

    async def _exec_single_async(self, name, ip, email, password, action, params):
        if ip not in self.ip_locks:
            self.ip_locks[ip] = asyncio.Lock()

        async with self.ip_locks[ip]:
            if KASA_LIB is not None:
                try:
                    dev = await self._get_device(ip, email, password)
                    light = self._find_light_module(dev)

                    if action == "on":
                        await dev.turn_on()
                        return True, f"«{name}» включена"
                    elif action == "off":
                        await dev.turn_off()
                        return True, f"«{name}» выключена"
                    elif action == "brightness" and light is not None:
                        br = int(params[0])
                        if br > 0 and not getattr(dev, "is_on", True):
                            await dev.turn_on()
                        await light.set_brightness(br)
                        return True, f"Яркость «{name}»: {br}%"
                    elif action == "hsv" and light is not None:
                        h, s, v = int(params[0]), int(params[1]), int(params[2])
                        if v > 0 and not getattr(dev, "is_on", True):
                            await dev.turn_on()
                        await light.set_hsv(h, s, v)
                        return True, f"Цвет «{name}» обновлен"
                    elif action == "temperature" and light is not None:
                        temp, br = int(params[0]), int(params[1])
                        if not getattr(dev, "is_on", True):
                            await dev.turn_on()
                        if hasattr(light, "set_color_temp"):
                            try:
                                await light.set_color_temp(temp, brightness=br)
                                return True, f"Температура «{name}»: {temp}K"
                            except Exception:
                                pass
                        h, s, v = self.kelvin_to_hsv(temp, br)
                        await light.set_hsv(h, s, v)
                        return True, f"Белый свет «{name}»: {temp}K"
                except Exception:
                    dev_old = self.devices_cache.pop(f"{ip}:{email}", None)
                    if dev_old is not None:
                        try:
                            await dev_old.disconnect()
                        except Exception:
                            pass

            return await asyncio.to_thread(self._exec_cli, name, ip, email, password, action, params)

    def _exec_cli(self, name, ip, email, password, action, params):
        args = [self.kasa_bin, "--host", ip, "--username", email, "--password", password]
        if action in ("on", "off"):
            args.append(action)
        elif action == "brightness":
            args.extend(["brightness", str(int(params[0]))])
        elif action == "hsv":
            args.extend(["hsv", str(int(params[0])), str(int(params[1])), str(int(params[2]))])
        elif action == "temperature":
            temp, br = int(params[0]), int(params[1])
            h, s, v = self.kelvin_to_hsv(temp, br)
            args.extend(["hsv", str(h), str(s), str(v)])

        try:
            res = subprocess.run(args, capture_output=True, text=True, timeout=10)
            if res.returncode == 0:
                return True, f"«{name}» ОК"
            err = (res.stderr or res.stdout).lower()
            if "authentication" in err or "challenge" in err or "credentials" in err:
                return False, "Ошибка авторизации Tapo: проверьте Email и Пароль"
            if "timeout" in err or "unreachable" in err or "connect" in err:
                return False, f"Лента «{name}» ({ip}) недоступна в сети"
            return False, f"Ошибка ответа от «{name}» ({ip})"
        except subprocess.TimeoutExpired:
            return False, f"Нет ответа от «{name}» ({ip}) — проверьте питание или IP"
        except FileNotFoundError:
            return False, "Не найден исполняемый файл kasa (~/.local/bin/kasa)"
        except Exception:
            return False, f"Ошибка подключения к «{name}» ({ip})"

    @staticmethod
    def kelvin_to_hsv(kelvin, brightness):
        ratio = (kelvin - 2500) / 4000.0
        ratio = max(0.0, min(1.0, ratio))
        if ratio < 0.5:
            t = ratio / 0.5
            h = int(30 + t * 10)
            s = int(80 * (1.0 - t))
        else:
            t = (ratio - 0.5) / 0.5
            h = 210
            s = int(t * 25)
        return h, s, int(brightness)

    def blink_identify(self, ip, email, password):
        """Коротко мигает выбранной лентой для визуального определения, где она находится."""
        async def _blink():
            self.status_callback(f" Тестовое мигание лентой {ip}...", THEME["accent"])
            await self._exec_single_async(ip, ip, email, password, "hsv", (185, 100, 100))
            await asyncio.sleep(0.45)
            await self._exec_single_async(ip, ip, email, password, "off", ())
            await asyncio.sleep(0.35)
            await self._exec_single_async(ip, ip, email, password, "hsv", (290, 100, 100))
            self.status_callback(f" Лента {ip} мигнула!", THEME["success"])

        asyncio.run_coroutine_threadsafe(_blink(), self.loop)

    def send_command(self, targets, email, password, action, params=(), silent=False, deduplicate=False):
        batch_tokens = {}
        for _, ip in targets:
            token = self.pending_per_ip.get(ip, 0) + 1
            self.pending_per_ip[ip] = token
            batch_tokens[ip] = token

        async def _run_for_target(name, ip):
            if deduplicate and self.pending_per_ip.get(ip) != batch_tokens[ip]:
                return True, "Пропущено"
            return await self._exec_single_async(name, ip, email, password, action, params)

        async def _runner():
            if not silent:
                names_str = ", ".join(n for n, _ in targets)
                self.status_callback(f"⏳ Отправка на: {names_str}...", THEME["warning"])

            results = await asyncio.gather(*[_run_for_target(n, ip) for n, ip in targets])

            if silent:
                return

            failed = [msg for ok, msg in results if not ok]
            succeeded = [msg for ok, msg in results if ok]

            if not failed:
                if len(targets) > 1:
                    self.status_callback(f" Выполнено для всех лент ({len(targets)} шт.)", THEME["success"])
                else:
                    self.status_callback(f" Выполнено: {targets[0][0]}", THEME["success"])
            elif succeeded:
                self.status_callback(f" Частично: {failed[0]}", THEME["warning"])
            else:
                self.status_callback(f" {failed[0]}", THEME["danger"])

        asyncio.run_coroutine_threadsafe(_runner(), self.loop)


class TapoGuiApp(ctk.CTk):
    ALL_DEVICES_KEY = " ВСЕ ЛЕНТЫ"

    def __init__(self):
        super().__init__()

        self.title("Tapo RGB Studio Ultra")
        self.geometry("780x880")
        self.minsize(720, 800)
        self.configure(fg_color=THEME["bg_main"])

        self.saved_devices = {}
        self.online_status = {}
        self.discovered_devices = []
        self.custom_colors = []
        self.custom_scenes = []
        self.debounce_timer = None
        self.is_topmost = False
        self.power_toggle_state = True

        # Текущее состояние цвета
        self.curr_h = 285
        self.curr_s = 100
        self.curr_v = 100
        self.curr_temp = 4500

        # Двухзонный микшер
        self.zone_top_hsv = (285, 100, 100)
        self.zone_bot_hsv = (185, 100, 100)

        # Фоновые эффекты, таймер сна, помодоро и расписание
        self.effect_running = False
        self.effect_name = None
        self.effect_speed = 1.0
        self.sleep_timer_end = None
        self.sleep_after_id = None
        self.schedule_enabled = False
        self.schedule_time_str = "23:30"
        self.schedule_action = "off"

        self.backend = TapoBackend(self.show_status_threadsafe)

        self._build_header()
        self._build_tabs()
        self._bind_shortcuts()
        self.load_config()
        self._update_color_preview_ui()
        self.check_all_devices_online()
        self._start_schedule_watcher()

    # ──────────────────────── ГОРЯЧИЕ КЛАВИШИ ────────────────────────

    def _bind_shortcuts(self):
        self.bind("<Up>", lambda e: self._shortcut_brightness(10))
        self.bind("<Down>", lambda e: self._shortcut_brightness(-10))
        for i in range(1, 9):
            self.bind(str(i), lambda e, idx=i - 1: self._shortcut_preset(idx))

    def _shortcut_brightness(self, delta):
        if isinstance(self.focus_get(), tk.Entry):
            return
        new_v = max(1, min(100, self.curr_v + delta))
        self.set_quick_brightness(new_v)

    def _shortcut_preset(self, idx):
        if isinstance(self.focus_get(), tk.Entry):
            return
        if 0 <= idx < len(DEFAULT_PRESETS):
            _, h, s, v, _ = DEFAULT_PRESETS[idx]
            self.apply_hsv_preset(h, s, v)

    # ──────────────────────── ШАПКА ПРИЛОЖЕНИЯ ────────────────────────

    def _build_header(self):
        self.header_frame = ctk.CTkFrame(
            self,
            corner_radius=16,
            fg_color=THEME["bg_header"],
            border_width=1,
            border_color=THEME["border"],
        )
        self.header_frame.pack(fill="x", padx=18, pady=(14, 8))

        top_row = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        top_row.pack(fill="x", padx=16, pady=(12, 6))

        # Левая часть: логотип и статус движка
        title_box = ctk.CTkFrame(top_row, fg_color="transparent")
        title_box.pack(side="left")

        self.live_dot = ctk.CTkFrame(
            title_box, width=18, height=18, corner_radius=9, fg_color="#cc00ff"
        )
        self.live_dot.pack(side="left", padx=(0, 8))

        ctk.CTkLabel(
            title_box,
            text="TAPO RGB ULTRA",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=THEME["text_main"],
        ).pack(side="left")

        engine_badge = " Fast API" if KASA_LIB else "🔧 CLI Mode"
        ctk.CTkLabel(
            title_box,
            text=engine_badge,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=THEME["accent"],
            text_color="white",
            corner_radius=6,
            padx=8,
            pady=2,
        ).pack(side="left", padx=8)

        ctk.CTkButton(
            title_box,
            text=" Найти ленты",
            width=118,
            height=28,
            fg_color=THEME["purple"],
            hover_color=THEME["purple_hover"],
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self.open_and_run_discovery,
        ).pack(side="left", padx=4)

        self.btn_pin = ctk.CTkButton(
            title_box,
            text=" Поверх окон",
            width=112,
            height=28,
            fg_color=THEME["bg_card"],
            hover_color=THEME["border"],
            font=ctk.CTkFont(size=11),
            command=self.toggle_topmost,
        )
        self.btn_pin.pack(side="left", padx=4)

        # Правая часть: кнопки питания
        power_box = ctk.CTkFrame(top_row, fg_color="transparent")
        power_box.pack(side="right")

        self.btn_on = ctk.CTkButton(
            power_box,
            text=" ВКЛ",
            width=88,
            height=34,
            fg_color=THEME["success"],
            hover_color=THEME["success_hover"],
            font=ctk.CTkFont(size=13, weight="bold"),
            command=lambda: self.send_to_selected("on"),
        )
        self.btn_on.pack(side="left", padx=(0, 6))

        self.btn_off = ctk.CTkButton(
            power_box,
            text=" ВЫКЛ",
            width=88,
            height=34,
            fg_color=THEME["danger"],
            hover_color=THEME["danger_hover"],
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self.turn_off_selected,
        )
        self.btn_off.pack(side="left")

        # Нижний ряд шапки: выбор активной зоны
        bottom_row = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        bottom_row.pack(fill="x", padx=16, pady=(4, 12))

        ctk.CTkLabel(
            bottom_row,
            text="Активная зона:",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=THEME["text_muted"],
        ).pack(side="left", padx=(0, 10))

        self.device_selector = ctk.CTkSegmentedButton(
            bottom_row,
            values=[self.ALL_DEVICES_KEY],
            command=self.on_device_select,
            font=ctk.CTkFont(size=13, weight="bold"),
            selected_color=THEME["accent"],
            selected_hover_color=THEME["accent_hover"],
        )
        self.device_selector.pack(side="left", fill="x", expand=True)

        ctk.CTkButton(
            bottom_row,
            text=" Пинг",
            width=76,
            height=28,
            fg_color=THEME["bg_card"],
            hover_color=THEME["border"],
            font=ctk.CTkFont(size=12),
            command=self.check_all_devices_online,
        ).pack(side="left", padx=(8, 0))

        # Информационная строка состояния
        self.status_bar = ctk.CTkFrame(self, height=26, fg_color="transparent")
        self.status_bar.pack(fill="x", padx=22, pady=(0, 4))

        self.status_label = ctk.CTkLabel(
            self.status_bar,
            text="Готов к работе • Горячие клавиши: ↑/↓ — яркость, 1-8 — пресеты",
            font=ctk.CTkFont(size=12),
            text_color=THEME["text_muted"],
            anchor="w",
            justify="left",
            wraplength=540,
        )
        self.status_label.pack(side="left", fill="x", expand=True)

        self.timer_status_label = ctk.CTkLabel(
            self.status_bar,
            text="",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=THEME["warning"],
        )
        self.timer_status_label.pack(side="right")

    def toggle_topmost(self):
        self.is_topmost = not self.is_topmost
        self.attributes("-topmost", self.is_topmost)
        self.btn_pin.configure(
            fg_color=THEME["accent"] if self.is_topmost else THEME["bg_card"],
            text=" Закреплено" if self.is_topmost else " Поверх окон",
        )

    # ──────────────────────── ВКЛАДКИ ────────────────────────

    def _build_tabs(self):
        self.tabs = ctk.CTkTabview(
            self,
            corner_radius=14,
            fg_color=THEME["bg_header"],
            border_width=1,
            border_color=THEME["border"],
            segmented_button_selected_color=THEME["accent"],
            segmented_button_selected_hover_color=THEME["accent_hover"],
        )
        self.tabs.pack(fill="both", expand=True, padx=18, pady=(0, 16))

        self.tab_color = self.tabs.add(" Спектр и Свет")
        self.tab_scenes = self.tabs.add(" Сцены и 2 Зоны")
        self.tab_effects = self.tabs.add(" Эффекты и Таймер")
        self.tab_network = self.tabs.add(" Поиск и Сеть")

        self._build_color_tab()
        self._build_scenes_tab()
        self._build_effects_tab()
        self._build_network_tab()

    # ──────────────────────── ВКЛАДКА 1: СПЕКТР И СВЕТ ────────────────────────

    def _build_color_tab(self):
        top_card = ctk.CTkFrame(
            self.tab_color, corner_radius=12, fg_color=THEME["bg_card"], border_width=1, border_color=THEME["border"]
        )
        top_card.pack(fill="x", padx=8, pady=6)

        # Верхняя часть: интерактивный 2D спектр слева + карточка превью справа
        spectrum_row = ctk.CTkFrame(top_card, fg_color="transparent")
        spectrum_row.pack(fill="x", padx=14, pady=(12, 8))

        # Интерактивный 2D спектр Hue (X) / Saturation (Y)
        canvas_holder = ctk.CTkFrame(spectrum_row, fg_color=THEME["bg_card_inner"], corner_radius=10, padx=4, pady=4)
        canvas_holder.pack(side="left", padx=(0, 14))

        self.spec_w = 320
        self.spec_h = 110
        self.spectrum_canvas = tk.Canvas(
            canvas_holder,
            width=self.spec_w,
            height=self.spec_h,
            bg="#000000",
            highlightthickness=0,
            cursor="crosshair",
        )
        self.spectrum_canvas.pack()
        self._draw_spectrum_canvas()
        self.spectrum_canvas.bind("<Button-1>", self._on_spectrum_click)
        self.spectrum_canvas.bind("<B1-Motion>", self._on_spectrum_click)

        # Правая колонка с живым превью, HEX-вводом и кнопками
        right_col = ctk.CTkFrame(spectrum_row, fg_color="transparent")
        right_col.pack(side="left", fill="both", expand=True)

        prev_top = ctk.CTkFrame(right_col, fg_color="transparent")
        prev_top.pack(fill="x", pady=(0, 6))

        self.color_preview = ctk.CTkFrame(
            prev_top, width=64, height=64, corner_radius=14, fg_color="#cc00ff", border_width=2, border_color="#ffffff"
        )
        self.color_preview.pack(side="left", padx=(0, 12))
        self.color_preview.pack_propagate(False)

        hex_box = ctk.CTkFrame(prev_top, fg_color="transparent")
        hex_box.pack(side="left", fill="both", expand=True)

        self.hex_label = ctk.CTkLabel(
            hex_box, text="#CC00FF", font=ctk.CTkFont(size=18, weight="bold")
        )
        self.hex_label.pack(anchor="w")

        self.hsv_sub_label = ctk.CTkLabel(
            hex_box, text="HSV: 285° • 100% • 100%", font=ctk.CTkFont(size=12), text_color=THEME["text_muted"]
        )
        self.hsv_sub_label.pack(anchor="w", pady=(0, 4))

        # Поле быстрого ввода #HEX и кнопки
        hex_input_row = ctk.CTkFrame(right_col, fg_color="transparent")
        hex_input_row.pack(fill="x", pady=(2, 0))

        self.hex_entry = ctk.CTkEntry(hex_input_row, placeholder_text="#RRGGBB", width=95, height=28)
        self.hex_entry.pack(side="left", padx=(0, 6))
        self.hex_entry.bind("<Return>", self.apply_hex_from_entry)

        ctk.CTkButton(
            hex_input_row,
            text="ОК",
            width=42,
            height=28,
            fg_color=THEME["accent"],
            command=self.apply_hex_from_entry,
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            hex_input_row,
            text=" Палитра",
            width=95,
            height=28,
            fg_color="#334155",
            hover_color="#475569",
            command=self.choose_color_dialog,
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            hex_input_row,
            text=" В избранное",
            width=110,
            height=28,
            fg_color=THEME["purple"],
            hover_color=THEME["purple_hover"],
            command=self.save_current_color_to_fav,
        ).pack(side="left")

        # Ползунки точной доводки (Hue, Saturation, Brightness)
        sliders_box = ctk.CTkFrame(top_card, fg_color="transparent")
        sliders_box.pack(fill="x", padx=16, pady=(2, 10))
        sliders_box.grid_columnconfigure((0, 1), weight=1)

        # Левый ползунок: Оттенок
        h_box = ctk.CTkFrame(sliders_box, fg_color="transparent")
        h_box.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.hue_label = ctk.CTkLabel(h_box, text="Оттенок (Hue): 285°", font=ctk.CTkFont(size=12))
        self.hue_label.pack(anchor="w")
        self.hue_slider = ctk.CTkSlider(h_box, from_=0, to=360, number_of_steps=360, command=self.on_hsv_slider_change)
        self.hue_slider.set(self.curr_h)
        self.hue_slider.pack(fill="x", pady=2)

        # Правый ползунок: Насыщенность
        s_box = ctk.CTkFrame(sliders_box, fg_color="transparent")
        s_box.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.sat_label = ctk.CTkLabel(s_box, text="Насыщенность (Saturation): 100%", font=ctk.CTkFont(size=12))
        self.sat_label.pack(anchor="w")
        self.sat_slider = ctk.CTkSlider(s_box, from_=0, to=100, number_of_steps=100, command=self.on_hsv_slider_change)
        self.sat_slider.set(self.curr_s)
        self.sat_slider.pack(fill="x", pady=2)

        # Ползунок Яркости на всю ширину
        self.val_label = ctk.CTkLabel(
            top_card, text=" Яркость (Brightness): 100%", font=ctk.CTkFont(size=13, weight="bold")
        )
        self.val_label.pack(anchor="w", padx=16, pady=(2, 0))
        self.val_slider = ctk.CTkSlider(
            top_card, from_=1, to=100, number_of_steps=99, command=self.on_brightness_slider_change
        )
        self.val_slider.set(self.curr_v)
        self.val_slider.pack(fill="x", padx=16, pady=(2, 8))

        # Кнопки быстрой яркости + Плавный закат
        quick_br = ctk.CTkFrame(top_card, fg_color="transparent")
        quick_br.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkLabel(quick_br, text="Быстрая яркость:", font=ctk.CTkFont(size=11), text_color=THEME["text_muted"]).pack(
            side="left", padx=(0, 8)
        )
        for pct in (5, 20, 40, 60, 80, 100):
            ctk.CTkButton(
                quick_br,
                text=f"{pct}%",
                width=48,
                height=24,
                fg_color=THEME["bg_card_inner"],
                hover_color=THEME["border"],
                command=lambda p=pct: self.set_quick_brightness(p),
            ).pack(side="left", padx=2)

        ctk.CTkButton(
            quick_br,
            text=" Плавный закат (10с)",
            width=145,
            height=24,
            fg_color="#b45309",
            hover_color="#d97706",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self.start_smooth_fade_off,
        ).pack(side="right")

        # Блок Белого света (Цветовая температура)
        temp_card = ctk.CTkFrame(
            self.tab_color, corner_radius=12, fg_color=THEME["bg_card"], border_width=1, border_color=THEME["border"]
        )
        temp_card.pack(fill="x", padx=8, pady=5)

        temp_top = ctk.CTkFrame(temp_card, fg_color="transparent")
        temp_top.pack(fill="x", padx=16, pady=(8, 2))

        self.temp_label = ctk.CTkLabel(
            temp_top, text="🌡 Белый свет (Температура): 4500K", font=ctk.CTkFont(size=13, weight="bold")
        )
        self.temp_label.pack(side="left")

        for k_val, k_title in ((2700, " 2700K Уют"), (4500, " 4500K Дневной"), (6500, " 6500K Холодный")):
            ctk.CTkButton(
                temp_top,
                text=k_title,
                width=110,
                height=22,
                fg_color=THEME["bg_card_inner"],
                hover_color=THEME["border"],
                font=ctk.CTkFont(size=11),
                command=lambda kv=k_val: self.set_quick_temperature(kv),
            ).pack(side="right", padx=3)

        self.temp_slider = ctk.CTkSlider(
            temp_card,
            from_=2500,
            to=6500,
            number_of_steps=80,
            button_color="#f59e0b",
            progress_color="#fcd34d",
            command=self.on_temp_slider_change,
        )
        self.temp_slider.set(4500)
        self.temp_slider.pack(fill="x", padx=16, pady=(4, 10))

        # Сетка быстрых пресетов и избранного
        presets_card = ctk.CTkFrame(
            self.tab_color, corner_radius=12, fg_color=THEME["bg_card"], border_width=1, border_color=THEME["border"]
        )
        presets_card.pack(fill="both", expand=True, padx=8, pady=5)

        ctk.CTkLabel(
            presets_card,
            text=" Быстрая палитра (клавиши 1–8) и Избранное:",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(8, 4))

        self.swatches_grid = ctk.CTkFrame(presets_card, fg_color="transparent")
        self.swatches_grid.pack(fill="x", padx=12, pady=(0, 6))

        self.fav_swatches_grid = ctk.CTkFrame(presets_card, fg_color="transparent")
        self.fav_swatches_grid.pack(fill="x", padx=12, pady=(0, 8))

        self._render_default_swatches()

    def _draw_spectrum_canvas(self):
        """Рисует 2D карту Hue (по горизонтали) и Saturation (по вертикали) + маркер выбора."""
        self.spectrum_canvas.delete("all")
        cols = 80
        rows = 22
        cw = self.spec_w / cols
        ch = self.spec_h / rows
        for c in range(cols):
            hue = (c / cols) * 360.0
            for r in range(rows):
                sat = 100.0 - (r / rows) * 100.0
                color_hex = self.hsv_to_hex(hue, sat, 95)
                x0 = c * cw
                y0 = r * ch
                self.spectrum_canvas.create_rectangle(
                    x0, y0, x0 + cw + 1, y0 + ch + 1, fill=color_hex, outline=""
                )
        self._draw_spectrum_cursor()

    def _draw_spectrum_cursor(self):
        self.spectrum_canvas.delete("cursor")
        x = (self.curr_h / 360.0) * self.spec_w
        y = ((100.0 - self.curr_s) / 100.0) * self.spec_h
        r = 6
        self.spectrum_canvas.create_oval(
            x - r, y - r, x + r, y + r, outline="#000000", width=3, tags="cursor"
        )
        self.spectrum_canvas.create_oval(
            x - r, y - r, x + r, y + r, outline="#ffffff", width=1.5, tags="cursor"
        )

    def _on_spectrum_click(self, event):
        self.stop_effect(silent=True)
        x = max(0, min(self.spec_w, event.x))
        y = max(0, min(self.spec_h, event.y))
        self.curr_h = int((x / self.spec_w) * 360)
        self.curr_s = int(100 - (y / self.spec_h) * 100)
        self.hue_slider.set(self.curr_h)
        self.sat_slider.set(self.curr_s)
        self._update_color_preview_ui()
        self._schedule_debounced(
            lambda: self.send_to_selected("hsv", (self.curr_h, self.curr_s, self.curr_v), deduplicate=True),
            delay_ms=120,
        )

    # ──────────────────────── ВКЛАДКА 2: СЦЕНЫ И 2 ЗОНЫ ────────────────────────

    def _build_scenes_tab(self):
        # Конструктор двухзонного градиента («Над столом» + «Под столом»)
        mixer_card = ctk.CTkFrame(
            self.tab_scenes, corner_radius=12, fg_color=THEME["bg_card"], border_width=1, border_color=THEME["border"]
        )
        mixer_card.pack(fill="x", padx=8, pady=6)

        m_head = ctk.CTkFrame(mixer_card, fg_color="transparent")
        m_head.pack(fill="x", padx=16, pady=(10, 6))

        ctk.CTkLabel(
            m_head,
            text="🌗 Двухзонный микшер («Над столом» + «Под столом»)",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(side="left")

        ctk.CTkButton(
            m_head,
            text="🔄 Поменять зоны местами (Swap)",
            width=215,
            height=28,
            fg_color=THEME["purple"],
            hover_color=THEME["purple_hover"],
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self.swap_two_zones,
        ).pack(side="right")

        mixer_row = ctk.CTkFrame(mixer_card, fg_color="transparent")
        mixer_row.pack(fill="x", padx=16, pady=(2, 12))

        self.zone_top_btn = ctk.CTkButton(
            mixer_row,
            text=" Задать цвет Зоны 1 (Верх)",
            height=34,
            fg_color="#cc00ff",
            text_color="#ffffff",
            font=ctk.CTkFont(weight="bold"),
            command=lambda: self.pick_zone_color("top"),
        )
        self.zone_top_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.zone_bot_btn = ctk.CTkButton(
            mixer_row,
            text=" Задать цвет Зоны 2 (Низ)",
            height=34,
            fg_color="#00eaff",
            text_color="#000000",
            font=ctk.CTkFont(weight="bold"),
            command=lambda: self.pick_zone_color("bottom"),
        )
        self.zone_bot_btn.pack(side="left", fill="x", expand=True, padx=6)

        ctk.CTkButton(
            mixer_row,
            text="▶ Применить дуэт",
            width=140,
            height=34,
            fg_color=THEME["accent"],
            hover_color=THEME["accent_hover"],
            font=ctk.CTkFont(weight="bold"),
            command=self.apply_custom_duo_zones,
        ).pack(side="right", padx=(6, 0))

        # Индивидуальный пульт лент
        zones_card = ctk.CTkFrame(
            self.tab_scenes, corner_radius=12, fg_color=THEME["bg_card"], border_width=1, border_color=THEME["border"]
        )
        zones_card.pack(fill="x", padx=8, pady=5)

        z_head = ctk.CTkFrame(zones_card, fg_color="transparent")
        z_head.pack(fill="x", padx=16, pady=(10, 4))

        ctk.CTkLabel(
            z_head,
            text="🎛 Индивидуальный пульт каждой ленты",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(side="left")

        ctk.CTkButton(
            z_head,
            text="🔄 Обновить онлайн-статус",
            width=165,
            height=26,
            fg_color=THEME["bg_card_inner"],
            hover_color=THEME["border"],
            font=ctk.CTkFont(size=11),
            command=self.check_all_devices_online,
        ).pack(side="right")

        self.per_device_container = ctk.CTkFrame(zones_card, fg_color="transparent")
        self.per_device_container.pack(fill="x", padx=12, pady=(0, 10))

        # Список готовых и пользовательских сцен
        scenes_card = ctk.CTkFrame(
            self.tab_scenes, corner_radius=12, fg_color=THEME["bg_card"], border_width=1, border_color=THEME["border"]
        )
        scenes_card.pack(fill="both", expand=True, padx=8, pady=5)

        header_row = ctk.CTkFrame(scenes_card, fg_color="transparent")
        header_row.pack(fill="x", padx=16, pady=(10, 4))

        ctk.CTkLabel(
            header_row,
            text=" Коллекция двухзонных сцен",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(side="left")

        ctk.CTkButton(
            header_row,
            text="➕ Сохранить текущий дуэт в сцены",
            width=220,
            height=28,
            fg_color=THEME["accent"],
            hover_color=THEME["accent_hover"],
            command=self.save_custom_scene,
        ).pack(side="right")

        self.scenes_scroll = ctk.CTkScrollableFrame(scenes_card, fg_color="transparent")
        self.scenes_scroll.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def pick_zone_color(self, which):
        init_hsv = self.zone_top_hsv if which == "top" else self.zone_bot_hsv
        init_hex = self.hsv_to_hex(*init_hsv)
        res = colorchooser.askcolor(color=init_hex, title="Выберите цвет зоны")
        if res and res[0]:
            r, g, b = res[0]
            h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            hsv_tuple = (int(h * 360), int(s * 100), max(5, int(v * 100)))
            hex_c = self.hsv_to_hex(*hsv_tuple)
            txt_c = "#000000" if (hsv_tuple[2] > 70 and hsv_tuple[1] < 45) else "#ffffff"
            if which == "top":
                self.zone_top_hsv = hsv_tuple
                self.zone_top_btn.configure(fg_color=hex_c, hover_color=hex_c, text_color=txt_c)
            else:
                self.zone_bot_hsv = hsv_tuple
                self.zone_bot_btn.configure(fg_color=hex_c, hover_color=hex_c, text_color=txt_c)
            self.apply_custom_duo_zones()

    def apply_custom_duo_zones(self):
        self.apply_scene({
            "name": "Пользовательский дуэт",
            "top": self.zone_top_hsv,
            "bottom": self.zone_bot_hsv,
        })

    def swap_two_zones(self):
        self.zone_top_hsv, self.zone_bot_hsv = self.zone_bot_hsv, self.zone_top_hsv
        hex1 = self.hsv_to_hex(*self.zone_top_hsv)
        hex2 = self.hsv_to_hex(*self.zone_bot_hsv)
        self.zone_top_btn.configure(fg_color=hex1, hover_color=hex1)
        self.zone_bot_btn.configure(fg_color=hex2, hover_color=hex2)
        self.apply_custom_duo_zones()

    # ──────────────────────── ВКЛАДКА 3: ЭФФЕКТЫ, ПОМОДОРО И ТАЙМЕР ────────────────────────

    def _build_effects_tab(self):
        eff_card = ctk.CTkFrame(
            self.tab_effects, corner_radius=12, fg_color=THEME["bg_card"], border_width=1, border_color=THEME["border"]
        )
        eff_card.pack(fill="x", padx=8, pady=6)

        top_row = ctk.CTkFrame(eff_card, fg_color="transparent")
        top_row.pack(fill="x", padx=16, pady=(10, 6))

        ctk.CTkLabel(
            top_row,
            text=" Живые спецэффекты и 2-зонный Ambilight",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(side="left")

        self.btn_stop_effect = ctk.CTkButton(
            top_row,
            text="⏹ Остановить эффект",
            width=155,
            height=28,
            fg_color=THEME["danger"],
            hover_color=THEME["danger_hover"],
            state="disabled",
            command=self.stop_effect,
        )
        self.btn_stop_effect.pack(side="right")

        eff_grid = ctk.CTkFrame(eff_card, fg_color="transparent")
        eff_grid.pack(fill="x", padx=12, pady=(0, 8))
        eff_grid.grid_columnconfigure((0, 1), weight=1)

        effects_info = [
            ("🖥️ 2-Зонный Ambilight (ПК)", "ambilight_2zone", "#2563eb", "Верх экрана -> верхняя лента, низ экрана -> нижняя"),
            (" Плавная Радуга", "rainbow", "#7c3aed", "Непрерывный мягкий перелив по всему спектру"),
            (" Океанская волна (2 зоны)", "ocean_wave", "#0891b2", "Асинхронный перелив морской волны между лентами"),
            (" Уютный Камин", "fireplace", "#ea580c", "Живое теплое мерцание пламени костра"),
            (" Гроза и Молнии", "thunder", "#4f46e5", "Ночной грозовой фронт со вспышками молний"),
            (" Кибер-Тревога (Полиция)", "police", "#dc2626", "Попеременный красно-синий стробоскоп по зонам"),
            (" Дыхание (Пульс)", "breathing", "#0d9488", "Мягкое затухание и разгорание выбранного цвета"),
            (" Помодоро (25м Учеба / 5м Отдых)", "pomodoro", "#16a34a", "Автосмена фокуса (4500K) и отдыха (изумруд)"),
        ]

        for idx, (title, code, color, desc) in enumerate(effects_info):
            r, c = divmod(idx, 2)
            item = ctk.CTkFrame(eff_grid, corner_radius=10, fg_color=THEME["bg_card_inner"])
            item.grid(row=r, column=c, padx=5, pady=4, sticky="nsew")

            ctk.CTkButton(
                item,
                text=title,
                fg_color=color,
                font=ctk.CTkFont(size=12, weight="bold"),
                height=30,
                command=lambda k=code: self.start_effect(k),
            ).pack(fill="x", padx=8, pady=(8, 3))

            ctk.CTkLabel(
                item, text=desc, font=ctk.CTkFont(size=11), text_color=THEME["text_muted"], wraplength=280
            ).pack(padx=8, pady=(0, 6))

        speed_row = ctk.CTkFrame(eff_card, fg_color="transparent")
        speed_row.pack(fill="x", padx=16, pady=(2, 10))

        self.speed_label = ctk.CTkLabel(speed_row, text="Скорость эффекта: 1.0x", font=ctk.CTkFont(size=12))
        self.speed_label.pack(side="left", padx=(0, 12))

        self.speed_slider = ctk.CTkSlider(
            speed_row, from_=0.3, to=3.0, number_of_steps=27, command=self.on_speed_change
        )
        self.speed_slider.set(1.0)
        self.speed_slider.pack(side="left", fill="x", expand=True)

        # Таймер сна и Расписание по часам
        timer_card = ctk.CTkFrame(
            self.tab_effects, corner_radius=12, fg_color=THEME["bg_card"], border_width=1, border_color=THEME["border"]
        )
        timer_card.pack(fill="x", padx=8, pady=5)

        ctk.CTkLabel(
            timer_card,
            text=" Таймер сна и Будильник по времени",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(10, 6))

        timer_btns = ctk.CTkFrame(timer_card, fg_color="transparent")
        timer_btns.pack(fill="x", padx=16, pady=(0, 8))

        for mins in (15, 30, 45, 60, 120):
            ctk.CTkButton(
                timer_btns,
                text=f"Сон: {mins} мин",
                width=95,
                height=28,
                fg_color=THEME["bg_card_inner"],
                hover_color=THEME["border"],
                command=lambda m=mins: self.set_sleep_timer(m),
            ).pack(side="left", padx=3)

        ctk.CTkButton(
            timer_btns,
            text=" Сброс таймера",
            width=120,
            height=28,
            fg_color="#475569",
            hover_color=THEME["danger"],
            command=self.cancel_sleep_timer,
        ).pack(side="right", padx=3)

        # Будильник по часам ЧЧ:ММ
        sched_row = ctk.CTkFrame(timer_card, fg_color="transparent")
        sched_row.pack(fill="x", padx=16, pady=(2, 12))

        self.sched_check_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            sched_row,
            text="Авто-действие в (ЧЧ:ММ):",
            variable=self.sched_check_var,
            command=self.on_schedule_toggle,
        ).pack(side="left", padx=(0, 8))

        self.sched_time_entry = ctk.CTkEntry(sched_row, width=75, height=28, placeholder_text="23:30")
        self.sched_time_entry.insert(0, "23:30")
        self.sched_time_entry.pack(side="left", padx=(0, 8))

        self.sched_action_combo = ctk.CTkComboBox(
            sched_row,
            values=["Выключить все ленты", "Включить Рассвет (Теплый 100%)", "Включить Киберпанк"],
            width=245,
            height=28,
        )
        self.sched_action_combo.pack(side="left")

    # ──────────────────────── ВКЛАДКА 4: ПОИСК ЛЕНТ ПО СЕТИ И НАСТРОЙКИ ────────────────────────

    def _build_network_tab(self):
        # Карточка автоматического сканера локальной сети
        scan_card = ctk.CTkFrame(
            self.tab_network, corner_radius=12, fg_color=THEME["bg_card"], border_width=1, border_color=THEME["border"]
        )
        scan_card.pack(fill="both", expand=True, padx=8, pady=6)

        scan_head = ctk.CTkFrame(scan_card, fg_color="transparent")
        scan_head.pack(fill="x", padx=16, pady=(12, 6))

        ctk.CTkLabel(
            scan_head,
            text=" Автопоиск лент Tapo в локальной сети",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(side="left")

        self.btn_scan_net = ctk.CTkButton(
            scan_head,
            text=" Сканировать сеть сейчас",
            width=200,
            height=32,
            fg_color=THEME["purple"],
            hover_color=THEME["purple_hover"],
            font=ctk.CTkFont(weight="bold"),
            command=self.run_network_discovery,
        )
        self.btn_scan_net.pack(side="right")

        ctk.CTkLabel(
            scan_card,
            text="Сканер ищет ленты через Tapo Broadcast и проверяет все IP вашей подсети (192.168.x.1–254). "
                 "Нажмите « Помигать», чтобы увидеть, какая лента откликнулась.",
            font=ctk.CTkFont(size=11),
            text_color=THEME["text_muted"],
            wraplength=680,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(0, 8))

        self.discovered_scroll = ctk.CTkScrollableFrame(
            scan_card, fg_color=THEME["bg_card_inner"], corner_radius=10, height=190
        )
        self.discovered_scroll.pack(fill="both", expand=True, padx=14, pady=(0, 12))

        ctk.CTkLabel(
            self.discovered_scroll,
            text="Нажмите « Сканировать сеть сейчас», чтобы найти все ленты Tapo и их текущие IP.",
            text_color=THEME["text_muted"],
        ).pack(pady=30)

        # Ручное добавление ленты и аккаунт Tapo
        bottom_split = ctk.CTkFrame(self.tab_network, fg_color="transparent")
        bottom_split.pack(fill="x", padx=8, pady=4)
        bottom_split.grid_columnconfigure((0, 1), weight=1)

        # Левая карточка: Аккаунт
        auth_card = ctk.CTkFrame(
            bottom_split, corner_radius=12, fg_color=THEME["bg_card"], border_width=1, border_color=THEME["border"]
        )
        auth_card.grid(row=0, column=0, sticky="nsew", padx=(0, 5), pady=2)

        ctk.CTkLabel(
            auth_card, text="🔐 Аккаунт TP-Link Tapo", font=ctk.CTkFont(size=13, weight="bold")
        ).pack(anchor="w", padx=14, pady=(10, 6))

        self.email_entry = ctk.CTkEntry(auth_card, placeholder_text="Email Tapo")
        self.email_entry.pack(fill="x", padx=14, pady=4)
        self.email_entry.bind("<KeyRelease>", self.save_config)

        pass_row = ctk.CTkFrame(auth_card, fg_color="transparent")
        pass_row.pack(fill="x", padx=14, pady=(4, 10))

        self.pass_entry = ctk.CTkEntry(pass_row, placeholder_text="Пароль", show="*")
        self.pass_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.pass_entry.bind("<KeyRelease>", self.save_config)

        self.show_pass_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            pass_row,
            text="👁",
            width=40,
            variable=self.show_pass_var,
            command=lambda: self.pass_entry.configure(show="" if self.show_pass_var.get() else "*"),
        ).pack(side="right")

        # Правая карточка: Ручное управление списком лент
        dev_card = ctk.CTkFrame(
            bottom_split, corner_radius=12, fg_color=THEME["bg_card"], border_width=1, border_color=THEME["border"]
        )
        dev_card.grid(row=0, column=1, sticky="nsew", padx=(5, 0), pady=2)

        ctk.CTkLabel(
            dev_card, text="⚙ Ручное добавление / Удаление", font=ctk.CTkFont(size=13, weight="bold")
        ).pack(anchor="w", padx=14, pady=(10, 6))

        entry_row = ctk.CTkFrame(dev_card, fg_color="transparent")
        entry_row.pack(fill="x", padx=14, pady=4)

        self.new_name_entry = ctk.CTkEntry(entry_row, placeholder_text="Имя (напр. над столом)", width=150)
        self.new_name_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.new_ip_entry = ctk.CTkEntry(entry_row, placeholder_text="IP (192.168.0.x)", width=130)
        self.new_ip_entry.pack(side="left")

        btn_dev_row = ctk.CTkFrame(dev_card, fg_color="transparent")
        btn_dev_row.pack(fill="x", padx=14, pady=(4, 10))

        ctk.CTkButton(
            btn_dev_row,
            text="➕ Сохранить",
            width=105,
            height=28,
            fg_color=THEME["success"],
            hover_color=THEME["success_hover"],
            command=self.add_or_update_device,
        ).pack(side="left", padx=(0, 6))

        self.del_combo = ctk.CTkComboBox(btn_dev_row, values=["Нет лент"], width=125, height=28)
        self.del_combo.pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            btn_dev_row,
            text="🗑",
            width=36,
            height=28,
            fg_color=THEME["danger"],
            hover_color=THEME["danger_hover"],
            command=self.delete_device,
        ).pack(side="left")

    # ──────────────────────── ЛОГИКА АВТОПОИСКА ЛЕНТ ПО СЕТИ ────────────────────────

    def open_and_run_discovery(self):
        self.tabs.set(" Поиск и Сеть")
        self.run_network_discovery()

    def run_network_discovery(self):
        self.btn_scan_net.configure(state="disabled", text="⏳ Поиск...")
        for w in self.discovered_scroll.winfo_children():
            w.destroy()
        ctk.CTkLabel(
            self.discovered_scroll,
            text="⏳ Сканирование локальной подсети и опрос устройств Tapo (около 3–5 сек)...",
            text_color=THEME["warning"],
        ).pack(pady=30)

        email = self.email_entry.get().strip()
        password = self.pass_entry.get().strip()
        known_ips = list(self.saved_devices.values())

        def _on_done(found_list):
            self.after(0, lambda: self._render_discovered_devices(found_list))

        self.backend.discover_network_devices(email, password, known_ips, _on_done)

    def _render_discovered_devices(self, found_list):
        self.btn_scan_net.configure(state="normal", text=" Сканировать сеть сейчас")
        self.discovered_devices = found_list

        for w in self.discovered_scroll.winfo_children():
            w.destroy()

        if not found_list:
            ctk.CTkLabel(
                self.discovered_scroll,
                text="Устройства не откликнулись на автопоиск. Убедитесь, что ленты включены в розетку и подключены к вашему Wi-Fi.",
                text_color=THEME["danger"],
                wraplength=600,
            ).pack(pady=25)
            self.show_status_threadsafe("По сети не найдено новых ответов от лент", THEME["warning"])
            return

        self.show_status_threadsafe(f"🎉 Найдено устройств в сети: {len(found_list)} шт.", THEME["success"])

        # Определяем, какие IP уже привязаны
        ip_to_saved_name = {ip: name for name, ip in self.saved_devices.items()}

        for dev in found_list:
            ip = dev["ip"]
            alias = dev.get("alias", "Tapo Strip")
            model = dev.get("model", "")
            bound_name = ip_to_saved_name.get(ip)

            row = ctk.CTkFrame(self.discovered_scroll, corner_radius=8, fg_color=THEME["bg_card"])
            row.pack(fill="x", padx=6, pady=4)

            badge_text = f" Привязана как «{bound_name}»" if bound_name else "🆕 Новое / Сменился IP"
            badge_col = THEME["success"] if bound_name else THEME["warning"]

            info_frame = ctk.CTkFrame(row, fg_color="transparent")
            info_frame.pack(side="left", padx=10, pady=6)

            ctk.CTkLabel(
                info_frame,
                text=f" {ip}  —  {alias} ({model})",
                font=ctk.CTkFont(size=13, weight="bold"),
            ).pack(anchor="w")

            ctk.CTkLabel(
                info_frame,
                text=badge_text,
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=badge_col,
            ).pack(anchor="w")

            # Кнопки действий справа
            actions = ctk.CTkFrame(row, fg_color="transparent")
            actions.pack(side="right", padx=8, pady=6)

            ctk.CTkButton(
                actions,
                text=" Помигать",
                width=92,
                height=28,
                fg_color=THEME["accent"],
                hover_color=THEME["accent_hover"],
                font=ctk.CTkFont(size=11, weight="bold"),
                command=lambda i=ip: self.backend.blink_identify(
                    i, self.email_entry.get().strip(), self.pass_entry.get().strip()
                ),
            ).pack(side="left", padx=3)

            # Быстрые кнопки назначения на существующие ленты ("над столом", "под столом")
            target_names = list(self.saved_devices.keys()) or ["над столом", "под столом"]
            for t_name in target_names[:2]:
                ctk.CTkButton(
                    actions,
                    text=f"➔ {t_name}",
                    width=105,
                    height=28,
                    fg_color="#334155",
                    hover_color=THEME["success"],
                    font=ctk.CTkFont(size=11),
                    command=lambda tn=t_name, i=ip: self.bind_discovered_ip(tn, i),
                ).pack(side="left", padx=3)

    def bind_discovered_ip(self, name, ip):
        self.saved_devices[name] = ip
        self.backend.clear_cache()
        self.update_device_selectors()
        self._render_per_device_controls()
        self.save_config()
        self._render_discovered_devices(self.discovered_devices)
        self.check_all_devices_online()
        self.show_status_threadsafe(f" Лента «{name}» теперь привязана к IP {ip}!", THEME["success"])

    # ──────────────────────── ПРОВЕРКА СВЯЗИ (PING) ────────────────────────

    def check_all_devices_online(self):
        if not self.saved_devices:
            return

        def _worker():
            self.show_status_threadsafe(" Проверка доступности лент в сети...", THEME["warning"])
            results = {}
            for name, ip in self.saved_devices.items():
                results[name] = self.backend.check_ip_online(ip)
            self.online_status = results
            self.after(0, self._render_per_device_controls)

            online_list = [n for n, ok in results.items() if ok]
            offline_list = [f"«{n}» ({self.saved_devices[n]})" for n, ok in results.items() if not ok]

            if not offline_list:
                self.show_status_threadsafe(
                    f" Все ленты в сети ({len(online_list)} из {len(results)})", THEME["success"]
                )
            elif online_list:
                self.show_status_threadsafe(
                    f" Не в сети: {', '.join(offline_list)} — нажмите « Найти ленты»", THEME["warning"]
                )
            else:
                self.show_status_threadsafe(
                    f" Ленты не отвечают: {', '.join(offline_list)} — проверьте питание или нажмите « Найти ленты»",
                    THEME["danger"],
                )

        threading.Thread(target=_worker, daemon=True).start()

    # ──────────────────────── ЛОГИКА ЦВЕТА И ПРЕСЕТОВ ────────────────────────

    def _render_default_swatches(self):
        for widget in self.swatches_grid.winfo_children():
            widget.destroy()

        for idx, (name, h, s, v, hex_col) in enumerate(DEFAULT_PRESETS):
            btn = ctk.CTkButton(
                self.swatches_grid,
                text=f"{idx + 1}. {name}",
                fg_color=hex_col,
                text_color="#000000" if (v > 70 and s < 45) else "#ffffff",
                hover_color=hex_col,
                height=30,
                corner_radius=8,
                font=ctk.CTkFont(size=12, weight="bold"),
                command=lambda h=h, s=s, v=v: self.apply_hsv_preset(h, s, v),
            )
            r, c = divmod(idx, 4)
            btn.grid(row=r, column=c, padx=4, pady=3, sticky="ew")
        self.swatches_grid.grid_columnconfigure((0, 1, 2, 3), weight=1)

    def _render_favorite_swatches(self):
        for widget in self.fav_swatches_grid.winfo_children():
            widget.destroy()

        if not self.custom_colors:
            return

        ctk.CTkLabel(
            self.fav_swatches_grid,
            text=" Избранные цвета (ПКМ — удалить):",
            font=ctk.CTkFont(size=11),
            text_color=THEME["text_muted"],
        ).grid(row=0, column=0, columnspan=6, sticky="w", padx=4, pady=(2, 2))

        for idx, item in enumerate(self.custom_colors):
            h, s, v = item["h"], item["s"], item["v"]
            hex_col = self.hsv_to_hex(h, s, v)
            btn = ctk.CTkButton(
                self.fav_swatches_grid,
                text=hex_col.upper(),
                fg_color=hex_col,
                text_color="#000000" if (v > 70 and s < 40) else "#ffffff",
                hover_color=hex_col,
                height=26,
                corner_radius=6,
                font=ctk.CTkFont(size=11, weight="bold"),
                command=lambda h=h, s=s, v=v: self.apply_hsv_preset(h, s, v),
            )
            btn.bind("<Button-3>", lambda e, i=idx: self.delete_fav_color(i))
            r, c = divmod(idx, 6)
            btn.grid(row=r + 1, column=c, padx=3, pady=2, sticky="ew")
        self.fav_swatches_grid.grid_columnconfigure((0, 1, 2, 3, 4, 5), weight=1)

    @staticmethod
    def hsv_to_hex(h, s, v):
        r, g, b = colorsys.hsv_to_rgb(
            (h % 360) / 360.0, max(0, min(100, s)) / 100.0, max(0, min(100, v)) / 100.0
        )
        return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"

    def _update_color_preview_ui(self):
        hex_col = self.hsv_to_hex(self.curr_h, self.curr_s, self.curr_v)
        self.color_preview.configure(fg_color=hex_col)
        self.live_dot.configure(fg_color=hex_col)
        self.hex_label.configure(text=hex_col.upper())
        self.hsv_sub_label.configure(text=f"HSV: {self.curr_h}° • {self.curr_s}% • {self.curr_v}%")
        self.hue_label.configure(text=f"Оттенок (Hue): {self.curr_h}°")
        self.sat_label.configure(text=f"Насыщенность (Saturation): {self.curr_s}%")
        self.val_label.configure(text=f" Яркость (Brightness): {self.curr_v}%")
        self._draw_spectrum_cursor()

    def apply_hex_from_entry(self, _=None):
        raw = self.hex_entry.get().strip().lstrip("#")
        if len(raw) == 6:
            try:
                r = int(raw[0:2], 16)
                g = int(raw[2:4], 16)
                b = int(raw[4:6], 16)
                h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
                self.apply_hsv_preset(int(h * 360), int(s * 100), max(1, int(v * 100)))
            except ValueError:
                self.show_status_threadsafe("Неверный формат HEX (пример: #FF00AA)", THEME["danger"])

    def on_hsv_slider_change(self, _=None):
        self.stop_effect(silent=True)
        self.curr_h = int(self.hue_slider.get())
        self.curr_s = int(self.sat_slider.get())
        self.curr_v = int(self.val_slider.get())
        self._update_color_preview_ui()
        self._schedule_debounced(
            lambda: self.send_to_selected("hsv", (self.curr_h, self.curr_s, self.curr_v), deduplicate=True)
        )

    def on_brightness_slider_change(self, val):
        self.curr_v = int(val)
        self._update_color_preview_ui()
        if not self.effect_running:
            self._schedule_debounced(
                lambda: self.send_to_selected("brightness", (self.curr_v,), deduplicate=True)
            )

    def set_quick_brightness(self, pct):
        self.val_slider.set(pct)
        self.curr_v = int(pct)
        self._update_color_preview_ui()
        self.send_to_selected("brightness", (self.curr_v,))

    def set_quick_temperature(self, kelvin):
        self.temp_slider.set(kelvin)
        self.on_temp_slider_change(kelvin)

    def on_temp_slider_change(self, val):
        self.stop_effect(silent=True)
        self.curr_temp = int(val)
        self.temp_label.configure(text=f"🌡 Белый свет (Температура): {self.curr_temp}K")
        h, s, v = TapoBackend.kelvin_to_hsv(self.curr_temp, self.curr_v)
        self.curr_h, self.curr_s = h, s
        self.hue_slider.set(h)
        self.sat_slider.set(s)
        self._update_color_preview_ui()
        self._schedule_debounced(
            lambda: self.send_to_selected("temperature", (self.curr_temp, self.curr_v), deduplicate=True)
        )

    def apply_hsv_preset(self, h, s, v):
        self.stop_effect(silent=True)
        self.curr_h, self.curr_s, self.curr_v = int(h), int(s), int(v)
        self.hue_slider.set(self.curr_h)
        self.sat_slider.set(self.curr_s)
        self.val_slider.set(self.curr_v)
        self._update_color_preview_ui()
        self.send_to_selected("hsv", (self.curr_h, self.curr_s, self.curr_v))

    def choose_color_dialog(self):
        initial_hex = self.hsv_to_hex(self.curr_h, self.curr_s, self.curr_v)
        color_code = colorchooser.askcolor(color=initial_hex, title="Выберите цвет ленты Tapo")
        if color_code and color_code[0]:
            r, g, b = color_code[0]
            h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            self.apply_hsv_preset(int(h * 360), int(s * 100), max(1, int(v * 100)))

    def save_current_color_to_fav(self):
        new_col = {"h": self.curr_h, "s": self.curr_s, "v": self.curr_v}
        if new_col not in self.custom_colors:
            self.custom_colors.append(new_col)
            self.save_config()
            self._render_favorite_swatches()
            self.show_status_threadsafe(" Цвет сохранен в избранное!", THEME["success"])

    def delete_fav_color(self, idx):
        if 0 <= idx < len(self.custom_colors):
            self.custom_colors.pop(idx)
            self.save_config()
            self._render_favorite_swatches()

    def start_smooth_fade_off(self):
        """Плавно снижает яркость до 0% за 10 секунд и выключает выбранные ленты."""
        targets = self.get_selected_targets()
        if not targets:
            return
        self.stop_effect(silent=True)
        self.effect_running = True
        self.effect_name = "fade_off"
        self.btn_stop_effect.configure(state="normal")
        self.show_status_threadsafe(" Плавное затухание лент (10 сек)...", THEME["warning"])

        def _fade():
            start_v = max(10, self.curr_v)
            for step_v in range(start_v, 0, -max(1, start_v // 8)):
                if not self.effect_running or self.effect_name != "fade_off":
                    return
                self._send_Direct(targets, "brightness", (step_v,), silent=True, deduplicate=True)
                time.sleep(1.1)
            if self.effect_running and self.effect_name == "fade_off":
                self._send_Direct(targets, "off", (), silent=False)
                self.after(0, lambda: self.stop_effect(silent=True))

        threading.Thread(target=_fade, daemon=True).start()

    # ──────────────────────── СЦЕНЫ И ИНДИВИДУАЛЬНЫЕ ЗОНЫ ────────────────────────

    def _render_per_device_controls(self):
        for w in self.per_device_container.winfo_children():
            w.destroy()

        if not self.saved_devices:
            ctk.CTkLabel(
                self.per_device_container,
                text="Нет сохраненных лент. Перейдите во вкладку « Поиск и Сеть».",
                text_color=THEME["text_muted"],
            ).pack(pady=8)
            return

        for name, ip in self.saved_devices.items():
            row = ctk.CTkFrame(self.per_device_container, corner_radius=8, fg_color=THEME["bg_card_inner"])
            row.pack(fill="x", pady=3, padx=4)

            is_online = self.online_status.get(name)
            badge = "" if is_online is True else ("" if is_online is False else "")

            ctk.CTkLabel(
                row, text=f"{badge} {name}", font=ctk.CTkFont(size=13, weight="bold"), width=140, anchor="w"
            ).pack(side="left", padx=12, pady=7)

            ctk.CTkLabel(row, text=ip, font=ctk.CTkFont(size=11), text_color=THEME["text_muted"]).pack(
                side="left", padx=4
            )

            ctk.CTkButton(
                row,
                text="Выкл",
                width=60,
                height=26,
                fg_color=THEME["danger"],
                hover_color=THEME["danger_hover"],
                command=lambda n=name, i=ip: self._send_Direct([(n, i)], "off"),
            ).pack(side="right", padx=(4, 10), pady=5)

            ctk.CTkButton(
                row,
                text="Вкл",
                width=60,
                height=26,
                fg_color=THEME["success"],
                hover_color=THEME["success_hover"],
                command=lambda n=name, i=ip: self._send_Direct([(n, i)], "on"),
            ).pack(side="right", padx=4, pady=5)

            ctk.CTkButton(
                row,
                text=" Мигнуть",
                width=85,
                height=26,
                fg_color="#334155",
                hover_color="#475569",
                command=lambda i=ip: self.backend.blink_identify(
                    i, self.email_entry.get().strip(), self.pass_entry.get().strip()
                ),
            ).pack(side="right", padx=4, pady=5)

            ctk.CTkButton(
                row,
                text="Применить текущий цвет",
                width=165,
                height=26,
                fg_color=THEME["accent"],
                hover_color=THEME["accent_hover"],
                command=lambda n=name, i=ip: self._send_Direct(
                    [(n, i)], "hsv", (self.curr_h, self.curr_s, self.curr_v)
                ),
            ).pack(side="right", padx=4, pady=5)

    def _render_scenes_list(self):
        for w in self.scenes_scroll.winfo_children():
            w.destroy()

        all_scenes = BUILT_IN_SCENES + self.custom_scenes
        for idx, sc in enumerate(all_scenes):
            card = ctk.CTkFrame(self.scenes_scroll, corner_radius=10, fg_color=THEME["bg_card_inner"])
            card.pack(fill="x", padx=4, pady=4)

            top_h, top_s, top_v = sc["top"]
            bot_h, bot_s, bot_v = sc["bottom"]
            hex1 = self.hsv_to_hex(top_h, top_s, top_v)
            hex2 = self.hsv_to_hex(bot_h, bot_s, bot_v)

            swatch_box = ctk.CTkFrame(card, fg_color="transparent")
            swatch_box.pack(side="left", padx=10, pady=8)

            ctk.CTkFrame(swatch_box, width=22, height=34, corner_radius=6, fg_color=hex1).pack(side="left", padx=2)
            ctk.CTkFrame(swatch_box, width=22, height=34, corner_radius=6, fg_color=hex2).pack(side="left", padx=2)

            txt_box = ctk.CTkFrame(card, fg_color="transparent")
            txt_box.pack(side="left", fill="both", expand=True, pady=6)

            ctk.CTkLabel(txt_box, text=sc["name"], font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w")
            ctk.CTkLabel(txt_box, text=sc.get("desc", ""), font=ctk.CTkFont(size=11), text_color=THEME["text_muted"]).pack(
                anchor="w"
            )

            if idx >= len(BUILT_IN_SCENES):
                custom_idx = idx - len(BUILT_IN_SCENES)
                ctk.CTkButton(
                    card,
                    text="🗑",
                    width=32,
                    height=28,
                    fg_color="#475569",
                    hover_color=THEME["danger"],
                    command=lambda ci=custom_idx: self.delete_custom_scene(ci),
                ).pack(side="right", padx=(0, 6))

            ctk.CTkButton(
                card,
                text="▶ Включить",
                width=105,
                height=30,
                fg_color=THEME["accent"],
                hover_color=THEME["accent_hover"],
                font=ctk.CTkFont(weight="bold"),
                command=lambda s=sc: self.apply_scene(s),
            ).pack(side="right", padx=10)

    def apply_scene(self, scene):
        self.stop_effect(silent=True)
        items = list(self.saved_devices.items())
        if not items:
            self.show_status_threadsafe("Нет сохраненных лент!", THEME["danger"])
            return

        # Синхронизируем цвета с двухзонным микшером
        self.zone_top_hsv = tuple(scene["top"])
        self.zone_bot_hsv = tuple(scene["bottom"])

        # Умное распределение: если есть лента "над столом" — ей верхний тон, "под столом" — нижний
        top_targets = [it for it in items if "над" in it[0].lower() or "верх" in it[0].lower()]
        bot_targets = [it for it in items if "под" in it[0].lower() or "низ" in it[0].lower()]

        if top_targets and bot_targets:
            self._send_Direct(top_targets, "hsv", scene["top"])
            self._send_Direct(bot_targets, "hsv", scene["bottom"])
            self.show_status_threadsafe(f" Сцена «{scene['name']}» применена на обе зоны!", THEME["success"])
        elif len(items) >= 2:
            self._send_Direct([items[0]], "hsv", scene["top"])
            self._send_Direct(items[1:], "hsv", scene["bottom"])
            self.show_status_threadsafe(f" Сцена «{scene['name']}» применена на обе зоны!", THEME["success"])
        else:
            self._send_Direct(items, "hsv", scene["top"])

    def save_custom_scene(self):
        name = f"Мой дуэт #{len(self.custom_scenes) + 1}"
        new_scene = {
            "name": f" {name}",
            "desc": f"Верх HSV{tuple(self.zone_top_hsv)} + Низ HSV{tuple(self.zone_bot_hsv)}",
            "top": list(self.zone_top_hsv),
            "bottom": list(self.zone_bot_hsv),
        }
        self.custom_scenes.append(new_scene)
        self.save_config()
        self._render_scenes_list()
        self.show_status_threadsafe(" Сцена сохранена в коллекцию!", THEME["success"])

    def delete_custom_scene(self, idx):
        if 0 <= idx < len(self.custom_scenes):
            self.custom_scenes.pop(idx)
            self.save_config()
            self._render_scenes_list()

    # ──────────────────────── ДИНАМИЧЕСКИЕ ЭФФЕКТЫ, ПОМОДОРО И РАСПИСАНИЕ ────────────────────────

    def on_speed_change(self, val):
        self.effect_speed = round(float(val), 1)
        self.speed_label.configure(text=f"Скорость эффекта: {self.effect_speed}x")

    def start_effect(self, effect_code):
        if effect_code == "ambilight_2zone" and not HAS_IMAGEGRAB:
            self.show_status_threadsafe("Для Ambilight установите Pillow: pip install pillow", THEME["danger"])
            return

        targets = self.get_selected_targets()
        if not targets:
            self.show_status_threadsafe("Сначала выберите или добавьте ленту!", THEME["danger"])
            return

        self.stop_effect(silent=True)
        self.effect_running = True
        self.effect_name = effect_code
        self.btn_stop_effect.configure(state="normal")
        self.show_status_threadsafe(f" Активен режим: {effect_code.upper()}", THEME["accent"])

        threading.Thread(target=self._effect_worker, args=(effect_code,), daemon=True).start()

    def stop_effect(self, silent=False):
        if self.effect_running:
            self.effect_running = False
            self.effect_name = None
            self.btn_stop_effect.configure(state="disabled")
            if not silent:
                self.show_status_threadsafe("⏹ Эффект остановлен", THEME["text_muted"])

    @staticmethod
    def _avg_region_hsv(img):
        pixels = list(img.getdata())
        n = max(1, len(pixels))
        r_avg = sum(p[0] for p in pixels) / n
        g_avg = sum(p[1] for p in pixels) / n
        b_avg = sum(p[2] for p in pixels) / n
        h, s, v = colorsys.rgb_to_hsv(r_avg / 255.0, g_avg / 255.0, b_avg / 255.0)
        s_boost = min(1.0, s * 1.45)
        v_boost = max(0.15, min(1.0, v * 1.15))
        return int(h * 360), int(s_boost * 100), int(v_boost * 100)

    def _effect_worker(self, code):
        step = 0
        pomo_start = time.time()
        while self.effect_running and self.effect_name == code:
            targets = self.get_selected_targets()
            if not targets:
                break

            delay = max(0.35, 1.1 / max(0.3, self.effect_speed))

            if code == "ambilight_2zone":
                try:
                    screen = ImageGrab.grab().resize((28, 16))
                    if len(targets) >= 2:
                        top_crop = screen.crop((0, 0, 28, 8))
                        bot_crop = screen.crop((0, 8, 28, 16))
                        hsv_top = self._avg_region_hsv(top_crop)
                        hsv_bot = self._avg_region_hsv(bot_crop)
                        # Ищем ленту "над столом" и "под столом"
                        top_t = [t for t in targets if "над" in t[0].lower()] or [targets[0]]
                        bot_t = [t for t in targets if t not in top_t] or [targets[-1]]
                        self._send_Direct(top_t, "hsv", hsv_top, silent=True, deduplicate=True)
                        self._send_Direct(bot_t, "hsv", hsv_bot, silent=True, deduplicate=True)
                    else:
                        hsv_all = self._avg_region_hsv(screen)
                        self._send_Direct(targets, "hsv", hsv_all, silent=True, deduplicate=True)
                except Exception:
                    pass
                delay = max(0.4, 0.7 / max(0.5, self.effect_speed))

            elif code == "rainbow":
                hue = (step * 18) % 360
                self._send_Direct(targets, "hsv", (hue, 100, self.curr_v), silent=True, deduplicate=True)

            elif code == "ocean_wave":
                palette = [(180, 100, 95), (195, 95, 85), (215, 100, 90), (235, 90, 75), (200, 100, 100)]
                if len(targets) >= 2:
                    c1 = palette[step % len(palette)]
                    c2 = palette[(step + 2) % len(palette)]
                    self._send_Direct([targets[0]], "hsv", c1, silent=True, deduplicate=True)
                    self._send_Direct(targets[1:], "hsv", c2, silent=True, deduplicate=True)
                else:
                    self._send_Direct(targets, "hsv", palette[step % len(palette)], silent=True, deduplicate=True)

            elif code == "fireplace":
                for t in targets:
                    hue = random.randint(10, 28)
                    sat = random.randint(92, 100)
                    br = random.randint(30, 95)
                    self._send_Direct([t], "hsv", (hue, sat, br), silent=True, deduplicate=True)

            elif code == "thunder":
                if random.random() < 0.22:
                    # Вспышка молнии
                    self._send_Direct(targets, "hsv", (210, 10, 100), silent=True, deduplicate=True)
                    time.sleep(0.25)
                    self._send_Direct(targets, "hsv", (235, 90, 25), silent=True, deduplicate=True)
                else:
                    br = random.randint(15, 40)
                    self._send_Direct(targets, "hsv", (230, 90, br), silent=True, deduplicate=True)

            elif code == "police":
                c_red = (0, 100, 100)
                c_blue = (235, 100, 100)
                if len(targets) >= 2:
                    first = c_red if step % 2 == 0 else c_blue
                    second = c_blue if step % 2 == 0 else c_red
                    self._send_Direct([targets[0]], "hsv", first, silent=True, deduplicate=True)
                    self._send_Direct(targets[1:], "hsv", second, silent=True, deduplicate=True)
                else:
                    self._send_Direct(
                        targets, "hsv", c_red if step % 2 == 0 else c_blue, silent=True, deduplicate=True
                    )
                delay = max(0.35, 0.55 / max(0.5, self.effect_speed))

            elif code == "breathing":
                levels = [15, 40, 70, 100, 70, 40]
                br = levels[step % len(levels)]
                self._send_Direct(targets, "hsv", (self.curr_h, self.curr_s, br), silent=True, deduplicate=True)

            elif code == "pomodoro":
                elapsed = int(time.time() - pomo_start) % 1800  # 30 минут полный цикл (25 мин + 5 мин)
                if elapsed < 1500:
                    rem = 1500 - elapsed
                    m, s = divmod(rem, 60)
                    self.after(
                        0, lambda m=m, s=s: self.timer_status_label.configure(text=f" Учеба: {m:02d}:{s:02d}")
                    )
                    if step % 15 == 0:
                        self._send_Direct(targets, "temperature", (4500, 95), silent=True, deduplicate=True)
                else:
                    rem = 1800 - elapsed
                    m, s = divmod(rem, 60)
                    self.after(
                        0, lambda m=m, s=s: self.timer_status_label.configure(text=f" Перерыв: {m:02d}:{s:02d}")
                    )
                    if step % 15 == 0:
                        self._send_Direct(targets, "hsv", (145, 90, 75), silent=True, deduplicate=True)
                delay = 1.0

            step += 1
            time.sleep(delay)

    def set_sleep_timer(self, minutes):
        self.cancel_sleep_timer(silent=True)
        self.sleep_timer_end = time.time() + minutes * 60
        self.show_status_threadsafe(f" Таймер сна установлен на {minutes} мин", THEME["warning"])
        self._tick_sleep_timer()

    def _tick_sleep_timer(self):
        if not self.sleep_timer_end:
            return
        remaining = int(self.sleep_timer_end - time.time())
        if remaining <= 0:
            self.sleep_timer_end = None
            self.timer_status_label.configure(text="")
            self.turn_off_selected()
            self.show_status_threadsafe(" Ленты автоматически выключены по таймеру сна", THEME["success"])
            return

        mins, secs = divmod(remaining, 60)
        self.timer_status_label.configure(text=f" Сон через {mins:02d}:{secs:02d}")
        self.sleep_after_id = self.after(1000, self._tick_sleep_timer)

    def cancel_sleep_timer(self, silent=False):
        if self.sleep_after_id:
            self.after_cancel(self.sleep_after_id)
            self.sleep_after_id = None
        self.sleep_timer_end = None
        self.timer_status_label.configure(text="")
        if not silent:
            self.show_status_threadsafe("Таймер сна отменен", THEME["text_muted"])

    def on_schedule_toggle(self):
        if self.sched_check_var.get():
            t_str = self.sched_time_entry.get().strip()
            self.show_status_threadsafe(f" Будильник активен на {t_str}", THEME["success"])
        else:
            self.show_status_threadsafe("Будильник по времени отключен", THEME["text_muted"])

    def _start_schedule_watcher(self):
        """Проверяет текущее время каждые 20 секунд для выполнения запланированного действия."""
        self._last_triggered_minute = ""

        def _check():
            if self.sched_check_var.get():
                now_hm = datetime.datetime.now().strftime("%H:%M")
                target_hm = self.sched_time_entry.get().strip()
                if now_hm == target_hm and self._last_triggered_minute != now_hm:
                    self._last_triggered_minute = now_hm
                    act = self.sched_action_combo.get()
                    if "Выключить" in act:
                        self.turn_off_selected()
                    elif "Рассвет" in act:
                        self.apply_hsv_preset(32, 70, 100)
                    else:
                        self.apply_scene(BUILT_IN_SCENES[0])
                    self.show_status_threadsafe(f" Выполнено по расписанию ({now_hm}): {act}", THEME["success"])
            self.after(15000, _check)

        self.after(15000, _check)

    # ──────────────────────── УПРАВЛЕНИЕ КОНФИГОМ И КОМАНДАМИ ────────────────────────

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    config = json.load(f)
                self.email_entry.delete(0, tk.END)
                self.email_entry.insert(0, config.get("email", ""))
                self.pass_entry.delete(0, tk.END)
                self.pass_entry.insert(0, config.get("password", ""))
                self.saved_devices = config.get("devices", {})
                self.custom_colors = config.get("custom_colors", [])
                self.custom_scenes = config.get("custom_scenes", [])

                self.update_device_selectors()
                self._render_favorite_swatches()
                self._render_per_device_controls()
                self._render_scenes_list()

                last_sel = config.get("last_selected", self.ALL_DEVICES_KEY)
                valid_values = [self.ALL_DEVICES_KEY] + list(self.saved_devices.keys())
                if last_sel in valid_values:
                    self.device_selector.set(last_sel)
                else:
                    self.device_selector.set(self.ALL_DEVICES_KEY)
            except Exception as e:
                self.show_status_threadsafe(f"Ошибка чтения конфига: {e}", THEME["danger"])
        else:
            self.update_device_selectors()
            self._render_per_device_controls()
            self._render_scenes_list()

    def save_config(self, event=None):
        config_data = {
            "email": self.email_entry.get().strip(),
            "password": self.pass_entry.get().strip(),
            "devices": self.saved_devices,
            "last_selected": self.device_selector.get(),
            "custom_colors": self.custom_colors,
            "custom_scenes": self.custom_scenes,
        }
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=4, ensure_ascii=False)
        except Exception:
            pass

    def update_device_selectors(self):
        names = list(self.saved_devices.keys())
        seg_values = [self.ALL_DEVICES_KEY] + names if names else ["Нет сохраненных лент"]
        self.device_selector.configure(values=seg_values)

        if self.device_selector.get() not in seg_values:
            self.device_selector.set(seg_values[0])

        if names:
            self.del_combo.configure(values=names)
            if self.del_combo.get() not in names:
                self.del_combo.set(names[0])
        else:
            self.del_combo.configure(values=["Нет лент"])
            self.del_combo.set("Нет лент")

    def on_device_select(self, choice):
        self.save_config()
        if choice == self.ALL_DEVICES_KEY:
            self.show_status_threadsafe(
                f"Выбрано синхронное управление всеми лентами ({len(self.saved_devices)} шт.)", THEME["text_muted"]
            )
        elif choice in self.saved_devices:
            ip = self.saved_devices[choice]
            st = self.online_status.get(choice)
            st_txt = " Онлайн" if st is True else (" Не в сети" if st is False else "")
            self.show_status_threadsafe(f"Активна лента «{choice}» ({ip}) {st_txt}", THEME["text_muted"])

    def add_or_update_device(self):
        name = self.new_name_entry.get().strip()
        ip = self.new_ip_entry.get().strip()
        if not name or not ip:
            self.show_status_threadsafe("Введите и имя, и IP-адрес ленты!", THEME["danger"])
            return

        self.saved_devices[name] = ip
        self.backend.clear_cache()
        self.update_device_selectors()
        self._render_per_device_controls()
        self.device_selector.set(name)

        self.new_name_entry.delete(0, tk.END)
        self.new_ip_entry.delete(0, tk.END)
        self.save_config()
        self.check_all_devices_online()

    def delete_device(self):
        target = self.del_combo.get()
        if target in self.saved_devices:
            del self.saved_devices[target]
            self.online_status.pop(target, None)
            self.backend.clear_cache()
            self.update_device_selectors()
            self._render_per_device_controls()
            self.save_config()
            self.show_status_threadsafe(f"Лента «{target}» удалена", THEME["text_muted"])

    def get_selected_targets(self):
        sel = self.device_selector.get()
        if sel == self.ALL_DEVICES_KEY:
            return list(self.saved_devices.items())
        if sel in self.saved_devices:
            return [(sel, self.saved_devices[sel])]
        return []

    def turn_off_selected(self):
        self.stop_effect(silent=True)
        self.send_to_selected("off")

    def send_to_selected(self, action, params=(), silent=False, deduplicate=False):
        targets = self.get_selected_targets()
        self._send_Direct(targets, action, params, silent=silent, deduplicate=deduplicate)

    def _send_Direct(self, targets, action, params=(), silent=False, deduplicate=False):
        email = self.email_entry.get().strip()
        password = self.pass_entry.get().strip()

        if not targets:
            self.show_status_threadsafe("Добавьте или найдите ленту во вкладке « Поиск и Сеть»!", THEME["danger"])
            return
        if not email or not password:
            self.show_status_threadsafe("Заполните Email и Пароль во вкладке « Поиск и Сеть»!", THEME["danger"])
            return

        self.backend.send_command(
            targets, email, password, action, params, silent=silent, deduplicate=deduplicate
        )

    def _schedule_debounced(self, callback, delay_ms=130):
        if self.debounce_timer:
            self.after_cancel(self.debounce_timer)
        self.debounce_timer = self.after(delay_ms, callback)

    def show_status_threadsafe(self, text, color=THEME["text_muted"]):
        self.after(0, lambda: self.status_label.configure(text=text, text_color=color))


if __name__ == "__main__":
    app = TapoGuiApp()
    app.mainloop()
