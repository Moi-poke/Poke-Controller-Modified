import importlib
import inspect
import os
import traceback
from glob import glob
from os.path import join, relpath
from types import ModuleType
from typing import Any, List, Optional

# 他ファイル（Camera.py / Window.py / GuiAssets.py / Sender.py）に合わせ
# loguru へ統一。getLogger 由来の初期化は不要になった。
from loguru import logger


def ospath(path: str) -> str:
    return path.replace("/", os.sep)


# Show all file names under the directory
def browseFileNames(
    path: str = ".", ext: str = "", recursive: bool = True, name_only: bool = True
) -> List[str]:
    search_path = join(path, "**") if recursive else path
    search_path = join(search_path, "*" + ext)

    if name_only:
        return [relpath(f, path) for f in glob(search_path, recursive=recursive)]
    else:
        return glob(search_path, recursive=recursive)


def getClassesInModule(module: ModuleType) -> List[type]:
    classes: List[type] = []
    for members in inspect.getmembers(module, inspect.isclass):
        classes.append(members[1])
    return classes


def getModuleNames(base_path: str) -> List[str]:
    """base_path 配下の .py をモジュール名（ドット区切り）に変換する。"""
    filenames = browseFileNames(path=base_path, ext=".py", name_only=False)

    names: List[str] = []
    for filename in filenames:
        # name[:-3] は拡張子を「長さ」で切っていたため、ディレクトリ名に
        # '.' が含まれるとモジュール名が壊れた。splitext で確実に落とす。
        stem, _ = os.path.splitext(filename)
        names.append(stem.replace(os.sep, ".").replace("/", "."))
    return names


def importAllModules(
    base_path: str, mod_names: Optional[List[str]] = None
) -> List[ModuleType]:
    """配下のモジュールを一括 import する。

    1つでも import に失敗すると全体が止まり、壊れたコマンドファイルが
    1つあるだけで全コマンドが読めなくなっていた。個別に握って残りを
    読み込み続ける。
    """
    modules: List[ModuleType] = []
    for name in getModuleNames(base_path) if mod_names is None else mod_names:
        logger.debug(f"Import module: {name}")
        try:
            modules.append(importlib.import_module(name))
        except Exception:
            logger.error(f"Failed to import module: {name}\n{traceback.format_exc()}")

    return modules
