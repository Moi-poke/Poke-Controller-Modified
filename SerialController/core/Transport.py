"""Transport.py - 後方互換の再公開口.

実体は core/transport/ へ移った（base.py が抽象、text_serial.py が
pyserial の実装、registry.py が名前からの選択）。既存のコードは
    from core.Transport import TextSerialTransport
    from core import Transport
と書いたまま変えずに使える。新規のコードは core.transport 側から読むこと.
"""

from core.transport import (
    BITS_PER_BYTE as BITS_PER_BYTE,
    DEFAULT_TRANSPORT as DEFAULT_TRANSPORT,
    LEGACY_ROW as LEGACY_ROW,
    LIVE_WORKER_CAPABILITIES as LIVE_WORKER_CAPABILITIES,
    MIN_SEND_INTERVAL as MIN_SEND_INTERVAL,
    PICO_LIVE_STATE as PICO_LIVE_STATE,
    READ_TIMEOUT as READ_TIMEOUT,
    SEND_INTERVAL_MARGIN as SEND_INTERVAL_MARGIN,
    SEND_ROW_BYTES as SEND_ROW_BYTES,
    VALID_CAPABILITIES as VALID_CAPABILITIES,
    WRITE_TIMEOUT as WRITE_TIMEOUT,
    PicoUartTransport as PicoUartTransport,
    TextSerialTransport as TextSerialTransport,
    Transport as Transport,
    create_transport as create_transport,
    describe_transports as describe_transports,
    get_transport_info as get_transport_info,
    list_transports as list_transports,
    load_transport_plugins as load_transport_plugins,
    register_transport as register_transport,
    resolve_transport_name as resolve_transport_name,
    unregister_transport as unregister_transport,
)
