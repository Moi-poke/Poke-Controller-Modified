# USB CDC Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Pico USB a HID+CDC composite in wireless mode so a PC gets a serial console without a UART adapter, while wired mode stays byte-identical pure HID.

**Architecture:** Isolate CDC in new `usb_cdc.c` (TinyUSB glue, same pattern as `usb_wired.h` hiding `tusb.h` from `ui.c`). Serve two static configuration descriptors (composite vs pure HID) switched on the live W flag. Split UI line assembly into pure `ui_line` module (host-testable) with source tags; gate `W` to UART lines only.

**Tech Stack:** C11, Pico SDK 2.3.0 (RP2350, board `pico2_w` fixed), TinyUSB device (HID+CDC), BTstack (untouched), CTest host tests (MSVC + `/utf-8`).

**Spec:** `docs/superpowers/specs/2026-09-06-usb-cdc-console-design.md`

## Global Constraints

- board は `pico2_w` 固定 (`CMakeLists.txt` 内で設定)。変えない。
- `main.c` に変更なし (`tud_init`/task 共用のため追加不要。再列挙は既存経路を使う)。
- UART ログ書式・`snprintf` バッファサイズを変えない。`usb ...` 行の接頭辞 `usb en=%u cfg=%u hs=%u` を変えない。`cdc=%u` の付加のみ。
- W 1 の記述子はバイト同一 (41B の static assert と HID 203B の assert を残す)。
- 新規リテラルは `W from UART only` のみ (既存流儀の usage 系)。
- `cap`/`util`/`usb_hid` は Pico・BTstack 非依存のままにする。新規 `ui_line` も Pico・BTstack 非依存にする (`pico/*.h` も `btstack.h` も include しない)。
- `ui.c` と `usb_cdc.h` に `tusb.h` を include しない (`ui.c` は `ui.h` 経由で btstack を含み、`hid_report_type_t` が衝突するため。窓口は `usb_cdc.h` のみ)。
- CDC Endpoint: 通知 0x83、OUT 0x02、IN 0x82 (HID の 0x81/0x01 と衝突なし)。
- 両コンソールの行長は 64 (`UI_LINE_MAX 64`)。
- ソースの日本語コメントは残す。改行コードの一括変換はしない。
- ビルドはフルパス cmake/ninja。ビルド前に VSCode saveAll。成果物 `build/`・`build-verify/` は git 管理外。
- 作業は `refactor/usb-cdc-console` で。main への push・PR は指示があるときのみ。
- 秘密 (LTK/IRK/AES 鍵) をログに出さない。

---

## File Structure

- Create: `src/ui_line.h` — 発信元タグ付き行組立の純粋 IF (`ui_src_t`、`ui_line_acc_t`、`UI_LINE_MAX 64`、`UI_LINE_READY` 等)。
- Create: `src/ui_line.c` — 蓄積・改行確定・溢れ破棄・`W` ゲート述語の純粋実装。
- Create: `src/usb_cdc.h` — TinyUSB CDC glue の公開 IF (`usb_cdc_connected`、`usb_cdc_read_char`、`usb_cdc_write_line`)。tusb 非公開。
- Create: `src/usb_cdc.c` — 上記の実装 (`tud_cdc_*` 呼び出しのみ)。
- Create: `tests/host/test_ui_line.c` — ホストテスト。
- Modify: `src/tusb_config.h` — `CFG_TUD_CDC` を 0→1 にし、FIFO サイズ 3 種を追加する。
- Modify: `src/usb_descriptors.c` — 複合構成記述子の追加と、モードによる構成記述子の切替。
- Modify: `src/usb_wired.h`・`src/usb_wired.c` — `usb_wired_was_mounted()` の追加。
- Modify: `src/link_conn.c` — `update()` の再列挙条件に既列挙歴を追加する。
- Modify: `src/ui.c` — 二重コンソール化 (蓄積分離・タグ付き投入・`W` ゲート・送信 fan-out・`cdc=` 追加)。
- Modify: `CMakeLists.txt` — `src/usb_cdc.c` と `src/ui_line.c` を SOURCES に追加する。
- Modify: `tests/host/CMakeLists.txt` — `test_ui_line` を追加する。

---

### Task 1: 純粋行組立モジュール `ui_line` (TDD)

**Files:**
- Create: `src/ui_line.h`
- Create: `src/ui_line.c`
- Create: `tests/host/test_ui_line.c`
- Modify: `tests/host/CMakeLists.txt`

**Interfaces:**
- Consumes: なし (新規純粋モジュール。`LINE_MAX 64` 相当は `UI_LINE_MAX 64` としてここで定義する)。
- Produces: `ui_src_t` (`UI_SRC_UART`/`UI_SRC_CDC`)、`ui_line_acc_t` (`buf[UI_LINE_MAX]`、`len`)、`UI_LINE_READY` (1) / `UI_LINE_INCOMPLETE` (0) / `UI_LINE_OVERFLOW` (2)、`void ui_line_reset(ui_line_acc_t *st)`、`int ui_line_feed(ui_line_acc_t *st, char c)`、`int ui_w_allowed(ui_src_t src)`。Task 3 の `ui.c` が使う。

- [ ] **Step 1: Write the failing test**

```c
/* tests/host/test_ui_line.c: test_cap.c と同形式。Pico/BTstack不要。 */
#include <stdio.h>
#include <string.h>
#include "ui_line.h"

static int fails;

#define CHECK(cond) do { \
    if (!(cond)) { printf("FAIL line %d: %s\n", __LINE__, #cond); fails++; } \
} while (0)

int main(void)
{
    ui_line_acc_t st;
    int r;
    ui_line_reset(&st);
    CHECK(st.len == 0);
    /* "?\n" → READY、内容と NUL 終端。 */
    r = ui_line_feed(&st, '?');
    CHECK(r == UI_LINE_INCOMPLETE);
    r = ui_line_feed(&st, '\n');
    CHECK(r == UI_LINE_READY);
    CHECK(st.len == 1 && strcmp(st.buf, "?") == 0);
    /* 空行の改行は INCOMPLETE のまま。 */
    ui_line_reset(&st);
    CHECK(ui_line_feed(&st, '\r') == UI_LINE_INCOMPLETE);
    CHECK(st.len == 0);
    /* 63 文字溜めて 64 文字目で OVERFLOW＋破棄。 */
    {
        int i;
        ui_line_reset(&st);
        for (i = 0; i < 63; i++) {
            CHECK(ui_line_feed(&st, 'A') == UI_LINE_INCOMPLETE);
        }
        CHECK(st.len == 63);
        CHECK(ui_line_feed(&st, 'B') == UI_LINE_OVERFLOW);
        CHECK(st.len == 0);
    }
    /* OVERFLOW 後は再蓄積できる。 */
    r = ui_line_feed(&st, 'P');
    CHECK(r == UI_LINE_INCOMPLETE);
    CHECK(ui_line_feed(&st, '\n') == UI_LINE_READY);
    CHECK(strcmp(st.buf, "P") == 0);
    /* NULL は INCOMPLETE。 */
    CHECK(ui_line_feed(NULL, 'x') == UI_LINE_INCOMPLETE);
    /* W ゲート述語。 */
    CHECK(ui_w_allowed(UI_SRC_UART) == 1);
    CHECK(ui_w_allowed(UI_SRC_CDC) == 0);
    if (fails == 0) { printf("OK uiline\n"); }
    return fails != 0;
}
```

- [ ] **Step 2: Add the CMake target and run test to verify it fails**

```cmake
add_executable(test_ui_line test_ui_line.c ../../src/ui_line.c)
target_include_directories(test_ui_line PRIVATE ../../src)
add_test(NAME ui_line COMMAND test_ui_line)
```

`tests/host/CMakeLists.txt` の `add_test(NAME usb ...)` の次の行に上記 3 行を足す。`ui_line.h`・`ui_line.c` が無い状態でホストテストの再構成・ビルドを行い、`test_ui_line` のビルド失敗を確認する。コマンドは `tests/host/CMakeLists.txt` の流儀に従い、`vcvars64.bat` 経由の cmd でフルパスの cmake/ctest を呼ぶ (AGENTS.md と `tests/host/CMakeLists.txt` 参照。素の PowerShell にコンパイラはない)。

- [ ] **Step 3: Write minimal implementation**

```c
/* src/ui_line.h */
#ifndef WAKECON_UI_LINE_H
#define WAKECON_UI_LINE_H

/* 発信元タグ付き行組立。Pico・BTstack 非依存。ホストテスト可能に保つ。 */

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define UI_LINE_MAX 64
#define UI_LINE_INCOMPLETE 0
#define UI_LINE_READY 1
#define UI_LINE_OVERFLOW 2

typedef enum {
    UI_SRC_UART = 0,
    UI_SRC_CDC = 1
} ui_src_t;

typedef struct {
    char buf[UI_LINE_MAX];
    int len;
} ui_line_acc_t;

void ui_line_reset(ui_line_acc_t *st);
int ui_line_feed(ui_line_acc_t *st, char c);
int ui_w_allowed(ui_src_t src);

#ifdef __cplusplus
}
#endif

#endif
```

```c
/* src/ui_line.c: 日本語コメントは残す。Pico・BTstack 非依存を保つ。 */
#include "ui_line.h"

void ui_line_reset(ui_line_acc_t *st)
{
    if (st == NULL) {
        return;
    }
    st->len = 0;
}

int ui_line_feed(ui_line_acc_t *st, char c)
{
    if (st == NULL) {
        return UI_LINE_INCOMPLETE;
    }
    if (c == '\n' || c == '\r') {
        if (st->len <= 0) {
            return UI_LINE_INCOMPLETE;
        }
        st->buf[st->len] = '\0';
        return UI_LINE_READY;
    }
    if (st->len >= UI_LINE_MAX - 1) {
        st->len = 0;
        return UI_LINE_OVERFLOW;
    }
    st->buf[st->len++] = c;
    return UI_LINE_INCOMPLETE;
}

int ui_w_allowed(ui_src_t src)
{
    return src == UI_SRC_UART ? 1 : 0;
}
```

- [ ] **Step 4: Run test to verify it passes**

ホストテストを再構成・ビルドし、`ctest --output-on-failure` で `util`・`cap`・`usb`・`ui_line` の全 PASS を確認する。既存 3 件が壊れていないこと。

- [ ] **Step 5: Commit**

```bash
git add src/ui_line.h src/ui_line.c tests/host/test_ui_line.c tests/host/CMakeLists.txt
git commit -m "feat: add source-tagged line accumulator with host test"
```

---

### Task 2: CDC 有効化と二重構成記述子 (ファームビルド＋PC 列挙確認)

**Files:**
- Modify: `src/tusb_config.h`
- Modify: `src/usb_descriptors.c`
- Modify: `CMakeLists.txt` (変更なし。`usb_cdc.c` は Task 3 で足す)

**Interfaces:**
- Consumes: なし (TinyUSB 0.18 のマクロ。`TUD_CDC_DESCRIPTOR` の 7 引数順は SDK の `device/usbd.h` 通り)。
- Produces: W 0 で複合構成 (HID+CDC)、W 1 で純粋 HID (41B 不変) を返す `tud_descriptor_configuration_cb`。Task 3 の `usb_cdc.c` と `ui.c` が使う土台。

- [ ] **Step 1: Enable CDC in `tusb_config.h`**

```c
/* CDC は W 0 の複合構成でのみ使う。W 1 の純 HID 記述子には現れない。 */
#define CFG_TUD_CDC 1
```

`#define CFG_TUD_CDC 0` の行を上書きする。直後に次を足す (RX 256・TX 512・EP 64。SDK の上書き可能定義と同名)。

```c
#ifndef CFG_TUD_CDC_RX_BUFSIZE
#define CFG_TUD_CDC_RX_BUFSIZE 256
#endif
#ifndef CFG_TUD_CDC_TX_BUFSIZE
#define CFG_TUD_CDC_TX_BUFSIZE 512
#endif
#ifndef CFG_TUD_CDC_EP_BUFSIZE
#define CFG_TUD_CDC_EP_BUFSIZE 64
#endif
```

- [ ] **Step 2: Add the composite configuration descriptor**

`src/usb_descriptors.c` の既存 `enum { ITF_NUM_HID, ITF_NUM_TOTAL }` は触らない。別 enum で番号を固定する (同名再定義の重複エラーを避ける)。

```c
/* 複合構成 (W 0) 用の番号。ITF_NUM_HID (=0) と衝突させない。 */
enum {
    ITF_NUM_CDC = 1,
    ITF_NUM_CDC_DATA = 2,
    ITF_NUM_TOTAL_COMPOSITE = 3,
};
```

`desc_configuration` (純粋 HID 41B) の直後に次を足す。HID 側の値は純粋側と同一にし、CDC は通知 0x83・OUT 0x02・IN 0x82 (HID の 0x81/0x01 と衝突なし) とする。

```c
/* W 0 用の複合構成。HID 部は純粋側と同一値にする。 */
#define COMPOSITE_CONFIG_TOTAL_LEN \
    (TUD_CONFIG_DESC_LEN + TUD_HID_INOUT_DESC_LEN + TUD_CDC_DESC_LEN)

static uint8_t const desc_configuration_composite[] = {
    TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_TOTAL_COMPOSITE, 0,
                          COMPOSITE_CONFIG_TOTAL_LEN,
                          TUSB_DESC_CONFIG_ATT_REMOTE_WAKEUP, 500),
    TUD_HID_INOUT_DESCRIPTOR(ITF_NUM_HID, 0, HID_ITF_PROTOCOL_NONE,
                             sizeof(desc_hid_report), 0x01, 0x80 | 0x01,
                             64, 8),
    TUD_CDC_DESCRIPTOR(ITF_NUM_CDC, 0, 0x83, 8, 0x02, 0x82, 64),
};

/* 自身の構成マクロ合計との一致だけ縛る (SDK 更新に頑健にするため
 * 純正値のような固定値は置かない)。 */
_Static_assert(sizeof(desc_configuration_composite) ==
              COMPOSITE_CONFIG_TOTAL_LEN,
              "composite config length");
```

`tud_descriptor_configuration_cb` を次に置き換える。`usb_wired.h` は bool/stdint のみで tusb と衝突しないため include してよい。

```c
#include "usb_wired.h"

uint8_t const *tud_descriptor_configuration_cb(uint8_t index)
{
    (void)index;
    if (usb_wired_is_enabled()) {
        return desc_configuration;
    }
    return desc_configuration_composite;
}
```

既存の 41B assert と `desc_configuration` 本体には触らない。

- [ ] **Step 3: Build the firmware**

```powershell
$cmake = Join-Path $env:USERPROFILE '.pico-sdk/cmake/v4.3.4/bin/cmake.exe'
$ninja = Join-Path $env:USERPROFILE '.pico-sdk/ninja/v1.13.2/ninja.exe'
& $cmake -S . -B build -G Ninja -DCMAKE_MAKE_PROGRAM:FILEPATH=$ninja
& $cmake --build build --target pico-wakecon
```

ビルド前に VSCode saveAll。警告なしで `pico-wakecon.uf2` ができること。

- [ ] **Step 4: Verify enumeration on PC in both modes**

`build/pico-wakecon.uf2` を書き込む。Pico の USB を PC に挿す。W モード既定 (W フラグなし) の起動直後にデバイスマネージャ等で CDC シリアル (COM ポート) と HID の両方が出ること。`W 1` 後に USB を挿し直すと純粋 HID のみ (COM ポートが出ない) こと。`W 0` に戻して挿し直すと再び両方が出ること。CDC 側の疎通 (文字入力の応答) は Task 3 の範囲のため問わない。

- [ ] **Step 5: Commit**

```bash
git add src/tusb_config.h src/usb_descriptors.c
git commit -m "feat: add CDC composite USB descriptors for wireless mode"
```

---

### Task 3: CDC ブリッジと二重コンソール化 (PC 疎通まで)

**Files:**
- Create: `src/usb_cdc.h`
- Create: `src/usb_cdc.c`
- Modify: `src/usb_wired.h` (追加1関数)
- Modify: `src/usb_wired.c` (追加1関数)
- Modify: `src/link_conn.c` (再列挙条件1行)
- Modify: `src/ui.c` (二重コンソール化・`W` ゲート・送信 fan-out・`cdc=` 追加)
- Modify: `CMakeLists.txt` (`src/usb_cdc.c`・`src/ui_line.c` を SOURCES に追加)

**Interfaces:**
- Consumes: Task 1 の `ui_src_t`・`ui_line_acc_t`・`UI_LINE_*`・`ui_line_feed`・`ui_line_reset`・`ui_w_allowed`。Task 2 の複合記述子。
- Produces: PC コンソール疎通 (`W` は CDC から拒否)。Task 4 の回帰の土台。

- [ ] **Step 1: Write `usb_cdc.h` and `usb_cdc.c`**

```c
/* src/usb_cdc.h */
#ifndef WAKECON_USB_CDC_H
#define WAKECON_USB_CDC_H

/* USB CDC コンソール (TinyUSB glue)。tusb.h を外に出さない。
 * ui.c は btstack 由来で tusb.h と衝突するため、この窓口だけを使う。 */

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ホストが開いているか (マウント＋DTR)。 */
bool usb_cdc_connected(void);
/* 1 文字読む。なければ -1。DTR の有無によらず読む (UART と対等にする)。 */
int usb_cdc_read_char(void);
/* 1 行出す (改行＋flush まで行う)。未接続なら何もしない。 */
void usb_cdc_write_line(const char *text);

#ifdef __cplusplus
}
#endif

#endif
```

```c
/* src/usb_cdc.c: 日本語コメントは残す。 */
#include "tusb.h"
#include "usb_cdc.h"

bool usb_cdc_connected(void)
{
    return tud_cdc_connected();
}

int usb_cdc_read_char(void)
{
    uint8_t ch;
    if (tud_cdc_available() == 0u) {
        return -1;
    }
    if (tud_cdc_read(&ch, 1u) != 1u) {
        return -1;
    }
    return (int)ch;
}

void usb_cdc_write_line(const char *text)
{
    if (text == NULL || !tud_cdc_connected()) {
        return;
    }
    tud_cdc_write_str(text);
    tud_cdc_write_str("\r\n");
    tud_cdc_write_flush();
}
```

TinyUSB の CDC 弱既定コールバックは定義しない (poll 駆動のため不要)。

- [ ] **Step 2: Add `usb_wired_was_mounted()`**

`src/usb_wired.h` に宣言を足す。

```c
/* 起動後の累積で一度でもマウントされたか (回復要否の判定用)。 */
bool usb_wired_was_mounted(void);
```

`src/usb_wired.c` に実体を足す (`wired_stats` は同ファイルの static のため直接読める)。

```c
bool usb_wired_was_mounted(void)
{
    return wired_stats.mount > 0u;
}
```

- [ ] **Step 3: Gate the recovery re-kick on mount history**

`src/link_conn.c` の `link_radio_update()` 内の再列挙条件を次に置き換える。起動直後 (未列挙) の 500ms 停止をなくすため。

```c
    if (quiet && usb_wired_was_mounted() && !usb_wired_is_configured()) {
        usb_wired_reconnect();
    }
```

- [ ] **Step 4: Rewire `ui.c` to dual consoles**

`#include "usb_cdc.h"` と `#include "ui_line.h"` を足す。`read_one_char` を UART 専用に改名する。

```c
static int read_uart_char(void)
{
    if (uart_is_readable(UART_ID)) {
        return (int)(unsigned char)uart_getc(UART_ID);
    }
    return -1;
}
```

`line_buf`・`line_len` は投入口として残し、蓄積は発信元ごとに分ける。

```c
static ui_line_acc_t uart_acc, cdc_acc;
static ui_src_t line_src;

static void deliver_line(ui_line_acc_t *acc, ui_src_t src)
{
    memcpy(line_buf, acc->buf, (size_t)acc->len);
    line_len = acc->len;
    line_src = src;
    handle_line();
    line_len = 0;
}

static void pump_source(int (*read_fn)(void), ui_line_acc_t *acc,
                        ui_src_t src)
{
    int ci;
    while ((ci = read_fn()) >= 0) {
        char c = (char)ci;
        int r;
        if (src == UI_SRC_UART) {
            probe_uart_rx_count++;
        }
        if (acc->len == 0 && !ui_cmd_is_known(c)) {
            continue;
        }
        r = ui_line_feed(acc, c);
        if (r == UI_LINE_READY) {
            deliver_line(acc, src);
            ui_line_reset(acc);
        } else if (r == UI_LINE_OVERFLOW) {
            probe_line_over++;
            ui_line_reset(acc);
        }
    }
}
```

`probe_uart_task` の読取ループを次に置き換える (以降の `link_poll`・番犬は不変)。

```c
    pump_source(read_uart_char, &uart_acc, UI_SRC_UART);
    pump_source(usb_cdc_read_char, &cdc_acc, UI_SRC_CDC);
```

`handle_line` の振分けに `W` ゲートを足す (他は不変)。

```c
        if (UI_CMDS[i].c == c0) {
            if (c0 == 'W' && !ui_w_allowed(line_src)) {
                probe_line("W from UART only");
                return;
            }
            UI_CMDS[i].fn();
            return;
        }
```

`cmd_w` の同値再指定は再列挙を省く。既存の解析・`store_wired` の後に次を足す (順序: 判定→設定→保存→再列挙→表示)。

```c
    if ((v == 1u) == usb_wired_is_enabled()) {
        report_usb_line();
        return;
    }
```

```c
    usb_wired_set_enabled(v == 1u);
    link_apply_wired_mode(v == 1u);
    store_wired(v == 1u);
    usb_wired_reconnect();
    report_usb_line();
```

`probe_line` の末尾に CDC fan-out を足す。

```c
    if (usb_cdc_connected()) {
        usb_cdc_write_line(text);
    }
```

`report_usb_line` の書式末尾に ` cdc=%u` を足し、引数に `usb_cdc_connected() ? 1u : 0u` を足す。接頭辞 `usb en=%u cfg=%u hs=%u` は変えない。

`CMakeLists.txt` の SOURCES に `src/usb_cdc.c` と `src/ui_line.c` を足す。

- [ ] **Step 5: Build the firmware**

Task 2 と同じフルパスの cmake/ninja 手順で増分ビルドする (ビルド前に saveAll)。警告なしでリンクできること。

- [ ] **Step 6: Verify the PC console**

`build/pico-wakecon.uf2` を書き込む。無線モード (W フラグなし) 起動で PC に COM ポートが出ること。COM ポートから `P` で `PONG` が返り、`S 4 8 80 80 80 80` 後に `WD` が出ること (入力経路の疎通)。`W 1` を COM から打つと `W from UART only` が返り、モードが変わらないこと (`?` の `usb en=0` で確認)。UART 側は従来通り動くこと (二重コンソール併存)。

- [ ] **Step 7: Commit**

```bash
git add src/usb_cdc.h src/usb_cdc.c src/usb_wired.h src/usb_wired.c src/link_conn.c src/ui.c CMakeLists.txt
git commit -m "feat: dual UART and USB CDC consoles with UART-only W"
```

---

### Task 4: 回帰確認と文書更新

**Files:**
- Modify: `README.md` (CDC 節の追加・構成表)
- Modify: `docs/DEVELOPMENT.md` (構成表・制約)

**Interfaces:**
- Consumes: Task 1〜3 の成果。
- Produces: マージ可能な完成形。

- [ ] **Step 1: Run all host tests**

ホストテストを再構成・ビルドし、`ctest --output-on-failure` で `util`・`cap`・`usb`・`ui_line` の全 PASS を確認する。

- [ ] **Step 2: Clean build**

`build-verify/` を作り直してクリーンビルドし、`pico-wakecon.uf2` ができること (手順は `docs/DEVELOPMENT.md` 通り)。

- [ ] **Step 3: Hardware regression matrix**

`build-verify/pico-wakecon.uf2` を書き込む。次を順に確認する。(1) PC＋W 0：COM 列挙・`P`/`S`/`?` 疎通・`W` 拒否・`cdc=1`。(2) Switch ドック＋W 1：従来通り認識・`S` 入力・`O` 色・`hs=1`。`st`・`color`・`saved` 行が従来書式のまま。(3) `W` 往復と起動時復元 (CDC の出現・消失が追従する)。(4) UART 併用時の動作 (両方から `S` が効き、`W` は UART だけが効く)。

- [ ] **Step 4: Update docs**

`README.md` に CDC 節を足す (W 0 で PC に COM ポートが出ること、`W` は UART のみ、W 0＋ドックはプロコン認識しない想定内)。構成表に `usb_cdc.c`・`ui_line.c` の行を足す。`docs/DEVELOPMENT.md` の構成表に同 2 行を足し、制約に CDC Endpoint (通知 0x83・OUT 0x02・IN 0x82) と新規リテラル `W from UART only` を足す。

- [ ] **Step 5: Commit**

```bash
git add README.md docs/DEVELOPMENT.md
git commit -m "docs: document USB CDC console"
```

---

## Self-Review

1. **Spec coverage:** 目標 (W 0 で PC シリアル代替) → Task 2〜3。構成 (usb_cdc 隔離・記述子 2 種・発信元タグ・W ゲート) → Task 1〜3。データフロー (両受信・W 拒否・両送信) → Task 3。遷移 (起動時決定・W 時再列挙・BT 無関係・W 0＋ドック非認識) → Task 2〜3。境界 (抜線破棄・非ブロック・cdc= 付加・秘密なし・固定長・W 1 不可視) → Task 1 (固定長・破棄)・Task 3 (残り)。検証 (TDD・実機 4 項目) → Task 1・Task 4。`sof` 参考値の扱いは既存実装のまま触らないため対象外。不足なし。
2. **Placeholder scan:** 固定値 (VID/PID・Endpoint・サイズ・リテラル・コマンド) はすべて具体値で記載。`TBD`・`TODO`・「適切に」等の記述なし。
3. **Type consistency:** `ui_src_t`・`ui_line_acc_t`・`UI_LINE_*`・`ui_line_feed`・`ui_w_allowed`・`usb_cdc_connected`・`usb_cdc_read_char`・`usb_cdc_write_line`・`usb_wired_was_mounted`・`W from UART only`・`cdc=%u` は全タスクで同一。`TUD_CDC_DESCRIPTOR` の 7 引数順は SDK の `device/usbd.h` 定義通り。`link_radio_update` の既存シグネチャは変えない。
