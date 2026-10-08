"""Nuitka entry, copied into the isolated compilation source tree."""
import asyncio
import ctypes
import json
import os
from pathlib import Path
import sys


def main():
    # Nuitka's standalone sys.executable retains the build-time basename even
    # when the binary is copied under another role name. Use the loaded image.
    kernel32 = ctypes.windll.kernel32
    kernel32.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong]
    kernel32.GetModuleFileNameW.restype = ctypes.c_ulong
    image_path = ctypes.create_unicode_buffer(32768)
    length = kernel32.GetModuleFileNameW(None, image_path, len(image_path))
    if not length or length >= len(image_path):
        raise RuntimeError("Cannot resolve the running WRelay executable")
    sys.executable = image_path.value
    root = Path(sys.executable).resolve().parent
    os.chdir(root)
    role = Path(sys.executable).stem.lower()
    # Frameworks inspect sys.stdout even in a Windows GUI executable. Open only
    # when no inherited redirected stream exists (diagnostics use redirection).
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if role == "wrelay.bot":
        # Keep configuration-guard messages and failures before bot.py's
        # rotating logger starts. The process manager intentionally discards
        # child streams, so a GUI executable otherwise exits without a clue.
        startup_log = root / "data" / "logs" / "bot-startup.log"
        startup_log.parent.mkdir(parents=True, exist_ok=True)
        sys.stderr = open(startup_log, "a", encoding="utf-8")
    elif sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    if "--build-info" in sys.argv:
        print((root / "build-info.json").read_text(encoding="utf-8"))
        return 0
    if "--migrate-autostart" in sys.argv:
        from plugins.paths import EDITION
        if EDITION != "release":
            return 1
        import wrelay_panel
        return 0 if not wrelay_panel.get_autostart() or wrelay_panel.set_autostart(True) else 1
    if "--diagnose-channels" in sys.argv:
        import wrelay_diagnose
        return asyncio.run(wrelay_diagnose.main())
    if "--check-components" in sys.argv:
        # Isolated installation checks call this before any credentials exist.
        import PIL.Image
        import webview
        import pystray
        import clr
        import wrelay_panel
        import wrelay_desktop
        import wrelay_restart
        import nonebot
        import nonebot.drivers.websockets
        import websockets.asyncio.client
        from nonebot.adapters.qq import Adapter as QQAdapter
        from nonebot.adapters.discord import Adapter as DiscordAdapter
        nonebot.init(driver="~fastapi+~httpx", _env_file=None, qq_bots=[], discord_bots=[])
        driver = nonebot.get_driver()
        driver.register_adapter(QQAdapter)
        driver.register_adapter(DiscordAdapter)
        modules = (
            "plugins.backfill", "plugins.backfill_api", "plugins.command",
            "plugins.health_api", "plugins.manage", "plugins.qq_fwd",
            "plugins.registration", "plugins.relay", "plugins.scheduled_message",
            "plugins.bilibili", "plugins.x_rss",
        )
        from plugins.compiled_plugins import install
        install()
        loaded = nonebot.load_all_plugins(modules, [])
        missing = set(modules) - {plugin.module_name for plugin in loaded}
        if missing:
            raise RuntimeError("Plugins did not load: " + ", ".join(sorted(missing)))
        PIL.Image.new("RGBA", (1, 1))
        from System.Reflection import Assembly
        assembly = Assembly.LoadFrom(str(root / "panel" / "WRelay.WindowChrome.dll"))
        assert assembly.GetType("WRelay.Desktop.WindowChrome", True)
        assert (root / "panel" / "web" / "onboarding.js").is_file()
        assert wrelay_panel.PROJECT_ROOT == str(root)
        assert wrelay_panel.BOT_ENTRY is None
        print(json.dumps({"ok": True, "plugins": len(modules)}))
        return 0
    if role == "wrelay.guardian":
        from panel.desktop_guardian import guardian_main
        guardian_main()
    elif role == "wrelay.bot":
        import bot  # This existing entry performs the credential guard and runs NoneBot.
    elif role == "wrelay.restart":
        import wrelay_restart
        wrelay_restart.main()
    else:
        import wrelay_desktop
        wrelay_desktop.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
