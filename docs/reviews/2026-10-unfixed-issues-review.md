# Poke-Controller-Modified 未修正問題レビュー依頼（改訂2版）

この文書は Markdown のままコピーして貼り付けてください。コードはすべて ``` で囲んであります。
見出しの重大度は**私の暫定**です。断定はあなたの判定を待ちます。

## 0. あなたへの依頼

あなたはローカルファイルを読めない前提です。以下に貼ったコードと実測だけを根拠に判断してください。書かれていない実装を推測で補わないでください。判断に足りない情報があれば「情報不足」と明示し、何が足りないかを書いてください。

対象は tkinter 製 Windows アプリ「Poke-Controller-Modified」（Switch を USB キャプチャ映像＋シリアル経由の仮想コントローラ入力で自動操作する）と、対になる Raspberry Pi Pico 2 W ファーム「switch-bcon」です。以下 15 件は読取専用の調査で挙がった**未修正**の問題で、私はそのうち主要なものを現物コードと実行で裏取りしました。裏取りの状態は各項目の【検証状態】に書きます。

## 1. 前提（プロジェクトの契約）

- 層構成: `SerialController/core/`（GUI 非依存の純ロジック）/ `services/`（手順。tkinter 禁止）/ `ui/`（tkinter パネル。`Window` の import 禁止）/ `Window.py`（組み立てのみ。起動時に一度だけ `os.chdir(BASE_DIR)`）。
- 送信経路は 2 系統:
  - legacy テキスト: 115200bps、`"0x00XX hat lx ly rx ry"` 形式の行を同期送信。
  - Pico バイナリ v3（`switch-bcon`）: 1Mbps。フレームは `[SYNC=0xAB][TYPE][LEN][PAYLOAD][SEQ][CRC8]`、CRC-8/SMBUS は TYPE..SEQ 範囲。STATE は「8ms ごとに現在姿勢を1発送り続ける」プロトコル。SEQ は方向別 mod 256。
- live 対応 transport（capability が `PICO_LIVE_STATE` / `BCON_STATE`）では、姿勢は `Sender` の scheduler キューへ積まれ、**常駐 worker スレッド**が 8ms ごとに取り出して送ります（同期送信しません）。以下「live worker」と呼びます。bcon ではさらに transport 自身の 120Hz ループ（`start_live_loop`）もあります。
- 規約（破れていれば指摘対象）:
  1. ワーカースレッドから Tk ウィジェット / Tk 変数 / `after()` を触らない。`DropOldestQueue` に積み、GUI スレッドが消化する。
  2. 公開操作から例外を漏らさない（失敗は bool + ログ）。
  3. リソースパスは `BASE_DIR` から解決する（cwd 依存は禁止）。
  4. `except Exception` には必ずログを付ける（黙って握るのは規約違反）。GUI への `print` は「利用者の次の行動が変わる失敗」のみ。
  5. コメント・利用者向け文字列は日本語。
- 検証状況: ホストは lint / format / mypy / 層境界 / 公開API / pytest 2139 件が緑。ファームはホストテスト 13 本が緑。**実機（COM ポート・Switch・キャプチャボード）での確認は未実施**です。
- 用語: 「revision」= 姿勢が変わるたびに増える世代番号。「材料（steps）」= 記録から作る中間表現（dict 列）で、その後 Blockly ブロック → Python コードへ変換される。「クリップ」= スティックを倒していた 1 区間。

## 2. 出力してほしい形式

各項目について:

1. **判定**: 実バグ / 仕様どおり / 情報不足
2. **根拠**: 貼られたコードのどこがどう効いて症状になるか。行番号はスニペット先頭に「ファイル:開始-終了」として書いてあるので、引用は**関数名＋スニペット内の該当行の文言**で示してください。
3. **重大度**: 高 / 中 / 低 と、その理由（発生頻度・回復不能か・データを失うか）
4. **推奨修正**: 最小差分。可能ならそのまま貼れるコード。私の修正案が不適切なら却下理由も
5. **見落とし**: 私の案が生む新しい問題（競合・順序・他経路への波及）
6. **追加で確認すべきこと**: ローカルでしか確認できない事項があれば、その確認手順

**出力が長くなる場合は 3 回に分けてください**: 「項目 1〜5」「項目 6〜10」「項目 11〜15 ＋ 参考 A/B ＋ 優先順位表」。各回の末尾に「続きあり」と明記してください。最後に全項目の優先順位表（項目 / 重大度 / 修正コスト / 推奨度）を付けてください。

---

## 項目 1 【高・私の暫定】記録停止時に「押しっぱなしのボタン」が解放されない

### 症状
記録中にスティックを倒しつつボタン（例 ZR）を押し、**そのボタンを離す前に「記録を止める」**と、生成される Python コードが次の形で終わり、コマンド終了まで押しっぱなしになります。

```python
self.hold(Button.ZR, wait=0)
```

材料（steps）にも `hold` が 1 件あるだけで対応する `hold_end` がありません。押下→解放が両方記録されている通常ケースでは `hold` / `hold_end` の対が出ます。

【検証状態】別の読取専用調査が node 上の実物 Blockly で材料と生成コードまで実測。私は 3 つの現物コード（下記）を読み、機構が症状と整合することを確認しました。**自分の再実行はしていません。** 調査時のイベント列は「スティック L を倒す → Button.ZR PRESS → そのまま記録停止（ZR の RELEASE は無し）」です。

### 期待される挙動（リポジトリのドキュメント）
```text
docs/BLOCKLY_EDITOR.md:54 付近:
「クリップ内のボタン・十字キーは hold/holdEnd になる。押下区間がクリップの
  境目にかかるものは、クリップを広げて丸ごと含める」
```
設計意図としても「片側だけ材料に残ると、押していない物を離す／押しっぱなしが残る形になる」と明記されています。

### 該当コード（現物・抜粋）

```python
# services/blockly_record.py:493-512
def _button_spans(
    buttons: list[tuple[float, str, bool]], t0: float
) -> list[tuple[float, float | None]]:
    """ボタン/Hat の押下区間 [(押下 at, 解放 at|None)] を返す。

    解放が記録に無い（押しっぱなしのまま停止した）ものは None。
    """
    opened: dict[str, float] = {}
    spans: list[tuple[float, float | None]] = []
    for t_ms, name, on in buttons:
        at = t0 + t_ms / 1000.0
        if on:
            opened[name] = at
        else:
            press = opened.pop(name, None)
            if press is not None:
                spans.append((press, at))
    spans.extend((at, None) for at in opened.values())
    spans.sort()
    return spans
```

```python
# services/blockly_record.py:515-564（_split_clips の後半。押下区間をクリップへ含める）
    # ボタン/Hat の押下区間を、またぐならクリップへ含める。
    spans = _button_spans(buttons, t0)          # -> [(press_at, release_at | None)]
    for _ in range(len(spans) + 1):
        grew = False
        for press_at, release_at in spans:
            tail = end if release_at is None else release_at   # ★ここ
            if press_at > end or tail < start:
                continue
            new_start, new_end = min(start, press_at), max(end, tail)
            if new_start != start or new_end != end:
                start, end = new_start, new_end
                grew = True
        if not grew:
            break
    return [(start, end)]
```

```python
# services/blockly_record.py:567-596
def _covered_events(events, clip_start, clip_end, moved) -> dict[int, bool]:
    """クリップの材料が再現するイベント id を返す。

    対象は 2 つ。クリップ内の押下から始まるボタン/Hat（hold/holdEnd に
    なる）と、トラックが残った側（moved）のスティック押下〜解放（stick
    区間と解放材料になる）。press 変換へ回すと同じ区間が二度再生され、
    記録に無い押し直し・倒し直しが挟まる。
    """
    covered: dict[int, bool] = {}
    opened: dict[tuple[str, str], InputLog.InputEvent] = {}
    for ev in events:
        if ev.action not in ("PRESS", "RELEASE"):
            continue
        key = (ev.kind, ev.name)
        if ev.action == "PRESS":
            opened[key] = ev
            inside = clip_start <= ev.at <= clip_end
            side = _stick_side(ev) if ev.kind == "stick" else ""
            if inside and (ev.kind != "stick" or side in moved):
                covered[id(ev)] = True          # ★解放が無くても covered になる
        else:
            press = opened.pop(key, None)
            if press is not None and covered.get(id(press)):
                covered[id(ev)] = True
    return covered
```

```python
# services/blockly_record.py:634-647（この関数の中の 704-718 行が hold/hold_end を作る）
def _clip_to_steps(
    sticks: list[tuple[str, float, float, float]],
    buttons: list[tuple[float, str, bool]],
    releases: dict[str, list[float]],
    t0: float,
    clip_start: float,
    clip_end: float,
    eps: float,
) -> tuple[list[dict[str, Any]], set[str]]:
    """1 クリップを stick/hold/解放の材料へ変換する。

    返すのは (材料、トラックが残った側)。残らなかった側の操作は呼び側が
    press 変換で拾う（材料から消さない）。
    """
```

```python
# services/blockly_record.py:704-718（_clip_to_steps の中。hold / hold_end を作る）
    steps: list[dict[str, Any]] = []
    # クリップ内のボタン/Hat は hold/holdEnd（待ちなし）へ。hold の待ちを
    # 入れると押下が延び、以後の区間がその分ずれる。
    for t_ms, name, on in buttons:
        at = t0 + t_ms / 1000.0
        if not (clip_start <= at <= clip_end):
            continue
        entry: dict[str, Any] = {
            "kind": "hold" if on else "hold_end",
            "target": name,
            "at_ms": round((at - clip_start) * 1000.0),
        }
        if on:
            entry["wait"] = 0
        steps.append(entry)
```

補足: `buttons` は記録されたボタン/Hat イベント列で、`on=True` が PRESS、`on=False` が RELEASE です。RELEASE が記録に無ければ `on=False` の要素が存在しないため、`_clip_to_steps` は `hold` だけを作ります。入口は純関数 `build_motion_steps(events, eps)`（blockly_record.py:417）です。

### 変更の経緯
この挙動は**未コミットの変更**で入りました。変更前（HEAD）は同じ PRESS を press 変換側で落としていたため「押されない」で済んでいました。今回 motion 経路へ一本化した結果「押しっぱなしが残る」側へ変わったため、既存テストはこの経路を守っていません。

### 私の修正案
`_clip_to_steps` の最後で「クリップ内に `hold` があり、同じ target の `hold_end` が無い」ものを検出し、クリップ終端（`at_ms = clip_len`）に `hold_end` を足す。
代替案: `_button_spans` の `release_at=None` を「記録末尾の時刻」として扱い、クリップをそこまで広げる。

### 聞きたいこと
- どちらの案が妥当か。あるいは「記録停止時に押下中だった入力は材料に含めない（press 変換に戻す）」方が元の設計に近いか。
- **押下がクリップ終端の直前にある場合**、`hold` と（私の案で足す）`hold_end` の `at_ms` がほぼ同じ値になります。8ms 周期の送信では押下が 1 回も線に出ない可能性があります。この場合どうすべきか（押下を 1 スロット分遅らせる、クリップを押下より前に広げる、押しっぱなしを許容する、など）。
- 押下がクリップ開始前に始まっているケース、複数ボタンが同時に押しっぱなしのケースで、私の案が壊れないか。

---

## 項目 2 【中・私の暫定】軌跡圧縮の ε 保証が守られない（原因を実測で特定済み）

### 症状
`core/motion.py` は「waypoint 列に圧縮しても、8ms 格子の各時刻で復元した XY は元の XY から ε 以内」を保証すると docstring に書いています。ε=2.5 の既定で、次の入力では実測 11.894 の誤差が残ります（後述のダンプはすべて私が再実行して取得）。

### 実測ダンプ（私の再実行。これが決定的な情報です）

```text
入力サンプル (t_ms, x, y)  ※ t0 からの相対。3 点とも 8ms 格子に載っている
  [(0, 131, 135), (8, 35, 151), (16, 66, 193)]

compress_track 内部の track（8ms 格子化＋前値ホールド後。compress_track:242-255）
  [(0.0, 131, 135), (8.0, 35, 151), (16.0, 66, 193)]

量子化後の quant (t_ms, r, th)
  [(0, 8, -166), (8, 96, -166), (16, 90, -134)]

_max_error_xy(track, quant) = 11.8945

_refine_quantized 呼び出し: eps=2.5, n_track=3, n_quant=3
  最悪時刻: t=0.0   元 (131,135)   復元 (120.24, 129.94)   誤差 11.894
  その時刻に点はあるか: True
  → break（点を 1 つも足さずに返る）
_refine_quantized の戻り: [(0,8,-166), (8,96,-166), (16,90,-134)]（変化なし）
compress_track の最終出力: [(0, 8, -166), (8, 96, -166), (16, 90, -134)]

原因の同定（すべて実測と一致）
  sample A = (131,135) → r = 7.616, th = -66.801
  DEADZONE_R = 8.0 なので A は「デッドゾーン内」
  → _fill_theta が A の θ を「次の有効な θ」= B の -166° で置換
  → quant の点は (r=round(7.616)=8, θ=-166) になり、XY は (120.24, 129.94)
  → 元の (131,135) との誤差 11.894（実測と完全一致）
  ※ 極座標補間の誤差ではありません。格子の全時刻での最悪値はこの 1 点だけです。
  ※ 入力が 16ms 間隔（格子が前値ホールドで埋まる）でも、同じ 11.894 が t=8 に出ます（同じ機構）。
```

ε を 1.0 / 0.5 に下げても出力が変わらないのは、`compress_track:269-270` の
`if eps <= QUANT_FLOOR: return quant`（QUANT_FLOOR = 1.2）で再検証自体が飛ぶためで、裏付けにはなりません。

### ε の基準（ご指摘の点）
ε は**元サンプルではなく、8ms 格子化＋前値ホールド後の track** に対して測られます（`compress_track:271` が `track` を渡している。track の作り方は同 242-255）。入力が格子に載っている今回は両者一致します。

### 該当コード（現物・抜粋）

```python
# core/motion.py:242-274（格子化と量子化後の再検証）
    # 8ms 格子化＋前値ホールド。
    grid: dict[int, tuple[float, float]] = {}
    for at, x, y in samples:
        slot = round((at - t0) / SLOT_S)
        grid[slot] = (float(x), float(y))
    if not grid:
        return []
    lo, hi = min(grid), max(grid)
    track: list[tuple[float, float, float]] = []
    last = grid[lo]
    for slot in range(lo, hi + 1):
        if slot in grid:
            last = grid[slot]
        track.append((slot * SLOT_S * 1000.0, last[0], last[1]))
    ...
    out: list[Waypoint] = []
    ideal: dict[float, tuple[float, float]] = {}
    for run in _split_at_center(track):
        points, shape = _compress_run(run, eps)
        out.extend(points)
        ideal.update(shape)
    ...
    quant = [Waypoint(round(w.t), round(w.r), round(w.th)) for w in out]
    if eps <= QUANT_FLOOR:
        return quant
    if _max_error_xy(track, _as_float(quant)) > eps:        # ★track で測る
        quant = _refine_quantized(track, quant, eps, ideal)
    return quant
```

```python
# core/motion.py:173-196（_fill_theta。r < DEADZONE_R の点の θ を隣から借りる）
def _fill_theta(
    polars: list[tuple[float, float, float]],
    raw_th: list[float],
) -> list[float]:
    """r が小さい点の θ を、有効な隣の θ で埋める（設計 ID:006）。

    前向きに埋めたあと、後ろ向きの有効値で上書きする。中心付近の θ は
    「意味のない値」なので、倒れてきた/倒れていく先の向きを使う。
    """
    fixed = list(raw_th)
    fwd: float | None = None
    for i, (_t, r, th) in enumerate(polars):
        if r >= DEADZONE_R:
            fwd = th
        elif fwd is not None:
            fixed[i] = fwd
    bwd: float | None = None
    for i in range(len(polars) - 1, -1, -1):
        _t, r, _th = polars[i]
        if r >= DEADZONE_R:
            bwd = fixed[i]
        elif bwd is not None:
            fixed[i] = bwd
    return fixed
```

```python
# core/motion.py:136-170（_rdp_polar。極座標の折れ線で復元した XY と元 XY の距離で間引く）
def _rdp_polar(pts, eps) -> list[int]:
    """RDP で残す点の添字を返す。誤差は XY 距離（時間方向）で測る。

    pts は [(t, r, θ, x, y)]。θ は展開済みで出力と同じ表現、(x, y) は元の
    サンプル。両端は必ず残す。
    """
    ...
        seg = [Waypoint(pts[lo][0], pts[lo][1], pts[lo][2])]
        seg.append(Waypoint(pts[hi][0], pts[hi][1], pts[hi][2]))
        worst, at = 0.0, -1
        for i in range(lo + 1, hi):
            t, _r, _th, x, y = pts[i]
            r, th = _restore_polar(seg, t)
            px, py = polar_to_xy(r, th)
            err = math.hypot(px - x, py - y)
            if err > worst:
                worst, at = err, i
        if worst > eps and at >= 0:
            keep[at] = True
            stack.append((lo, at))
            stack.append((at, hi))
    return [i for i, k in enumerate(keep) if k]
```

```python
# core/motion.py:280 付近（_refine_quantized の核。整形して引用）
def _refine_quantized(track, quant, eps, ideal=None):
    if ideal is None:
        ideal = {t: xy_to_polar(x, y) for t, x, y in track}
    out = [Waypoint(round(w.t), round(w.r), round(w.th)) for w in quant]
    if len(out) < 2 or eps <= QUANT_FLOOR:
        return out
    for _ in range(REFINE_MAX_ROUNDS):          # REFINE_MAX_ROUNDS = 4
        qf = _as_float(out)
        worst, at_t = 0.0, 0.0
        for t, x, y in track:                   # 格子の全時刻
            r, th = _restore_polar(qf, t)
            px, py = polar_to_xy(r, th)
            err = math.hypot(px - x, py - y)
            if err > worst:
                worst, at_t = err, t
        if worst <= eps:
            break
        if any(w.t == round(at_t) for w in out):
            # その時刻には既に点がある。残る差は丸めの下限で、縮まない。
            break                               # ★ここで打ち切る
        pr = ideal.get(at_t)
        if pr is None:
            break
        out.append(
            Waypoint(float(round(at_t)), float(round(pr[0])), float(round(pr[1])))
        )
        out.sort(key=lambda w: w.t)
    return out
```

### 私の修正案（実測に基づく改訂案）
`_refine_quantized` の打ち切りを「同じ時刻に点がある → break」から
**「同じ時刻の点の値 (r, θ) を ideal の値で置き換える。置き換えで誤差が減らない場合だけ break」**に変える。
この例では t=0 の点が (8, -166) → (8, round(-66.801) = -67) になり、XY は (131.1, 135.4) 前後で誤差は 1 未満に落ちる見込みです（r の丸め分のみ）。

### 聞きたいこと
- この診断（デッドゾーンの θ 借用＋丸めが誤差源）で正しいか。他に効いている要因はないか。
- 上の改訂案で十分か。それとも根本原因は `_fill_theta` の「隣の θ を借りる」規則そのものか（借りた θ で XY が最大 2×DEADZONE_R 動く）。規則を変える場合の代替案は何か。
- 打ち切り条件（「減らないなら break」）をどう判定するのが安全か（1 回置き換えて再測し、改善が閾値未満なら止める、など）。
- `QUANT_FLOOR` の早期 return は「保証できない ε を黙って受ける」形になっています。docstring の保証文言と実装のどちらを直すべきか。

---

## 項目 3 【中・私の暫定】bcon の STATUS-ACK 判定が「自動 STATUS」で成立してしまう

### 症状
`switch-bcon` の transport は「要求を送り、STATUS が来たら受理された」と判定します（期限まで 80ms 間隔で再送）。ところが `hello()` が auto_status を要求する（flags bit0=0x01）ため、Pico は**非要求（unsolicited）の STATUS を 1 秒ごと**に送ります。加えて **4 種類のコマンドは受理時に即時 STATUS を積みません**（下記ファーム抜粋）。結果、Pico が要求を受理していなくても True が返り得ます。

【検証状態】次の 3 点は私が現物で確認しました: (a) `_send_until_status` の待ち行列が「T_STATUS なら何でも」であること、(b) ファームで即時 STATUS を積むコマンドと積まないコマンドの別、(c) `_tx_seq` が STATE 送出と共用で、`last_seq` が全受理フレームで更新されること。動的な再現（偽シリアルに自動 STATUS だけを流す実験）は別の調査が行い、`request_reconnect(timeout=1.0) -> True`（送出 5 発、STATUS の last_seq は 0）を報告しています。私は再実行していません。

### 該当コード（現物）

```python
# core/transport/bcon.py:1958-1994
    def _send_until_status(self, type_: int, payload: bytes, limit: float, label: str) -> bool:
        """STATUS-ACK が来るまで同じフレームを繰り返し送る。

        Pico は baud hunt 中、有効frame 2連続で確定し、確定した瞬間に
        inbox を捨てる。そのため1発では「baud確定」と「命令実行」を同時に
        満たせず、1発だけ送ると必ず失敗する。hunt は 150ms x 4スロット =
        600ms 周期で回っているので、1周以上を覆うだけ再送する。ACK は
        購読側で拾う。
        """
        found: dict[str, Any] = {}
        done = threading.Event()
        entry = ((T_STATUS,), done, found)      # ★「T_STATUS なら何でも」ACK
        with self._rx_lock:
            self._rx_waiters.append(entry)
        try:
            deadline = time.monotonic() + limit
            sent = 0
            while not done.is_set():
                if time.monotonic() >= deadline:
                    return False
                if not self._send_session_frame(type_, payload, label):
                    return False
                sent += 1
                done.wait(0.08)
            self._logger.debug(f"{label}: {sent}回送ってACKを受けた")
            return True
        finally:
            with self._rx_lock:
                if entry in self._rx_waiters:
                    self._rx_waiters.remove(entry)
```

```python
# core/transport/bcon.py:1241-1243（auto_status を要求している）
                    ok = self._send_session_frame(
                        T_HELLO, bytes([PROTO_VER, 0x01]), "bcon:HELLO"
                    )
```

```python
# core/transport/bcon.py:432-443（STATE 送出も _tx_seq を消費する）
        with self._write_lock:
            with self._lock:
                seq = self._tx_seq
                self._tx_seq = (self._tx_seq + 1) & 0xFF
                ...
            frame = frame_build(T_STATE, payload, seq)
```

```c
// Switch-bcon/src/proto/dispatch.c:94-107（受理時に即時 STATUS を積む側）
        case T_STATUS_REQ:
            ob_push(s, ACT_SEND_STATUS, 0);
            ob_push(s, ACT_SEND_PLAYER_INFO, 0);
            return V3_IGNORE;
        case T_BAUD_SET: {
            ...
            // B §3: 旧rateのままACK相当 (STATUS即時)。切替はguard後に双方。
            ob_push(s, ACT_SEND_STATUS, 0);
            return V3_IGNORE;
        }
```

```c
// Switch-bcon/src/proto/dispatch.c:109-147（BOOTSEL / RECONNECT も即時 STATUS あり）
        case T_BOOTSEL: {
            ...
            // WIRED/BAUD_SETと同型: 旧rateのままACK相当 (STATUS即時)。
            // 発火は500ms後の再起動で適用する (main.cが実行)。
            ob_push(s, ACT_SEND_STATUS, 0);
            return V3_IGNORE;
        }
        case T_RECONNECT:
            // 待機状態の解除要求: LEN0のみ。実行は main.c の exec_fx
            // (BTコールバック内では行わない)。旧rateのままACK相当。
            if (len != 0) { s->errcode = ERR_BAD_LEN; return V3_IGNORE; }
            s->fx = FX_RECONNECT;
            s->fx_arg = 0u;
            ob_push(s, ACT_SEND_STATUS, 0);
            return V3_IGNORE;
```

```c
// Switch-bcon/src/proto/dispatch.c:62-93（この 3 つは受理時に即時 STATUS を積まない）
        case T_CAPTURE_START: {
            uint8_t sec;
            if (len != 1 || payload == NULL) { s->errcode = ERR_BAD_LEN; return V3_IGNORE; }
            sec = payload[0];
            if (sec < 1u || sec > 60u) { s->errcode = cfg_err(type); return V3_IGNORE; }
            s->cap_seconds = sec;
            s->fx = FX_CAPTURE_START;
            s->fx_arg = sec;
            return V3_IGNORE;
        }
        case T_BEACON_START:
            if (!s->cap_valid) { s->errcode = cfg_err(type); return V3_IGNORE; }
            s->fx = FX_BEACON_START;
            return V3_IGNORE;
        ...
        case T_WIRED_MODE: {
            uint8_t v;
            if (len != 1 || payload == NULL) { s->errcode = ERR_BAD_LEN; return V3_IGNORE; }
            v = payload[0];
            if (v > 1u) { s->errcode = cfg_err(type); return V3_IGNORE; }
            s->wired_val = v;
            s->fx = FX_WIRED_MODE;
            s->fx_arg = v;
            return V3_IGNORE;
        }
```

```c
// Switch-bcon/src/proto/dispatch.c:121-130（EMULATE_MODE も即時 STATUS なし）
        case T_EMULATE_MODE: {
            uint8_t v;
            if (len != 1 || payload == NULL) { s->errcode = ERR_BAD_LEN; return V3_IGNORE; }
            v = payload[0];
            if (v > 2u) { s->errcode = cfg_err(type); return V3_IGNORE; }
            s->emulate_val = v;
            s->fx = FX_EMULATE_MODE;
            s->fx_arg = v;
            return V3_IGNORE;
        }
```

```c
// Switch-bcon/src/main.c:350（全受理フレームで last_seq を更新する。STATE も含む）
    g_s.last_seq = seq;
```

```c
// Switch-bcon/src/main.c:1215-1224（1 秒ごとの自動 STATUS）
    if (g_vs.auto_status) {
        send_status();
    }
}

static void stats_handler(btstack_timer_source_t *ts) {
    stats_print();
    btstack_run_loop_set_timer(ts, 1000);
    btstack_run_loop_add_timer(ts);
}
```

```c
// Switch-bcon/src/main.c:772-799（STATUS の中身。last_seq は最後に受理した PC->Pico seq）
static void send_status(void) {
    ...
    mutex_enter_blocking(&g_m);
    last_seq = g_s.last_seq;
    crc_err = g_s.crc_err;
    drop_ev = g_s.drop_ev;
    overruns = g_s.overruns;
    perrcode = g_s.perrcode;
    mutex_exit(&g_m);
    ...
    v3_pack_status(flags, last_seq, (uint16_t)crc_err, (uint16_t)drop_ev,
                   (g_vs.errcode != 0u) ? g_vs.errcode : perrcode, payload);
    uart_tx_frame(T_STATUS, payload, 7);
}
```

```python
# BconSetup.py:380-394（接続時は両方の送出を止めてから会話する）
        # まず STATE を止める。120Hz loop と Sender 側 worker の双方を
        # 落とす。片方だけだと残った方が送り続ける。
        try:
            stop_loop = getattr(transport, "stop_live_loop", None)
            if callable(stop_loop):
                stop_loop()
        except Exception:
            pass
        if sender is not None:
            try:
                stop_worker = getattr(sender, "stopLiveWorker", None)
                if callable(stop_worker):
                    stop_worker()
            except Exception:
                pass
```

補足: `_last_status["last_seq"]` は現状 UI 表示にしか使っていません。仕様書は「ACK = STATUS」とだけ定めています。拒否時（`cfg_err(type)` → `ERR_*`）は即時 STATUS を積まないため、拒否は「ACK が来ない」形でしか見えません。

### 私の修正案（ご指摘を受けて撤回・再提案）
- 当初案「送った seq == STATUS の last_seq で照合する」は**撤回します**。ご指摘のとおり、`_tx_seq` は live（STATE）送出と共用で毎秒 125 回消費されるため 256 は約 2 秒で一周し、`main.c:350` により `last_seq` は「直近に受理された（多くは STATE の）seq」になります。照合は誤一致か取り逃がしになります。
- 再提案（層の分担）:
  1. ファーム: 受理時に即時 STATUS を積まない 4 コマンド（CAPTURE_START / BEACON_START / WIRED_MODE / EMULATE_MODE）にも `ob_push(s, ACT_SEND_STATUS, 0)` を足す。**拒否時にも即時 STATUS を積む**（errcode を載せて返す）。
  2. ホスト: 「STATUS が来た」ではなく「送信後に観測した STATUS の errcode が 0 か」で判定する。live 送出中は自動 STATUS が混ざるので、送信前後の errcode を比較するか、会話中は live を止める（BconSetup はすでに止めている。`send_beacon` 経路は止めていない）。
  3. それでも相関 ID が要るなら STATUS に専用フィールドを足す（プロトコル変更＝ PROTO_VER 更新）。

### 聞きたいこと
- 層の分担として 1/2/3 のどれを採るべきか。2 の「会話中は live を止める」を `send_beacon` にも強制するのは妥当か（利用者スクリプトから呼ばれるので、止め忘れの経路が残る）。
- `last_seq` を使わない前提で、ほかに「受理された」を確実に見分ける手段はあるか（crc16/drop16/errcode の差分、ACT_SEND_STATUS に type を載せる拡張、など）。
- 拒否時に即時 STATUS を返す変更は、baud hunt 中の「有効フレーム 2 連続で確定」という前提を壊さないか。

---

## 項目 4 【中・私の暫定】範囲キャプチャの押下が余白で未クランプ → 選んでいない領域が「保存しました」で保存される

### 症状
プレビューは 16:9 の映像を枠へ収めて中央に置くため余白（letterbox / pillarbox）ができます。Ctrl+Shift+ドラッグの**押下**が余白で起きると、キャプチャ座標が負値／画像幅超えのまま保持され、そのまま `crop_ax` になります。numpy のスライスは負値・超過を例外にしないため、選んでいない領域が保存されます。

```text
実測（ファイル書き込みなし。実メソッドを駆動。報告からの引用で、私は再実行していない）
letterbox (表示 1000x700、映像は y=68 から):
  表示 (100,10) -> (500,600) が capture (128,-74) -> (640,680)
  実際に保存されたスライス: (34, 512, 3)     ← 意図は 754 行
pillarbox (表示 2000x720、映像は x=360 から) の右余白押下:
  crop_ax=[340,300,1540,600]（画像幅は 1280）
  numpy が黙って切り詰め (300, 940, 3)        ← 意図は 1200 幅
左余白押下: スライス幅 0 -> imwrite 失敗 -> 「範囲キャプチャに失敗」（こちらは無害）
```
ドラッグ中の `MotionRangeSS` はクランプしており、`mouseCtrlLeftPress` も `clamp=True` を持ちます。`StartRangeSS` だけがクランプしていません。

【検証状態】コード読解は私が実施（下記すべて現物）。数値は別調査の実測で、私は再実行していません。

### 該当コード（現物）

```python
# GuiAssets.py:1091-1102
    def _eventToCapture(self, event: Any) -> tuple[int, int]:
        """マウスイベントの座標をキャプチャ座標へ直す。

        すべての入力がここを入口にして、以後はキャプチャ座標だけで扱う。
        倍率と余白はビューポートから毎回求める（リサイズ直後でもずれない）。

        クランプはしない。ここを通る値はフレームを切り出さない（四隅や
        中心だけ）ので、余白のクリックを映像の端に丸めるのは
        mouseCtrlLeftPress の責務。
        """
        return self._mapper().to_capture(event.x, event.y)
```

```python
# GuiAssets.py:1059（引数の有無で基準フレームが変わる）
def _mapper(self, frame: Any = None) -> CoordinateMapper:
```

```python
# GuiAssets.py:371-373（範囲キャプチャの初期値。ドラッグしなければ 0 のまま）
        self.min_x, self.min_y = 0, 0
        self.max_x, self.max_y = 0, 0
        self.ss = None
```

```python
# GuiAssets.py:1107-1125（StartRangeSS。クランプ無しで保持）
    def StartRangeSS(self, event: Any) -> None:
        """範囲選択を開始する。選択枠は最初からキャプチャ座標で持つ。"""
        self.ss = self.camera.readFrame(copy=True)
        ...
        self.min_x, self.min_y = self._eventToCapture(event)     # ★負値/超過が入る
        self._guide = RectState(
            x0=self.min_x, y0=self.min_y,
            x1=self.min_x + 1, y1=self.min_y + 1, visible=True,
        )
```

```python
# GuiAssets.py:1141-1153（MotionRangeSS は self.ss を基準にクランプしている＝非対称）
    def MotionRangeSS(self, event: Any) -> None:
        mapper = self._mapper(self.ss)
        capture_x, capture_y = mapper.to_capture(event.x, event.y)
        capture_width, capture_height = mapper.capture_size
        self.max_x = min(capture_width, max(0, capture_x))
        self.max_y = min(capture_height, max(0, capture_y))
        self._guide = replace(self._guide, x1=self.max_x + 1, y1=self.max_y + 1)
```

```python
# GuiAssets.py:1155-1202（ReleaseRangeSS。省略部はログ出力のみ。私が全文を確認）
    def ReleaseRangeSS(self, event: Any) -> None:
        """選択範囲を切り出して保存する。min/max はすでにキャプチャ座標。"""
        logger.info(
            "Mouse up: Show ({}, {}) / Capture ({}, {})".format(
                event.x, event.y, self.max_x, self.max_y,
            )
        )
        if self.min_x > self.max_x:
            self.min_x, self.max_x = self.max_x, self.min_x
        if self.min_y > self.max_y:
            self.min_y, self.max_y = self.max_y, self.min_y

        # ここで二度と変換しない。StartRangeSS / MotionRangeSS が
        # キャプチャ座標で持ち回しているので、二度目の変換は縮尺を
        # 二重に掛けることになる（余白のある表示では 2 回分ずれる）。
        crop_x0, crop_y0 = self.min_x, self.min_y
        crop_x1, crop_y1 = self.max_x, self.max_y
        saved = self.camera.saveCapture(
            crop=1,
            crop_ax=[crop_x0, crop_y0, crop_x1, crop_y1],       # ★そのまま切り出し
        )
        if saved:
            saved_to = getattr(self.camera, "capture_dir", "Captures")
            print(
                f"範囲キャプチャを保存しました: ({crop_x0}, {crop_y0})-"
                f"({crop_x1}, {crop_y1}) → {saved_to}"
            )
        else:
            print("範囲キャプチャに失敗しました（詳細はログファイル）")
        # 予約 ID は控えておく。終了時に残っていると破棄途中の Frame を
        # 触るため、stopCapture で取り消す。
        ...
        self._select_after_id = self.after(250, self._clearGuide)
```

```python
# core/Camera.py:203-215（検証なしのスライス）
def _crop_image(image: np.ndarray, crop: Any, crop_ax: list | None) -> np.ndarray:
    """crop 指定に従って切り出す。crop=1 は座標指定、crop=2 は幅高さ指定。"""
    if crop is None:
        return image

    ax = crop_ax if crop_ax else [0, 0, CAPTURE_SIZE[0], CAPTURE_SIZE[1]]
    if crop in (1, "1"):
        # ax = [x1, y1, x2, y2]
        return image[ax[1] : ax[3], ax[0] : ax[2]]
    if crop in (2, "2"):
        # ax = [x, y, w, h]
        return image[ax[1] : ax[1] + ax[3], ax[0] : ax[0] + ax[2]]
    return image
```

補足: `_eventToCapture` の docstring にある「この経路の値はフレームを切り出さない」は、少なくとも `StartRangeSS` には成り立ちません。また `StartRangeSS` は `_mapper()`（現在のフレーム基準）、`MotionRangeSS` は `_mapper(self.ss)`（押下時に固定したフレーム基準）を使っており、基準がずれる余地があります。

### 聞きたいこと
- クランプを入れる場所は `_eventToCapture`（全入力共通）と `StartRangeSS` のどちらが妥当か。前者だと「四隅や中心だけ」を扱う他の利用者に影響します。
- `StartRangeSS` が `_mapper()`、`MotionRangeSS` が `_mapper(self.ss)` を使う非対称は意図的か。押下と移動の間でフレームサイズが変わった場合（解像度変更・カメラ切替）に座標がずれませんか。
- `_crop_image` に範囲検査を入れて「範囲外なら失敗（False + ログ）」にする案は、既存の利用者（テンプレートマッチの crop、Blockly の crop 指定）を壊さないか。黙って切り詰めるのと失敗させるのどちらが安全か。

---

## 項目 5 【中・私の暫定】16/24bpp の表示環境では映像が 1 画素も描かれないのに成功を返す

### 症状
バックバッファ（DIB を包む ndarray）は表示深度に応じて (H,W,2) / (H,W,3) になりますが、合成は常に 4ch 前提で `cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA, dst=back)` を呼びます。OpenCV は dst の型が合わないと**例外も警告も出さず内部で再確保し、dst を書かない**（別調査の実測: 2ch/3ch の dst を渡しても例外なし・dst は全ゼロのまま。私は再実行していません）。結果、画面に出る絵は映像で一度も書かれず、GDI のオーバーレイだけが残ります。`compose` は成功を返すのでカウンタにも警告にも出ません。

### 該当コード（現物）

```python
# core/gdi_surface.py:68
_SUPPORTED_BIT_DEPTHS = (16, 24, 32)
```

```python
# core/gdi_surface.py:988-995（16/24 も「対応」として選ばれる）
        try:
            # GetDeviceCaps はメモリ DC だと 0 を返すので子窓の DC で問う。
            reported = self._api.get_device_caps(window_dc, _BITSPIXEL)
            bit_count = (
                reported
                if reported in _SUPPORTED_BIT_DEPTHS
                else _SUPPORTED_BIT_DEPTHS[-1]
            )
            info = DibInfo(
                bi_width=width,
                bi_height=-height,
```

```python
# core/gdi_surface.py:1138-1148（4ch 前提の 1 経路。戻り値も見ていない）
        # frame は常にバックバッファと同じ大きさなので、変換 1 経路で済む。
        # 換算は present の StretchBlt が行うので、ここでは引伸ばさない。
        cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA, dst=back)
        # 映像を書いた直後・オーバーレイを描く前に base を撮る。
        if self._base is None or self._base.shape != back.shape:
            self._base = np.empty_like(back)
        np.copyto(self._base, back)
        self._draw_overlay(overlay)
        self._pending_frame = True
        return RenderResult(True, time.perf_counter_ns() - started, "ok")
```

補足: 既存テストは DIB ヘッダの bit count が 16/24/32 のいずれかであることしか見ておらず、この組み合わせは「想定内」として扱われています。16bpp は Windows で今も選択でき、24bpp は RDP / VM で報告例があります。

### 聞きたいこと
- 最小で正しい直し方はどれか: (a) 深度に関わらず 32bpp の DIB を作る、(b) `cvtColor` を 2ch/3ch/4ch に分岐する、(c) 書けたかを検証して失敗を返す。
- OpenCV の「dst を黙って無視する」挙動に依存しない書き方（`np.copyto` や変換結果の明示利用）で、性能を落とさずに済む方法はあるか。
- 16bpp の DIB に BGRA を書く場合、色成分の並び（RGB555/BGR565）まで面倒を見る必要があるか。

---

## 項目 6 【中・私の暫定】ワーカースレッドから `root.after()` を直接呼んでいる（規約違反）

### 症状と規約
規約は「ワーカースレッドから Tk ウィジェット / Tk 変数 / `after()` を触らない。`DropOldestQueue` に積み GUI スレッドが消化する」。実際には複数か所が裏スレッドから `root.after(0, ...)` を呼んでいます。CPython の `_tkinter` は mainloop 実行中に限り他スレッドの呼び出しを main スレッドへ marshal します（その間**呼び出し側スレッドはブロック**）。mainloop が回っていない／ディスパッチ中は `RuntimeError` になります（コード自身がそれを想定して再試行しているのが証拠）。実害の形は「GUI がモーダルダイアログ中などに、カメラを開いたスレッドが最大 10 秒スピンし、期限切れで開いたばかりのカメラを破棄して結果を捨てる（通知なし）」などです。あわせて、これらの `except Exception: pass` は規約 4（ログ必須）にも違反しています。

### 該当コード（現物）

```python
# ui/serial_panel.py:709-712（bcon 表示の同期。裏スレッドから。ログも無い）
                try:
                    self.root.after(0, lambda: self._apply_bcon_wired_display(wired))
                except Exception:
                    pass
                return
```

```python
# ui/serial_panel.py:780-795（切替の後始末。裏スレッドから）
        def job() -> None:
            try:
                setter(*args, **kwargs)
            except Exception as e:
                # print はキュー経由のため裏からでも安全。利用者の目に
                # 届け、詳細はファイルへ残す。
                print(f"{label}に失敗しました: {e}")
                logger.warning(f"{label}に失敗しました: {e}")
            try:
                if getattr(self, "_closing", False):
                    return
                self.root.after(0, self._finish_bcon_setter)   # ★
            except Exception:
                pass

        threading.Thread(target=job, daemon=True).start()
```

```python
# ui/serial_panel.py:1190-1195（読取スレッドからの通知）
    def _onGamepadDisplay(self, snapshot: Any) -> None:
        """読取スレッドからの通知。Tk スレッドへ予約して描く。"""
        try:
            self.root.after(0, self._paintGamepadMirror, snapshot)
        except Exception:
            pass
```

```python
# ui/camera_panel.py:450-466（CameraOpener スレッド。RuntimeError を想定して再試行）
        deadline = time.monotonic() + _OPENED_HANDOFF_TIMEOUT_S
        while True:
            try:
                self.root.after(0, self._on_camera_opened, cam_id, seq, camera, ok)
                return
            except tk.TclError:
                # 終了済み。開いてしまった物は閉じる（保持リーク防止）。
                try:
                    camera.destroy()
                except Exception:
                    pass
                return
            except RuntimeError:
                if time.monotonic() >= deadline:
                    logger.warning(
                        "カメラ open 結果の受け渡しを断念: mainloop が "
```

```python
# GuiAssets.py:133-143（コマンドのワーカースレッドから枠描画を予約）
    def _marshal(self, name: str, *args: Any, **kwargs: Any) -> None:
        target = self._proxy_target
        root = self._proxy_root
        if target is None or root is None:
            return
        try:
            root.after(0, lambda: self._call_guarded(target, name, args, kwargs))
        except Exception:
            # 終了後など after 自体が積めないときは捨てる。枠表示は
            # 無くても操作に影響しない。
            pass
```

```python
# tests/test_ui_no_block_on_rpc.py:210-229（現状を固定しているテスト）
    host.root = _FakeRoot()
    refreshed: list[bool] = []
    host._refresh_bcon_rows = lambda: refreshed.append(True)
    monkeypatch.setattr(tkmsg, "askquestion", lambda *a: "yes")

    began = time.perf_counter()
    serial_panel.SerialPanelMixin.applyBconWired(host)
    elapsed = time.perf_counter() - began
    assert elapsed < FAST_S, f"applyBconWired が停滞しました: {elapsed:.1f}秒"
    # 裏で setter が呼ばれたこと（Tk は待たない）。
    assert _wait_for(lambda: len(fake.wired_calls) == 1), "裏で setter が呼ばれません"
    # setter の完了前は反映の予約もないこと（Tk へ触らない）。
    assert host.root.afters == [], "setter 完了前に Tk へ触っています"
    fake.release.set()
    # 反映は Tk スレッドへの予約経由であること。
    assert _wait_for(lambda: len(host.root.afters) > 0), "after(0) 予約がありません"
    for ms, cb in list(host.root.afters):
        assert ms == 0
        cb()
    assert refreshed, "after(0) で _refresh_bcon_rows が呼ばれません"
```

このテストは「待機中に動くのは worker だけ」なので、**worker が `after` を呼ぶ現状を固定**しています。修正するならテストの設計も変える必要があります。

### 聞きたいこと
- 実害の評価: Windows + python.org ビルドで、この書き方は「動くが mainloop 停止中は失敗する」で正しいか。壊れ方の最悪ケースは何か（Tcl の状態破壊はあり得るか）。
- 最小の改修案: 既存の GUI 側ポーリング（500ms 周期のランプ巡回や 1ms のプレビュー時計）に相乗りしてフラグを見る方式と、`DropOldestQueue` + GUI 側 drain のどちらが適切か。
- カメラ open 結果の受け渡しは待ち合わせが要る（開いた実体を確実に渡す）が、キュー方式でも取りこぼしなく渡せるか。期限切れ時にカメラを破棄する現状の判断は妥当か。
- テストをどう書き換えれば「Tk を裏から触っていない」ことを引き続き守れるか。

---

## 項目 7 【中・私の暫定】カメラが要求解像度を拒否するとプレビューが永久に黒（原因は GUI に出ない）

### 症状
`Camera`（アプリが使うスレッド版）はフレームをリサイズしません。デバイスが 1280x720 を拒否すると、届くフレームは実解像度のままです。

- フィルタ / 色補正 OFF: 合成が `dimension_mismatch` で全フレームを捨てる → プレビューは永久に黒。警告は loguru（ファイル + stderr）のみで GUI ログ欄（stdout 経由）には出ません。
- フィルタ ON: `np.copyto` が毎 tick `ValueError`（別調査の実測: could not broadcast input array from shape (360,640,3) into shape (720,1280,3)）になり、包括 except が "capture failed" を書き続けます。

設計書は "never scaled, fails loudly" と明記しており捨てる判断自体は意図的ですが、"loudly" の実体がファイルログ 1 行だけです。

### 該当コード（現物）

```python
# core/gdi_surface.py:1100-1123
        frame_size = frame.shape[1::-1]
        # ここで止めないと 640x360 の frame が 1280x720 のバッファへ範囲外書
        # き込む。判定の相手は受け皿でも client でもなく、カメラが返す映像の
        # 解像度（CAPTURE_SIZE）である。
        if frame_size != CAPTURE_SIZE:
            self.frames_discarded_dimension_mismatch += 1
            self._note_failure(
                "dimension_mismatch",
                "キャプチャ解像度でないので捨てた {}x{} のまま capture={} "
                "owned={} viewport={} dest={} "
                "frames_discarded_dimension_mismatch={}",
                frame_size[0], frame_size[1], CAPTURE_SIZE,
                self._size, self._viewport, self._dest,
                self.frames_discarded_dimension_mismatch,
            )
            return RenderResult(
                False, time.perf_counter_ns() - started, "dimension_mismatch"
            )
```

```python
# GuiAssets.py:936-958（フィルタ/補正が有効なときだけ通る。形が違うと ValueError）
    def _convert(self, frame: Any) -> np.ndarray:
        """補正と抽出を順に適用した BGR を作業バッファへ作る。"""
        from core import preview_filter as _pf

        work = frame
        if self._correction_active:
            np.copyto(self._correct_buf, _pf.apply_correction(work, self._correction))
            work = self._correct_buf
        if self._filter_enabled:
            np.copyto(
                self._filter_buf,
                _pf.apply_filter(work, self._filter_lower, self._filter_upper, self._filter_mode),
            )
            work = self._filter_buf
        return work
```

```python
# core/Camera.py:401-407（設定の適用結果は残すが、効かなかった場合の処置はログのみ）
        if not camera.isOpened():
            logger.error(f"Camera ID {cameraId} cannot open.")
            camera.release()
            return False

        # 実測値を設定後に残す。警告を見逃した側にも後から渡せる。
        self._capture_readback = _configure_capture(camera, self.capture_size, self.fps)
```

`_configure_capture` の docstring は「MJPG を指定しても set() は効かず False を返すだけ」という実機状態を想定しており、`size_not_applied` 警告を用意しています＝この状態は起こり得ます。

### 聞きたいこと
- 受け取ったフレームを `CAPTURE_SIZE` へ正規化する層を 1 つ入れるべきか。入れるならカメラ側と描画直前のどちらか。
- 正規化しない方針を維持する場合、利用者が原因に到達できる最小の通知は何か（GUI ログ欄への 1 行、ダイアログ、プレビュー面への文字表示）。
- `np.copyto` の前に形を検証して「縮小して合わせる」のは、画素の意味を変える（認識精度に影響する）ので避けるべきか。

---

## 項目 8 【低・私の暫定】走行記録 CSV のハンドルが途中例外で閉じられない（FD 漏れ）

```python
# services/serial_service.py:685-716
            csv_path = ""
            if transport is not None:
                try:
                    os.makedirs(self._log_dir, exist_ok=True)
                    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                    csv_path = os.path.join(
                        self._log_dir,
                        run_journal.run_csv_name(stamp, profile, name),
                    )
                    handle = open(csv_path, "w", newline="", encoding="utf-8")
                    writer = csv.writer(handle)
                    writer.writerow(["t", "line"])
                    orig = transport.send_row

                    def _rec(row: str, measure_perf: bool = True, _o: Any = orig) -> Any:
                        started = time.perf_counter()
                        try:
                            return _o(row, measure_perf)
                        finally:
                            self._record_wire_row(writer, handle, started, row)

                    transport.send_row = _rec  # type: ignore[method-assign]
                    self._run_file = handle          # ★登録は差し替え成功後
                    self._run_writer = writer
                    self._run_transport = transport
                    self._run_csv_path = csv_path
                except Exception:
                    logger.debug("ワイヤ記録の開始に失敗", exc_info=True)
                    self._run_csv_path = ""          # ★handle は閉じない
            self._run_active = True
```

補足: `_close_run_file()` は `self._run_file` しか見ません。`transport.send_row = _rec` は `__slots__` や property を持つ transport、不変 dataclass、プラグイン製 transport では失敗し得ます。

### 聞きたいこと
- 私の案（`open` 直後に `self._run_file = handle` を入れ、`except` で `self._close_run_file()` を呼ぶ）で十分か。
- より良い形（ExitStack 相当、差し替え失敗時に記録を諦めて続行する、など）はあるか。
- `transport.send_row` を差し替える設計自体（他に副作用がないか）に問題はないか。

---

## 項目 9 【低・私の暫定】終了処理の階段が例外に無防備 / 終了が無限に再試行される

```python
# Window.py:566-581（_closing は一度立つと戻らない）
        if self._closing:
            logger.debug("exit() は既に実行中のため無視しました")
            return
        if not tkmsg.askyesno("確認", "Poke Controllerを終了しますか？"):
            return

        self._closing = True
        self._exit_requested = True
        self._exit_phase = "stopping_preview"
        self.runner.notify_closing()
        self._cancel_exit_afters()
        self._continue_exit()
```

```python
# Window.py:607-654（段ごとに try が無い。最後に root.destroy()）
    def _continue_exit(self) -> None:
        if not self._exit_requested:
            return
        if self._exit_phase == "stopping_preview":
            self._exit_retry_after_id = None
            if self._stop_preview_for_exit() is not StopResult.STOPPED:
                self._schedule_preview_stop_retry()      # ★上限なしの 50ms 再試行
                return
            self._exit_phase = "preview_stopped"

        if self._exit_phase != "preview_stopped":
            return

        self._exit_phase = "surface_released"
        if self.preview is not None:
            self.preview.surface.release()               # ★無防備

        self._exit_phase = "stopping_services"
        if self.menu is not None:
            try:
                self.menu.closeAll()
            except Exception as exc:
                logger.warning(f"子窓を閉じるときに例外: {exc}")
        self._close_bcon_tab()                           # ★無防備
        self.runner.shutdown(self.ser)
        self.serial.stop_keyboard()
        self.closingController()                         # ★無防備
        if self.serial.shutdown():
            print("Serial disconnected")
        self._save_exit_settings_and_stats()
        if self.camera is not None:
            self.camera.destroy()                        # ★無防備（cv2.error があり得る）
        self._stop_audio()
        cv2.destroyAllWindows()
        sys.stdout = sys.__stdout__
        logger.debug("Stop Poke Controller")
        self._exit_phase = "root_destroyed"
        self.root.destroy()

    def _schedule_preview_stop_retry(self) -> None:
        # 停止未確定のあいだは 50ms 後に判断し直す。
        self._exit_phase = "stopping_preview"
```

`_stop_preview_for_exit` と `menu.closeAll` は守られており「守る」意図は元からあります。`_closing` を False に戻す経路はありません。

### 聞きたいこと
- `_continue_exit` 全体を `try/finally` にして「どの段で失敗しても `root.destroy()` までは進む」形にしてよいか。それとも段ごとに握って続行すべきか。
- 失敗時に `_closing` を戻して再試行可能にするのは安全か（半端に解放した状態で 2 度目の終了が走る）。
- プレビュー停止の 50ms 再試行に上限を設ける場合、上限到達時の安全な代替は何か。

---

## 項目 10 【低・私の暫定】`switch-bcon-proc` に `last_rumble` が無く、振動表示が必ず「（なし）」

```python
# core/transport/bcon_proc.py:42-64（子プロセスへ渡せる呼び出しの許可リスト）
_ALLOWED_CALLS = frozenset(
    {
        "is_open", "hello", "ping", "request_status", "request_color",
        "last_status", "last_player_info", "baud_hunt", "set_baud_index",
        "set_wired_mode", "set_emulate_mode", "send_beacon", "send_config",
        "start_live_loop", "stop_live_loop", "live_loop_running", "live_stats",
        "request_bootsel", "request_reconnect",
    }
)   # ★ "last_rumble" が無い
```

```python
# core/transport/bcon_proc.py:1072-1090（last_status / last_player_info はある）
    def last_status(self) -> dict[str, int]:
        ok, result = self._call("last_status", (), timeout=3.0)
        if not ok or not isinstance(result, dict):
            return dict(_ZERO_STATUS)
        ...
    def last_player_info(self) -> bytes:
        ok, result = self._call("last_player_info", (), timeout=3.0)
        if not ok or result is None:
```

```python
# core/transport/bcon.py:1450-1456（本家にはある）
    def last_rumble(self) -> bytes:
        """直近RUMBLE payloadの写し。未受信は空。例外は投げない。"""
        try:
            with self._lock:
                return bytes(self._last_rumble)
        except Exception:
            return b""
```

```python
# BconSetup.py:1881-1894（getattr で取れないと「（なし）」へ落ちる）
            try:
                rumble_fn = getattr(transport, "last_rumble", None)
                rumble = bytes(rumble_fn()) if callable(rumble_fn) else b""
            except Exception:
                rumble = b""
            if rumble:
                try:
                    left, right = decode_rumble(rumble)
                    left, right = int(left) & 0xFF, int(right) & 0xFF
                except Exception:
                    left, right = 0, 0
                self._queue.put(("log", f"RUMBLE: L={left} R={right}"))
            else:
                self._queue.put(("log", "振動:（なし）"))
```

別調査が機械的に確認: `dir(BconTransport) - dir(BconProcTransport)` の差分は `last_rumble` の 1 件だけ。クラス docstring は「操作面は本家と同一にする」と書いています。

### 聞きたいこと
- `last_rumble` を proc 側と許可リストへ足すだけで足りるか。子プロセス側で RUMBLE を保持しているかは別途確認が必要という理解でよいか。
- 「本家と同一の操作面」を将来守るためのテストは、どのメソッドを列挙するのが妥当か。

---

## 項目 11 【低・私の暫定】パッケージ manifest の検証がバックスラッシュ区切りの親参照を通す

```python
# core/pack_manifest.py:47-59
def _is_bad_rel(value: str, *, need_py: bool = False) -> str | None:
    """zip内相対パスとして不正なら理由、正常なら None を返す。"""
    if not value or not value.strip():
        return "空です"
    if value.startswith("/") or value.startswith(chr(92)):      # 原文は二重引用符内の単一バックスラッシュ
        return "絶対パスは使えません（相対で書いてください）"
    if ".." in PurePosixPath(value).parts:      # ★PurePosixPath はバックスラッシュを区切りにしない
        return "親参照は使えません"               # 原文は .. をバッククォートで囲んだ文言
    if ":" in value:
        return "コロンは使えません（ドライブ文字・代替 stream のため）"
    if need_py and not value.lower().endswith(".py"):
        return "末尾は .py にしてください"
    return None
```

（上のコードは、バックスラッシュとバッククォートが転記で壊れるのを避けるため 2 箇所だけ表記を置き換えています。ロジックは変えていません。）

実測（別調査。私は再実行していない）:

```python
# 検証を通ってしまう入力（errors == []）
templates = [".." + chr(92) + "evil.png"]
entry     = ".." + chr(92) + "evil.py"

# 提案する正規化（先頭で区切りを統一してから判定する）
value = value.replace(chr(92), "/")
```

一方 `pathlib.Path` は Windows でバックスラッシュを区切りとして扱うため、`validate_package_layout` は配布物の外を `is_file()` で探します。実際の書き込みは下流の `pack_zip._checked_rel`（区切りを正規化してから検査）と `script_pack._is_safe_rel` が防いでいるため、実害は「検証時の基底外参照」と「エラー文と実装の矛盾」に限定されます。

### 聞きたいこと
- 先頭で区切りを正規化してから判定する案で十分か。
- 他に見落としている Windows 固有の迂回（末尾のドット/空白、UNC、予約名 CON/PRN、8.3 短縮名、代替データストリームのコロンは既に禁止）はあるか。

---

## 項目 12 【低・私の暫定】タグ保存だけ固定名の一時ファイル（並列起動中の同時保存でタグ全消失しうる）

```python
# core/CommandTags.py:78-93
    """Commands/tags.json へ書き出す。成否を返す。

    一時ファイルへ書いてから置き換える。途中で落ちても、元のファイルが
    半端な内容で残らない（settings.ini と同じ作法）。
    """
    path = util.ospath(TAGS_JSON)
    tmp = f"{path}.tmp"                 # ★固定名
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(overrides, fp, ensure_ascii=False, indent=2)
            fp.write(chr(10))           # 原文は改行文字のエスケープ表記
        os.replace(tmp, path)
    except OSError as exc:
        message = f"Failed to write {TAGS_JSON}: {exc}"
        print(message)
```

（`fp.write` の引数は原文では改行のエスケープ表記です。転記で実改行に化けるのを避けるため `chr(10)` と書いています。）

`tags.json` はプロファイル非依存で共有。`CommandStats.py:105` と `CommandHistory.py:173` は `tempfile.mkstemp` で一意名にしています。読み側は壊れた JSON を空辞書で握るため、次の保存で空を書き戻すとタグ付けが全部消えます。

### 聞きたいこと
- `mkstemp` + `os.replace` に変えるだけで十分か。保存前に読み直してマージすべきか。

---

## 項目 13 【低・私の暫定】`_rx_loop` が `in_waiting` の例外でログ無しに停止する

```python
# core/transport/bcon.py:1078-1095
                break
            try:
                waiting = getattr(ser, "in_waiting", None)
                if waiting is None:
                    chunk = ser.read(64)
                else:
                    try:
                        available = int(waiting() if callable(waiting) else waiting)
                    except Exception:
                        break               # ★ログ無しで RX ポンプ停止
                    if available <= 0:
                        try:
                            time.sleep(0.001)
                        except Exception:
```

読み取り例外側は warning + exc_info で残しています。

### 聞きたいこと
- `break` を「warning を 1 行残して `continue`」に変えると、壊れた ser でビジーループになる危険はあるか。連続失敗回数で止めるべきか。

---

## 項目 14 【低・私の暫定】`saveFrame` の既定フォルダが cwd 相対 / 認識キャッシュの握り潰し

```python
# core/CommandVision.py:1017-1034（既定 folder="Debug" が cwd 相対）
    def saveFrame(self, name: str = "frame", crop=None, folder: str = "Debug") -> str:
        """いまの画面を日時つきで保存し、保存先のパスを返す。"""
        crop = crop or []
        frame = self._cropOrRaise(self._readFrameOrRaise(), crop)

        os.makedirs(folder, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        stamp += f"_{int((time.time() % 1) * 1000):03d}"
        safe = re.sub(r"[^0-9A-Za-z_.-]", "_", str(name))
        filespec = path.join(folder, f"{stamp}_{safe}.png")
```

```python
# core/CommandVision.py:793-803（規約 4 違反の握り潰し）
        if match_key is not None:
            try:
                with self._vision_cache_lock:
                    cache[match_key] = (
                        float(max_val),
                        (int(max_loc[0]), int(max_loc[1])),
                        int(w),
                        int(h),
                    )
            except Exception:
                pass
```

規約は「リソースパスは `BASE_DIR` から解決」「`except Exception` にはログ」。`Camera` は `__file__` から `Captures` を作っており、ここだけ cwd 依存です。アプリ内では起動時の `os.chdir` に救われていますが、別エントリから import すると保存先が変わります。

### 聞きたいこと
- `saveFrame` の既定を `__file__` 基準に変えると、既存の利用者スクリプト（`folder` を渡さない物）の保存先が変わる互換問題はないか。渡された相対パスは cwd 基準のままにすべきか。
- キャッシュの握り潰しは「握るのをやめる」「1 行ログを足す」のどちらが妥当か。

---

## 項目 15 【低・私の暫定】`openCamera` の例外漏れと、解放を断念した後の復帰不能

```python
# core/Camera.py:396-407（_configure_capture は set/get を 6 回呼ぶ。try が無い）
        else:
            logger.debug("Not NT OS")
            camera = cv2.VideoCapture(cameraId)

        if not camera.isOpened():
            logger.error(f"Camera ID {cameraId} cannot open.")
            camera.release()
            return False

        # 実測値を設定後に残す。警告を見逃した側にも後から渡せる。
        self._capture_readback = _configure_capture(camera, self.capture_size, self.fps)
```

```python
# ui/camera_panel.py:583-586（GUI スレッド側は except が無い＝Tk コールバックのトレースになる）
        try:
            opened = self.camera.openCamera(cam_id)
        finally:
            self._camera_open_lock.release()
```

```python
# core/Camera.py:494-520（join 上限を超えたら解放を断念する）
    def _release_later(self, thread: threading.Thread) -> None:
        """取得スレッドの終了を待ってから解放する見張りを裏で走らせる。"""
        with self._lock:
            camera, self.camera = self.camera, None

        def waiter() -> None:
            thread.join(timeout=THREAD_RELEASE_TIMEOUT)
            if thread.is_alive():
                logger.error(
                    "Camera thread is still alive. "
                    "Skip releasing the device to avoid a crash."
                )
                return                      # ★デバイスは掴んだまま
            if camera is None:
                return
            camera.release()
            logger.debug("Camera destroyed (deferred)")

        threading.Thread(target=waiter, name="CameraReleaser", daemon=True).start()
```

`read()` が固まったまま USB を抜く等で join が上限を超えると、デバイスは解放されず `self.camera` は None になります。次の `openCamera` は同じデバイスを掴みに行き、DirectShow では通常「使用中」で失敗 → アプリ再起動まで映像が戻りません。

### 聞きたいこと
- `openCamera` の `_configure_capture` を try で包む場合、失敗時に「続行して readback を None のまま」と「release して False」のどちらが妥当か。
- 解放を断念した状態をどう表現するのが良いか（フラグを公開して以後の open を拒否＋GUI へ理由を出す、等）。

---

## 参考 A: Stop 打切りで止めた live worker を起こし直す（修正済み・判断の妥当性を見てほしい）

打切り（30 秒停止しなかった）で `discardLive` と `stopLiveWorker` を呼びます。live 経路は姿勢をキューへ積むだけで同期送信しないため、worker を止めたままにすると**以後の実行が 1 行も出ない無音状態**になります（打切りを使うまで気づけません）。そこで「止めたことを覚えて、塞ぎを外すときに起こし直す」ようにしました。

```python
# services/command_runner.py（修正後）
        if waited >= STOP_ESCALATE_MS:
            self._fence_live(self._ser)        # discardLive + stopLiveWorker(0.5)
            self._fenced_thread = thread
            ...
    def _fence_live(self, ser: Any) -> None:
        try:
            if ser is not None:
                capable = getattr(ser, "isLiveCapable", None)
                running = getattr(ser, "isLiveWorkerRunning", None)
                if callable(capable) and callable(running):
                    try:
                        if capable() and running():
                            self._fenced_live = True     # 止めたことを覚える
                    except Exception as e:
                        logger.warning(f"live worker の稼働確認で例外: {e}")
                discard = getattr(ser, "discardLive", None)
                if callable(discard):
                    try:
                        discard()
                    except Exception as e:
                        logger.warning(f"liveの破棄で例外: {e}")
                stopper = getattr(ser, "stopLiveWorker", None)
                if callable(stopper):
                    try:
                        stopper(0.5)
                    except Exception as e:
                        logger.warning(f"live worker の停止で例外: {e}")
        except Exception as e:
            logger.warning(f"線の封鎖で例外: {e}")

    def _resume_fenced_live(self) -> None:
        """塞ぎで止めた live worker を起こし直す。"""
        resume, self._fenced_live = self._fenced_live, False
        if not resume or self._closing:
            return
        ser = self._ser
        try:
            capable = getattr(ser, "isLiveCapable", None)
            if not callable(capable) or not capable():
                return
            starter = getattr(ser, "startLiveWorker", None)
            if callable(starter):
                starter()
        except Exception as e:
            logger.warning(f"live worker の再開で例外: {e}")
```

呼び出しは「塞ぎを外す 2 か所」＝ 死んだのを確認した時（`_reap_fenced_thread`）と、`request_start` で塞ぎを外した時です。`_closing` 中は起こしません。

### 相手側のコード（ご指摘の欠落分）

```python
# core/serial/sender.py:1598-1619（putLive。呼ぶと scheduler と _live_lock を取る）
    def putLive(self, snap=None, *, _closing: bool = False) -> None:
        self._ensureLiveState()
        if self._live_closing and not _closing:
            return
        if snap is None:
            snap = self.snapshot()
        self._live_sched.push(snap)
        with self._live_lock:
            self._live_stats["put"] += 1
```

```python
# core/serial/sender.py:1781-1819（_startLiveWorker。錠の中で登録と起動を行う）
    def _startLiveWorker(self, transport=None) -> threading.Thread | None:
        self._ensureLiveState()
        if transport is None:
            transport = getattr(self, "transport", None)
        if transport is None:
            return None
        with self._live_lock:
            thread = self._live_thread
            if thread is not None and thread.is_alive():
                return None
        self._live_stop.clear()
        with self._live_lock:
            self._live_last_snap = None
            self._live_last_sent_at = None
            ...
        self.putLive(self.snapshot())          # 錠の外
        with self._live_lock:
            thread = self._live_thread
            if thread is not None and thread.is_alive():
                return None
            started = threading.Thread(target=self._liveLoop, args=(transport,),
                                       name="PicoLiveWorker", daemon=True)
            self._live_thread = started
            started.start()
            return started
```

```python
# core/serial/sender.py:1849-1887（stopLiveWorker / _stopLiveWorkerIf。join は錠の外）
    def stopLiveWorker(self, timeout: float = 1.0) -> bool:
        self._ensureLiveState()
        self._live_stop.set()                  # ← stop 印を立てる
        with self._live_lock:
            thread = self._live_thread
        if thread is None:
            return True
        thread.join(timeout)                   # 錠を持たずに待つ
        alive = thread.is_alive()
        if not alive:
            with self._live_lock:
                if self._live_thread is thread:
                    self._live_thread = None
        return not alive

    def _stopLiveWorkerIf(self, thread, timeout: float = 1.0) -> bool:
        self._ensureLiveState()
        if thread is not None:
            with self._live_lock:
                if self._live_thread is not thread:
                    return True                # 勝者に任せる
        return self.stopLiveWorker(timeout)
```

```python
# core/serial/sender.py:1821-1829（discardLive。送出中のものは残す）
    def discardLive(self) -> bool:
        self._ensureLiveState()
        return bool(self._live_sched.clear())
```

```python
# core/serial/sender.py:208-267（setTransport。_lock と _live_lock の両方を取る）
    def setTransport(self, transport: Transport.Transport) -> bool:
        """通信方式を切り替える。live worker を停止してから切り替え、
        新しい通信方式が live 対応であれば 1 本だけ起動する。"""
        with self._lock:
            gen = int(getattr(self, "_transport_gen", 0)) + 1
            self._transport_gen = gen
            old = self.transport
        # 止める相手はこの時点で残っている worker に限る。
        self._ensureLiveState()
        with self._live_lock:
            mine = self._live_thread
        # 錠の外で止める・閉じる（最大で 2 秒弱かかることがある）。
        if not self._stopLiveWorkerIf(mine):
            self._logger.error("PicoLiveWorker did not stop; transport unchanged")
            with self._lock:
                if gen != int(getattr(self, "_transport_gen", 0)):
                    return False
            return False
        try:
            try:
                old.remove_listener(self.input_logger.feed)
            except Exception:
                self._logger.debug("旧線の listener 解除に失敗", exc_info=True)
            try:
                old.close()
            except Exception:
                self._logger.error(
                    f"Failed to close previous transport: {traceback.format_exc()}"
                )
        except Exception:
            self._logger.error(
                f"Failed to close previous transport: {traceback.format_exc()}"
            )
        with self._lock:
            # 二重切替で先を越されていたら、新しい方は捨てて抜ける。
            if gen != int(getattr(self, "_transport_gen", 0)):
                return False
            self.transport = transport
            self.transport._logger = self._logger
            self.transport.set_hooks(self._onWriteBegin, self._onWriteEnd)
            self._input_log_linked = bool(
                self.transport.add_listener(self.input_logger.feed)
            )
            linked = bool(self._input_log_linked)
            need_live = self._liveCapable()
        if not linked:
            ...
```

### 聞きたいこと
- 再開条件（live 対応 かつ 止めたことを覚えている かつ 終了処理中でない）で十分か。
- **打切りで止めきれなかった古いスレッドがまだ生きていて、あとから `apply*` を呼べます**。塞ぎを外した後にその申告が新しいコマンドの入力と混ざって線へ出る危険はないか（現状は「古いスレッドが死んだのを確認してから外す」ので、生きている間は新規開始を断ります。ただし `shutdown` 経路など、死を確認した直後にどの申告が残るかは追い切れていません）。
- `stopLiveWorker` は `_live_stop` を立てますが、その後の `startLiveWorker` が `_live_stop.clear()` するので再開できる、という理解で正しいか。他に再開を妨げる状態（`_live_closing` など）はないか。

---

## 参考 B: snapshot と putLive を同じ臨界区間に入れた（修正済み・デッドロックの見落としを見てほしい）

```python
# core/serial/sender.py（修正後。applyButtons / applyHat / applyStick の 3 か所が同型）
        snap = None
        owner = self._ownerKey(source)
        with self._lock:
            ...
            if self._applyComposed():
                self._bumpRevision()
                if self._liveCapable():
                    snap = self.snapshot()
            # 列への投入まで錠の中で行う。snapshot と push の間に錠を離すと、
            #   別スレッドの新しい revision が先に積まれ、古い状態が後ろに
            #   並ぶ（線には古い状態が最後に残る）。
            if snap is not None:
                self.putLive(snap)
```

修正前は `putLive` が `with` の外にあり、同時申告で古い revision が新しい物の後ろに積まれ、線には古い状態が最後に残りました（離したはずのボタンが押しっぱなしで固定）。別調査が 4 スレッド × 3000 回で 24000 push 中 475 件が非単調になるのを実測しています（私は再実行していません）。

導入したロック順序は「`self._lock` → scheduler の錠 / `self._live_lock`」です。逆向き（`self._live_lock` を保持したまま `self._lock` を取る）経路が無いこと、scheduler が Sender へコールバックしないことは私が確認しました。上の putLive / _startLiveWorker / stopLiveWorker / setTransport / discardLive のコードが、その根拠に必要な抜粋です。

### 聞きたいこと
- この入れ子でデッドロックし得る経路に見落としがないか（特に `stopLiveWorker` / `setTransport` / `_startLiveWorker` との組み合わせ。上に本体を貼りました）。
- 「古い revision を捨てる」設計の方が安全という判断はあり得るか。

---

## 補足

- ホスト: lint / format / mypy / 層境界検査 / 公開 API 検査 / pytest 2139 passed・26 skipped。
- ファーム: ホストテスト 13 本（protocol / usb / config / baud / pokecon / rumble / personality / joy_scopeA / joy_scopeB / wired_player / player_capsaved / color_getinfo / reconnect_policy）が全緑。
- 実機（COM ポート・Switch・キャプチャボード）での確認はできていません。
- 各項目の【検証状態】に、私が再実行したのか別調査の報告なのかを書いています。数値の再現性に疑いがあれば、その旨を指摘してください。
