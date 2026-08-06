#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
監控烏帽子小屋 freecalend 日曆:目標日變成白色(可預約)時,推播文字通知到 LINE,
並盡力附上一張標註過的當月日曆截圖。

設定(環境變數,建議放 ~/.config/eboshi-watch/env,權限 600):
    LINE_CHANNEL_TOKEN  LINE Messaging API 的 long-lived channel access token
    LINE_GROUP_ID       推播目的地 id。U=個人 / C=群組 / R=聊天室 皆可
    EBOSHI_SNAPSHOT_REPO  (選用) 圖片託管用的 GitHub public repo,格式 owner/name
    GH_BIN                (選用) gh 執行檔絕對路徑,launchd 環境 PATH 很精簡故需指定

—— freecalend DOM 的三個反直覺陷阱(2026-08-06 實測確認) ——
1) 日期 id `hidukemok-<MEM>-<year>-<month>-<day>` 的月份是 1-based,不是 0-based。
   證據:當天 2026-08-06 的格子 id 為 ...-2026-8-6 且帶 day_honjitu;
   且 id 為 2026-8 的月份渲染到第 31 天(9 月沒有 31 號)。
2) `day_aru` 不是「已滿」旗標,而是「這格有日期」(ある)。112 格中 92 格帶它,
   恰等於 31+30+31 天,其中含大量純白可預約日。誤用會讓判斷恆為已滿 → 永不通知。
3) 不可用背景色判斷。rgb(247,254,255)=週六底色、rgb(254,231,251)=週日/假日底色,
   都不是狀態,會把每個週末誤判成非白。

正確訊號:狀態覆蓋類別 `color-<MEM>-para<N>`
    para1=満員/団体満員  para111=営業最終日  para116=臨時休業
白色可預約 = 無任何 color-*-para* 類別,且無 .ccexp 文字。

季末注意:本小屋 9/30 為営業最終日,10 月整月空白是「歇業」不是「有空位」。
若把 TARGET 改到營業期外,「空白=可預約」的前提不成立。
"""

import base64
import datetime
import json
import os
import subprocess
import sys
import tempfile

import requests
from playwright.sync_api import sync_playwright

# ====== 設定 ======
CALENDAR_URL = "https://freecalend.com/open/mem161109"
MEM_ID       = "161109"
PHONE        = "050-3171-2604(平日 10:00-17:00)"

TARGET_YEAR  = 2026
TARGET_MONTH = 9      # 1-based,直接用人類月份
TARGET_DAY   = 6

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "eboshi_notified.flag")

LINE_CHANNEL_TOKEN = os.environ.get("LINE_CHANNEL_TOKEN", "")
LINE_GROUP_ID      = os.environ.get("LINE_GROUP_ID", "")
SNAPSHOT_REPO      = os.environ.get("EBOSHI_SNAPSHOT_REPO", "")
GH_BIN             = os.environ.get("GH_BIN", "gh")
# ==================


PROBE_JS = r"""
({mem, year, month, targetDay, annotate}) => {
  const boxOf = (node) => {
    let cur = node;
    for (let i = 0; i < 8 && cur.parentElement; i++) {
      cur = cur.parentElement;
      if (cur.classList
          && cur.classList.contains('daywrap')
          && cur.classList.contains('caldate')) return cur;
    }
    return null;
  };

  // 自我校驗用:頁面把哪一格標成「本日」
  let todayId = null;
  for (const el of document.querySelectorAll('.daywrap.caldate.day_honjitu')) {
    const a = el.querySelector('[id^="hidukemok-"]');
    if (a) { todayId = a.id; break; }
  }

  const days = [];
  let minL = 1e9, minT = 1e9, maxR = -1e9, maxB = -1e9, boxCount = 0;

  for (let d = 1; d <= 31; d++) {
    const anchor = document.querySelector(`[id="hidukemok-${mem}-${year}-${month}-${d}"]`);
    if (!anchor) continue;
    const box = boxOf(anchor);
    if (!box) continue;
    boxCount++;

    const statusClasses = [...box.classList].filter(c => c.startsWith(`color-${mem}-para`));
    const labels = [...box.querySelectorAll('.ccexp')]
                     .map(e => (e.innerText || '').trim())
                     .filter(Boolean);
    const isWhite = statusClasses.length === 0 && labels.length === 0;
    days.push({day: d, isWhite, labels, statusClasses});

    if (annotate) {
      const r = box.getBoundingClientRect(), sx = window.scrollX, sy = window.scrollY;
      minL = Math.min(minL, r.left + sx); minT = Math.min(minT, r.top + sy);
      maxR = Math.max(maxR, r.right + sx); maxB = Math.max(maxB, r.bottom + sy);
      box.style.outline = isWhite ? '3px solid #00b050' : '3px solid #d40000';
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
  }

  const target = days.find(x => x.day === targetDay) || null;
  const clip = (annotate && boxCount)
    ? {x: minL - 8, y: minT - 8, width: (maxR - minL) + 16, height: (maxB - minT) + 16}
    : null;
  return {todayId, days, target, clip, boxCount};
}
"""


def assert_month_convention(today_id, today):
    """
    用頁面自己標的「本日」格驗證 id 的月份慣例仍是 1-based。
    慣例若改變就中止,避免靜靜地監控錯的月份。
    """
    if not today_id:
        print("⚠️ 頁面上找不到『本日』格,略過月份慣例校驗。", file=sys.stderr)
        return
    expected = f"hidukemok-{MEM_ID}-{today.year}-{today.month}-{today.day}"
    if today_id != expected:
        raise SystemExit(
            f"❌ freecalend 的日期 id 慣例已改變,停止執行以免監控錯的月份。\n"
            f"   預期『本日』格 id = {expected}\n"
            f"   實際頁面標記為     = {today_id}"
        )


def check_and_capture(today):
    """
    一次瀏覽器工作階段內完成「判讀 + (必要時)截圖」。

    刻意不分成兩次載入:若判讀完才重新開頁面截圖,兩者之間日曆可能已經變動,
    圖就不再是觸發通知的那個狀態。同一個 session 才能保證圖文一致。

    回傳 (target_dict | None, days_list, shot_path | None)
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # 窄視窗讓整月接近正方形,在手機上才讀得清楚(桌面寬度會變成細長條)
        page = browser.new_page(viewport={"width": 620, "height": 1200}, device_scale_factor=3)
        shot_path = None
        try:
            page.goto(CALENDAR_URL, wait_until="networkidle", timeout=45000)
            try:
                page.wait_for_selector(".daywrap.caldate", timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(2500)

            args = {"mem": MEM_ID, "year": TARGET_YEAR, "month": TARGET_MONTH,
                    "targetDay": TARGET_DAY, "annotate": False}
            result = page.evaluate(PROBE_JS, args)
            assert_month_convention(result.get("todayId"), today)

            target = result.get("target")
            if target and target.get("isWhite"):
                # 只在真的要通知時才花時間標註與截圖
                args["annotate"] = True
                annotated = page.evaluate(PROBE_JS, args)
                if annotated.get("clip"):
                    shot_path = os.path.join(tempfile.gettempdir(), "eboshi_snapshot.png")
                    page.screenshot(path=shot_path, clip=annotated["clip"])
        finally:
            browser.close()

    return result.get("target"), result.get("days", []), shot_path


def summarize_open_days(days):
    """把可預約日壓成 '6, 8, 10-29' 這種緊湊區間字串。"""
    open_days = [d["day"] for d in days if d["isWhite"]]
    if not open_days:
        return "(無)"
    parts, start, prev = [], open_days[0], open_days[0]
    for d in open_days[1:] + [None]:
        if d is not None and d == prev + 1:
            prev = d
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        if d is not None:
            start = prev = d
    return ", ".join(parts)


def push_line(messages):
    """送出訊息陣列。無 token 時只印出內容(方便本機試跑)。"""
    if not LINE_CHANNEL_TOKEN or not LINE_GROUP_ID:
        print("⚠️ 未設定 LINE_CHANNEL_TOKEN / LINE_GROUP_ID,略過推播。內容:")
        print(json.dumps(messages, ensure_ascii=False, indent=2))
        return
    r = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {LINE_CHANNEL_TOKEN}"},
        json={"to": LINE_GROUP_ID, "messages": messages},
        timeout=20,
    )
    r.raise_for_status()


def upload_snapshot(path):
    """
    把截圖推到 GitHub public repo,回傳 raw URL;失敗回 None。

    檔名帶時間戳是必要的:raw.githubusercontent 回 cache-control: max-age=300,
    同名覆蓋會讓 LINE 在 5 分鐘內抓到上一次的舊圖。
    """
    if not SNAPSHOT_REPO:
        return None
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    repo_path = f"snapshots/{stamp}.png"
    with open(path, "rb") as f:
        content = base64.b64encode(f.read()).decode()
    payload = {"message": f"snapshot {stamp}", "content": content}

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
        json.dump(payload, tf)
        tmp = tf.name
    try:
        subprocess.run(
            [GH_BIN, "api", "-X", "PUT",
             f"repos/{SNAPSHOT_REPO}/contents/{repo_path}", "--input", tmp],
            check=True, capture_output=True, timeout=120,
        )
    finally:
        os.unlink(tmp)
    return f"https://raw.githubusercontent.com/{SNAPSHOT_REPO}/main/{repo_path}"


def already_notified():
    try:
        with open(STATE_FILE) as f:
            return f.read().strip() == "1"
    except FileNotFoundError:
        return False


def set_notified(v):
    with open(STATE_FILE, "w") as f:
        f.write("1" if v else "0")


def main():
    today = datetime.date.today()
    stamp = f"{TARGET_YEAR}/{TARGET_MONTH}/{TARGET_DAY}"
    run_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    target, days, shot = check_and_capture(today)

    if target is None:
        print(f"[{run_at}] ❓ 找不到 {stamp} 的日期格,可能該月已不在預設顯示範圍。")
        sys.exit(1)

    desc = " / ".join(target["labels"]) or (
        "無文字但帶 " + ",".join(target["statusClasses"]) if target["statusClasses"] else "空白")

    if not target["isWhite"]:
        set_notified(False)   # 又滿了,重設以便下次變白再通知
        print(f"[{run_at}] {stamp} = {desc} → 尚無空位。")
        return

    print(f"[{run_at}] 🎉 {stamp} = {desc} → 白色(可預約)")
    if already_notified():
        print("   先前已通知過,略過推播。")
        return

    text = (
        f"🏔 烏帽子小屋 {TARGET_MONTH}/{TARGET_DAY} 出現空位了!\n"
        f"日曆該日已變白(可預約)。\n"
        f"\n"
        f"{TARGET_MONTH}月可預約: {summarize_open_days(days)}\n"
        f"\n"
        f"電話預約:{PHONE}\n"
        f"日曆:{CALENDAR_URL}"
    )

    # 文字先送、無條件送。圖片是加分項,絕不能因為它失敗而讓通知漏掉。
    push_line([{"type": "text", "text": text}])
    set_notified(True)
    print("✅ 已送出文字通知。")

    if shot and SNAPSHOT_REPO:
        try:
            url = upload_snapshot(shot)
            if url:
                push_line([{"type": "image",
                            "originalContentUrl": url,
                            "previewImageUrl": url}])
                print(f"✅ 已附上截圖:{url}")
        except Exception as e:
            # 截圖失敗不影響通知本身,只記錄
            print(f"⚠️ 截圖附加失敗(通知本身已送達):{type(e).__name__}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
