"""Bridge NoneBot 2.5's source-file loader to Nuitka's compiled module loader.

The hook is used only by compiled applications and only for explicitly registered
NoneBot plugins. Plugin context/rollback follow the pinned NoneBot loader.
"""
import sys
from importlib.abc import Loader, MetaPathFinder


def install():
    from plugins.paths import COMPILED
    if not COMPILED or any(isinstance(item, CompiledPluginFinder) for item in sys.meta_path):
        return
    sys.meta_path.insert(0, CompiledPluginFinder())


class CompiledPluginLoader(Loader):
    def __init__(self, original, manager, name):
        self.original, self.manager, self.name = original, manager, name

    def create_module(self, spec):
        return self.original.create_module(spec)

    def exec_module(self, module):
        from nonebot.plugin import _current_plugin, _new_plugin, _revert_plugin
        plugin = _new_plugin(self.name, module, self.manager)
        module.__plugin__ = plugin
        token = _current_plugin.set(plugin)
        try:
            self.original.exec_module(module)
        except BaseException:
            _revert_plugin(plugin)
            raise
        finally:
            _current_plugin.reset(token)
        plugin.metadata = getattr(module, "__plugin_meta__", None)


class CompiledPluginFinder(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        from nonebot.plugin import _managers
        from nonebot.plugin.manager import PluginFinder
        manager = next((item for item in reversed(_managers)
                        if fullname in item.controlled_modules.values()), None)
        if manager is None:
            return None
        for finder in tuple(sys.meta_path):
            if finder is self or isinstance(finder, (CompiledPluginFinder, PluginFinder)):
                continue
            spec = finder.find_spec(fullname, path, target)
            if spec is not None and spec.loader is not None:
                spec.loader = CompiledPluginLoader(spec.loader, manager, fullname)
                return spec
        return None
