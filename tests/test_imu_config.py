"""IMU設定節の契約（実機不要）。"""

import configparser


def _fresh() -> configparser.ConfigParser:
    import config

    parser = configparser.ConfigParser()
    parser.optionxform = str  # type: ignore[assignment]
    config.complete_missing(parser)
    return parser


def test_imu_section_defaults():
    parser = _fresh()
    imu = parser["IMU"]
    assert imu["source"] == "sensor"
    assert imu["pad_index"] == "0"
    assert imu["sine_axis"] == "2"
    assert imu["sine_amp"] == "4096"
    assert imu["sine_period_s"] == "2.0"
    assert imu["step_axis"] == "2"
    assert imu["step_raw"] == "5899"
    assert imu["step_duration_s"] == "4.0"
    assert imu["acc_z"] == "4096"
    assert imu["accel_axes"] == "2,0,1"
    assert imu["gyro_signs"] == "-1,-1,1"
    assert imu["hid_accel_axes"] == "0,1,2"
    assert imu["hid_accel_signs"] == "1,1,1"
    assert imu["hid_gyro_axes"] == "0,1,2"
    assert imu["hid_gyro_signs"] == "1,1,1"
    assert imu["hid_gyro_scale"] == "1.0,1.0,1.0"
    assert imu["gyro_bias"] == "0.0,0.0,0.0"


def test_imu_section_repairs_invalid():
    import config

    parser = _fresh()
    parser["IMU"]["source"] = "bad"
    parser["IMU"]["sine_axis"] = "9"
    parser["IMU"]["sine_amp"] = "-1"
    parser["IMU"]["sine_period_s"] = "0"
    parser["IMU"]["step_raw"] = "99999"
    parser["IMU"]["accel_axes"] = "0,5,1"
    parser["IMU"]["gyro_signs"] = "2,1,1"
    parser["IMU"]["hid_accel_axes"] = "9,9,9"
    parser["IMU"]["hid_gyro_signs"] = "1,0,1"
    parser["IMU"]["gyro_bias"] = "nan,0,0"
    parser["IMU"]["hid_gyro_scale"] = "5,0,1"
    changed = config.complete_missing(parser)
    imu = parser["IMU"]
    assert imu["source"] == "sensor"
    assert imu["sine_axis"] == "2"
    assert imu["sine_amp"] == "4096"
    assert imu["sine_period_s"] == "2.0"
    assert imu["step_raw"] == "5899"
    assert imu["accel_axes"] == "2,0,1"
    assert imu["gyro_signs"] == "-1,-1,1"
    assert imu["hid_accel_axes"] == "0,1,2"
    assert imu["hid_gyro_signs"] == "1,1,1"
    assert imu["gyro_bias"] == "0.0,0.0,0.0"
    assert imu["hid_gyro_scale"] == "1.0,1.0,1.0"
    assert any(c.startswith("IMU.") for c in changed)


def test_imu_section_keeps_valid():
    import config

    parser = _fresh()
    parser["IMU"]["source"] = "step"
    parser["IMU"]["gyro_bias"] = "0.01,-0.02,0.0"
    parser["IMU"]["hid_gyro_scale"] = "0.5,1.0,2.0"
    before = config.complete_missing(parser)
    assert not [c for c in before if c.startswith("IMU.")]
    assert parser["IMU"]["source"] == "step"
    assert parser["IMU"]["gyro_bias"] == "0.01,-0.02,0.0"
    assert parser["IMU"]["hid_gyro_scale"] == "0.5,1.0,2.0"


def test_apply_gyro_deadband_zeroes_small_axes() -> None:
    """不感帯はジャイロのみ・各軸独立。加速度と閾値超えは残す。"""
    from core.imu_wave import apply_gyro_deadband

    sample = (100, -200, 4096, 20, -49, 50)
    assert apply_gyro_deadband(sample, 0.0) == sample
    # 50raw未満だけ0（20→0、-49→0、50は残る）
    assert apply_gyro_deadband(sample, 50.0) == (100, -200, 4096, 0, 0, 50)
    assert apply_gyro_deadband(sample, 1000.0) == (100, -200, 4096, 0, 0, 0)


def test_sample_transform_identity_and_signs() -> None:
    """軸・符号変換（接頭辞なし＝SDL路）。恒等ならNone、指定時は軸と符号を適用。"""
    from ImuPanel import ImuPanel

    ident = {
        "accel_axes": "0,1,2",
        "accel_signs": "1,1,1",
        "gyro_axes": "0,1,2",
        "gyro_signs": "1,1,1",
    }
    assert ImuPanel._sample_transform(ident) is None

    signs = dict(ident)
    signs["gyro_signs"] = "1,-1,-1"
    fn = ImuPanel._sample_transform(signs)
    assert fn is not None
    assert fn((100, 200, 300, 10, 20, 30)) == (100, 200, 300, 10, -20, -30)

    axes = {
        "accel_axes": "2,0,1",
        "accel_signs": "-1,-1,1",
        "gyro_axes": "2,0,1",
        "gyro_signs": "-1,-1,1",
    }
    fn2 = ImuPanel._sample_transform(axes)
    assert fn2 is not None
    assert fn2((1, 2, 3, 4, 5, 6)) == (-3, -1, 2, -6, -4, 5)


def test_sample_transform_hid_uses_own_keys() -> None:
    """HID直結は hid_* キーだけを見る。SDL用の既定値に引きずられない。"""
    from ImuPanel import ImuPanel

    sdl_defaults = {
        "accel_axes": "2,0,1",
        "accel_signs": "-1,-1,1",
        "gyro_axes": "2,0,1",
        "gyro_signs": "-1,-1,1",
        "hid_accel_axes": "0,1,2",
        "hid_accel_signs": "1,1,1",
        "hid_gyro_axes": "0,1,2",
        "hid_gyro_signs": "1,1,1",
    }
    assert ImuPanel._sample_transform(sdl_defaults, hid=True) is None
    # SDL路は従来どおり接頭辞なしキーで回転が掛かる。
    assert ImuPanel._sample_transform(sdl_defaults) is not None

    # hid_* が無い旧プロファイルでも恒等（None）へ倒す。
    legacy = {k: v for k, v in sdl_defaults.items() if not k.startswith("hid_")}
    assert ImuPanel._sample_transform(legacy, hid=True) is None

    # hid_* を明示すればHIDでも反転を掛けられる。
    flipped = dict(sdl_defaults)
    flipped["hid_gyro_signs"] = "1,-1,-1"
    fn = ImuPanel._sample_transform(flipped, hid=True)
    assert fn is not None
    assert fn((100, 200, 300, 10, 20, 30)) == (100, 200, 300, 10, -20, -30)
