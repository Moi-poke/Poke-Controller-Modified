"""CommandStats.py - 後方互換の再公開口.

実体は core/CommandStats.py へ移った。新規のコードは core 側から読むこと。
"""

from core.CommandStats import (
    FREQUENT_MIN as FREQUENT_MIN,
    RECENT_MAX as RECENT_MAX,
    STATS_JSON as STATS_JSON,
    TAG_FREQUENT as TAG_FREQUENT,
    TAG_RECENT as TAG_RECENT,
    choices as choices,
    formatLast as formatLast,
    frequentNames as frequentNames,
    lastUsed as lastUsed,
    load as load,
    pathFor as pathFor,
    recentNames as recentNames,
    record as record,
    save as save,
    summary as summary,
    usedCount as usedCount,
    virtualTags as virtualTags,
)
