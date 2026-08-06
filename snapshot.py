#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通知用的日曆截圖:標註整月狀態,上傳到 GitHub public repo,回傳可供 LINE 取用的網址。

只在「真的要發通知」時才會被呼叫,所以慢一點無所謂 —— 例行的每 30 分鐘檢查
走純 HTTP,完全不碰瀏覽器。

playwright 刻意在函式內才 import:萬一它因系統更新壞掉,只會讓截圖失效,
不會讓整支監控程式連載入都失敗。爆炸半徑限縮在附加功能內。
"""
import base64
import datetime
import json
import os
import subprocess
import tempfile

MEM_ID = "161109"
CALENDAR_URL = f"https://freecalend.com/open/mem{MEM_ID}"

# 綠框=可預約 / 紅框=有狀態 / 藍框+★=監控目標。
# 判讀規則與 calendar_http.py 一致:有 color-<MEM>-paraN 類別或 .ccexp 文字 = 有狀態。
# 不可用背景色判斷 —— 週六/週日有底色但不是狀態。
ANNOTATE_JS = """
({mem, year, month, targetDay}) => {
  const boxOf = (n) => {
    let c = n;
    for (let i = 0; i < 8 && c.parentElement; i++) {
      c = c.parentElement;
      if (c.classList && c.classList.contains('daywrap')
          && c.classList.contains('caldate')) return c;
    }
    return null;
  };
  let minL = 1e9, minT = 1e9, maxR = -1e9, maxB = -1e9, n = 0;
  for (let d = 1; d <= 31; d++) {
    const a = document.querySelector(`[id="hidukemok-${mem}-${year}-${month}-${d}"]`);
    if (!a) continue;
    const box = boxOf(a);
    if (!box) continue;
    n++;
    const r = box.getBoundingClientRect(), sx = window.scrollX, sy = window.scrollY;
    minL = Math.min(minL, r.left + sx); minT = Math.min(minT, r.top + sy);
    maxR = Math.max(maxR, r.right + sx); maxB = Math.max(maxB, r.bottom + sy);

    const statusClasses = [...box.classList].filter(c => c.startsWith(`color-${mem}-para`));
    const labels = [...box.querySelectorAll('.ccexp')]
                     .map(e => (e.innerText || '').trim()).filter(Boolean);
    const isFree = statusClasses.length === 0 && labels.length === 0;
    box.style.outline = isFree ? '3px solid #00b050' : '3px solid #d40000';
    box.style.outlineOffset = '-3px';
    if (d === targetDay) {
      box.style.boxShadow = 'inset 0 0 0 6px #0050ff';
      box.style.position = 'relative';
      const tag = document.createElement('div');
      tag.textContent = '★';
      tag.style.cssText = 'position:absolute;right:1px;bottom:0;color:#0050ff;'
                        + 'font:bold 18px sans-serif;z-index:9999';
      box.appendChild(tag);
    }
  }
  return n ? {x: minL - 8, y: minT - 8,
              width: (maxR - minL) + 16, height: (maxB - minT) + 16} : null;
}
"""


def capture(year, month, target_day, mem_id=MEM_ID, timeout=60000):
    """截取標註過的當月日曆,回傳暫存檔路徑。失敗時丟例外由呼叫端處理。"""
    from playwright.sync_api import sync_playwright   # 延遲 import,見模組說明

    path = os.path.join(tempfile.gettempdir(), "eboshi_snapshot.png")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            # 窄視窗讓整月接近正方形,手機上才讀得清楚(桌面寬度會變成細長條)
            page = browser.new_page(viewport={"width": 620, "height": 1200},
                                    device_scale_factor=3)
            page.goto(CALENDAR_URL, wait_until="networkidle", timeout=timeout)
            try:
                page.wait_for_selector(".daywrap.caldate", timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(2500)
            clip = page.evaluate(ANNOTATE_JS, {"mem": mem_id, "year": year,
                                               "month": month, "targetDay": target_day})
            if not clip:
                raise RuntimeError(f"頁面上找不到 {year}/{month} 的日期格")
            page.screenshot(path=path, clip=clip)
        finally:
            browser.close()
    return path


def upload(path, repo, gh_bin):
    """
    上傳到 repo 並回傳 raw 網址。

    檔名帶時間戳是必要的:raw.githubusercontent 回 cache-control: max-age=300,
    同名覆蓋會讓 LINE 抓到 5 分鐘內的舊圖。
    """
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    repo_path = f"snapshots/{stamp}.png"
    with open(path, "rb") as f:
        content = base64.b64encode(f.read()).decode()

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
        json.dump({"message": f"snapshot {stamp}", "content": content}, tf)
        tmp = tf.name
    try:
        subprocess.run(
            [gh_bin, "api", "-X", "PUT",
             f"repos/{repo}/contents/{repo_path}", "--input", tmp],
            check=True, capture_output=True, timeout=120,
        )
    finally:
        os.unlink(tmp)
    return f"https://raw.githubusercontent.com/{repo}/main/{repo_path}"
