"""Settings UI package. Re-exports the dialog so callers can keep using
`from core.settings import SettingsDialog`."""

from core.settings.dialog import SettingsDialog

__all__ = ["SettingsDialog"]
