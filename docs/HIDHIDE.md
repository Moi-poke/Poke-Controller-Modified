# HidHide とコントローラ入力

Switch Pro コントローラ（互換機を含む）の HID ジャイロとパッド入力を
同じアプリで使うには、Steam / SDL に同じ HID を触らせない必要がある。
Steam は USB の Switch Pro を simple report mode（0x3F）へ切り替える
ことがあり、HID 側が 0x30 を待っていると数秒間入力が止まる（SDL #3450）。
HidHide は対象デバイスを他プロセスから隠し、許可したアプリだけに
HID を見せる。

## 前提

- HidHide は **手動でインストール**する。アプリはカーネルドライバを
  自動インストールしない。
- 公式配布: <https://github.com/nefarius/HidHide/releases/latest>
- Windows 10/11 64-bit。管理者権限が必要。
- インストール後、必要なら再起動する。

## 設定

1. `HidHideCLI.exe --dev-gaming` で対象の `deviceInstancePath` と
   `baseContainerDeviceInstancePath` を調べる。
2. アプリの Python 実行ファイルを許可する。venv の python は
   uv の base python と同一実体のことがあるため、両方登録する。

```text
HidHideCLI.exe --app-reg "C:\path\to\.venv\Scripts\python.exe"
HidHideCLI.exe --app-reg "C:\Users\<user>\AppData\Roaming\uv\python\<...>\python.exe"
```

3. HID 子と USB 親を hide する（XInput の回り込み対策）。

```text
HidHideCLI.exe --dev-hide "HID\VID_057E&PID_2009&MI_00\..."
HidHideCLI.exe --dev-hide "USB\VID_057E&PID_2009\..."
HidHideCLI.exe --cloak-on
```

4. 設定後、コントローラを挿し直す（またはデバイスを無効→有効）。
5. `HidHideCLI.exe --cloak-state --dev-list --app-list` で確認する。

GUI の HidHide Configuration Client でも同じ設定ができる。

## アプリ側の挙動

- HidHide が **ready**（cloak-on / アプリ許可 / Switch HID を hide）の
  ときだけ、HID 直結のパッド・ジャイロを使う。
- 未導入・未設定なら、Switch HID のパッド/ジャイロは無効になり、
  コントローラタブに案内を出す。Xbox/SDL コントローラは引き続き使える。
- 通常は HID 有線のみ。SDL をデバッグ用に解禁するときだけ、起動前に
  `POKECON_SDL_DEBUG=1` を設定する。

## 注意

- HidHide の許可リストは実行ファイル単位。Python を許可すると、
  同じ Python を使う別スクリプトも隠したデバイスを見られる。配布時は
  専用 exe にするか、許可対象を絞る。
- 一時的に解除する: `HidHideCLI.exe --cloak-off`
- 元に戻す: `--dev-unhide` / `--app-unreg`、または HidHide をアンインストール。
