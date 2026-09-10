// テンプレ切出しの純粋部（DOMなし。node vm検証用）。
// 画素換算は Python 側 rect_to_pixels と同方向（四捨五入・はみ出し丸め・8px）。
var PokeconCapture = (function () {
  "use strict";
  function clamp01(v) {
    return Math.min(1, Math.max(0, v));
  }
  function normalizeDrag01(ax, ay, bx, by) {
    var x = clamp01(Math.min(ax, bx));
    var y = clamp01(Math.min(ay, by));
    return {
      x: x,
      y: y,
      width: clamp01(Math.max(ax, bx)) - x,
      height: clamp01(Math.max(ay, by)) - y,
    };
  }
  function toPixels(rect, natW, natH) {
    var x1 = Math.min(natW, Math.max(0, Math.round(rect.x * natW)));
    var y1 = Math.min(natH, Math.max(0, Math.round(rect.y * natH)));
    var x2 = Math.min(
      natW,
      Math.max(0, Math.round((rect.x + rect.width) * natW)),
    );
    var y2 = Math.min(
      natH,
      Math.max(0, Math.round((rect.y + rect.height) * natH)),
    );
    return { x1: x1, y1: y1, x2: x2, y2: y2 };
  }
  function isValidPixels(rect, natW, natH) {
    if (!natW || !natH) {
      return false;
    }
    var p = toPixels(rect, natW, natH);
    return p.x2 - p.x1 >= 8 && p.y2 - p.y1 >= 8;
  }
  // 矩形全体の平行移動。はみ出しは画像内に丸める（大きさは保つ）。
  function moveRect01(rect, dx, dy) {
    return {
      x: clamp01(Math.min(Math.max(rect.x + dx, 0), 1 - rect.width)),
      y: clamp01(Math.min(Math.max(rect.y + dy, 0), 1 - rect.height)),
      width: rect.width,
      height: rect.height,
    };
  }
  // 外枠ハンドルでのリサイズ。handle は nw/n/ne/e/se/s/sw/w。
  // 動かす辺だけをずらし、対辺は固定する。はみ出しは丸め、逆転は対辺で止める。
  function resizeRect01(rect, handle, dx, dy) {
    var x1 = rect.x;
    var y1 = rect.y;
    var x2 = rect.x + rect.width;
    var y2 = rect.y + rect.height;
    if (handle.indexOf("w") !== -1) {
      x1 = clamp01(Math.min(x1 + dx, x2));
    }
    if (handle.indexOf("e") !== -1) {
      x2 = clamp01(Math.max(x2 + dx, x1));
    }
    if (handle.indexOf("n") !== -1) {
      y1 = clamp01(Math.min(y1 + dy, y2));
    }
    if (handle.indexOf("s") !== -1) {
      y2 = clamp01(Math.max(y2 + dy, y1));
    }
    return { x: x1, y: y1, width: x2 - x1, height: y2 - y1 };
  }
  // 正規化矩形→実画像画素（x, y, width, height の整数）。数値欄表示用。
  function rect01ToPx(rect, natW, natH) {
    var p = toPixels(rect, natW, natH);
    return { x: p.x1, y: p.y1, width: p.x2 - p.x1, height: p.y2 - p.y1 };
  }
  // 実画像画素→正規化矩形。数値欄入力用（はみ出しは丸める）。
  function pxToRect01(x, y, w, h, natW, natH) {
    if (!natW || !natH) {
      return { x: 0, y: 0, width: 0, height: 0 };
    }
    var nx = clamp01(Math.min(Math.max(x / natW, 0), 1));
    var ny = clamp01(Math.min(Math.max(y / natH, 0), 1));
    return {
      x: nx,
      y: ny,
      width: clamp01(Math.max(x + w, 0) / natW) - nx,
      height: clamp01(Math.max(y + h, 0) / natH) - ny,
    };
  }
  // CROP欄（"x1,y1,x2,y2" 実画素）の解釈。おかしければ null。
  function parseCropText(s) {
    var m = String(s == null ? "" : s).match(
      /^\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*$/
    );
    if (!m) {
      return null;
    }
    var x1 = parseInt(m[1], 10);
    var y1 = parseInt(m[2], 10);
    var x2 = parseInt(m[3], 10);
    var y2 = parseInt(m[4], 10);
    if (!(x2 > x1 && y2 > y1)) {
      return null;
    }
    return { x1: x1, y1: y1, x2: x2, y2: y2 };
  }
  // CROP欄への整形。
  function formatCrop(x1, y1, x2, y2) {
    return x1 + "," + y1 + "," + x2 + "," + y2;
  }
  // 当たり判定。rect 上の点が8ハンドル・内側・外側のどれかを返す。
  // 返値は 'nw'/'n'/'ne'/'e'/'se'/'s'/'sw'/'w'/'inside'/null。tol は正規化単位の許容幅。
  function hitHandle(rect, x, y, tol) {
    if (!rect) {
      return null;
    }
    var x1 = rect.x;
    var y1 = rect.y;
    var x2 = rect.x + rect.width;
    var y2 = rect.y + rect.height;
    function near(px, py) {
      return Math.max(Math.abs(x - px), Math.abs(y - py)) <= tol;
    }
    var xm = (x1 + x2) / 2;
    var ym = (y1 + y2) / 2;
    if (near(x1, y1)) {
      return "nw";
    }
    if (near(x2, y1)) {
      return "ne";
    }
    if (near(x1, y2)) {
      return "sw";
    }
    if (near(x2, y2)) {
      return "se";
    }
    if (near(xm, y1)) {
      return "n";
    }
    if (near(xm, y2)) {
      return "s";
    }
    if (near(x1, ym)) {
      return "w";
    }
    if (near(x2, ym)) {
      return "e";
    }
    if (x >= x1 && x <= x2 && y >= y1 && y <= y2) {
      return "inside";
    }
    return null;
  }
  return {
    clamp01: clamp01,
    normalizeDrag01: normalizeDrag01,
    toPixels: toPixels,
    isValidPixels: isValidPixels,
    moveRect01: moveRect01,
    resizeRect01: resizeRect01,
    rect01ToPx: rect01ToPx,
    pxToRect01: pxToRect01,
    parseCropText: parseCropText,
    formatCrop: formatCrop,
    hitHandle: hitHandle,
  };
})();
