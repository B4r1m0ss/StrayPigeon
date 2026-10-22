# StrayPigeon

> **Email-signaled, end-to-end encrypted P2P chat.**
> 一只迷途的信鸽，也总能找到同伴。

![tests](https://img.shields.io/badge/tests-85%20passing-brightgreen)
![python](https://img.shields.io/badge/python-3.10%2B-blue)
![license](https://img.shields.io/badge/license-GPL--3.0-green)
![platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-lightgrey)

StrayPigeon 单一主窗口（邮箱信令 + UDP 打洞 + QUIC 数据面）：

![StrayPigeon GUI](docs/images/gui.png)

**StrayPigeon** 是一个完全开源的点对点聊天应用。它用你已有的邮箱（SMTP/IMAP）做信令，不依赖自建信令服务器；连接建立后，聊天和文件直接走 QUIC。

- **信令/打洞**：legacy `Session`，SMTP/IMAP + STUN + P2P1 UDP punch；
- **数据面**：aioquic QUIC，单 UDP 端口、最多 16 个会话、文件传输；
- **入口**：`python -m p2p.gui` → `SingleWindow`，只有一个 GUI。

## 当前状态

- **真实 163 ↔ 126 手工验收通过**：跨提供商 SMTP 信令、STUN/打洞、QUIC 连接、文字聊天、双向文件、连续文件、QUIC 心跳；
- **85 个单元测试通过**；系统 Python 没装 aioquic 时：85 tests，12 skipped；
- **QUIC 直接建立在 legacy Session 打洞后的同一个 UDP socket 上**，不再二次 P2P1；
- **QUIC PING 每 15 秒**，`idle_timeout=600s`；
- **尚未做安全审计**；QUIC TLS 当前是 `CERT_NONE`，证书指纹固定（fingerprint pinning）还没实现，不能当作生产级端到端认证。

## 为什么叫 StrayPigeon

邮件是信鸽：它只负责把“地址”和“公钥/候选”送到对方手里。真正的消息不经过邮件服务器，而是两只鸽子在 NAT 之间直接对上暗号。

项目原名 `email-p2p`。代码里的 vault 目录仍保留 `email-p2p`，是为了兼容已有数据。

## 它是怎么工作的

```text
┌──────────────┐        SMTP/IMAP        ┌──────────────┐
│  机器 A       │  p2p1/<sid>/<seq>      │  机器 B       │
│  Session     │ ◀────────────────────▶ │  Session     │
└──────┬───────┘   候选 / PSK 封装       └──────┬───────┘
       │ STUN + P2P1 punch                      │
       ▼                                        ▼
┌─────────────────────────────────────────────────────┐
│        同一个 UDP socket / NAT 五元组                │
│   QUIC client  ◀──── QUIC/TLS ────▶  QUIC server    │
│   chat stream / file stream / PING                  │
└─────────────────────────────────────────────────────┘
```

### 1. 邮箱信令

双方各自有 identity（自己的邮箱），每个 conversation 对应一个 contact。  
邀请码 `p2p1:<Base32>` 带外发送，里面包含本次对话的 PSK、role 和双方邮箱；**邀请码不经过邮件**。

### 2. STUN + P2P1 打洞

legacy `Session`：

```text
bind 同一个 UDP 端口
→ STUN 收集 srflx 候选
→ 邮箱交换 CANDIDATE
→ 双方互发 P2P1 探测包
→ punch 返回对方 observed peer_addr
```

### 3. handoff：同一个 socket 交给 QUIC

```text
Session 打洞成功
→ 不关闭 socket
→ 把 sock + peer_addr + sid + psk + role 交给 QuicController
→ initiator: QuicConnection.connect(peer_addr)
→ responder: QuicServer 直接挂在这个 sock 上
→ 第一包就是 QUIC Initial
```

**不会在 QUIC 层再打一次 P2P1。**  
这是本项目最重要的一点：NAT 映射绑定在本地 UDP 端口上，换 socket 或重新打洞都会破坏它。

### 4. QUIC 数据面

- QUIC/TLS 1.3 握手，通常 1 个 RTT；
- 文字消息走独立 stream；
- 文件分块走 stream；
- QUIC PING 每 15 秒发送一次，保持 NAT 映射和 idle timer；
- 连接意外关闭时，GUI 会显示 `closed`，不会假装还连着。

## 特性

- **邮箱即信令**：不需要额外信令服务器；SMTP 或 IMAP 都能送候选。
- **Contact 实例**：每个对话一个 contact，独立 PSK、role、local_port；邀请码自动携带 role，加入方保存相反 role。
- **多联系人**：一个 identity 可以有多个 contact；左侧列表 + 每个 contact 一个 tab。
- **QUIC 多会话**：单 UDP 监听端口、最多 16 个并发会话；并发上限 1–16 可调。
- **文件传输**：1–4 MiB 分块、`.part`、`.part.json` 续传、SHA-256 校验、原子改名、进度条；initiator/responder 两个方向都支持；同一个 tab 串行发送，上一份完成后再发下一份。
- **QUIC 心跳**：15 秒 PING；空闲阈值 600 秒。
- **连接反馈**：`Connecting…` 按钮、tab 内不确定进度条、`[conn]` 阶段日志、左侧状态栏。
- **加密 vault**：scrypt + ChaCha20-Poly1305，保存邮箱授权码和 PSK；口令不落盘。
- **GUI + CLI**：PyQt6 桌面端；命令行可用于脚本/服务器环境。
- **删除 contact**：同时删除 contact、PSK 和 vault 中对应条目；不可恢复，不通知对方。
- **跨平台**：Windows / Linux。

## 快速开始

### 0. 准备

- Python 3.10+
- 两个支持 IMAP/SMTP 的邮箱（推荐不同提供商，例如 126 ↔ 163）
- 各自的邮箱授权码/应用专用密码（不是登录密码）
- 能访问 UDP 外网；对称 NAT（APDM）目前没有中继兜底
- QUIC 需要 `aioquic`（已包含在 `requirements-gui.txt`）

### 1. 安装

Linux / Arch（系统 Python 受 PEP 668 保护，用 venv）：

```bash
git clone https://github.com/B4r1m0ss/StrayPigeon.git
cd StrayPigeon
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements-gui.txt
.venv/bin/python -m p2p.gui
```

Windows：

```powershell
git clone https://github.com/B4r1m0ss/StrayPigeon.git
cd StrayPigeon
py -3 -m pip install -r requirements-gui.txt
py -3 -m p2p.gui
```

只跑 CLI：

```bash
python3 -m pip install -r requirements.txt
```

### 2. 创建身份

```bash
python3 scripts/setup.py --mode smtp
```

只填写**你自己的**邮箱、IMAP/SMTP 主机、授权码和 vault 口令。这一步不会询问对话对象。

GUI 首次启动也可以点 **Create identity…**，走同样的向导。

### 3. 创建/加入对话

```bash
python3 scripts/contact.py new --peer bob@example.com
# 默认本端 initiator；邀请码里带 role
# 把打印出来的 p2p1: 邀请码带外发给对方

python3 scripts/contact.py join "p2p1:..."
python3 scripts/contact.py list
```

一个 identity 可以有多个 contact；每个 contact 有自己的 PSK、role、local_port。

## GUI

```bash
python3 -m p2p.gui
# Windows: py -3 -m p2p.gui
```

### 界面

- 左侧：identity、**Vault…**、contact 列表、**New contact / Join invite / Delete contact**、Max sessions、Local UDP port、Peer host/port、**Connect / Disconnect**、**Allow UDP**、状态栏；
- 右侧：每个 contact 一个 tab；**Send**、**Send file…**、文件进度、连接状态。

### 连接流程

1. 首次启动点 **Create identity…**；
2. 解锁 vault；
3. 选 contact；没有就 **New contact**（生成 initiator + 邀请码）或 **Join invite**（自动存 responder）；
4. 点 **Connect**；
5. 状态依次显示：

```text
[conn] starting signaling / STUN / UDP punch…
[conn] local UDP socket bound
[conn] peer candidates received
[conn] UDP punching…
[conn] punch done; handing off to QUIC
[conn] starting QUIC handshake on the punched socket…
[conn] connected
```

6. 输入消息，Enter / **Send**；**Send file…** 发文件；**Disconnect** 断开。

### 注意

- **启动时不会自动连接**：MailRouter 只更新候选，必须手动点 Connect；
- Connect 期间 **Connect** 按钮禁用，结束后自动恢复；
- 连接期间可以点 **Disconnect** 中止信令/打洞；
- **Delete contact** 会停止该 contact 的 worker/QUIC 会话，并删除 vault 中对应条目；
- **Vault…** 支持修改口令、清除/重置、锁定；
- **Allow UDP** 放行本机当前 UDP 端口（Windows UAC；Linux 显示 ufw/firewalld/nft 命令）；
- 接收文件在**启动 GUI 时的工作目录**下的 `state/received/`；传输中是 `.part`，完成后 SHA-256 校验再改名。

### SMTP TLS

- **465 = 隐式 TLS（STARTTLS 不勾）**；
- **587 = STARTTLS（勾选）**。

163 / 126 的 SMTP 用 465，STARTTLS 保持关闭。

### 实网建议

```text
机器 A（Windows/163）: New contact（initiator），local port 40000
机器 B（Linux/126）:   Join invite（responder），local port 40000
STUN: stun.miwifi.com:3478, stun.cloudflare.com:3478
```

- 两边都要点 **Connect**；
- 两边都要允许本机 UDP 端口（至少 Private/Domain）；
- `advertise_host` 当前固定 `127.0.0.1`，跨机器主要靠 STUN srflx；LAN 纯 host 候选需要后续把本机 LAN IP 暴露出来。

## CLI

```bash
python3 scripts/run_vault.py --vault ~/.config/email-p2p/vault.json \
  --contact bob@example.com --role initiator --port 40000
```

只有一个 contact 时可以省略 `--contact`。Windows 的 vault 路径是：

```text
%APPDATA%\email-p2p\vault.json
```

CLI 仍然使用 legacy Session 的 UDP AEAD 数据面；GUI 使用 QUIC。

## 什么是 `p2p1`

有两个不同的东西：

1. **邀请码**：`p2p1:<Base32>`，包含双方邮箱、本次对话 PSK、role；用于 New contact / Join invite。**它不进邮件**，仍然复制粘贴、带外发送。
2. **信令邮件主题**：`p2p1/<sid>/<seq>`，邮件正文是 PSK 加密后的 AEAD envelope。邮件服务商能看到主题、sid/seq、双方地址和时间，但看不到 PSK/邀请码；MailRouter 只读取这些信令邮件。

## 实网验收

已手工验证：

```text
163（Windows） ↔ 126（Linux）
跨提供商 SMTP 信令
STUN + P2P1 punch
QUIC over the same punched UDP socket
文字聊天
文件双向传输
连续多文件
QUIC PING 心跳 / 空闲后再聊天
```

单元/offscreen 测试还覆盖：

- 16 会话单端口、16 × 1 MB 并发文件；
- 4 MB 单文件、断点续传、SHA-256；
- handoff 后文件双向、连续多文件；
- 连接失败/关闭、超时阶段、worker 生命周期；
- SingleWindow 的 vault/contact/firewall shell；
- 85 tests，系统 Python 无 aioquic 时 12 skipped。

## 安全模型

- **PSK**：每个 contact 独立 32 字节，随机生成；编码在邀请码里，带外传递；不经过邮箱。
- **邀请码**：含双方邮箱/共享邮箱、PSK、role、checksum；不是身份口令，但拿到邀请码的人可以加入该对话。
- **vault**：邮箱授权码和 PSK 用 `scrypt(n=2^15, r=8, p=1)` + `ChaCha20-Poly1305` 加密；口令不保存。
- **Legacy 数据面**：X25519 临时密钥 → HKDF → ChaCha20-Poly1305；序号防重放；心跳不携带聊天内容。
- **QUIC 数据面**：QUIC/TLS 加密；**当前客户端 `CERT_NONE`，没有证书指纹固定**，所以只能算实验性加密通道，不能算生产级端到端认证。后续需要把服务器证书 fingerprint 写入邀请码/contact 并强制校验。
- **信令**：IMAP 993 / SMTP 465 走 TLS；邮件服务商能看到 `p2p1/<sid>/<seq>` 元数据，但看不到 PSK/聊天内容；邀请码不经过邮件。
- **没有中继**：没有 TURN/relay；双方都是对称 NAT 时可能打洞失败。

## 已知限制

- **QUIC TLS 证书未固定**：当前 `CERT_NONE`，存在 MITM 风险；生产前必须实现 fingerprint pinning。
- **没有 TURN/relay 兜底**：对称 NAT/CGNAT 下可能无法直连。
- **`advertise_host` 固定 127.0.0.1**：跨机器主要依赖 STUN srflx；LAN 纯 host 候选需要后续加 GUI 字段。
- **大文件未做 100 GB 级实测**：目前 4 MB 单文件、16 × 1 MB 并发；`.part.json` 每 chunk 写一次，超大文件需要优化。
- **消息不持久化、不离线补发**：聊天记录只在当前进程内；断线后旧消息不补发。
- **无多设备同步、语音、群聊**。
- **GUI 仍是 MVP**：`SingleWindow` 是唯一入口；旧 `MainWindow` 类还在 `app.py` 中但不再使用，后续删除；没有安装包、托盘/通知。
- **Allow UDP 只处理本机防火墙**：路由器/NAT 不在范围内；Windows 规则默认只对 Private/Domain 生效。
- **没有做安全审计**。

## 项目结构

```text
p2p/
  session.py           # Legacy STUN/UDP 会话；quic_handoff 可交出 socket
  config.py            # Config + Config.from_vault（per-contact，无全局 env）
  keyexchange.py       # X25519 + HKDF + AEAD
  stun.py              # 最小 STUN 客户端
  punch.py             # P2P1 探测/回包
  quic_transport.py    # QUIC protocol + 16-session manager + PING keepalive
  quic_file.py         # 分块/续传/SHA-256/进度
  quic_punch.py        # handoff socket 的 QUIC client/server（可选独立打洞）
  contacts.py          # identity/contact 迁移、role、invite
  invite.py            # p2p1 邀请码编解码
  onboarding.py        # identity 构建、连接检查、vault 创建/重置
  runtime_env.py       # CLI 的 vault → 环境变量 fallback
  vault.py             # scrypt + ChaCha20-Poly1305、change/clear
  gui/
    app.py             # 入口/VaultDialog；旧 MainWindow 已废弃
    setup_wizard.py    # identity 向导（无 peer）
    vault_manager.py   # 修改口令/清除/锁定
    quic_controller.py # Qt ↔ asyncio/QUIC 桥
    multi_session.py   # contact 列表 + 多 tab 基础窗口
    single_window.py   # 唯一 GUI：identity/vault/contact/firewall shell
    worker.py          # Session 信令/punch/handoff → Qt signals
  signal/
    file.py            # 共享目录
    imap_store.py      # 共享邮箱（测试）
    smtp_mail.py       # 互相发邮件（产品模式）
    mail_router.py     # 单 IMAP poller + sid 路由
scripts/
  setup.py             # 只配置 identity
  contact.py           # new / join / list（支持 --role/--port）
  run_vault.py         # Legacy CLI
  allow_firewall.ps1   # Windows UDP 端口放行（UAC）
  pair_test.py         # 两个 vault 非交互测试
tests/                 # 28 个测试文件，85 个测试
docs/images/gui.png
DEVELOPMENT.md
LICENSE
```

## 测试

```bash
# 需要 aioquic；没有时 QUIC 测试会 skip
.venv/bin/python -m unittest discover -s tests -v   # 85 tests
# Windows: py -3 -m unittest discover -s tests -v

python3 scripts/demo_same_machine.py                # Legacy 同机加密 demo

# GUI offscreen
QT_QPA_PLATFORM=offscreen python3 -m p2p.gui
```

## 文档

- [DEVELOPMENT.md](DEVELOPMENT.md)：开发记录、实验过程和更多细节；
- [docs/images/gui.png](docs/images/gui.png)：SingleWindow 预览；
- [setup.identity.example.json](setup.identity.example.json)：非交互 identity 示例。

## 许可证

本项目以 **GNU GPL v3.0** 发布，见 [LICENSE](LICENSE)。

GUI 使用 **PyQt6**（GPLv3）；Qt 本身是 LGPL/GPL。如果你需要更宽松的许可证，可以把 GUI 替换为 PySide6（LGPL），核心协议代码不依赖 Qt。

## 致谢

- [cryptography](https://cryptography.io/)：X25519 / HKDF / ChaCha20-Poly1305；
- [aioquic](https://github.com/aiortc/aioquic)：QUIC/TLS 传输；
- [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) / Qt：桌面 GUI；
- 公共 STUN 服务器：`stun.miwifi.com`、`stun.cloudflare.com`；
- 所有参与测试和提建议的人。

---

**StrayPigeon** — 邮件是信鸽，消息走 P2P。
