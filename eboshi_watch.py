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

import base64
import datetime
import os
import subprocess
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

# 「已通知」旗標的儲存位置。雲端沙箱每次都是全新的,本機檔案存不下來,
# 沒有持久狀態就會每小時重複通知。設了 token + repo 就改存 GitHub。
# 未設則沿用本機檔案 —— 本機 launchd 的行為完全不變。
STATE_TOKEN = os.environ.get("EBOSHI_GH_TOKEN", "")
STATE_REPO = (os.environ.get("EBOSHI_STATE_REPO")
              or os.environ.get("EBOSHI_SNAPSHOT_REPO") or "")
STATE_PATH = os.environ.get("EBOSHI_STATE_PATH", "state/notified.flag")
USE_REMOTE_STATE = bool(STATE_TOKEN and STATE_REPO)

# 每次執行的紀錄。與上面的「狀態」刻意分開:
# 狀態需要明確的 EBOSHI_GH_TOKEN 才啟用(行為改變,不該意外開啟);
# 紀錄則是純附加、失敗無害,所以本機可退而使用 gh CLI 既有的認證,
# 不必把任何憑證寫進設定檔。
LOG_REPO = os.environ.get("EBOSHI_LOG_REPO") or STATE_REPO
LOG_SKIPS = os.environ.get("EBOSHI_LOG_SKIPS", "1") != "0"   # 是否記錄窗口外的略過
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


def _gh(method, **kw):
    url = f"https://api.github.com/repos/{STATE_REPO}/contents/{STATE_PATH}"
    return requests.request(
        method, url,
        headers={"Authorization": f"Bearer {STATE_TOKEN}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"},
        timeout=20, **kw)


def _remote_state():
    """回傳 (旗標內容 or None, sha or None)。404 = 檔案還不存在。"""
    r = _gh("GET")
    if r.status_code == 404:
        return None, None
    r.raise_for_status()
    body = r.json()
    return base64.b64decode(body["content"]).decode().strip(), body["sha"]


def _log_token():
    """紀錄用的 token:優先環境變數,否則借用本機 gh CLI 的認證。"""
    if STATE_TOKEN:
        return STATE_TOKEN
    try:
        r = subprocess.run(["gh", "auth", "token"], capture_output=True,
                           text=True, timeout=10)
        return r.stdout.strip() or None
    except Exception:
        return None


def append_run_log(line):
    """
    把一行執行紀錄附加到 repo 的 logs/YYYY-MM.log。

    純附加、盡力而為:任何失敗只印警告,絕不影響監控結果。
    呼叫點刻意放在推播之後,確保紀錄壞掉不會擋住通知。
    按月分檔,避免單一檔案無限成長(每次更新都要整檔重傳)。
    """
    if not LOG_REPO:
        return
    token = _log_token()
    if not token:
        return

    now = datetime.datetime.now(JST)
    path = f"logs/{now:%Y-%m}.log"
    url = f"https://api.github.com/repos/{LOG_REPO}/contents/{path}"
    headers = {"Authorization": f"Bearer {token}",
               "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}

    for attempt in (1, 2):          # sha 衝突時重試一次
        try:
            r = requests.get(url, headers=headers, timeout=20)
            if r.status_code == 404:
                existing, sha = "", None
            else:
                r.raise_for_status()
                body = r.json()
                existing = base64.b64decode(body["content"]).decode()
                sha = body["sha"]

            content = (existing.rstrip("\n") + "\n" + line).lstrip("\n") + "\n"
            payload = {"message": f"log: {line[:60]}",
                       "content": base64.b64encode(content.encode()).decode()}
            if sha:
                payload["sha"] = sha
            p = requests.put(url, headers=headers, json=payload, timeout=20)
            if p.status_code == 409 and attempt == 1:
                continue            # 併發寫入,重讀 sha 再試
            p.raise_for_status()
            return
        except Exception as e:
            if attempt == 2:
                print(f"⚠️ 寫入執行紀錄失敗(不影響監控):{e}", file=sys.stderr)


def already_notified():
    if USE_REMOTE_STATE:
        try:
            content, _ = _remote_state()
            return content == "1"
        except Exception as e:
            # 讀不到狀態時偏向「尚未通知」—— 寧可多送一則,也不要因為狀態服務
            # 出問題而讓真正的空位通知消失。但要留下明顯的痕跡。
            print(f"⚠️ 讀取遠端狀態失敗,視為尚未通知(可能重複推播):{e}",
                  file=sys.stderr)
            return False
    try:
        with open(STATE_FILE) as f:
            return f.read().strip() == "1"
    except FileNotFoundError:
        return False


def set_notified(v):
    value = "1" if v else "0"
    if USE_REMOTE_STATE:
        try:
            _, sha = _remote_state()
            payload = {"message": f"state: notified={value}",
                       "content": base64.b64encode(value.encode()).decode()}
            if sha:
                payload["sha"] = sha
            r = _gh("PUT", json=payload)
            r.raise_for_status()
        except Exception as e:
            print(f"⚠️ 寫入遠端狀態失敗,下次執行可能重複推播:{e}", file=sys.stderr)
        return
    try:
        with open(STATE_FILE, "w") as f:
            f.write(value)
    except OSError as e:
        print(f"⚠️ 無法寫入狀態檔 {STATE_FILE}: {e}", file=sys.stderr)


def _run():
    """執行一次檢查。回傳 (log 用的結果字串, 結束碼)。"""
    run_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    stamp = f"{TARGET_MONTH}/{TARGET_DAY}"

    # 窗口外直接離開,連網路都不碰。這是「檢查窗口」的權威判斷,
    # 不依賴 launchd 的排程時間 —— 那個會跟著機器時區跑。
    inside, when = in_booking_window()
    if not inside:
        print(f"[{run_at}] 略過({when},預約時段外)")
        # when 形如「2026-08-07 00:05 JST Fri 早於 09:30」,取原因即可,
        # 日期與星期已由 log 行首的時間戳表達。
        parts = when.split(" ", 4)
        return f"SKIP   {parts[4] if len(parts) > 4 else when}", 0

    try:
        days = fetch_month(TARGET_YEAR, TARGET_MONTH)
    except CalendarProtocolError as e:
        # 協定看不懂時中止,絕不預設成「可預約」而發出假警報
        print(f"[{run_at}] ❌ 日曆協定解析失敗,中止:{e}", file=sys.stderr)
        return f"ERROR  protocol: {e}", 1
    except requests.RequestException as e:
        print(f"[{run_at}] ❌ 連線失敗:{e}", file=sys.stderr)
        return f"ERROR  network: {type(e).__name__}", 1

    target = days[TARGET_DAY]
    desc = target["status"] or target["color"] or "空白"

    if not target["free"]:
        set_notified(False)   # 又滿了,重設以便下次變白再通知
        print(f"[{run_at}] {TARGET_YEAR}/{stamp} = {desc} → 尚無空位。")
        return f"CHECK  {stamp}={desc}  no-action", 0

    print(f"[{run_at}] 🎉 {TARGET_YEAR}/{stamp} → 可預約(來源:{target['source']})")
    if already_notified():
        print("   先前已通知過,略過推播。")
        return f"CHECK  {stamp}=FREE  already-notified", 0

    text = (
        f"🏔 烏帽子小屋 {TARGET_MONTH}/{TARGET_DAY} 出現空位了!\n"
        f"\n"
        f"該日目前狀態:可預約(日曆已變白)\n"
        f"\n"
        f"電話預約:{PHONE}\n"
        f"日曆:{CALENDAR_URL}"
    )
    try:
        push_line(text)
    except Exception as e:
        print(f"❌ 推播失敗:{e}", file=sys.stderr)
        return f"CHECK  {stamp}=FREE  PUSH-FAILED: {e}", 1
    set_notified(True)
    print("✅ 已送出通知。")
    return f"CHECK  {stamp}=FREE  ★NOTIFIED★", 0


def main():
    outcome, code = _run()

    # 紀錄放在最後、包在 try 裡:寫 log 是網路操作會失敗,
    # 而監控是主線 —— 旁支斷了主線要照走。
    if outcome.startswith("SKIP") and not LOG_SKIPS:
        pass
    else:
        try:
            now = datetime.datetime.now(JST)
            append_run_log(f"{now:%Y-%m-%d %H:%M} JST {now:%a}  {outcome}")
        except Exception as e:
            print(f"⚠️ 寫入執行紀錄失敗(不影響監控):{e}", file=sys.stderr)

    if code:
        sys.exit(code)


if __name__ == "__main__":
    main()
