# yt-dlp 下载失败

**现象**：prepare failed，last_error 含 yt-dlp 退出码 / HTTP 403 / "Sign in to confirm"。

**诊断**：`docker exec financeopinionradar-postgres-1 psql -U radar -d radar -tAc "SELECT metadata_json->'last_error'->>'message' FROM source_item WHERE id=<id>"`

**恢复**：
- cookie 过期 → 重新导出浏览器 cookie（Netscape），`.env` 更新 `YTDLP_COOKIES_FILE`
- YouTube PO Token 墙 → 已挂账（需 bgutil POT provider），暂不支持
- 偶发 403 → 直接重驱 prepare（README「抖音 VOD 跟踪」命令同样适用于 YT/B 站）

**会重复数据吗**：不会（failed→resolved 幂等白名单）。
**需要重跑吗**：需要，手动重驱。
