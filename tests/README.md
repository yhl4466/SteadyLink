# 测试

针对“稳定连接管理器”关键路径的自动化故障注入测试，不依赖外网（下载/HLS 使用本地 HTTP 服务器）。

## 运行

```bash
python tests/run_tests.py -v
```

或者：

```bash
python -m unittest discover -s tests -t . -v
```

## 覆盖范围

| 文件 | 内容 |
|---|---|
| `test_core_tokens.py` | 核心启动令牌：过期线程不能停掉新连接；坏节点/坏路径不残留进程 |
| `test_monitor_race.py` | 自动重连使用当前活动节点；停止标志与代际编号能中止过期任务 |
| `test_proxy_backup.py` | 系统代理备份结构校验、备份损坏拒绝接管、崩溃遗留恢复（会恢复你的原始代理设置） |
| `test_online_stats.py` | 在线时长按“两次检查都正常”的区间累计，断线等待不计 |
| `test_download_http.py` | 206/Content-Range 校验、If-Range/ETag 变化检测、资源变化后自动重下、忽略 Range 回退单流、分片合并、跨资源续传丢弃 |
| `test_hls.py` | AES-128 解密正确性、错误密钥必须失败、不支持的加密方式明确拒绝 |
| `test_resume_meta.py` | URL/大小/ETag/HLS 摘要（含密钥 URI/IV/序列号）不一致时清空分片；无元数据的旧分片一律重下 |

## 说明

- `test_core_tokens.py` 需要 `bin/xray.exe`（运行 `python scripts/setup_bin.py` 下载），缺失时自动跳过；
- `test_proxy_backup.py` 仅 Windows 运行，会读写 HKCU 代理设置，用例结束会恢复原始值；
- HLS 测试需要 `pycryptodome`（已在 `requirements.txt`）；
- 测试不会写入仓库目录：运行时会临时重定向 `v2core` 的 runtime 路径，下载文件放在系统临时目录。
