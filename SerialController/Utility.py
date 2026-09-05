"""Utility.py - 後方互換の再公開口.

実体は core/Utility.py へ移った。新規のコードは core 側から読むこと。
"""

from core.Utility import (
    browseFileNames as browseFileNames,
    getClassesInModule as getClassesInModule,
    getModuleNames as getModuleNames,
    importAllModules as importAllModules,
    ospath as ospath,
)
