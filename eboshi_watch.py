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

# 只在日本電話預約時段內檢查。空位幾乎都來自電話取消,時段外偵測到也無法行動。
# 起點提前 30 分鐘,讓開線那一刻就已經是最新狀態。
#
# 時區刻意寫死 JST 而不是依賴系統時區:launchd 的排程時間會跟著機器所在時區跑,
# 出國時整個窗口會平移。用固定的 UTC+9 判斷,不管機器在台灣還是日本都對齊同一段時間。
# JST 全年無日光節約時間,所以固定 offset 是安全的。
JST = datetime.timezone(datetime.timedelta(hours=9))
WINDOW_START = datetime.time(9, 30)    # 電話 10:00 開線,提前 30 分
WINDOW_END = datetime.time(17, 0)      # 電話 17:00 收線
WEEKDAYS_ONLY = True                   # 平日 = 週一~週五(未排除日本國定假日)

STATE_FILE = os.environ.get(
    "EBOSHI_STATE_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "eboshi_notified.flag"),
)
LINE_CHANNEL_TOKEN = os.environ.get("LINE_CHANNEL_TOKEN", "")
LINE_GROUP_ID = os.environ.get("LINE_GROUP_ID", "")
# ==================


def in_booking_window(now_jst=None):
    """回傳 (是否在窗口內, 說明字串)。時間一律以 JST 判斷。"""
    now = now_jst or datetime.datetime.now(JST)
    stamp = now.strftime("%Y-%m-%d %H:%M JST %a")
    if WEEKDAYS_ONLY and now.weekday() >= 5:      # 5=六 6=日
        return False, f"{stamp} 非平日"
    t = now.time()
    if t < WINDOW_START:
        return False, f"{stamp} 早於 {WINDOW_START:%H:%M}"
    if t > WINDOW_END:
        return False, f"{stamp} 晚於 {WINDOW_END:%H:%M}"
    return True, stamp


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

    # 窗口外直接離開,連網路都不碰。這是「檢查窗口」的權威判斷,
    # 不依賴 launchd 的排程時間 —— 那個會跟著機器時區跑。
    inside, when = in_booking_window()
    if not inside:
        print(f"[{run_at}] 略過({when},預約時段外)")
        return

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
        f"該日目前狀態:可預約(日曆已變白)\n"
        f"\n"
        f"電話預約:{PHONE}\n"
        f"日曆:{CALENDAR_URL}"
    )
    push_line(text)
    set_notified(True)
    print("✅ 已送出通知。")


if __name__ == "__main__":
    main()
