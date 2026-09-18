# ASR 显存/内存溢出

**现象**：transcribing 卡死或 failed，日志含 CUDA OOM / MemoryError；本机 CPU mlx 模式则表现为进程被 kill。

**诊断**：`ps aux | grep python | awk '{print $3,$4}'` 看内存；`dmesg`/Console.app 看 OOM kill。

**恢复**：降 `ASR_COMPUTE_TYPE=int8`（已是）或换 small 模型； mlx 模式下 mlx-metal 按统一内存自动管理，减小并发（worker `--concurrency=1`）后重跑。

**会重复数据吗**：不会——转录段落按 (item, sequence) 唯一，重跑前残留会被幂等跳过。
**需要重跑吗**：prepare failed → 重驱即可。
