#!/usr/bin/env python3
"""
Falling Into A Black Hole Simulator v2 — Tkinter

Educational / visual simulator, not a full GR ray tracer.
Core idea: show the sky from the point of view of an observer falling toward a
Schwarzschild black hole, with approximate lensing, Doppler/redshift, tidal
stretching, photon sphere markers, accretion disk, visible Doppler/redshift,
star streaking, tunnel vision, presets and video export.

Run:
    python blackhole_tk.py

Recommended:
    conda create -n blackhole-simulator python=3.12 pip tk -y
    conda activate blackhole-simulator
    pip install -r requirements.txt
"""

from __future__ import annotations

import math
import os
import random
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageTk

try:
    import imageio.v2 as imageio
    HAVE_IMAGEIO = True
except Exception:
    HAVE_IMAGEIO = False

try:
    import matplotlib.cm as cm
    HAVE_MPL = True
except Exception:
    HAVE_MPL = False

# ----------------------------- Physics constants ---------------------------
G = 6.67430e-11
C = 299_792_458.0
M_SUN = 1.98847e30
AU = 149_597_870_700.0

PRESETS: Dict[str, Dict[str, float | str]] = {
    "Stellar 10 M☉": {
        "mass_solar": 10.0,
        "description": "Small stellar black hole: violent tides near the horizon.",
    },
    "Sagittarius A*": {
        "mass_solar": 4.154e6,
        "description": "Milky Way central black hole: huge horizon, gentler local tides.",
    },
    "M87*": {
        "mass_solar": 6.5e9,
        "description": "Supermassive black hole: enormous horizon and very slow visual scale.",
    },
}

PARTICLE_COLORS = {
    "photon": (255, 245, 190),
    "electron": (100, 185, 255),
    "proton": (255, 110, 90),
    "neutron": (185, 185, 210),
    "dust": (230, 210, 160),
}

# ----------------------------- Helpers -------------------------------------
def clamp(x: float, a: float, b: float) -> float:
    return max(a, min(b, x))


def fmt_si(x: float, unit: str = "m") -> str:
    ax = abs(x)
    if ax >= 1e12:
        return f"{x/1e12:.3g} T{unit}"
    if ax >= 1e9:
        return f"{x/1e9:.3g} G{unit}"
    if ax >= 1e6:
        return f"{x/1e6:.3g} M{unit}"
    if ax >= 1e3:
        return f"{x/1e3:.3g} k{unit}"
    return f"{x:.3g} {unit}"


def schwarzschild_radius(mass_kg: float) -> float:
    return 2.0 * G * mass_kg / (C * C)


def photon_sphere_radius(rs: float) -> float:
    return 1.5 * rs


def isco_radius(rs: float) -> float:
    # Schwarzschild innermost stable circular orbit: 3 Rs = 6GM/c^2
    return 3.0 * rs


def gravitational_redshift_factor(r_over_rs: float) -> float:
    # frequency observed at infinity / local emitted frequency for static emitter
    if r_over_rs <= 1.0:
        return 0.0
    return math.sqrt(max(0.0, 1.0 - 1.0 / r_over_rs))


def escape_velocity_beta(r_over_rs: float) -> float:
    # Newtonian escape speed in units of c happens to be sqrt(Rs/r).
    return clamp(math.sqrt(max(0.0, 1.0 / max(r_over_rs, 1e-6))), 0.0, 0.999)


def tidal_strength(mass_kg: float, r_m: float, length_m: float = 2.0) -> float:
    # Difference in acceleration across length L: approx 2GM L / r^3
    return 2.0 * G * mass_kg * length_m / max(r_m ** 3, 1e-30)


def color_shift(rgb: Tuple[int, int, int], redshift: float, doppler: float) -> Tuple[int, int, int]:
    """Very simple visual color shift. redshift<1 dims and warms; doppler>1 brightens/blues."""
    r, g, b = [v / 255.0 for v in rgb]
    warmth = clamp(1.0 - redshift, 0.0, 1.0)
    blue = clamp(doppler - 1.0, 0.0, 1.5)
    r = r * (0.55 + 0.75 * redshift) + 0.40 * warmth
    g = g * (0.45 + 0.75 * redshift)
    b = b * (0.35 + 0.80 * redshift) + 0.30 * blue
    intensity = clamp(0.35 + 1.15 * redshift * doppler, 0.0, 2.8)
    return tuple(int(clamp(v * intensity, 0.0, 1.0) * 255) for v in (r, g, b))


# ----------------------------- Scene data ----------------------------------
@dataclass
class Star:
    theta: float       # azimuth angle around view center
    rho: float         # normalized angular distance from view center: 0..1
    mag: float         # brightness
    temp: float        # color temperature-ish 0..1
    twinkle: float


@dataclass
class Tracer:
    kind: str
    angle: float
    r: float           # in Rs units
    vr: float
    spin: float
    phase: float


class SkyField:
    def __init__(self, n: int = 1800, seed: int = 42):
        rng = random.Random(seed)
        self.stars: List[Star] = []
        for _ in range(n):
            # Uniform density on disk: rho sqrt(random)
            rho = math.sqrt(rng.random())
            theta = rng.random() * math.tau
            mag = rng.random() ** 3.0
            temp = rng.random()
            twinkle = rng.random() * math.tau
            self.stars.append(Star(theta, rho, mag, temp, twinkle))

    @staticmethod
    def star_color(temp: float, brightness: float) -> Tuple[int, int, int]:
        if temp < 0.25:
            base = (255, 185, 120)
        elif temp < 0.55:
            base = (255, 235, 190)
        elif temp < 0.80:
            base = (215, 230, 255)
        else:
            base = (160, 195, 255)
        return tuple(int(clamp(c * brightness, 0, 255)) for c in base)


# ----------------------------- Main app ------------------------------------
class BlackHoleV2Tk:
    def __init__(self, w: int = 1280, h: int = 820):
        self.root = tk.Tk()
        self.root.title("Falling Into A Black Hole Simulator v2 — Observer POV")
        self.root.minsize(1180, 720)

        self.w, self.h = w, h
        self.sky = SkyField(n=2200)
        self.frame_index = 0
        self.running = False
        self.last_tick = time.time()
        self._imgtk: Optional[ImageTk.PhotoImage] = None
        self._current_img: Optional[Image.Image] = None

        self._build_ui()
        self._make_tracers()
        self.root.bind("<Configure>", self._on_resize)
        self.render()

    # --------------------------- UI ----------------------------------------

    def _fmt_slider_value(self, name: str, value: float) -> str:
        if name == "r":
            if value > 1.0:
                zone = "outside"
            else:
                zone = "inside horizon"
            return f"r/Rs: {value:.2f} ({zone})"
        if name == "fov":
            return f"FOV: {value:.0f}°"
        if name == "lensing":
            level = "none" if value < 0.15 else "subtle" if value < 0.9 else "strong" if value < 1.8 else "extreme"
            return f"Lensing: {value:.2f} ({level})"
        if name == "disk":
            level = "off" if value < 0.05 else "dim" if value < 0.5 else "medium" if value < 1.0 else "bright"
            return f"Disk glow: {value:.2f} ({level})"
        if name == "time":
            level = "very slow" if value < 0.2 else "slow" if value < 0.55 else "normal" if value < 1.15 else "fast"
            return f"Time scale: {value:.2f}x ({level})"
        if name == "roll":
            return f"Camera roll: {value:.0f}°"
        return f"{value:.2f}"

    def _slider_changed(self, name: str, var: tk.DoubleVar, label_var: tk.StringVar, redraw: bool = True) -> None:
        label_var.set(self._fmt_slider_value(name, float(var.get())))
        self._set_status()
        if redraw:
            self.render()

    def _refresh_value_labels(self) -> None:
        if hasattr(self, "r_value_var"):
            self.r_value_var.set(self._fmt_slider_value("r", float(self.r_var.get())))
            self.fov_value_var.set(self._fmt_slider_value("fov", float(self.fov_var.get())))
            self.lensing_value_var.set(self._fmt_slider_value("lensing", float(self.lensing_var.get())))
            self.disk_value_var.set(self._fmt_slider_value("disk", float(self.disk_var.get())))
            self.time_value_var.set(self._fmt_slider_value("time", float(self.time_scale_var.get())))
            self.roll_value_var.set(self._fmt_slider_value("roll", float(self.roll_var.get())))

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=(6, 4, 6, 4))
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(top, text="Preset").pack(side=tk.LEFT, padx=(0, 4))
        self.preset_var = tk.StringVar(value="Sagittarius A*")
        preset_menu = ttk.OptionMenu(top, self.preset_var, self.preset_var.get(), *PRESETS.keys(), command=lambda _: self.apply_preset())
        preset_menu.pack(side=tk.LEFT)

        ttk.Label(top, text="Mass M☉").pack(side=tk.LEFT, padx=(10, 4))
        self.mass_var = tk.DoubleVar(value=float(PRESETS["Sagittarius A*"]["mass_solar"]))
        ttk.Entry(top, textvariable=self.mass_var, width=10).pack(side=tk.LEFT)

        self.r_var = tk.DoubleVar(value=18.0)
        self.r_value_var = tk.StringVar(value=self._fmt_slider_value("r", self.r_var.get()))
        ttk.Label(top, textvariable=self.r_value_var, width=22).pack(side=tk.LEFT, padx=(10, 4))
        ttk.Scale(top, from_=0.72, to=80.0, variable=self.r_var, length=150,
                  command=lambda _: self._slider_changed("r", self.r_var, self.r_value_var)).pack(side=tk.LEFT)

        self.fov_var = tk.DoubleVar(value=95.0)
        self.fov_value_var = tk.StringVar(value=self._fmt_slider_value("fov", self.fov_var.get()))
        ttk.Label(top, textvariable=self.fov_value_var, width=10).pack(side=tk.LEFT, padx=(10, 4))
        ttk.Scale(top, from_=35.0, to=150.0, variable=self.fov_var, length=120,
                  command=lambda _: self._slider_changed("fov", self.fov_var, self.fov_value_var)).pack(side=tk.LEFT)

        self.lensing_var = tk.DoubleVar(value=1.2)
        self.lensing_value_var = tk.StringVar(value=self._fmt_slider_value("lensing", self.lensing_var.get()))
        ttk.Label(top, textvariable=self.lensing_value_var, width=21).pack(side=tk.LEFT, padx=(10, 4))
        ttk.Scale(top, from_=0.0, to=3.0, variable=self.lensing_var, length=120,
                  command=lambda _: self._slider_changed("lensing", self.lensing_var, self.lensing_value_var)).pack(side=tk.LEFT)

        self.disk_var = tk.DoubleVar(value=0.85)
        self.disk_value_var = tk.StringVar(value=self._fmt_slider_value("disk", self.disk_var.get()))
        ttk.Label(top, textvariable=self.disk_value_var, width=22).pack(side=tk.LEFT, padx=(10, 4))
        ttk.Scale(top, from_=0.0, to=1.5, variable=self.disk_var, length=100,
                  command=lambda _: self._slider_changed("disk", self.disk_var, self.disk_value_var)).pack(side=tk.LEFT)

        # Keep action buttons on their own row. This prevents them from being pushed
        # off-screen when the first parameter row becomes wide on Windows/Tk.
        second = ttk.Frame(self.root, padding=(6, 0, 6, 4))
        second.pack(side=tk.TOP, fill=tk.X)

        actions = ttk.Frame(second)
        actions.pack(side=tk.LEFT, padx=(0, 12))
        ttk.Button(actions, text="Reset Fall", command=self.reset_fall, width=12).pack(side=tk.LEFT, padx=2)
        ttk.Button(actions, text="Play/Pause", command=self.toggle, width=12).pack(side=tk.LEFT, padx=2)
        ttk.Button(actions, text="Step", command=self.step_once, width=8).pack(side=tk.LEFT, padx=2)
        ttk.Button(actions, text="Save PNG", command=self.save_png, width=10).pack(side=tk.LEFT, padx=2)
        ttk.Button(actions, text="Export MP4/GIF", command=self.export_video, width=16).pack(side=tk.LEFT, padx=2)

        ttk.Separator(second, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        self.doppler_var = tk.BooleanVar(value=True)
        self.tides_var = tk.BooleanVar(value=True)
        self.hud_var = tk.BooleanVar(value=True)
        self.disk_check_var = tk.BooleanVar(value=True)
        self.tracer_var = tk.BooleanVar(value=True)
        self.body_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(second, text="Doppler/redshift", variable=self.doppler_var, command=self.render).pack(side=tk.LEFT)
        ttk.Checkbutton(second, text="Tidal stretch", variable=self.tides_var, command=self.render).pack(side=tk.LEFT, padx=8)
        ttk.Checkbutton(second, text="Accretion disk", variable=self.disk_check_var, command=self.render).pack(side=tk.LEFT)
        ttk.Checkbutton(second, text="Particle tracers", variable=self.tracer_var, command=self.render).pack(side=tk.LEFT, padx=8)
        ttk.Checkbutton(second, text="Observer body", variable=self.body_var, command=self.render).pack(side=tk.LEFT)
        ttk.Checkbutton(second, text="HUD", variable=self.hud_var, command=self.render).pack(side=tk.LEFT, padx=8)

        self.time_scale_var = tk.DoubleVar(value=0.55)
        self.time_value_var = tk.StringVar(value=self._fmt_slider_value("time", self.time_scale_var.get()))
        ttk.Label(second, textvariable=self.time_value_var, width=24).pack(side=tk.LEFT, padx=(18, 4))
        ttk.Scale(second, from_=0.05, to=2.0, variable=self.time_scale_var, length=130,
                  command=lambda _: self._slider_changed("time", self.time_scale_var, self.time_value_var, redraw=False)).pack(side=tk.LEFT)

        self.roll_var = tk.DoubleVar(value=0.0)
        self.roll_value_var = tk.StringVar(value=self._fmt_slider_value("roll", self.roll_var.get()))
        ttk.Label(second, textvariable=self.roll_value_var, width=17).pack(side=tk.LEFT, padx=(18, 4))
        ttk.Scale(second, from_=-180.0, to=180.0, variable=self.roll_var, length=130,
                  command=lambda _: self._slider_changed("roll", self.roll_var, self.roll_value_var)).pack(side=tk.LEFT)

        self.canvas = tk.Canvas(self.root, width=self.w, height=self.h, bg="#000008", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.status = ttk.Label(self.root, anchor="w", font=("Consolas", 10))
        self.status.pack(fill=tk.X)

    def _on_resize(self, event) -> None:
        if event.widget is self.root:
            self.w = max(320, self.canvas.winfo_width())
            self.h = max(260, self.canvas.winfo_height())

    def apply_preset(self) -> None:
        self.mass_var.set(float(PRESETS[self.preset_var.get()]["mass_solar"]))
        self.reset_fall()

    def reset_fall(self) -> None:
        self.r_var.set(40.0)
        self.roll_var.set(0.0)
        self.frame_index = 0
        self._refresh_value_labels()
        self._make_tracers()
        self.render()

    def toggle(self) -> None:
        self.running = not self.running
        self.last_tick = time.time()
        if self.running:
            self._tick()

    def step_once(self) -> None:
        self._advance(1.0 / 30.0)
        self.render()

    # --------------------------- Particle tracers --------------------------
    def _make_tracers(self) -> None:
        rng = random.Random(7)
        kinds = ["photon", "electron", "proton", "neutron", "dust"]
        self.tracers: List[Tracer] = []
        for i in range(90):
            kind = kinds[i % len(kinds)]
            self.tracers.append(
                Tracer(
                    kind=kind,
                    angle=rng.random() * math.tau,
                    r=rng.uniform(2.0, 28.0),
                    vr=rng.uniform(0.04, 0.22) * (1.8 if kind == "photon" else 1.0),
                    spin=rng.uniform(-1.0, 1.0),
                    phase=rng.random() * math.tau,
                )
            )

    def _advance(self, dt: float) -> None:
        r = float(self.r_var.get())
        # Accelerating fall in r/Rs units. It is deliberately visual, not exact proper time integration.
        beta = escape_velocity_beta(r)
        dr = (0.22 + 1.8 * beta * beta) * dt * 30.0 * float(self.time_scale_var.get())
        self.r_var.set(max(0.72, r - dr))
        self.roll_var.set(float(self.roll_var.get()) + 8.0 * dt * float(self.time_scale_var.get()))
        self._refresh_value_labels()
        self.frame_index += 1
        for tr in self.tracers:
            tr.r -= tr.vr * dt * 30.0 * float(self.time_scale_var.get())
            tr.angle += (0.015 / max(tr.r, 0.4) + 0.006 * tr.spin) * dt * 30.0
            if tr.r < 0.85:
                tr.r = random.uniform(8.0, 32.0)
                tr.angle = random.random() * math.tau

    def _tick(self) -> None:
        if not self.running:
            return
        now = time.time()
        dt = clamp(now - self.last_tick, 0.001, 0.08)
        self.last_tick = now
        self._advance(dt)
        self.render()
        self.root.after(16, self._tick)

    # --------------------------- Rendering ---------------------------------
    def render(self) -> None:
        img = self._render_frame(self.w, self.h)
        self._current_img = img
        self._imgtk = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self._imgtk, anchor="nw")
        self._set_status()

    def _render_frame(self, w: int, h: int, supersample: int = 1) -> Image.Image:
        W, H = w * supersample, h * supersample
        img = Image.new("RGB", (W, H), (0, 0, 8))
        draw = ImageDraw.Draw(img, "RGBA")
        cx, cy = W / 2.0, H / 2.0
        min_dim = min(W, H)

        mass_kg = float(self.mass_var.get()) * M_SUN
        rs_m = schwarzschild_radius(mass_kg)
        r_rs = float(self.r_var.get())
        beta = escape_velocity_beta(max(r_rs, 1.0001))
        red = gravitational_redshift_factor(max(r_rs, 1.0001))
        doppler_front = math.sqrt((1 + beta) / max(1 - beta, 1e-6)) if self.doppler_var.get() else 1.0
        lensing = float(self.lensing_var.get())
        fov = math.radians(float(self.fov_var.get()))
        roll = math.radians(float(self.roll_var.get()))

        # Background radial gradient: the sky dims and reddens as the observer approaches the horizon.
        bg = self._background_gradient(W, H, red)
        img.paste(bg)
        draw = ImageDraw.Draw(img, "RGBA")

        # Apparent black disk grows as r approaches Rs. This is an artistic apparent angular radius.
        shadow_r = min_dim * clamp(0.10 + 0.45 / max(r_rs - 0.15, 0.25), 0.10, 1.15)
        photon_r = shadow_r * 1.50
        horizon_r = shadow_r * 0.68

        if self.disk_check_var.get() and self.disk_var.get() > 0.01:
            self._draw_accretion_disk(draw, W, H, shadow_r, photon_r, r_rs, red, beta, roll)

        self._draw_lensed_stars(draw, W, H, shadow_r, photon_r, r_rs, red, doppler_front, lensing, fov, roll)

        if self.tracer_var.get():
            self._draw_tracers(draw, W, H, shadow_r, r_rs, red, roll)

        # Photon sphere ring and event horizon. Inside the horizon, make the outside universe squeeze overhead.
        ring_alpha = int(120 * clamp(2.0 / max(r_rs, 0.5), 0.25, 1.0))
        draw.ellipse((cx - photon_r, cy - photon_r, cx + photon_r, cy + photon_r), outline=(255, 185, 80, ring_alpha), width=max(1, int(2 * supersample)))
        draw.ellipse((cx - shadow_r, cy - shadow_r, cx + shadow_r, cy + shadow_r), fill=(0, 0, 0, 235))
        draw.ellipse((cx - horizon_r, cy - horizon_r, cx + horizon_r, cy + horizon_r), outline=(90, 120, 255, 90), width=max(1, int(2 * supersample)))

        if r_rs < 1.0:
            self._draw_inside_horizon_overlay(draw, W, H, r_rs)

        # Gravitational tunnel vision: a subtle vignette that strengthens near the horizon.
        # Kept before body/HUD so the first-person overlay and text remain readable.
        self._draw_tunnel_vision(draw, W, H, r_rs)

        if self.body_var.get():
            self._draw_observer_body(draw, W, H, r_rs, mass_kg, rs_m)
        elif self.tides_var.get():
            self._draw_tidal_object(draw, W, H, r_rs, mass_kg, rs_m)

        if self.hud_var.get():
            self._draw_hud(draw, W, H, mass_kg, rs_m, r_rs, beta, red)

        if supersample > 1:
            img = img.resize((w, h), Image.Resampling.LANCZOS)
        return img

    def _background_gradient(self, W: int, H: int, red: float) -> Image.Image:
        y = np.linspace(-1, 1, H)[:, None]
        x = np.linspace(-1, 1, W)[None, :]
        rr = np.sqrt(x * x + y * y)
        glow = np.clip(1.0 - rr, 0, 1) ** 2
        base = np.zeros((H, W, 3), dtype=np.uint8)
        base[..., 0] = (5 + 18 * glow + 25 * (1 - red)).astype(np.uint8)
        base[..., 1] = (6 + 10 * glow).astype(np.uint8)
        base[..., 2] = (18 + 40 * glow * red).astype(np.uint8)
        return Image.fromarray(base, "RGB")

    def _draw_lensed_stars(self, draw: ImageDraw.ImageDraw, W: int, H: int, shadow_r: float, photon_r: float,
                           r_rs: float, red: float, doppler_front: float, lensing: float, fov: float, roll: float) -> None:
        cx, cy = W / 2.0, H / 2.0
        max_rad = math.hypot(W, H) * 0.72
        compression = clamp((r_rs - 0.75) / 8.0, 0.15, 1.0)

        # Star streaking is intentionally subtle far away and visible near the photon-sphere/horizon zone.
        # This makes the fall feel faster without turning the render into an arcade warp-speed effect.
        fall_beta = escape_velocity_beta(max(r_rs, 1.0001))
        streak_power = clamp((12.0 - r_rs) / 11.0, 0.0, 1.0) ** 1.25
        streak_power *= clamp(0.45 + 0.75 * lensing, 0.45, 1.35)

        for s in self.sky.stars:
            theta = s.theta + roll * 0.42
            rho = s.rho
            # Lensing: stars are pulled into an Einstein-ring-like band around the shadow.
            screen_r = rho * max_rad * math.tan(fov / 2.0) / math.tan(math.radians(95) / 2.0)
            lens_pull = lensing * (photon_r ** 2) / max(screen_r + photon_r * 0.35, 1.0)
            if screen_r > shadow_r * 0.55:
                screen_r = screen_r + lens_pull
            if r_rs < 1.0:
                # Inside horizon, the external universe appears compressed into a shrinking window.
                screen_r *= compression
                theta += 0.8 * (1.0 - r_rs)
            x = cx + screen_r * math.cos(theta)
            y = cy + screen_r * math.sin(theta)
            if x < -16 or y < -16 or x >= W + 16 or y >= H + 16:
                continue
            # Occulted by shadow unless very close to photon ring.
            dist = math.hypot(x - cx, y - cy)
            if dist < shadow_r * 0.92:
                continue
            ring_boost = 1.0 + 1.8 * math.exp(-((dist - photon_r) / max(photon_r * 0.16, 1.0)) ** 2)
            direction_doppler = 1.0
            if self.doppler_var.get():
                # Forward direction is upward in the image: blue/bright above, red/dim below.
                forward = (cy - y) / max(math.hypot(x - cx, y - cy), 1.0)
                direction_doppler = math.sqrt((1 + 0.55 * fall_beta * forward) / max(1 - 0.55 * fall_beta * forward, 1e-6))
            brightness = (0.25 + 1.6 * s.mag) * ring_boost * clamp(red * direction_doppler, 0.05, 3.0)
            rgb = SkyField.star_color(s.temp, clamp(brightness, 0.03, 1.8))
            rgb = color_shift(rgb, red, direction_doppler if self.doppler_var.get() else 1.0)
            size = 1 + int(2.5 * s.mag * ring_boost)
            alpha = int(clamp(100 + 140 * brightness, 30, 255))

            # Draw a radial smear away/toward the black hole. Strongest near the photon ring.
            local_streak = streak_power * (0.25 + 0.75 * clamp((dist - shadow_r) / max(photon_r * 1.9, 1.0), 0.0, 1.0))
            local_streak *= (0.55 + 0.70 * ring_boost)
            if local_streak > 0.05:
                ux = (x - cx) / max(dist, 1.0)
                uy = (y - cy) / max(dist, 1.0)
                length = clamp((2.0 + 18.0 * local_streak) * (0.35 + s.mag), 1.0, 28.0)
                x0 = x - ux * length * 0.55
                y0 = y - uy * length * 0.55
                x1 = x + ux * length * 0.45
                y1 = y + uy * length * 0.45
                draw.line((x0, y0, x1, y1), fill=(*rgb, int(alpha * 0.42)), width=max(1, int(size)))

            draw.ellipse((x - size, y - size, x + size, y + size), fill=(*rgb, alpha))

    def _draw_accretion_disk(self, draw: ImageDraw.ImageDraw, W: int, H: int, shadow_r: float, photon_r: float,
                             r_rs: float, red: float, beta: float, roll: float) -> None:
        cx, cy = W / 2.0, H / 2.0
        intensity = float(self.disk_var.get())
        # Tilted disk ellipse; many translucent strokes gives a hot plasma look.
        # Visible Doppler/redshift. With Doppler ON the approaching side becomes
        # blue-white/brighter while the receding side becomes redder/dimmer. With it OFF,
        # both sides use nearly symmetric warm plasma colors.
        inner = max(photon_r * 0.96, shadow_r * 1.15)
        outer = shadow_r * 3.2
        tilt = 0.34
        steps = 74
        doppler_strength = clamp(0.18 + 0.95 * beta, 0.0, 1.15) if self.doppler_var.get() else 0.0
        for i in range(steps):
            t = i / (steps - 1)
            rad = inner * (1 - t) + outer * t
            alpha = int(132 * intensity * (1 - t) ** 1.35)
            if alpha <= 1:
                continue

            bbox = (cx - rad, cy - rad * tilt, cx + rad, cy + rad * tilt)
            start = math.degrees(roll) + 5
            width = max(1, int((2.2 + 1.7 * (1 - t)) * intensity))

            if self.doppler_var.get():
                # Approaching side: hot blue-white/yellow, visibly brighter.
                approach_boost = 1.15 + 1.35 * doppler_strength
                app_base = (
                    int(clamp(235 + 20 * (1 - t), 0, 255)),
                    int(clamp(225 + 30 * (1 - t), 0, 255)),
                    int(clamp(135 + 115 * doppler_strength + 35 * (1 - t), 0, 255)),
                )
                app_rgb = color_shift(app_base, clamp(red * 1.10, 0.0, 1.0), approach_boost)
                app_alpha = int(clamp(alpha * (1.12 + 0.72 * doppler_strength), 0, 255))

                # Receding side: red/orange and darker.
                rec_base = (
                    int(clamp(215 + 35 * (1 - t), 0, 255)),
                    int(clamp(70 + 55 * (1 - t), 0, 255)),
                    int(clamp(30 + 15 * (1 - t), 0, 255)),
                )
                rec_rgb = color_shift(rec_base, clamp(red * 0.58, 0.0, 1.0), 0.62)
                rec_alpha = int(clamp(alpha * (0.38 + 0.18 * (1 - doppler_strength)), 8, 160))
            else:
                hot = int(255 * (1 - t * 0.55))
                app_rgb = color_shift((hot, int(150 + 80 * (1 - t)), int(50 + 40 * t)), red, 1.0)
                rec_rgb = color_shift((hot, int(145 + 75 * (1 - t)), int(48 + 42 * t)), red, 1.0)
                app_alpha = alpha
                rec_alpha = max(10, int(alpha * 0.82))

            # Draw half arcs separately to make the asymmetry obvious.
            draw.arc(bbox, start=start, end=start + 180, fill=(*app_rgb, app_alpha), width=width)
            draw.arc(bbox, start=start + 180, end=start + 360, fill=(*rec_rgb, rec_alpha), width=max(1, width - 1))

        # A faint relativistic beaming wedge gives immediate ON/OFF feedback without overwhelming the image.
        if self.doppler_var.get() and intensity > 0.05:
            beam_alpha = int(clamp(35 + 85 * doppler_strength * intensity, 0, 130))
            beam_w = outer * (1.05 + 0.25 * doppler_strength)
            beam_h = outer * tilt * (0.78 + 0.10 * doppler_strength)
            # Left/approaching side highlight; right/receding side warm shadow.
            draw.pieslice((cx - beam_w, cy - beam_h, cx + beam_w, cy + beam_h),
                          start=160 + math.degrees(roll), end=205 + math.degrees(roll),
                          fill=(230, 245, 255, beam_alpha))
            draw.pieslice((cx - beam_w, cy - beam_h, cx + beam_w, cy + beam_h),
                          start=335 + math.degrees(roll), end=20 + math.degrees(roll),
                          fill=(255, 70, 45, int(beam_alpha * 0.45)))

    def _draw_tracers(self, draw: ImageDraw.ImageDraw, W: int, H: int, shadow_r: float, r_rs: float, red: float, roll: float) -> None:
        cx, cy = W / 2.0, H / 2.0
        for tr in self.tracers:
            local_r = shadow_r * (tr.r / max(r_rs, 0.8))
            if local_r < shadow_r * 0.75:
                continue
            theta = tr.angle + roll
            x = cx + local_r * math.cos(theta)
            y = cy + local_r * math.sin(theta) * 0.72
            color = color_shift(PARTICLE_COLORS[tr.kind], red, 1.15 if tr.kind == "photon" else 1.0)
            size = 2 if tr.kind != "photon" else 1
            trail = 12 if tr.kind == "photon" else 7
            x2 = cx + (local_r + trail) * math.cos(theta - 0.12)
            y2 = cy + (local_r + trail) * math.sin(theta - 0.12) * 0.72
            draw.line((x2, y2, x, y), fill=(*color, 80), width=1)
            draw.ellipse((x - size, y - size, x + size, y + size), fill=(*color, 190))


    def _draw_observer_body(self, draw: ImageDraw.ImageDraw, W: int, H: int, r_rs: float, mass_kg: float, rs_m: float) -> None:
        """First-person body overlay: legs/boots and hands visibly stretch near the horizon.

        Important design choice:
        - Real tidal force is very small near the horizon of supermassive black holes.
        - For an immersive visual simulator, we combine the physical tidal estimate with an
          artistic proximity factor so the user can *see* spaghettification during the fall.
        - The rest of the simulator is unchanged; only the body/hands overlay uses this
          exaggerated visual stretch.
        """
        cx, cy = W / 2.0, H / 2.0
        r_m = max(r_rs * rs_m, 1e-6)

        # Physical component: huge for stellar black holes, tiny for Sagittarius A*/M87*.
        tide = tidal_strength(mass_kg, r_m, 2.0) if self.tides_var.get() else 1e-9
        physical = clamp((math.log10(max(tide, 1e-9)) + 2.0) / 7.0, 0.0, 1.0)

        # Visual component: starts gently inside ~8 Rs and becomes dramatic below ~3 Rs.
        # This is what makes the first-person body visibly elongate during the fall.
        proximity = clamp((8.0 - r_rs) / 7.0, 0.0, 1.0)
        photon_zone = clamp((3.2 - r_rs) / 2.2, 0.0, 1.0)
        horizon_zone = clamp((1.45 - r_rs) / 0.45, 0.0, 1.0)
        visual = clamp(0.55 * proximity**1.35 + 0.30 * photon_zone**1.10 + 0.35 * horizon_zone, 0.0, 1.0)

        # If tidal stretch is disabled, keep only a very mild POV body effect.
        danger = max(physical, visual) if self.tides_var.get() else visual * 0.25
        danger = clamp(danger, 0.0, 1.0)
        stretch_x = 1.0 + 2.4 * danger

        # Subtle helmet visor / suit vignette, increasingly distorted near the horizon.
        visor_alpha = int(35 + 75 * danger)
        visor_wobble = H * 0.025 * danger
        draw.ellipse(
            (W*0.05 - visor_wobble, H*0.04, W*0.95 + visor_wobble, H*1.18 + visor_wobble),
            outline=(130, 170, 230, visor_alpha),
            width=max(1, int(3 + 5*danger)),
        )
        if danger > 0.55:
            draw.arc((W*0.04, H*0.03, W*0.96, H*1.20), start=200, end=340,
                     fill=(255, 145, 120, int(80 + 90*danger)), width=max(1, int(2 + 3*danger)))

        # Legs/boots near the bottom. As danger rises, legs become longer and thinner.
        leg_len = H * (0.16 + 0.31 * danger)
        leg_w = W * max(0.010, 0.037 - 0.026 * danger)
        base_y = H * (0.985 + 0.025 * danger)
        top_y = base_y - leg_len
        gap = W * (0.046 - 0.010 * danger)
        suit = (115, 150, 205, int(120 + 60 * danger))
        suit_edge = (220, 238, 255, int(145 + 65 * danger))
        warning = (255, 120, 105, int(55 + 155 * danger))

        for side in (-1, 1):
            x = cx + side * gap
            # Tapered leg polygon: narrower at the black-hole-facing end.
            top_w = leg_w * max(0.38, 0.78 - 0.34 * danger)
            bot_w = leg_w * (1.00 + 0.20 * danger)
            poly = [(x - top_w, top_y), (x + top_w, top_y), (x + bot_w, base_y), (x - bot_w, base_y)]
            draw.polygon(poly, fill=suit, outline=suit_edge)

            # Long center highlight makes the stretch readable even on dark backgrounds.
            draw.line((x, top_y - H*0.03*danger, x, base_y + H*0.035*danger),
                      fill=(230, 245, 255, int(60 + 95*danger)), width=max(1, int(1 + 2*danger)))

            boot_w = bot_w * (1.55 + 0.28 * danger)
            boot_h = 18 + int(26 * danger)
            draw.rounded_rectangle((x - boot_w, base_y - boot_h, x + boot_w, base_y + 10 + 10*danger),
                                   radius=6, fill=(45, 55, 75, int(175 + 45*danger)),
                                   outline=suit_edge, width=1)

            if danger > 0.28:
                bands = 3 + int(5 * danger)
                for k in range(bands):
                    yy = top_y + (base_y - top_y) * (k + 1) / (bands + 1)
                    band_w = leg_w * (0.35 + 0.55 * (k / max(1, bands)))
                    draw.line((x - band_w, yy, x + band_w, yy), fill=warning, width=1)

        # Hands/arms: extend downward and outward, fingers become streaks near the horizon.
        arm_y = H * (0.71 - 0.05 * danger)
        arm_len = W * (0.09 + 0.13 * danger)
        arm_drop = H * (0.03 + 0.22 * danger)
        hand_r = max(6, int(min(W, H) * (0.011 + 0.005 * danger)))
        for side in (-1, 1):
            shoulder_x = cx + side * W * (0.27 + 0.02 * danger)
            elbow_x = shoulder_x + side * arm_len * 0.50
            elbow_y = arm_y + arm_drop * 0.30
            hand_x = shoulder_x + side * arm_len
            hand_y = arm_y + arm_drop

            arm_width = max(2, int(6 - 3*danger))
            draw.line((shoulder_x, arm_y, elbow_x, elbow_y, hand_x, hand_y),
                      fill=(115, 150, 205, int(105 + 55*danger)), width=arm_width)

            # Stretched palm/hand instead of a static circle.
            palm_len = hand_r * (1.25 + 2.6 * danger)
            palm_w = hand_r * max(0.45, 1.00 - 0.38 * danger)
            draw.ellipse((hand_x - palm_w, hand_y - palm_len, hand_x + palm_w, hand_y + palm_len),
                         fill=(180, 205, 235, int(130 + 70*danger)), outline=suit_edge, width=1)

            if danger > 0.35:
                for f in range(4):
                    offset = (f - 1.5) * palm_w * 0.55
                    finger_len = palm_len * (0.75 + 0.45 * danger)
                    draw.line((hand_x + offset, hand_y - palm_len*0.20,
                               hand_x + offset * 0.45, hand_y + finger_len),
                              fill=(225, 238, 255, int(75 + 110*danger)), width=1)

        if danger > 0.18:
            msg = f"Body stretch: {stretch_x:.2f}x   tidal visual: {danger*100:.0f}%"
            box = (W*0.34, H*0.895, W*0.66, H*0.945)
            draw.rounded_rectangle(box, radius=8, fill=(0, 0, 0, 120), outline=(255, 140, 120, int(80 + 90*danger)))
            draw.text((W*0.36, H*0.910), msg, fill=(255, 205, 185, 225))

    def _draw_tidal_object(self, draw: ImageDraw.ImageDraw, W: int, H: int, r_rs: float, mass_kg: float, rs_m: float) -> None:
        cx, cy = W / 2.0, H / 2.0
        # Represent the falling object/body as it is stretched radially.
        r_m = max(r_rs * rs_m, 1e-6)
        tide = tidal_strength(mass_kg, r_m, 2.0)
        stretch = clamp(math.log10(max(tide, 1e-9)) + 2.0, 0.0, 7.0)
        length = 28 + stretch * 28
        width = max(4, 18 - stretch * 2.0)
        y0 = H * 0.78
        x0 = cx
        color = (145, 190, 255, 140)
        draw.ellipse((x0 - width, y0 - length, x0 + width, y0 + length), fill=color, outline=(220, 240, 255, 170), width=1)
        draw.line((x0, y0 - length * 1.25, x0, y0 + length * 1.25), fill=(255, 255, 255, 80), width=1)
        if stretch > 4:
            for k in range(6):
                yy = y0 - length + 2 * length * k / 5
                draw.line((x0 - width * 0.7, yy, x0 + width * 0.7, yy), fill=(255, 120, 120, 90), width=1)

    def _draw_inside_horizon_overlay(self, draw: ImageDraw.ImageDraw, W: int, H: int, r_rs: float) -> None:
        cx, cy = W / 2.0, H / 2.0
        alpha = int(clamp((1.0 - r_rs) * 210, 20, 190))
        draw.rectangle((0, 0, W, H), fill=(0, 0, 0, alpha))
        radius = min(W, H) * clamp(0.16 + 0.55 * r_rs, 0.08, 0.70)
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=(130, 170, 255, 150), width=2)
        draw.text((cx - radius * 0.72, cy - radius - 28), "outside universe compressed into a shrinking sky-window", fill=(180, 205, 255, 170))

    def _draw_tunnel_vision(self, draw: ImageDraw.ImageDraw, W: int, H: int, r_rs: float) -> None:
        """Darken the periphery as the observer approaches the horizon.

        This is a cinematic approximation of gravitational/tunnel vision. It is deliberately
        drawn as transparent rings so the render stays fast and Tkinter-friendly.
        """
        cx, cy = W / 2.0, H / 2.0
        proximity = clamp((10.0 - r_rs) / 9.0, 0.0, 1.0)
        horizon = clamp((2.2 - r_rs) / 1.4, 0.0, 1.0)
        strength = clamp(0.42 * proximity + 0.58 * horizon, 0.0, 1.0)
        if strength <= 0.015:
            return

        max_radius = math.hypot(W, H) * 0.70
        clear_radius = min(W, H) * (0.62 - 0.22 * strength)
        rings = 18
        for i in range(rings):
            t = i / max(1, rings - 1)
            r0 = clear_radius + (max_radius - clear_radius) * t
            alpha = int(clamp((t ** 1.7) * (65 + 120 * strength), 0, 205))
            # Very slight red/brown tint near the horizon; mostly neutral darkening.
            draw.ellipse((cx - r0, cy - r0, cx + r0, cy + r0),
                         outline=(9 + int(22 * strength), 5, 14, alpha),
                         width=max(2, int(6 + 18 * strength)))

        if horizon > 0.15:
            # A subtle inner shadow around the black disk increases the “no return” feeling.
            pulse = int(35 + 55 * horizon)
            inner = min(W, H) * (0.22 + 0.06 * horizon)
            draw.ellipse((cx - inner, cy - inner, cx + inner, cy + inner),
                         outline=(0, 0, 0, pulse), width=max(2, int(8 + 12 * horizon)))

    def _draw_hud(self, draw: ImageDraw.ImageDraw, W: int, H: int, mass_kg: float, rs_m: float, r_rs: float, beta: float, red: float) -> None:
        r_m = r_rs * rs_m
        tide = tidal_strength(mass_kg, max(r_m, 1.0), 2.0)
        lines = [
            "Observer POV: radial fall toward a Schwarzschild black hole",
            f"Mass: {self.mass_var.get():.4g} M☉    Rs: {fmt_si(rs_m)}    photon sphere: {fmt_si(photon_sphere_radius(rs_m))}",
            f"Current radius: {r_rs:.3f} Rs = {fmt_si(r_m)}    beta≈{beta:.3f}c    gravitational redshift factor≈{red:.3f}",
            f"Tidal Δa over 2 m: {tide:.3e} m/s²    FOV: {self.fov_var.get():.0f}°    lensing: {self.lensing_var.get():.2f}    disk glow: {self.disk_var.get():.2f}",
            f"Time scale: {self.time_scale_var.get():.2f}x    camera roll: {self.roll_var.get():.0f}°    observer body: {'ON' if self.body_var.get() else 'OFF'}",
            "Markers: blue=event horizon, amber=photon sphere, hot ellipse=accretion disk; Visible Doppler, star streaks, tunnel vision",
        ]
        pad = 10
        box_h = 20 * len(lines) + 12
        draw.rounded_rectangle((10, 10, W - 10, 10 + box_h), radius=10, fill=(0, 0, 0, 135), outline=(120, 160, 255, 80))
        y = 18
        for line in lines:
            draw.text((20, y), line, fill=(220, 232, 255, 225))
            y += 20
        if r_rs <= 1.0:
            draw.text((20, y + 6), "INSIDE EVENT HORIZON: no outward worldline escapes; visualization becomes interpretive.", fill=(255, 150, 120, 245))

    def _set_status(self) -> None:
        desc = PRESETS.get(self.preset_var.get(), {}).get("description", "")
        self.status.config(
            text=(
                f"r/Rs={self.r_var.get():.3f} | Mass={self.mass_var.get():.4g} M☉ | "
                f"Time={self.time_scale_var.get():.2f}x | FOV={self.fov_var.get():.0f}° | "
                f"Lensing={self.lensing_var.get():.2f} | Disk={self.disk_var.get():.2f} | {desc}"
            )
        )

    # --------------------------- Saving/export -----------------------------
    def save_png(self) -> None:
        if self._current_img is None:
            return
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = filedialog.asksaveasfilename(
            title="Save PNG",
            defaultextension=".png",
            filetypes=[("PNG image", "*.png")],
            initialfile=f"blackhole_pov_{ts}.png",
        )
        if not path:
            return
        img = self._render_frame(self.w, self.h, supersample=2)
        img.save(path, optimize=True)
        messagebox.showinfo("Saved", f"Saved: {os.path.abspath(path)}")

    def export_video(self) -> None:
        if not HAVE_IMAGEIO:
            messagebox.showerror("Missing dependency", "Install imageio and imageio-ffmpeg first: pip install imageio imageio-ffmpeg")
            return
        path = filedialog.asksaveasfilename(
            title="Export MP4 or GIF",
            defaultextension=".mp4",
            filetypes=[("MP4 video", "*.mp4"), ("GIF animation", "*.gif")],
            initialfile="blackhole_fall_pov.mp4",
        )
        if not path:
            return
        old_r = float(self.r_var.get())
        old_roll = float(self.roll_var.get())
        was_running = self.running
        self.running = False
        try:
            frames = 180
            fps = 30
            width, height = 960, 540
            start_r, end_r = 45.0, 0.78
            if path.lower().endswith(".gif"):
                writer = imageio.get_writer(path, mode="I", duration=1 / fps)
            else:
                writer = imageio.get_writer(path, fps=fps, codec="libx264", quality=8)
            with writer:
                for i in range(frames):
                    t = i / (frames - 1)
                    # Smooth ease-in fall; slow far away, fast near the horizon.
                    eased = t ** 1.85
                    self.r_var.set(start_r * (1 - eased) + end_r * eased)
                    self.roll_var.set(old_roll + 95.0 * t)
                    img = self._render_frame(width, height, supersample=1)
                    writer.append_data(np.asarray(img))
                    self.status.config(text=f"Exporting video... frame {i+1}/{frames}")
                    self.root.update_idletasks()
            messagebox.showinfo("Export complete", f"Saved: {os.path.abspath(path)}")
        except Exception as e:
            messagebox.showerror("Export failed", str(e))
        finally:
            self.r_var.set(old_r)
            self.roll_var.set(old_roll)
            self._refresh_value_labels()
            self.running = was_running
            self.render()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    BlackHoleV2Tk().run()
