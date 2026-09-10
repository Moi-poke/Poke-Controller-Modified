"""WindowUtils.acceptsAudioArg の検証。"""

import WindowUtils


class NoArg:
    def __init__(self) -> None:
        pass


class WithAudio:
    def __init__(self, audio: object = None) -> None:
        _ = audio


class WithVarArgs:
    def __init__(self, *args: object) -> None:
        _ = args


def test_accepts_audio_arg() -> None:
    assert WindowUtils.acceptsAudioArg(NoArg) is False
    assert WindowUtils.acceptsAudioArg(WithAudio) is True
    assert WindowUtils.acceptsAudioArg(WithVarArgs) is True
