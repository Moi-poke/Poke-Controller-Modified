# IMU（ジャイロ）経路

`AGENTS.md` から分離したサブシステム固有の注意。IMU・HID・ジャイロ・0x30/0x40 に触るときだけ読む。

- PC→Pico は `T_IMU_SAMPLE`(0x04/LEN12, 6×int16-LE = ax,ay,az,gx,gy,gz) を5ms間隔。PCの0x30は1レポートに5ms間隔の3サンプルを持つので、リーダーは3スロットを順に供給し（`read_next()`）、FWが直近3件を保持して0x30のスロット0..2へ古い順に載せる。**0x30の送出周期は15ms固定**（これより速いとコンソールがジャイロを過積分する。`jyapayne/switch-pico` の実装註記）。
- **0x40の引数はIMU形式の選択**（実測: FW 4.51→`02`=圧縮クォータニオン、3.72→`01`=RAW）。FWは引数を`imu_mode`として保持し、1ならRAW 36B、2なら NS-LIB-HID `ns_mode_2_s` 同形の36B（accel y,x,z＋最大成分基準の30bitクォータニオン＋11bit ms）を返す。`3`は未解明として仮定しない。**現行の実機検証は 3.72＋RAW**（Mario Tennis / Splatoonで確認）。4.51を名乗る場合はmode2のクォータニオン経路が必須。
- 有線0x30のIMUは既定でrole0のみ（`usb_imu_wired_role_ok`）。role1/2で有線ジャイロを試す診断は `JOY_WIRED_IMU_ALL=1`。FW版のA/Bは `JOY_USB_FW_JOY_OLD=1`（Joy役を3.72に）。
- role2の軸写像はFW側で固定（`k_joy_negate_axis`: 加速度・ジャイロともY,Z反転）。SPI校正値の回転だけ `JOY_SPI_CAL_ROT`（0=なし/1=YZ/4=XZ）で切り分けられる。PC側 `[IMU] accel_*/gyro_axes・signs` は恒等が前提（HID直結でも適用される）。
- `[IMU] gyro_dead_dps` は送出直前のジャイロ各軸に効く（`core.imu_wave.apply_gyro_deadband`）。`gyro_scale` は実測用トリム。**加工後の値を返さないと不感帯が送出に効かない**（過去に実際に踏んだ）。
- HIDリーダー (`core.nintendo_hid`) はプロセス共有・読み取り専用スレッドが最新0x30を保持する。`ImuPanel` は停止時に共有ハンドルを閉じない。読取失敗が続いたら自分で閉じ、パッド/IMU側が再オープンする（UF2書き込みでUSBが再列挙されるため必須）。
- IMUパネルの入力源は通常 **「パッドAPI」（SDL）／「HID直結」** の2つ。`sine`/`step`/`rest`/`manual`、反転・感度・詳細表示は**デバッグ**時だけ出す。ボタンは「開始/停止」。
