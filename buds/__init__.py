"""Control realme Buds earbuds directly over Bluetooth SPP."""

from .device import Buds, BudsError, DeviceInfo, NotConnected, find_ports
from .presets import DEFAULT_EQ_SLOT, PRESETS, Preset
from .protocol import EQ_GAIN_MAX, EQ_GAIN_MIN, FEATURES, EqProfile

__version__ = "1.0.0"

__all__ = [
    "Buds", "BudsError", "NotConnected", "DeviceInfo", "find_ports",
    "PRESETS", "Preset", "DEFAULT_EQ_SLOT",
    "EqProfile", "FEATURES", "EQ_GAIN_MIN", "EQ_GAIN_MAX",
]
