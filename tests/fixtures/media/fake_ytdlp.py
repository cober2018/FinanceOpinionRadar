#!/usr/bin/env python3
"""假 yt-dlp：FAKE_YTDLP_BEHAVIOR ∈ success|private|badurl|timeout|badjson。

success 时从 FAKE_YTDLP_PAYLOAD 读 JSON 原样打到 stdout。
"""
import json
import os
import sys
import time

behavior = os.environ.get("FAKE_YTDLP_BEHAVIOR", "success")

if behavior == "timeout":
    time.sleep(60)
    sys.exit(0)
if behavior in ("private", "badurl"):
    msg = (
        "ERROR: [private] This video is private."
        if behavior == "private"
        else "ERROR: Unsupported URL: some garbage"
    )
    print(msg, file=sys.stderr)
    sys.exit(1)
if behavior == "badjson":
    print("this is not json")
    sys.exit(0)

payload = json.loads(os.environ.get("FAKE_YTDLP_PAYLOAD", "{}"))
print(json.dumps(payload, ensure_ascii=False))
