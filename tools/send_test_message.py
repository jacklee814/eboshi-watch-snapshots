#!/usr/bin/env python3
"""Send one clearly-labelled test message to the configured LINE group.

Verifies the notification path the watcher actually uses -- POST to
api.line.me/v2/bot/message/push -- which a read-only GET probe cannot cover.
Reuses eboshi_watch.push_line so it exercises the real code path, not a
reimplementation of it.

Run it with:  python3 tools/send_test_message.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MESSAGE = (
    "【測試訊息】烏帽子小屋空床監控已從電腦搬到雲端排程，"
    "平日 JST 09:30-16:30 每小時檢查一次 9/6 的空位。"
    "這則只是確認通知管道暢通，沒有空位，請忽略。"
)


def main():
    import eboshi_watch as W

    print(f"LINE_GROUP_ID set: {bool(W.LINE_GROUP_ID)}")
    print(f"LINE_CHANNEL_TOKEN set: {bool(W.LINE_CHANNEL_TOKEN)}")
    if not (W.LINE_GROUP_ID and W.LINE_CHANNEL_TOKEN):
        print("FAIL: LINE credentials missing from the environment")
        return 1

    print(f"sending: {MESSAGE}")
    try:
        W.push_line(MESSAGE)
    except Exception as e:
        print(f"FAIL {type(e).__name__}: {e}")
        return 1
    print("SENT OK -- push_line returned without raising")
    return 0


if __name__ == "__main__":
    sys.exit(main())
