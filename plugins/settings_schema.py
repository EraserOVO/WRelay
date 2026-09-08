"""兼容入口：配置模型和校验规则统一定义在 settings_model.py。"""

from plugins.settings_model import SettingsModel, validate_settings


__all__ = ["SettingsModel", "validate_settings"]
