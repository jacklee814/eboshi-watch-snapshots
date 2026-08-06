#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
freecalend 日曆狀態讀取器 —— 純 HTTP,不需要瀏覽器。

協定(2026-08-06 逆向並與瀏覽器逐日對帳驗證):
  POST https://freecalend.com/open/data
  keys 參數是「雙重 URL 編碼」的 JSON: {"data":[[key, ver, ver, flags, []], ...]}
  key 格式 cald-<MEM>-<year>-<month>-<day>,月份是 1-based。
  flags 必須是 [["ok-<MEM>-all-r-cald", true]] —— 少了它伺服器一律回 none,
  看起來像「沒有變更」,實際上是沒授權讀取。

回應判讀:
  ["get", key, ver, "noexs"]            該日無記錄        → 可預約
  ["set", <json>] 且 status/color 皆空   有記錄但無狀態    → 可預約
  ["set", <json>] 且 status 或 color 有值                  → 額滿/休業
  其他                                   語意不明          → 丟例外,絕不當成可預約

最後一條是刻意的:這支程式的失敗模式若偏向「可預約」,就會在額滿時對群組發假警報。
寧可壞掉也不要說謊。
"""
import json
import urllib.parse

import requests

MEM_ID = "161109"
BASE_VER = "1602144014"
API = "https://freecalend.com/open/data"
DAYS_IN_MONTH = {1: 31, 2: 29, 3: 31, 4: 30, 5: 31, 6: 30,
                 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}


class CalendarProtocolError(RuntimeError):
    """伺服器回應不符合已知協定 —— 寧可中止也不要猜。"""


def fetch_month(year, month, mem_id=MEM_ID, timeout=30):
    """
    回傳 {day: {"free": bool, "status": str, "color": str, "source": str}}。
    任何無法明確判讀的日子都會讓整個呼叫丟 CalendarProtocolError。
    """
    ndays = DAYS_IN_MONTH[month]
    if month == 2 and (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)):
        ndays = 29
    prefix = f"cald-{mem_id}-{year}-{month}-"
    ok_flag = [[f"ok-{mem_id}-all-r-cald", True]]

    entries = [[f"{prefix}{d}", BASE_VER, BASE_VER, ok_flag, []]
               for d in range(1, ndays + 1)]
    payload = json.dumps({"data": entries}, separators=(",", ":"))
    enc = urllib.parse.quote(urllib.parse.quote(payload, safe=""), safe="")
    body = (f"target_mem_no={mem_id}&version=3&mem_no=0"
            f"&keys={enc}&dokisuru=true&fversion=-1")

    r = requests.post(
        API, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                 "Referer": f"https://freecalend.com/open/mem{mem_id}",
                 "User-Agent": "Mozilla/5.0"},
        timeout=timeout,
    )
    r.raise_for_status()

    try:
        items = json.loads(r.text)
    except json.JSONDecodeError as e:
        raise CalendarProtocolError(f"回應不是 JSON: {e}; 前 200 字: {r.text[:200]}")

    days = {}
    for item in items:
        action = item[0]
        if action == "set":
            inner = json.loads(item[1])
            key = inner[2]
            if not key.startswith(prefix):
                continue
            arr = inner[3]
            status = (arr[1] if len(arr) > 1 else "") or ""
            color = (arr[2] if len(arr) > 2 else "") or ""
            days[int(key[len(prefix):])] = {
                "free": not status and not color,
                "status": status, "color": color, "source": "set",
            }
        elif action == "get":
            key = item[1]
            if not key.startswith(prefix):
                continue
            flag = item[3] if len(item) > 3 else None
            if flag != "noexs":
                raise CalendarProtocolError(
                    f"{key} 回 get 但旗標是 {flag!r} 而非 'noexs',語意未知")
            days[int(key[len(prefix):])] = {
                "free": True, "status": "", "color": "", "source": "noexs",
            }

    missing = [d for d in range(1, ndays + 1) if d not in days]
    if missing:
        raise CalendarProtocolError(
            f"{year}/{month} 有 {len(missing)} 天沒有明確答覆: {missing}")
    return days


if __name__ == "__main__":
    days = fetch_month(2026, 9)
    free = sorted(d for d, v in days.items() if v["free"])
    print("可預約:", free)
    print("9/6 :", days[6])
