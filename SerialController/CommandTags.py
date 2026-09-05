"""CommandTags.py - 後方互換の再公開口.

実体は core/CommandTags.py へ移った。新規のコードは core 側から読むこと。
"""

from core.CommandTags import (
    SKIP_FOLDERS as SKIP_FOLDERS,
    TAGS_JSON as TAGS_JSON,
    TAG_ALL as TAG_ALL,
    TAG_UNCLASSIFIED as TAG_UNCLASSIFIED,
    buildCommandMap as buildCommandMap,
    collectTags as collectTags,
    commandName as commandName,
    displayName as displayName,
    folderTags as folderTags,
    loadOverrides as loadOverrides,
    saveOverrides as saveOverrides,
    sortTagChoices as sortTagChoices,
)
