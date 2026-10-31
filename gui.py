#!/usr/bin/env python3
"""Desktop interface for realme Buds.

Run with:  python gui.py
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from buds import FEATURES, PRESETS, Buds, EqProfile, find_ports
from buds.presets import DEFAULT_EQ_SLOT
from buds.protocol import EQ_GAIN_MAX, EQ_GAIN_MIN

BASS_GAIN_MIN, BASS_GAIN_MAX = -5, 5
BASS_LABELS = ("Low", "Mid", "High")

# Toggles worth exposing; the rest are diagnostic or read-only in practice.
TOGGLE_IDS = [24, 27, 6, 26, 9, 29, 17, 4, 5, 8]


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("realme Buds Control")
        self.geometry("620x560")
        self.minsize(560, 520)

        self.buds: Buds | None = None
        self._results: queue.Queue = queue.Queue()
        self._ports: list[tuple[str, str]] = []

        self._build_connection_bar()
        self._build_tabs()
        self._build_status_bar()

        self.refresh_ports()
        self.after(100, self._poll_results)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------- layout --
    def _build_connection_bar(self) -> None:
        bar = ttk.Frame(self, padding=(10, 10, 10, 4))
        bar.pack(fill="x")

        ttk.Label(bar, text="Port").pack(side="left")
        self.port_var = tk.StringVar()
        self.port_box = ttk.Combobox(bar, textvariable=self.port_var,
                                     width=34, state="readonly")
        self.port_box.pack(side="left", padx=6)

        ttk.Button(bar, text="Refresh", command=self.refresh_ports).pack(side="left")
        self.connect_btn = ttk.Button(bar, text="Connect", command=self.toggle_connection)
        self.connect_btn.pack(side="left", padx=6)

    def _build_tabs(self) -> None:
        self.tabs = ttk.Notebook(self, padding=(8, 4))
        self.tabs.pack(fill="both", expand=True)
        self._build_eq_tab()
        self._build_bass_tab()
        self._build_features_tab()
        self._build_device_tab()

    def _build_eq_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="Equaliser")

        top = ttk.Frame(tab)
        top.pack(fill="x", pady=(0, 10))
        ttk.Label(top, text="Preset").pack(side="left")
        self.preset_var = tk.StringVar(value="classic")
        preset_box = ttk.Combobox(top, textvariable=self.preset_var, width=14,
                                  state="readonly",
                                  values=[p.name for p in PRESETS.values()])
        preset_box.pack(side="left", padx=6)
        preset_box.bind("<<ComboboxSelected>>", lambda _e: self.load_preset())
        ttk.Button(top, text="Load", command=self.load_preset).pack(side="left")

        self.preset_note = ttk.Label(tab, text="", wraplength=560,
                                     foreground="#555", justify="left")
        self.preset_note.pack(fill="x", pady=(0, 10))

        grid = ttk.LabelFrame(tab, text="Bands", padding=10)
        grid.pack(fill="both", expand=True)
        ttk.Label(grid, text="Centre (Hz)").grid(row=0, column=0, sticky="w")
        ttk.Label(grid, text="Gain (dB)").grid(row=0, column=1, sticky="w", padx=10)

        self.freq_vars: list[tk.StringVar] = []
        self.gain_vars: list[tk.IntVar] = []
        for i in range(6):
            freq = tk.StringVar(value="0")
            gain = tk.IntVar(value=0)
            self.freq_vars.append(freq)
            self.gain_vars.append(gain)
            ttk.Entry(grid, textvariable=freq, width=9).grid(
                row=i + 1, column=0, pady=3, sticky="w")
            scale = tk.Scale(grid, from_=EQ_GAIN_MIN, to=EQ_GAIN_MAX,
                             orient="horizontal", variable=gain, length=330,
                             resolution=1, showvalue=True)
            scale.grid(row=i + 1, column=1, padx=10, sticky="we")
        grid.columnconfigure(1, weight=1)

        actions = ttk.Frame(tab)
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="Apply to earbuds",
                   command=self.apply_eq).pack(side="left")
        ttk.Button(actions, text="Read from earbuds",
                   command=self.read_eq).pack(side="left", padx=6)
        self.load_preset()

    def _build_bass_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="Bass engine")

        ttk.Label(tab, wraplength=560, justify="left", foreground="#555",
                  text="A separate processor from the equaliser, with its own "
                       "range. Using it for low-end weight leaves the equaliser "
                       "free to do tonal work.").pack(fill="x", pady=(0, 12))

        self.bass_enabled = tk.BooleanVar(value=True)
        ttk.Checkbutton(tab, text="Enable dynamic bass engine",
                        variable=self.bass_enabled).pack(anchor="w", pady=(0, 8))

        self.bass_vars: list[tk.IntVar] = []
        box = ttk.Frame(tab)
        box.pack(fill="x")
        for i, label in enumerate(BASS_LABELS):
            var = tk.IntVar(value=0)
            self.bass_vars.append(var)
            ttk.Label(box, text=label, width=6).grid(row=i, column=0, sticky="w")
            tk.Scale(box, from_=BASS_GAIN_MIN, to=BASS_GAIN_MAX,
                     orient="horizontal", variable=var, length=380,
                     resolution=1).grid(row=i, column=1, sticky="we", pady=2)
        box.columnconfigure(1, weight=1)

        actions = ttk.Frame(tab)
        actions.pack(fill="x", pady=12)
        ttk.Button(actions, text="Apply", command=self.apply_bass).pack(side="left")
        ttk.Button(actions, text="Read", command=self.read_bass).pack(side="left", padx=6)

    def _build_features_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="Features")
        ttk.Label(tab, wraplength=560, justify="left", foreground="#555",
                  text="Greyed-out entries are toggles this firmware did not "
                       "answer for, which means it does not implement them."
                  ).pack(fill="x", pady=(0, 10))

        self.feature_vars: dict[int, tk.BooleanVar] = {}
        self.feature_boxes: dict[int, ttk.Checkbutton] = {}
        holder = ttk.Frame(tab)
        holder.pack(fill="both", expand=True)
        for row, fid in enumerate(TOGGLE_IDS):
            var = tk.BooleanVar(value=False)
            self.feature_vars[fid] = var
            box = ttk.Checkbutton(holder, text=FEATURES.get(fid, f"Feature {fid}"),
                                  variable=var, state="disabled",
                                  command=lambda f=fid: self.toggle_feature(f))
            box.grid(row=row % 5, column=row // 5, sticky="w", padx=(0, 24), pady=4)
            self.feature_boxes[fid] = box

        ttk.Button(tab, text="Read from earbuds",
                   command=self.read_features).pack(anchor="w", pady=10)

    def _build_device_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="Device")
        self.info_text = tk.Text(tab, height=16, wrap="word", relief="flat",
                                 background=self.cget("background"))
        self.info_text.pack(fill="both", expand=True)
        self.info_text.configure(state="disabled")
        ttk.Button(tab, text="Read device info",
                   command=self.read_info).pack(anchor="w", pady=(8, 0))

    def _build_status_bar(self) -> None:
        self.status = tk.StringVar(value="Not connected")
        ttk.Separator(self).pack(fill="x")
        ttk.Label(self, textvariable=self.status, anchor="w",
                  padding=(10, 4)).pack(fill="x")

    # ------------------------------------------------------ worker plumbing --
    def _run(self, fn, done=None, busy: str = "Working...") -> None:
        """Run a serial operation off the UI thread."""
        if not (self.buds and self.buds.connected):
            self.status.set("Not connected")
            return
        self.status.set(busy)

        def worker():
            try:
                self._results.put((done, fn(), None))
            except Exception as exc:  # surfaced in the status bar
                self._results.put((done, None, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_results(self) -> None:
        try:
            while True:
                callback, result, error = self._results.get_nowait()
                if error:
                    self.status.set(f"Error: {error}")
                    self._set_disconnected()
                elif callback:
                    callback(result)
        except queue.Empty:
            pass
        self.after(120, self._poll_results)

    # ------------------------------------------------------------- actions --
    def refresh_ports(self) -> None:
        self._ports = find_ports()
        labels = [port.label for port in self._ports]
        self.port_box["values"] = labels
        candidates = [port for port in self._ports if port.is_candidate]
        if candidates:
            # find_ports puts recognised earbuds first
            self.port_var.set(candidates[0].label)
        elif labels and not self.port_var.get():
            self.port_var.set(labels[0])
        if not labels:
            self.status.set("No serial ports. Connect the earbuds to this computer.")
        else:
            self.status.set(f"{len(candidates)} paired device(s) available")

    def _selected_port(self) -> str | None:
        label = self.port_var.get()
        for port in self._ports:
            if port.label == label:
                return port.device
        return None

    def toggle_connection(self) -> None:
        if self.buds and self.buds.connected:
            self.buds.close()
            self._set_disconnected()
            return
        port = self._selected_port()
        if not port:
            messagebox.showwarning("No port", "Pick a serial port first.")
            return
        try:
            self.buds = Buds(port)
            self.buds.connect()
        except Exception as exc:
            messagebox.showerror("Connection failed", str(exc))
            self._set_disconnected()
            return
        self.connect_btn.configure(text="Disconnect")
        self.status.set(f"Connected on {port}")
        self.read_info()
        self.read_features()
        self.read_bass()

    def _set_disconnected(self) -> None:
        self.buds = None
        self.connect_btn.configure(text="Connect")
        for box in self.feature_boxes.values():
            box.configure(state="disabled")
        self.status.set("Not connected")

    def load_preset(self) -> None:
        name = self.preset_var.get()
        preset = next((p for p in PRESETS.values() if p.name == name), None)
        if not preset:
            return
        for i, (hz, gain) in enumerate(preset.bands):
            self.freq_vars[i].set(str(hz))
            self.gain_vars[i].set(gain)
        for i, value in enumerate(preset.bass):
            self.bass_vars[i].set(value)
        self.preset_note.configure(text=preset.description)

    def _collect_bands(self) -> list[tuple[int, int]] | None:
        bands = []
        for freq_var, gain_var in zip(self.freq_vars, self.gain_vars):
            try:
                hz = int(freq_var.get())
            except ValueError:
                messagebox.showerror("Bad frequency",
                                     f"'{freq_var.get()}' is not a number.")
                return None
            if not 20 <= hz <= 20000:
                messagebox.showerror("Bad frequency",
                                     f"{hz} Hz is outside 20-20000.")
                return None
            bands.append((hz, gain_var.get()))
        return bands

    def apply_eq(self) -> None:
        bands = self._collect_bands()
        if bands is None:
            return
        name = self.preset_var.get() or "Custom"
        profile = EqProfile(eq_id=DEFAULT_EQ_SLOT, name=name[:12], bands=bands)

        def done(ok):
            self.status.set(f"EQ '{profile.name}' applied"
                            if ok else "Earbuds rejected the EQ")

        self._run(lambda: self.buds.write_eq(profile), done, "Writing EQ...")

    def read_eq(self) -> None:
        def done(profiles):
            active = next((p for p in profiles if p.active), None)
            if not active:
                self.status.set("No active custom EQ on the earbuds")
                return
            for i, (hz, gain) in enumerate(active.bands[:6]):
                self.freq_vars[i].set(str(hz))
                self.gain_vars[i].set(gain)
            self.status.set(f"Loaded '{active.name}' from the earbuds")

        self._run(self.buds.eq_profiles, done, "Reading EQ...")

    def apply_bass(self) -> None:
        values = [v.get() for v in self.bass_vars]
        enabled = self.bass_enabled.get()

        def done(ok):
            self.status.set("Bass engine updated" if ok
                            else "Earbuds rejected the bass settings")

        self._run(lambda: self.buds.set_bass_engine(values, enabled), done,
                  "Writing bass settings...")

    def read_bass(self) -> None:
        def done(bands):
            for band, value, _lo, _hi in bands:
                if 1 <= band <= len(self.bass_vars):
                    self.bass_vars[band - 1].set(value)
            if bands:
                self.status.set("Bass engine read")

        self._run(self.buds.bass_engine, done, "Reading bass engine...")

    def read_features(self) -> None:
        def done(features):
            for fid, box in self.feature_boxes.items():
                if fid in features:
                    self.feature_vars[fid].set(bool(features[fid]))
                    box.configure(state="normal")
                else:
                    box.configure(state="disabled")
            self.status.set(f"{len(features)} features reported")

        self._run(self.buds.features, done, "Reading features...")

    def toggle_feature(self, feature_id: int) -> None:
        wanted = self.feature_vars[feature_id].get()
        label = FEATURES.get(feature_id, str(feature_id))

        def done(ok):
            if ok:
                self.status.set(f"{label}: {'on' if wanted else 'off'}")
            else:
                self.feature_vars[feature_id].set(not wanted)
                self.status.set(f"Earbuds refused to change {label}")

        self._run(lambda: self.buds.set_feature(feature_id, wanted), done,
                  f"Setting {label}...")

    def read_info(self) -> None:
        def done(info):
            lines = [
                f"Firmware        {info.firmware or 'unknown'}",
                f"Region code     {info.region if info.region is not None else 'unknown'}",
            ]
            if info.battery:
                names = {1: "Left", 2: "Right", 3: "Case"}
                for key, pct in sorted(info.battery.items()):
                    lines.append(f"{names.get(key, key):<15} {pct}%")
            if info.active_preset is not None:
                lines.append(f"Active preset   {info.active_preset}")
            if info.capabilities:
                lines.append(f"\n{len(info.capabilities)} commands supported "
                             "by this firmware.")
            if info.features:
                lines.append("\nFeatures reported:")
                for fid, state in sorted(info.features.items()):
                    lines.append(f"  [{fid:>2}] {FEATURES.get(fid, 'unknown'):<24}"
                                 f" {'on' if state else 'off'}")
            self.info_text.configure(state="normal")
            self.info_text.delete("1.0", "end")
            self.info_text.insert("1.0", "\n".join(lines))
            self.info_text.configure(state="disabled")
            self.status.set("Device info read")

        self._run(self.buds.info, done, "Reading device...")

    def _on_close(self) -> None:
        if self.buds:
            self.buds.close()
        self.destroy()


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
