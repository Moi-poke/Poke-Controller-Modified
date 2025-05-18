import importlib
import inspect
import os
from glob import glob
from os.path import join, relpath
from loguru import logger
import hashlib


def ospath(path: str) -> str:
    return path.replace("/", os.sep)


# Show all file names under the directory
def browseFileNames(
    path: str = ".", ext: str = "", recursive: bool = True, name_only: bool = True
) -> list[str]:
    search_path = join(path, "**") if recursive else path
    search_path = join(search_path, "*" + ext)

    if name_only:
        return [relpath(f, path) for f in glob(search_path, recursive=recursive)]
    else:
        return glob(search_path, recursive=recursive)


def getClassesInModule(module: str) -> list[type]:
    classes = []
    for members in inspect.getmembers(module, inspect.isclass):
        classes.append(members[1])
    return classes


def getModuleNames(base_path: str) -> list[str]:
    filenames = browseFileNames(path=base_path, ext=".py", name_only=False)
    return [name[:-3].replace(os.sep, ".") for name in filenames]


def importAllModules(base_path: str, mod_names: list[str] | None = None) -> list:
    modules = []
    for name in getModuleNames(base_path) if mod_names is None else mod_names:
        logger.debug(f"Import module: {name}")
        modules.append(importlib.import_module(name))

    return modules


def calculate_sha256(file_path: str) -> str | None:
    """
    指定されたファイルのSHA256ハッシュ値を計算します。

    Args:
        file_path (str): ハッシュ値を計算するファイルのパス。

    Returns:
        str | None: 計算されたハッシュ値（16進数文字列）。ファイルが存在しない場合はNone。
    """
    if not os.path.exists(file_path):
        logger.error(f"File not found for hashing: {file_path}")
        return None
    try:
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            # Read and update hash string value in blocks of 4K
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except Exception as e:
        logger.error(f"Error calculating SHA256 for {file_path}: {e}")
        return None
