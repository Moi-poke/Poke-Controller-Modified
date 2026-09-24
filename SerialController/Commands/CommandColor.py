"""Commands/CommandColor.py - 後方互換の再公開口.

実体は core/procon_color.py へ移った。既存・新規の利用者台本は
    from Commands.CommandColor import parse_color_hex
と書いて使う。新規のアプリ内コードは core 側から読むこと。
"""

from core.procon_color import (
    build_color_payload as build_color_payload,
    config_reject_type as config_reject_type,
    describe_bcon_errcode as describe_bcon_errcode,
    is_bcon_transport as is_bcon_transport,
    parse_color_hex as parse_color_hex,
    send_config_frame as send_config_frame,
)
