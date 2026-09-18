# 平台 adapter 失效（抖音/YouTube/B 站改版）

**现象**：discover/resolve 大面积 failed；dtk 返回风控/结构错误。

**诊断**：
- 抖音：`curl $DOUYIN_API_BASE_URL/docs` 探活；README「升级外部镜像为第一响应」
- YouTube/B 站：手动 resolve 一个已知视频复现

**恢复**：dtk → 升级镜像；yt-dlp → `pip install -U yt-dlp`；仍不行 → 按 ADR-0007 走回退路线。

**会重复数据吗**：不会（upsert 幂等）。
**需要重跑吗**：失败轮次等下一 beat 自动补。
