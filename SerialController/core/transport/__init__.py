"""transport - 送信の下回り（線そのもの）を受け持つ層。

base.py が抽象と能力名、text_serial.py が pyserial の実装、
registry.py が名前からの選択（設定画面・起動引数用）を持つ。
旧位置の core/Transport.py からも同じ名前で読める。
"""

from core.transport.base import (
    LEGACY_ROW as LEGACY_ROW,
    LIVE_WORKER_CAPABILITIES as LIVE_WORKER_CAPABILITIES,
    PICO_LIVE_STATE as PICO_LIVE_STATE,
    VALID_CAPABILITIES as VALID_CAPABILITIES,
    Transport as Transport,
)
from core.transport.registry import (
    DEFAULT_TRANSPORT as DEFAULT_TRANSPORT,
    create_transport as create_transport,
    describe_transports as describe_transports,
    get_transport_info as get_transport_info,
    list_transports as list_transports,
    load_transport_plugins as load_transport_plugins,
    register_transport as register_transport,
    resolve_transport_name as resolve_transport_name,
    unregister_transport as unregister_transport,
)
from core.transport.text_serial import (
    BITS_PER_BYTE as BITS_PER_BYTE,
    MIN_SEND_INTERVAL as MIN_SEND_INTERVAL,
    READ_TIMEOUT as READ_TIMEOUT,
    SEND_INTERVAL_MARGIN as SEND_INTERVAL_MARGIN,
    SEND_ROW_BYTES as SEND_ROW_BYTES,
    WRITE_TIMEOUT as WRITE_TIMEOUT,
    PicoUartTransport as PicoUartTransport,
    TextSerialTransport as TextSerialTransport,
)
