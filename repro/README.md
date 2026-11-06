# StrayPigeon 复现包

本目录是论文《复用邮箱信令的直连 P2P 通信：设计、实现与受控边界评估》的复现材料：
**逐轮 JSON + 汇总脚本 + 校验脚本 + JSON Schema**。

**不含**原始 pcap、邮箱授权码与真实网络标识。已脱敏：

- 邮箱地址 → `<mailbox>`
- 内网地址（10/127/172.16-31/192.168） → `<lan-ip>`
- 公网地址 → `<public-ip>`
- 本地路径 → `<redacted-path>`

端口号保留（临时端口，无标识性）。

## 内容

```
data/                 25 个 JSON：逐轮记录与汇总
schema.json           这些 JSON 的结构契约（draft-07）
validate.py           解析 + 结构检查 + 用这些 JSON 重算论文里的关键数字
aggregate.py          从逐轮记录重新汇总出论文里的统计量
```

## 怎么用

```bash
python3 validate.py                 # 校验数据自洽并重算关键数字
python3 aggregate.py                # 打印由 rows 重新汇总的统计量
python3 aggregate.py --json         # 同上，机器可读
```

`validate.py` 会打印每项检查的 OK/MISS，任何一项不通过就以非零码退出。

## 文件对应关系

| 文件 | 内容 | 论文位置 |
|---|---|---|
| `e1_100_summary.json` | E1 100 轮连接时间的中位/p95/max | 表 5 |
| `e6_100_summary.json` | E6 同上 | 表 5 |
| `e1_session_20.json` / `e1_session_80.json` | E1 逐轮记录（含 4 个状态时间戳） | 表 5、图 2 |
| `e6_cloud_b_20.json` / `e6_cloud_b_80.json` | E6 逐轮记录 | 表 5、图 2 |
| `sessions_phase_summary.json` | E1/E6 阶段分解与探针启动偏差 | 表 5、图 2 |
| `e3_netns_batch_summary.json` / `e3_netns_batch2_summary.json` | E3 三臂逐轮 | 表 6、图 3 |
| `e3_newsocket_lan.json` | 新 socket 的无 NAT 对照（3/3） | §7.2 |
| `e4_apdm_batch.json` / `e4_apdm_actual.json` | E4 30 次打洞记录（28 有效） | 表 7 |
| `e5_lan_summary.json` | LAN 4/64/256 MiB | 表 8、图 4 |
| `e5_netem_summary.json` | netem 100 Mbps 与 100 ms+1% | 表 8 |
| `e5_netem_decomp.json` | 弱网分解的 13 轮 | §7.4 |
| `e5_big_files.json` / `e5_ab_4c4g.json` | 1/2 GiB | 表 8、图 4 |
| `e5_netem_summary.json` | netem 两档 | 表 8 |
| `e1b_lan_4m.json` / `e1b_lan_64m.json` | 真实邮箱 + LAN 文件传输 | §7.1 |
| `e6b_cloud.json` | 云→家庭 NAT 1 MiB ×6 | §7.1 |
| `e2_netns_batch_summary.json` 等 | E2 PING 对照（off 有效 5、on 25） | 表 9 |
| `e8_pinning_pilot.json` | pinning 50/50 | 表 10 |
| `e9_batch.json` | 16 会话 / MTU / 模拟续传 | 表 11 |

## 不重跑实验能验证到什么

- 每个数字都能从 `data/` 重算，包括中位数、成功率、SHA 一致性；
- 结构契约由 `schema.json` 固定，字段缺失或类型不符会被 `validate.py` 指出；
- 无法验证的部分：真实的邮件投递时延、NAT 行为、netem 拓扑——这些需要按论文 §6 重建环境。

## 已知限制

- E2 的 PING-off 扩量批次（20 次）因 testbed 超时未计入，只保留 5 次有效样本，`e2_netns_batch2_summary.json` 里 `exit=124` 的行就是那些尝试。
- E4 的 30 次里有 2 次 testbed 未起来，记录中 `shared` 为空。
- E6b 只有 6 轮且呈双峰，不足以给出吞吐分布。
