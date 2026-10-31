#!/usr/bin/env python3
"""Desktop interface for realme Buds.

    python gui.py

Every device operation is queued on the controller's single worker thread, so
nothing here touches the serial link directly and overlapping reads cannot
steal each other's replies.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from buds import FEATURES, PRESETS, EqProfile
from buds.controller import Controller, Result
from buds.curve import F_MAX, F_MIN, curve_points, x_for
from buds.presets import DEFAULT_EQ_SLOT
from buds.protocol import EQ_GAIN_MAX, EQ_GAIN_MIN

BASS_MIN, BASS_MAX = -5, 5
BASS_LABELS = ("Low", "Mid", "High")
# 11 and 54 are here deliberately: on many models the firmware never reports
# them, and showing them disabled says so plainly rather than hiding it.
TOGGLES = [24, 27, 6, 26, 9, 29, 17, 4, 5, 8, 11, 54]
POLL_MS = 60

BG = "#1c1c1e"
CARD = "#2a2a2d"
LINE = "#3a3a3e"
ACCENT = "#4a9eda"
MUTED = "#8b8b90"
GOOD = "#3fb950"
BAD = "#e05c5c"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class CurveView(ctk.CTkFrame):
    """Draws the combined response of the six bands."""

    def __init__(self, master, get_bands):
        super().__init__(master, fg_color=CARD, corner_radius=10)
        self._get_bands = get_bands
        self.canvas = tk.Canvas(self, bg=CARD, highlightthickness=0, height=190)
        self.canvas.pack(fill="both", expand=True, padx=2, pady=2)
        self.canvas.bind("<Configure>", lambda _e: self.redraw())

    def redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        w = c.winfo_width() or 600
        h = c.winfo_height() or 190
        pad = 10

        # horizontal guides, 0 dB emphasised
        for frac, label in ((0.0, "+8"), (0.25, "+4"), (0.5, "0"),
                            (0.75, "-4"), (1.0, "-8")):
            y = 6 + frac * (h - 12)
            c.create_line(pad, y, w - pad, y,
                          fill=LINE if frac != 0.5 else "#4a4a50",
                          dash=() if frac == 0.5 else (2, 4))
            c.create_text(w - pad - 2, y - 7, text=label, anchor="ne",
                          fill=MUTED, font=("Segoe UI", 9))

        # decade markers
        for freq, label in ((100, "100"), (1000, "1k"), (10000, "10k")):
            x = x_for(freq, w, pad)
            c.create_line(x, 6, x, h - 6, fill=LINE, dash=(2, 5))
            c.create_text(x, h - 4, text=label, anchor="s",
                          fill=MUTED, font=("Segoe UI", 9))

        bands = self._get_bands()
        pts = curve_points(bands, w, h - 12, gain_limit=8.0, pad=pad)
        pts = [(x, y + 6) for x, y in pts]

        flat = [coord for point in pts for coord in point]
        if len(flat) >= 4:
            fill = flat + [pts[-1][0], h / 2, pts[0][0], h / 2]
            c.create_polygon(fill, fill="#4a9eda", outline="", stipple="gray25")
            c.create_line(flat, fill=ACCENT, width=2, smooth=True)

        for centre, gain in bands:
            if centre <= 0:
                continue
            x = x_for(centre, w, pad)
            y = (h - 12) / 2 - (gain / 8.0) * ((h - 12) / 2) + 6
            y = max(7, min(h - 7, y))
            c.create_oval(x - 4, y - 4, x + 4, y + 4,
                          fill=ACCENT, outline=CARD, width=2)


class BandRow(ctk.CTkFrame):
    """One band: an editable centre frequency and a gain slider."""

    def __init__(self, master, index: int, on_change):
        super().__init__(master, fg_color="transparent")
        self._on_change = on_change
        self.index = index

        self.freq_var = ctk.StringVar(value="1000")
        self.gain = 0

        self.entry = ctk.CTkEntry(self, textvariable=self.freq_var, width=72,
                                  justify="right", border_color=LINE)
        self.entry.grid(row=0, column=0, padx=(0, 4))
        self.entry.bind("<KeyRelease>", lambda _e: self._changed())
        ctk.CTkLabel(self, text="Hz", width=20, text_color=MUTED,
                     font=("Segoe UI", 11)).grid(row=0, column=1, padx=(0, 10))

        self.slider = ctk.CTkSlider(
            self, from_=EQ_GAIN_MIN, to=EQ_GAIN_MAX,
            number_of_steps=EQ_GAIN_MAX - EQ_GAIN_MIN,
            command=self._slider_moved, progress_color=ACCENT,
            button_color=ACCENT, button_hover_color="#6ab0ea")
        self.slider.set(0)
        self.slider.grid(row=0, column=2, sticky="ew")

        self.value = ctk.CTkLabel(self, text="0 dB", width=48,
                                  font=("Segoe UI", 11))
        self.value.grid(row=0, column=3, padx=(8, 0))
        self.columnconfigure(2, weight=1)

    def _slider_moved(self, raw) -> None:
        self.gain = int(round(float(raw)))
        self.value.configure(text=f"{self.gain:+d} dB")
        self._changed()

    def _changed(self) -> None:
        self._on_change()

    def set_band(self, freq: int, gain: int) -> None:
        self.freq_var.set(str(freq))
        self.gain = gain
        self.slider.set(gain)
        self.value.configure(text=f"{gain:+d} dB")

    def band(self) -> tuple[int, int] | None:
        try:
            freq = int(float(self.freq_var.get()))
        except ValueError:
            return None
        if not F_MIN <= freq <= F_MAX:
            return None
        return freq, self.gain

    def set_enabled(self, on: bool) -> None:
        state = "normal" if on else "disabled"
        self.entry.configure(state=state)
        self.slider.configure(state=state)


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("realme Buds Control")
        self.geometry("760x720")
        self.minsize(700, 660)
        self.configure(fg_color=BG)

        self.controller = Controller()
        self.ports: list = []
        self._feature_vars: dict[int, ctk.BooleanVar] = {}
        self._feature_boxes: dict[int, ctk.CTkSwitch] = {}
        self._suspend_feature_events = False

        self._build_header()
        self._build_body()
        self._build_status()

        self.refresh_ports()
        self._set_connected(False)
        self._pump_id = self.after(POLL_MS, self._pump)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------- layout --
    def _build_header(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=CARD, corner_radius=10)
        bar.pack(fill="x", padx=14, pady=(14, 8))

        left = ctk.CTkFrame(bar, fg_color="transparent")
        left.pack(side="left", padx=14, pady=12)

        self.port_menu = ctk.CTkOptionMenu(left, values=["No devices"], width=230,
                                           fg_color="#333338", button_color="#3f3f45",
                                           button_hover_color="#4a4a52")
        self.port_menu.pack(side="left")
        ctk.CTkButton(left, text="Refresh", width=76, command=self.refresh_ports,
                      fg_color="#3a3a40", hover_color="#45454c").pack(side="left", padx=8)
        self.connect_btn = ctk.CTkButton(left, text="Connect", width=100,
                                         command=self.toggle_connection,
                                         fg_color=ACCENT, hover_color="#6ab0ea")
        self.connect_btn.pack(side="left")

        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.pack(side="right", padx=14)
        self.dot = ctk.CTkLabel(right, text="●", text_color=BAD,
                                font=("Segoe UI", 16))
        self.dot.pack(side="left", padx=(0, 6))
        self.device_label = ctk.CTkLabel(right, text="Not connected",
                                         font=("Segoe UI", 13, "bold"))
        self.device_label.pack(side="left")
        self.battery_label = ctk.CTkLabel(right, text="", text_color=MUTED,
                                          font=("Segoe UI", 11))
        self.battery_label.pack(side="left", padx=(10, 0))

    def _build_body(self) -> None:
        self.tabs = ctk.CTkTabview(self, fg_color=CARD, corner_radius=10,
                                   segmented_button_selected_color=ACCENT,
                                   segmented_button_selected_hover_color="#6ab0ea")
        self.tabs.pack(fill="both", expand=True, padx=14, pady=4)
        for name in ("Equaliser", "Bass", "Features", "Device"):
            self.tabs.add(name)
        self._build_eq_tab(self.tabs.tab("Equaliser"))
        self._build_bass_tab(self.tabs.tab("Bass"))
        self._build_features_tab(self.tabs.tab("Features"))
        self._build_device_tab(self.tabs.tab("Device"))

    def _build_eq_tab(self, tab) -> None:
        self.curve = CurveView(tab, self.current_bands)
        self.curve.pack(fill="x", pady=(6, 10))

        presets = ctk.CTkFrame(tab, fg_color="transparent")
        presets.pack(fill="x", pady=(0, 10))
        self.preset_buttons: dict[str, ctk.CTkButton] = {}
        for key, preset in PRESETS.items():
            btn = ctk.CTkButton(presets, text=preset.name, width=92, height=30,
                                fg_color="#34343a", hover_color="#41414a",
                                command=lambda k=key: self.load_preset(k))
            btn.pack(side="left", padx=(0, 6))
            self.preset_buttons[key] = btn

        self.preset_note = ctk.CTkLabel(tab, text="", text_color=MUTED,
                                        wraplength=640, justify="left",
                                        font=("Segoe UI", 11))
        self.preset_note.pack(fill="x", pady=(0, 8))

        self.rows: list[BandRow] = []
        holder = ctk.CTkFrame(tab, fg_color="transparent")
        holder.pack(fill="x")
        for i in range(6):
            row = BandRow(holder, i, self._bands_changed)
            row.pack(fill="x", pady=3)
            self.rows.append(row)

        actions = ctk.CTkFrame(tab, fg_color="transparent")
        actions.pack(fill="x", pady=(14, 4))
        self.apply_btn = ctk.CTkButton(actions, text="Apply to earbuds", width=150,
                                       command=self.apply_eq, fg_color=ACCENT,
                                       hover_color="#6ab0ea")
        self.apply_btn.pack(side="left")
        self.read_eq_btn = ctk.CTkButton(actions, text="Read from earbuds", width=150,
                                         command=self.read_eq, fg_color="#3a3a40",
                                         hover_color="#45454c")
        self.read_eq_btn.pack(side="left", padx=8)
        self.load_preset("classic", apply_bass=False)

    def _build_bass_tab(self, tab) -> None:
        ctk.CTkLabel(tab, justify="left", wraplength=640, text_color=MUTED,
                     font=("Segoe UI", 11),
                     text="A separate processor from the equaliser with its own "
                          "range. Using it for low-end weight leaves the "
                          "equaliser free for tonal work."
                     ).pack(fill="x", pady=(10, 14))

        self.bass_switch = ctk.CTkSwitch(tab, text="Dynamic bass engine",
                                         progress_color=ACCENT)
        self.bass_switch.select()
        self.bass_switch.pack(anchor="w", pady=(0, 16))

        self.bass_sliders: list[ctk.CTkSlider] = []
        self.bass_values: list[ctk.CTkLabel] = []
        for label in BASS_LABELS:
            row = ctk.CTkFrame(tab, fg_color="transparent")
            row.pack(fill="x", pady=5)
            ctk.CTkLabel(row, text=label, width=54, anchor="w").pack(side="left")
            value = ctk.CTkLabel(row, text="0", width=34)
            slider = ctk.CTkSlider(row, from_=BASS_MIN, to=BASS_MAX,
                                   number_of_steps=BASS_MAX - BASS_MIN,
                                   progress_color=ACCENT, button_color=ACCENT,
                                   command=lambda v, lb=value: lb.configure(
                                       text=f"{int(round(float(v))):+d}"))
            slider.set(0)
            slider.pack(side="left", fill="x", expand=True, padx=10)
            value.pack(side="left")
            self.bass_sliders.append(slider)
            self.bass_values.append(value)

        actions = ctk.CTkFrame(tab, fg_color="transparent")
        actions.pack(fill="x", pady=18)
        self.bass_apply = ctk.CTkButton(actions, text="Apply", width=120,
                                        command=self.apply_bass, fg_color=ACCENT,
                                        hover_color="#6ab0ea")
        self.bass_apply.pack(side="left")
        self.bass_read = ctk.CTkButton(actions, text="Read", width=120,
                                       command=self.read_bass, fg_color="#3a3a40",
                                       hover_color="#45454c")
        self.bass_read.pack(side="left", padx=8)

    def _build_features_tab(self, tab) -> None:
        ctk.CTkLabel(tab, justify="left", wraplength=640, text_color=MUTED,
                     font=("Segoe UI", 11),
                     text="Switches your firmware did not report are disabled, "
                          "which means it does not implement them."
                     ).pack(fill="x", pady=(10, 14))

        grid = ctk.CTkFrame(tab, fg_color="transparent")
        grid.pack(fill="both", expand=True)
        for i, fid in enumerate(TOGGLES):
            var = ctk.BooleanVar(value=False)
            switch = ctk.CTkSwitch(grid, text=FEATURES.get(fid, f"Feature {fid}"),
                                   variable=var, progress_color=ACCENT,
                                   command=lambda f=fid: self.toggle_feature(f))
            switch.grid(row=i % 5, column=i // 5, sticky="w", padx=(0, 40), pady=9)
            switch.configure(state="disabled")
            self._feature_vars[fid] = var
            self._feature_boxes[fid] = switch

        self.features_read = ctk.CTkButton(tab, text="Read from earbuds", width=150,
                                           command=self.read_features,
                                           fg_color="#3a3a40", hover_color="#45454c")
        self.features_read.pack(anchor="w", pady=14)

    def _build_device_tab(self, tab) -> None:
        self.info_box = ctk.CTkTextbox(tab, fg_color="#232326", wrap="word",
                                       font=("Consolas", 12))
        self.info_box.pack(fill="both", expand=True, pady=10)
        self.info_box.configure(state="disabled")
        self.info_read = ctk.CTkButton(tab, text="Read device info", width=150,
                                       command=self.read_info, fg_color="#3a3a40",
                                       hover_color="#45454c")
        self.info_read.pack(anchor="w", pady=(0, 10))

    def _build_status(self) -> None:
        self.status = ctk.CTkLabel(self, text="Not connected", anchor="w",
                                   text_color=MUTED, font=("Segoe UI", 11))
        self.status.pack(fill="x", padx=18, pady=(2, 12))

    # -------------------------------------------------------------- state --
    def _set_connected(self, on: bool, name: str = "") -> None:
        self.connect_btn.configure(text="Disconnect" if on else "Connect")
        self.dot.configure(text_color=GOOD if on else BAD)
        self.device_label.configure(text=name or ("Connected" if on else "Not connected"))
        if not on:
            self.battery_label.configure(text="")
        for widget in (self.apply_btn, self.read_eq_btn, self.bass_apply,
                       self.bass_read, self.features_read, self.info_read):
            widget.configure(state="normal" if on else "disabled")
        for row in self.rows:
            row.set_enabled(on)
        if not on:
            self._suspend_feature_events = True
            for fid, switch in self._feature_boxes.items():
                switch.configure(state="disabled")
                self._feature_vars[fid].set(False)
            self._suspend_feature_events = False

    def _say(self, text: str, error: bool = False) -> None:
        self.status.configure(text=text, text_color=BAD if error else MUTED)

    # ---------------------------------------------------------- event loop --
    def _pump(self) -> None:
        for result in self.controller.poll():
            if result.disconnected:
                self._set_connected(False)
                self._say(result.message or "Earbuds disconnected", error=True)
        self._pump_id = self.after(POLL_MS, self._pump)

    def _handle(self, label: str, on_ok):
        """Build a result callback that reports failures consistently."""

        def done(result: Result) -> None:
            if not result.ok:
                self._say(f"{label} failed: {result.message}", error=True)
                return
            on_ok(result.value)

        return done

    # ------------------------------------------------------------- actions --
    def refresh_ports(self) -> None:
        self.ports = self.controller.ports()
        candidates = [p for p in self.ports if p.is_candidate]
        shown = candidates or self.ports
        if not shown:
            self.port_menu.configure(values=["No devices"])
            self.port_menu.set("No devices")
            self._say("No serial ports. Connect the earbuds to this computer.")
            return
        labels = [p.label for p in shown]
        self.port_menu.configure(values=labels)
        self.port_menu.set(labels[0])
        self._say(f"{len(candidates)} paired device(s) found")

    def _selected_port(self):
        label = self.port_menu.get()
        return next((p for p in self.ports if p.label == label), None)

    def toggle_connection(self) -> None:
        if self.controller.connected:
            self.controller.disconnect(
                self._handle("Disconnect", lambda _v: (
                    self._set_connected(False), self._say("Disconnected"))))
            return

        port = self._selected_port()
        if not port:
            messagebox.showwarning("No device", "Choose a device first.")
            return
        self._say(f"Connecting on {port.device}...")
        self.connect_btn.configure(state="disabled")

        def connected(result: Result) -> None:
            self.connect_btn.configure(state="normal")
            if not result.ok:
                self._set_connected(False)
                self._say(result.message, error=True)
                messagebox.showerror("Connection failed", result.message)
                return
            self._set_connected(True, port.name or port.device)
            self._say(f"Connected on {port.device}, firmware {result.value}")
            # queued, so these run one after another rather than colliding
            self.read_info()
            self.read_features()
            self.read_bass()
            self.read_eq()

        self.controller.connect(port, connected)

    def current_bands(self) -> list[tuple[int, int]]:
        bands = []
        for row in self.rows:
            band = row.band()
            if band:
                bands.append(band)
        return bands

    def _bands_changed(self) -> None:
        self.curve.redraw()

    def load_preset(self, key: str, apply_bass: bool = True) -> None:
        preset = PRESETS.get(key)
        if not preset:
            return
        for row, (freq, gain) in zip(self.rows, preset.bands):
            row.set_band(freq, gain)
        if apply_bass:
            for slider, label, value in zip(self.bass_sliders, self.bass_values,
                                            preset.bass):
                slider.set(value)
                label.configure(text=f"{value:+d}")
        self.preset_note.configure(text=preset.description)
        for k, btn in self.preset_buttons.items():
            btn.configure(fg_color=ACCENT if k == key else "#34343a")
        self.curve.redraw()

    def apply_eq(self) -> None:
        bands = []
        for i, row in enumerate(self.rows, start=1):
            band = row.band()
            if band is None:
                messagebox.showerror(
                    "Invalid frequency",
                    f"Band {i}: enter a whole number between "
                    f"{int(F_MIN)} and {int(F_MAX)} Hz.")
                return
            bands.append(band)

        name = next((p.name for k, p in PRESETS.items()
                     if list(p.bands) == bands), "Custom")
        profile = EqProfile(eq_id=DEFAULT_EQ_SLOT, name=name[:12], bands=bands)
        self._say(f"Writing '{profile.name}'...")
        self.controller.submit(
            "write_eq", lambda b: b.write_eq(profile),
            self._handle("Apply", lambda ok: self._say(
                f"Applied '{profile.name}'" if ok
                else "The earbuds rejected that curve")))

    def read_eq(self) -> None:
        def loaded(profiles) -> None:
            active = next((p for p in profiles if p.active), None)
            if not active:
                self._say("No custom EQ is active on the earbuds")
                return
            for row, (freq, gain) in zip(self.rows, active.bands):
                row.set_band(freq, gain)
            self.curve.redraw()
            self._say(f"Loaded '{active.name}' from the earbuds")

        self.controller.submit("read_eq", lambda b: b.eq_profiles(),
                               self._handle("Read EQ", loaded))

    def apply_bass(self) -> None:
        values = [int(round(s.get())) for s in self.bass_sliders]
        enabled = bool(self.bass_switch.get())
        self._say("Writing bass settings...")
        self.controller.submit(
            "write_bass", lambda b: b.set_bass_engine(values, enabled),
            self._handle("Apply bass", lambda ok: self._say(
                "Bass engine updated" if ok
                else "The earbuds rejected those bass settings")))

    def read_bass(self) -> None:
        def loaded(bands) -> None:
            for band, value, *_ in bands:
                if 1 <= band <= len(self.bass_sliders):
                    self.bass_sliders[band - 1].set(value)
                    self.bass_values[band - 1].configure(text=f"{value:+d}")
            if bands:
                self._say("Bass engine read")

        self.controller.submit("read_bass", lambda b: b.bass_engine(),
                               self._handle("Read bass", loaded))

    def read_features(self) -> None:
        def loaded(features) -> None:
            self._suspend_feature_events = True
            for fid, switch in self._feature_boxes.items():
                if fid in features:
                    switch.configure(state="normal")
                    self._feature_vars[fid].set(bool(features[fid]))
                else:
                    switch.configure(state="disabled")
                    self._feature_vars[fid].set(False)
            self._suspend_feature_events = False
            supported = sum(1 for f in TOGGLES if f in features)
            self._say(f"{supported} of {len(TOGGLES)} switches supported "
                      "by this firmware")

        self.controller.submit("read_features", lambda b: b.features(),
                               self._handle("Read features", loaded))

    def toggle_feature(self, feature_id: int) -> None:
        if self._suspend_feature_events:
            return
        wanted = bool(self._feature_vars[feature_id].get())
        label = FEATURES.get(feature_id, str(feature_id))

        def done(result: Result) -> None:
            if result.ok and result.value:
                self._say(f"{label}: {'on' if wanted else 'off'}")
                return
            # put the switch back where it was
            self._suspend_feature_events = True
            self._feature_vars[feature_id].set(not wanted)
            self._suspend_feature_events = False
            self._say(f"The earbuds refused to change {label}", error=True)

        self.controller.submit("set_feature",
                               lambda b: b.set_feature(feature_id, wanted), done)

    def read_info(self) -> None:
        def loaded(info) -> None:
            names = {1: "Left", 2: "Right", 3: "Case"}
            if info.battery:
                parts = [f"{names.get(k, k)} {v}%"
                         for k, v in sorted(info.battery.items()) if k != 3]
                self.battery_label.configure(text="  ".join(parts))

            lines = [
                f"Firmware       {info.firmware or 'unknown'}",
                f"Region code    {info.region if info.region is not None else 'unknown'}",
            ]
            if info.battery:
                for key, pct in sorted(info.battery.items()):
                    lines.append(f"{names.get(key, key):<14} {pct}%")
            if info.active_preset is not None:
                lines.append(f"Active preset  {info.active_preset}")
            if info.capabilities:
                lines.append(f"\n{len(info.capabilities)} commands supported "
                             f"by this firmware.")
            if info.features:
                lines.append("\nSwitch features reported:")
                for fid, state in sorted(info.features.items()):
                    lines.append(f"  [{fid:>2}] {FEATURES.get(fid, 'unknown'):<24}"
                                 f" {'on' if state else 'off'}")
                missing = [f for f in (11, 19, 54) if f not in info.features]
                if missing:
                    lines.append("\nNot implemented by this firmware:")
                    for fid in missing:
                        lines.append(f"  [{fid:>2}] {FEATURES.get(fid, 'unknown')}")

            self.info_box.configure(state="normal")
            self.info_box.delete("1.0", "end")
            self.info_box.insert("1.0", "\n".join(lines))
            self.info_box.configure(state="disabled")
            self._say("Device information updated")

        self.controller.submit("read_info", lambda b: b.info(),
                               self._handle("Read device", loaded))

    def _on_close(self) -> None:
        # Cancel the polling loop first, or Tk keeps firing it into a window
        # that no longer exists.
        if getattr(self, "_pump_id", None):
            try:
                self.after_cancel(self._pump_id)
            except Exception:
                pass
            self._pump_id = None
        self.controller.stop()
        self.destroy()

    def destroy(self) -> None:
        if getattr(self, "_pump_id", None):
            try:
                self.after_cancel(self._pump_id)
            except Exception:
                pass
            self._pump_id = None
        super().destroy()


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
