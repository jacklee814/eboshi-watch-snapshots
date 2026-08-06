#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
烏帽子小屋空位監控 —— 純 HTTP 版,不需要瀏覽器。

目標日變成可預約時推播純文字通知到 LINE。訊息內含整月空位摘要,
資訊量其實比截圖更適合在手機上快速判讀。

環境變數:
    LINE_CHANNEL_TOKEN   LINE Messaging API channel access token
    LINE_GROUP_ID        推播目的地。U=個人 / C=群組 / R=聊天室 皆可
    EBOSHI_STATE_FILE    (選用) 已通知旗標的路徑。未設則放在腳本旁邊。
                         雲端沙箱沒有持久儲存,不設就等於每次都是全新狀態。

—— freecalend 的三個反直覺陷阱(實測確認) ——
1) 日期的月份是 1-based。誤寫 -1 會監控錯月。
2) 舊版曾用 DOM class `day_aru` 判斷已滿 —— 那其實是「這格有日期」,
   112 格中 92 格都有,納入判斷會讓結果恆為已滿,永遠不通知。
3) 週末底色不是狀態,用背景色判斷會把每個週末誤判。

本版改用官方前端自己呼叫的資料端點,判讀規則見 calendar_http.py。
與瀏覽器版逐日對帳:2026 年 8/9/10 三個月共 92 天,零不一致。

季末注意:本小屋 9/30 為営業最終日,10 月整月「可預約」其實是歇業。
把目標設在營業期外時,「無狀態 = 可預約」的前提不成立。
"""

import datetime
import json
import os
import sys

import requests

from calendar_http import CalendarProtocolError, fetch_month

# ====== 設定 ======
CALENDAR_URL = "https://freecalend.com/open/mem161109"
PHONE = "050-3171-2604(平日 10:00-17:00)"

TARGET_YEAR = 2026
TARGET_MONTH = 9      # 1-based,直接用人類月份
TARGET_DAY = 6

STATE_FILE = os.environ.get(
    "EBOSHI_STATE_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "eboshi_notified.flag"),
)
LINE_CHANNEL_TOKEN = os.environ.get("LINE_CHANNEL_TOKEN", "")
LINE_GROUP_ID = os.environ.get("LINE_GROUP_ID", "")
# ==================


def summarize(days):
    """把可預約日壓成 '8, 10-29' 這種緊湊區間字串。"""
    free = sorted(d for d, v in days.items() if v["free"])
    if not free:
        return "(無)"
    parts, start, prev = [], free[0], free[0]
    for d in free[1:] + [None]:
        if d is not None and d == prev + 1:
            prev = d
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        if d is not None:
            start = prev = d
    return ", ".join(parts)


def push_line(text):
    if not LINE_CHANNEL_TOKEN or not LINE_GROUP_ID:
        print("⚠️ 未設定 LINE_CHANNEL_TOKEN / LINE_GROUP_ID,略過推播。內容:")
        print(text)
        return
    r = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {LINE_CHANNEL_TOKEN}"},
        json={"to": LINE_GROUP_ID, "messages": [{"type": "text", "text": text}]},
        timeout=20,
    )
    r.raise_for_status()


def already_notified():
    try:
        with open(STATE_FILE) as f:
            return f.read().strip() == "1"
    except FileNotFoundError:
        return False


def set_notified(v):
    try:
        with open(STATE_FILE, "w") as f:
            f.write("1" if v else "0")
    except OSError as e:
        # 雲端沙箱可能沒有可寫路徑;不讓它擋住通知本身
        print(f"⚠️ 無法寫入狀態檔 {STATE_FILE}: {e}", file=sys.stderr)


def main():
    run_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    stamp = f"{TARGET_YEAR}/{TARGET_MONTH}/{TARGET_DAY}"

    try:
        days = fetch_month(TARGET_YEAR, TARGET_MONTH)
    except CalendarProtocolError as e:
        # 協定看不懂時中止,絕不預設成「可預約」而發出假警報
        print(f"[{run_at}] ❌ 日曆協定解析失敗,中止:{e}", file=sys.stderr)
        sys.exit(1)
    except requests.RequestException as e:
        print(f"[{run_at}] ❌ 連線失敗:{e}", file=sys.stderr)
        sys.exit(1)

    target = days[TARGET_DAY]
    desc = target["status"] or target["color"] or "空白"

    if not target["free"]:
        set_notified(False)   # 又滿了,重設以便下次變白再通知
        print(f"[{run_at}] {stamp} = {desc} → 尚無空位。")
        return

    print(f"[{run_at}] 🎉 {stamp} → 可預約(來源:{target['source']})")
    if already_notified():
        print("   先前已通知過,略過推播。")
        return

    text = (
        f"🏔 烏帽子小屋 {TARGET_MONTH}/{TARGET_DAY} 出現空位了!\n"
        f"\n"
        f"{TARGET_MONTH}月可預約: {summarize(days)}\n"
        f"\n"
        f"電話預約:{PHONE}\n"
        f"日曆:{CALENDAR_URL}"
    )
    push_line(text)
    set_notified(True)
    print("✅ 已送出通知。")


if __name__ == "__main__":
    main()
