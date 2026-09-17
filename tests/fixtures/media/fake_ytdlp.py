#!/usr/bin/env python3
"""假 yt-dlp：FAKE_YTDLP_BEHAVIOR ∈ success|private|badurl|timeout|badjson|writeout。

success 时从 FAKE_YTDLP_PAYLOAD 读 JSON 原样打到 stdout。
writeout：往 -o 模板所在目录写 FAKE_YTDLP_WRITE_NAME（内容 FAKE_YTDLP_CONTENT），
模拟 --write-subs/--download 的产物。
FAKE_YTDLP_ARGS_FILE 设置时（任何行为）把 argv dump 成 JSON 供断言。
"""
import json
import os
import sys
import time

behavior = os.environ.get("FAKE_YTDLP_BEHAVIOR", "success")

args_file = os.environ.get("FAKE_YTDLP_ARGS_FILE")
if args_file:
    with open(args_file, "w") as fh:
        json.dump(sys.argv, fh, ensure_ascii=False)

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
if behavior == "progress":
    # 模拟真实下载：stdout 是进度日志（非 JSON），产物照常落盘，退出码 0
    print("[BiliBili] Extracting URL: https://example.com/video")
    print("[BiliBili] Format(s) 1080P 高码率 are missing; you have to become a premium member")
    print("[download] 100% of 12.34MiB in 00:01")
    outdir = os.path.dirname(sys.argv[sys.argv.index("-o") + 1])
    name = os.environ.get("FAKE_YTDLP_WRITE_NAME", "abc123.m4a")
    with open(os.path.join(outdir, name), "w") as fh:
        fh.write(os.environ.get("FAKE_YTDLP_CONTENT", ""))
    sys.exit(0)
if behavior == "writeout":
    outdir = os.path.dirname(sys.argv[sys.argv.index("-o") + 1])
    name = os.environ.get("FAKE_YTDLP_WRITE_NAME", "abc123.zh-Hans.json3")
    with open(os.path.join(outdir, name), "w") as fh:
        fh.write(os.environ.get("FAKE_YTDLP_CONTENT", ""))

payload = json.loads(os.environ.get("FAKE_YTDLP_PAYLOAD", "{}"))
print(json.dumps(payload, ensure_ascii=False))
