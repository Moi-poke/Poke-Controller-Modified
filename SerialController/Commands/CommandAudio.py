"""Commands/CommandAudio.py - 後方互換の再公開口.

実体は core/CommandAudio.py。新規のコードは core 側から読むこと。
"""

from core.CommandAudio import (
    AUDIO_CLIP_DIR as AUDIO_CLIP_DIR,
    TEMPLATE_AUDIO_PATH as TEMPLATE_AUDIO_PATH,
    AudioMixin as AudioMixin,
)
from core.audio_dsp import band_power as band_power, is_tone as is_tone
