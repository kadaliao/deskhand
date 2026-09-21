"""macOS implementation: accessibility, pixels, quartz input.

Importing this package does not import pyobjc. Every framework import happens
inside a function, so the package stays importable and testable off a Mac.
"""

from .ax import AXSource, Frame, require_permission, trusted
from .ocr import OCRSource, screen_capture_allowed
from .screen import MacSensor, open_sensor, permissions, window_box

__all__ = [
    "AXSource",
    "Frame",
    "MacSensor",
    "OCRSource",
    "open_sensor",
    "permissions",
    "require_permission",
    "screen_capture_allowed",
    "trusted",
    "window_box",
]
