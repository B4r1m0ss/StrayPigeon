# StrayPigeon

> **Email-signaled, end-to-end encrypted P2P chat.**
> 一只迷途的信鸽，也总能找到同伴。

![tests](https://img.shields.io/badge/tests-64%20passing-brightgreen)
![python](https://img.shields.io/badge/python-3.10%2B-blue)
![license](https://img.shields.io/badge/license-GPL--3.0-green)
![platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-lightgrey)

![StrayPigeon GUI](docs/images/gui.png)

**StrayPigeon** 是一个完全开源的点对点加密聊天应用。它不需要自建信令服务器：用你已有的邮箱（SMTP/IMAP）交换连接候选和公钥，用 STUN + UDP 打洞建立直连，消息用 X25519 + HKDF + ChaCha20-Poly1305 端到端加密。桌面端是 PyQt6，支持 Windows 和 Linux。

**English:** StrayPigeon is an open-source, email-signaled P2P encrypted chat app for Windows and Linux. It uses your existing mailbox for signaling, STUN + UDP hole punching for direct connections, and X25519/HKDF/ChaCha20-Poly1305 for end-to-end encrypted messages.

> 注意：这是一个研究/毕业设计原型，**尚未经过安全审计**。不要用于生命攸关或高价值场景。

## 为什么叫 StrayPigeon

邮件是信鸽：它只负责把“地址”和“公钥”送到对方手里。
真正的消息不经过邮件服务器，而是两只鸽子在 NAT 之间直接对上暗号。
即使走散了，一只迷途的信鸽也总能找到同伴。

项目原名 `email-p2p`。代码里的 vault 目录仍保留 `email-p2p`，是为了兼容已有数据。

## 特性

- **邮箱即信令**：不需要额外的信令服务器；SMTP 或 IMAP 都能送候选。
- **真正的 P2P 数据面**：STUN 发现公网映射，多候选 UDP 打洞；媒体/消息不经过邮箱。
- **端到端加密**：临时 X25519 协商会话密钥，HKDF 派生，ChaCha20-Poly1305 AEAD；序号防重放。
- **身份与联系人分离**：setup 只保存**你自己的邮箱身份**；每个对话是一个 contact，拥有独立 PSK 和（IMAP 测试模式的）独立 folder。
- **多联系人**：一个 identity 可以保存多个 contact，GUI 里点选即连。
- **NAT 保活**：默认每 15 秒 `PING`/`PONG`，避免 UDP 映射在空闲后失效。
- **加密 vault**：scrypt + ChaCha20-Poly1305，保存邮箱授权码和 PSK；口令不落盘。
- **GUI + CLI**：PyQt6 桌面端；命令行仍可用于脚本/服务器环境。
- **跨平台**：Windows / Linux。

## 它是怎么工作的

```text
┌────────────────┐        SMTP / IMAP: 候选 + 公钥（PSK 封装）        ┌────────────────┐
│  StrayPigeon A │ ─────────────────────────────────────────────────▶ │  StrayPigeon B │
│  identity A    │                                                    │  identity B    │
└───────┬────────┘                                                    └───────┬────────┘
        │                                                                     │
        │  STUN → 公网映射 / NAT 分类                                          │
        └────────────────── UDP 打洞 + keepalive ─────────────────────────────┘
                         X25519 → HKDF → ChaCha20-Poly1305
```

1. 双方各自配置自己的邮箱身份，不交换 peer；
2. 发起方针对某个联系人生成邀请码（含本次对话的 PSK），带外发给对方；
3. 双方通过邮箱交换候选、NAT 类型和临时公钥；
4. STUN 发现公网地址，双方同时向候选发 UDP 探测，打洞成功即直连；
5. 用 X25519 协商会话密钥，之后的聊天帧全部 AEAD 加密；
6. 每 15 秒心跳维持 NAT 映射；断线/退出由用户显式触发。

## 快速开始

### 0. 准备

- Python 3.10+
- 两个支持 IMAP/SMTP 的邮箱（推荐不同提供商，例如 126 ↔ 163）
- 各自的邮箱授权码/应用专用密码（不是登录密码）
- 能访问 UDP 外网；对称 NAT（APDM）目前没有中继兜底

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

### 2. 创建身份（不包含 peer）

```bash
python3 scripts/setup.py --mode smtp
```

只填写**你自己的**邮箱、IMAP/SMTP 主机、授权码和 vault 口令。这一步不会询问对话对象。

也可以非交互（示例文件只有占位符，注意 chmod 600 并在用完后删除）：

```bash
python3 scripts/setup.py --config setup.identity.example.json
```

### 3. 创建/加入对话

```bash
python3 scripts/contact.py new --peer bob@example.com
# 默认本端 initiator；也可以显式 --role responder
# 邀请码里会带 role；加入方自动存相反 role
# 把打印出来的 p2p1: 邀请码带外发给对方

python3 scripts/contact.py join "p2p1:..."
python3 scripts/contact.py list
```

一个 identity 可以有多个 contact；每个 contact 有自己的 PSK 和 folder。

旧的扁平 vault 会自动迁移，不需要重新 setup：

```bash
python3 scripts/run_vault.py --list-contacts
```

### 4. GUI

```bash
python3 -m p2p.gui
# Windows: py -3 -m p2p.gui
```

1. 首次启动点 **Create identity…**，进入 Setup 向导（也可以先用 `scripts/setup.py`）；向导只创建 identity，不问 peer；
2. 解锁 vault；
3. 在左侧选择 contact；没有就点 **New contact** / **Join invite**；
4. 设置 role、port、advertise、STUN；
5. 点 **Connect**；
6. 连上后输入消息，Enter 或 **Send**；**Disconnect** 断开。

- 连接过程会依次显示 `bound → peer ready → punching`；punching 有最多 300 秒的进度条；
- 点 **Allow UDP** 可以放行本机防火墙的当前 UDP 端口：
  - **Windows**：弹 UAC，默认只开 `Private/Domain`；勾选后可选 public；
  - **Linux**：显示 ufw / firewalld / nft 命令，复制到终端执行；
  - 只开当前 UDP 端口，**不会关闭防火墙**。
- 点 **Vault…**：
  - **Change passphrase**：验证旧口令后重新加密，identity/contacts 保留；
  - **Clear vault / set up again**：直接删除 vault，无备份，所有 contacts/PSK 丢失，随后可重新建 identity；
  - **Lock vault**：清空内存并回到解锁界面。

> SMTP TLS 选择：**465 = 隐式 TLS（STARTTLS 不勾）**；**587 = STARTTLS（勾选）**。
> 163 / 126 的 SMTP 用 465，STARTTLS 保持关闭。

两台机器的常用组合：

```text
机器 A（发起方）: role=initiator, port=40000, advertise=127.0.0.1
机器 B（响应方）: role=responder, port=40000, advertise=127.0.0.1
STUN: stun.miwifi.com:3478, stun.cloudflare.com:3478
```

同一局域网可以把 `advertise` 换成各自的 LAN IP。

### 5. CLI

```bash
python3 scripts/run_vault.py --vault ~/.config/email-p2p/vault.json \
  --contact bob@example.com --role initiator --port 40000
```

只有一个 contact 时可以省略 `--contact`。Windows 的 vault 路径是 `%APPDATA%\email-p2p\vault.json`。

## 当前状态

- **64 个单元测试通过**；
- 同机加密 demo：`PASS`；
- 真实 163（Windows）↔ 126（Linux）跨提供商 SMTP 加密聊天：`CONNECTED`；
- GUI MVP：vault 解锁、**Create identity 向导**、vault 修改口令/清除/锁定、contact 列表、per-contact role/local_port、New/Join、连接、聊天、Disconnect、punching 进度、Allow UDP 防火墙辅助；Linux offscreen smoke `PASS`；
- QUIC spike：aioquic 单 UDP 端口、16 个并发连接、256 KB stream 回环测试通过；
- QUIC chat/session：`QuicSessionManager` 单 UDP 端口、16 会话上限、per-session stream 聊天测试通过；
- 文件传输层：4 MB 文件流式落盘、SHA-256 校验、断点续传测试通过；
- 尚未完成：文件传输 GUI、16 会话/大文件压测、PyInstaller GUI exe/安装包、托盘/通知、自动重连；
- **未做安全审计**。

## 安全模型

- **PSK**：每个 contact 独立 32 字节，随机生成；编码在邀请码里，带外传递；不经过邮箱。
- **邀请码**：含双方邮箱/共享邮箱、PSK、checksum；不是身份口令，但拿到邀请码的人可以加入该对话。
- **vault**：邮箱授权码和 PSK 用 `scrypt(n=2^15, r=8, p=1)` + `ChaCha20-Poly1305` 加密；口令不保存。
- **传输**：IMAP 993 / SMTP 465 走 TLS；邮件服务商能看到信令元数据，但看不到聊天内容。
- **数据面**：X25519 临时密钥 → HKDF → ChaCha20-Poly1305；序号防重放；心跳不携带聊天内容。
- **没有中继**：没有 TURN/relay；双方都是对称 NAT 时会返回 `E_SYMMETRIC_NAT`。

## 已知限制

- **对称 NAT 无解**：双方 APDM 时打洞失败；同 LAN 或 VPN 可以绕过。
- **SMTP 投递不可靠**：可能延迟、进垃圾箱、被限流；163 的 IMAP 不匹配 `SUBJECT` 搜索时会自动回退 `ALL`，代价是慢。
- **UDP 不保证可靠/有序**：没有消息重传；丢包就是丢消息。
- **NAT 映射会超时**：默认 15 秒 keepalive；如果运营商回收更短，改 `--keepalive 5`。
- **无消息持久化**：聊天记录只在当前进程里，JSONL 日志不保存消息正文。
- **无文件传输/语音/多设备**。
- **Allow UDP 只处理本机防火墙**：路由器、NAT、对称 NAT 不在它范围内；Windows 规则默认只对 Private/Domain 生效。
- **元数据不保护**：邮件服务商知道谁在何时通信。
- **vault 清除/重置不可恢复**：不备份；清除会删除 identity、全部 contacts 和 PSK，旧口令立即失效；SSD/日志文件系统上的“安全擦除”只能尽力而为。
- **忘记 vault 口令只能清除后重建**：口令不保存，无法恢复；对方需要重新收邀请。
- **GUI 仍是 MVP**：安装包、托盘/通知在路线图上。

## 项目结构

```text
p2p/
  session.py          # 握手、打洞、聊天、GUI event/outbox
  config.py           # 参数 + 环境变量
  keyexchange.py      # X25519 + HKDF + AEAD
  stun.py             # 最小 STUN 客户端
  punch.py            # P2P1 探测/回包
  contacts.py         # identity/contact 迁移与创建/加入
  invite.py           # 邀请码编解码
  onboarding.py       # identity 构建、连接检查、vault 创建/重置
  runtime_env.py      # vault → 进程环境
  vault.py            # scrypt + ChaCha20-Poly1305、change/clear
  gui/
    app.py            # PyQt6 主窗口
    setup_wizard.py   # identity 向导（无 peer）
    vault_manager.py  # 修改口令/清除/锁定
    worker.py         # Session → Qt signals
  signal/
    file.py           # 共享目录
    imap_store.py     # 共享邮箱（测试）
    smtp_mail.py      # 互相发邮件（产品模式）
scripts/
  setup.py            # 只配置 identity
  contact.py          # new / join / list
  run_vault.py        # CLI 运行
  allow_firewall.ps1  # Windows UDP 端口放行（UAC）
  pair_test.py        # 两个 vault 非交互测试
  ...
tests/                # 18 个测试文件，64 个测试
docs/images/gui.png
DEVELOPMENT.md        # 开发/实验记录
LICENSE
```

## 测试

```bash
.venv/bin/python -m unittest discover -s tests -v   # 64 tests (needs aioquic)
# Windows: py -3 -m unittest discover -s tests -v
python3 scripts/demo_same_machine.py       # 同机加密 demo
```

GUI 的 offscreen smoke：

```bash
QT_QPA_PLATFORM=offscreen python3 -m p2p.gui
```

## 文档

- [DEVELOPMENT.md](DEVELOPMENT.md)：开发记录、实验过程、旧流程和更多细节；
- [docs/images/gui.png](docs/images/gui.png)：GUI 预览；
- [setup.identity.example.json](setup.identity.example.json)：非交互 identity 示例。

## 许可证

本项目以 **GNU GPL v3.0** 发布，见 [LICENSE](LICENSE)。

GUI 使用 **PyQt6**（GPLv3）；Qt 本身是 LGPL/GPL。如果你需要更宽松的许可证，可以把 GUI 替换为 PySide6（LGPL），核心协议代码不依赖 Qt。

## 致谢

- [cryptography](https://cryptography.io/)：X25519 / HKDF / ChaCha20-Poly1305；
- [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) / Qt：桌面 GUI；
- 公共 STUN 服务器：`stun.miwifi.com`、`stun.cloudflare.com`；
- 所有参与测试和提建议的人。

---

**StrayPigeon** — 邮件是信鸽，消息走 P2P。
