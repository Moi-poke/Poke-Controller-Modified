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
    parser["IMU"]["gyro_bias"] = "nan,0,0"
    changed = config.complete_missing(parser)
    imu = parser["IMU"]
    assert imu["source"] == "sensor"
    assert imu["sine_axis"] == "2"
    assert imu["sine_amp"] == "4096"
    assert imu["sine_period_s"] == "2.0"
    assert imu["step_raw"] == "5899"
    assert imu["accel_axes"] == "2,0,1"
    assert imu["gyro_signs"] == "-1,-1,1"
    assert imu["gyro_bias"] == "0.0,0.0,0.0"
    assert any(c.startswith("IMU.") for c in changed)


def test_imu_section_keeps_valid():
    import config

    parser = _fresh()
    parser["IMU"]["source"] = "step"
    parser["IMU"]["gyro_bias"] = "0.01,-0.02,0.0"
    before = config.complete_missing(parser)
    assert not [c for c in before if c.startswith("IMU.")]
    assert parser["IMU"]["source"] == "step"
    assert parser["IMU"]["gyro_bias"] == "0.01,-0.02,0.0"
