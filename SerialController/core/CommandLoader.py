import importlib
import sys
import traceback
from types import ModuleType

from core import Utility as util
from loguru import logger


class CommandLoader:
    def __init__(self, base_path: str, base_class: type) -> None:
        self.path: str = base_path
        self.base_type: type = base_class
        self.modules: list[ModuleType] = []

    def load(self) -> list[type]:
        if not self.modules:  # load if empty
            self.modules = util.importAllModules(self.path)

        # return command class types
        return self.getCommandClasses()

    def reload(self) -> list[type]:
        loaded_module_dic = {mod.__name__: mod for mod in self.modules}
        cur_module_names = util.getModuleNames(self.path)

        # Load only not loaded modules
        not_loaded_module_names = list(
            set(cur_module_names) - set(loaded_module_dic.keys())
        )
        if len(not_loaded_module_names) > 0:
            self.modules.extend(
                util.importAllModules(self.path, not_loaded_module_names)
            )

        # Reload commands except deleted ones
        # 1つが壊れても残りは読み直す。壊れた1件で全体を止めない。
        for mod_name in list(set(cur_module_names) & set(loaded_module_dic.keys())):
            try:
                importlib.reload(loaded_module_dic[mod_name])
            except Exception:
                # ファイル名と経緯を残して次へ進む（単体分離を保つ）。
                logger.error(
                    f"再読み込みに失敗しました: {mod_name}\n{traceback.format_exc()}"
                )
                continue

        # Unload deleted commands
        for mod_name in list(set(loaded_module_dic.keys()) - set(cur_module_names)):
            self.modules.remove(loaded_module_dic[mod_name])
            sys.modules.pop(
                loaded_module_dic[mod_name].__name__
            )  # Un-import module forcefully

        # return command class types
        return self.getCommandClasses()

    def getCommandClasses(self) -> list[type]:
        classes: list[type] = []
        for mod in self.modules:
            classes.extend(
                [
                    c
                    for c in util.getClassesInModule(mod)
                    if issubclass(c, self.base_type) and hasattr(c, "NAME") and c.NAME
                ]
            )

        return classes
