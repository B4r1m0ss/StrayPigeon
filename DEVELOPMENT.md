# StrayPigeon (v0.3)

> 原名 `email-p2p`。邮件信令 + UDP 打洞的 P2P 加密聊天；一只迷途的信鸽，也总能找到同伴。
>
> **StrayPigeon — email-signaled, end-to-end encrypted P2P chat with UDP hole punching.**

一个能跑的最小原型：**两台机器各自在 NAT 后面，通过邮件/共享存储交换候选与公钥，然后 UDP 打洞直连，用协商出来的会话密钥加密聊天。**

v0.3 的完成度：

| 能力 | 状态 |
|---|---|
| 共享目录信令（`FileChannel`） | ✅ 已测 |
| STUN 公网映射 + 双服务器 EIM/APDM 分类 | ✅ 已测 |
| 多候选 UDP 打洞（200 ms，30 s 窗口） | ✅ 已测 |
| PSK 认证信令 + 临时 X25519 + AEAD | ✅ 已测 |
| 聊天帧加密 + 序号防重放 | ✅ 已测 |
| 信令 `ts` + 已见 `sid` 防重放 | ✅ 已实现 |
| netns 跨 NAT 模拟（cone/public/sym） | ✅ 已测 |
| Linux / Windows 代码路径 | ✅ 跨平台；Windows CI 已配，实机待明天 |
| IMAP 共享邮箱（测试模式） | ✅ 已在 126 邮箱验证（同机双进程）；仅用于测试 |
| SMTP 每用户邮箱（产品模式） | ✅ 163↔126 真实跨提供商 SMTP `CONNECTED`；NAT 映射靠 keepalive 维持 |
| 交互式设置 + 加密 vault | ✅ `setup.py` / `run_vault.py`；scrypt + ChaCha20-Poly1305 |
| 非交互 config → setup → pair_test | ✅ 已实测（126 测试模式，PASS） |
| QUIC 传输 | ⛔ 占位（v0.4） |
| IPv6 优先路径 | ⛔ 未做 |

> 状态（2026-10-20）：**38 个单元测试通过**；同机加密 demo `PASS`；netns 跨 NAT：`cone` 成功、`public` 成功、`sym` 返回 `E_SYMMETRIC_NAT`；真实 126 IMAP 双进程加密聊天 `PASS`（**共享邮箱测试模式**）；126 IMAP + SMTP 登录检查通过；**非交互 `setup --config` + `pair_test` 已实测 PASS**；真实 163（Windows）↔ 126（Linux）跨提供商 SMTP 加密聊天 `CONNECTED`；已加 `--keepalive`、identity/contact 拆分和 PyQt6 GUI MVP（offscreen smoke PASS）。
>
> 详细实验记录：[`../lab/RESULTS.md`](../lab/RESULTS.md)。
>
> 论文骨架：[`../docs/PAPER_SKELETON.md`](../docs/PAPER_SKELETON.md)。

---

## 0.0 新流程：identity + contact（最小验证）

`setup.py` 现在只配置**你自己的邮箱身份**，不再询问 peer：

```bash
python3 scripts/setup.py --mode smtp
```

也可以非交互（示例文件含明文秘密，chmod 600 并在用完后删除）：

```bash
python3 scripts/setup.py --config setup.identity.example.json
```

每个对话用 `contact.py` 创建/加入；peer、PSK、folder 都存在 contact 里：

```bash
python3 scripts/contact.py new --peer bob@example.com
python3 scripts/contact.py join "p2p1:..."
python3 scripts/contact.py list
python3 scripts/run_vault.py --contact bob@example.com --role initiator --port 40000
```

- 旧扁平 vault 会自动迁移；`python3 scripts/run_vault.py --list-contacts` 查看迁移后的联系人；
- 一个 identity 可以有多个 contact，用 `--contact` 选择本次对话；
- 旧的 flat create/join 流程保留在 `setup_legacy.py`，仅用于兼容/参考。

### 0.0.1 GUI（PyQt6 MVP）

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements-gui.txt
.venv/bin/python -m p2p.gui
```

（Arch/Ubuntu 的系统 Python 受 PEP 668 保护，不能直接 `pip install`；用 venv。`--system-site-packages` 可复用已装的 PyQt6/cryptography。）

Windows：

```powershell
py -3 -m pip install -r requirements-gui.txt
py -3 -m p2p.gui
```

- 首次启动先解锁 vault；没有 vault 时先用 `setup.py` 建 identity；
- 左侧是 contact 列表，可 **New contact / Join invite**；
- 选 contact，设置 role / port / advertise / STUN，点 **Connect**；
- 连上后输入消息，Enter 或 **Send**；**Disconnect** 断开；
- Disconnect 不阻塞 UI：候选等待和 punch 会被 stop 事件中断；IMAP 阻塞调用有 10 秒超时；
- 当前是 MVP：identity 向导、托盘、通知、exe 打包下一步；
- PyQt6 是 GPLv3；本项目完全开源，兼容。

---

## 0. 用户模式：交互式设置 + 加密存储（推荐）

> 下面 0.1–0.2 描述的是旧的扁平 setup（`setup_legacy.py`）；新流程见上面的 0.0。

### 0.1 首次设置（默认：每人一个邮箱）

```bash
cd serverless-p2p/email-p2p
python3 scripts/setup.py
```

它会依次询问：

- 你的邮箱地址；
- 对方的邮箱地址（只是对方地址，不是共享邮箱）；
- 你的 IMAP host（126/163/QQ/Gmail 自动填，可改）；
- 你的 SMTP host；
- SMTP 端口（默认 465；Outlook 等用 587 + STARTTLS）；
- 是否 STARTTLS；
- **不再问短名**：你的用户名自动用你的邮箱，对方的用户名自动用对方邮箱；
- **你自己的**授权码 / 应用密码（`getpass`，不回显）；
- vault 口令（两次，至少 8 位）。

设置过程中先测试：你的 IMAP（LOGIN/ID/APPEND/SEARCH/FETCH）+ 你的 SMTP 登录；通过才保存。

输出：

- **加密 vault**：Linux `~/.config/email-p2p/vault.json`；Windows `%APPDATA%\email-p2p\vault.json`；权限 `0600`；
- **`p2p1:...` 邀请码**：直接打印在终端，复制给对面即可；**不依赖文件路径**；
- **前缀可省略**：粘贴 `p2p1:` 后面的整段 base32 也能识别；新邀请码带 12 位校验和，复制错会直接报 `invite checksum mismatch`；
- **`invite.txt`**：只是可选备份，含双方邮箱 + PSK，**不含任何授权码**；
- **PSK 不需要你手输**：initiator 的 `setup.py` 自动生成 32 字节随机 PSK，编码进邀请码。

对方拿到邀请码后，直接粘贴（带不带 `p2p1:` 都行）：

```bash
python3 scripts/setup.py --join "<邀请码>"
```

或者交互式：`python3 scripts/setup.py` → 选 `j` → 粘贴邀请码。旧的 `--join-file` 仍兼容，但推荐直接用邀请码，Windows 下没有路径/转义问题。

对方的邮箱地址来自邀请码（你填的那个）；他输入**自己的** IMAP/SMTP host 和**自己的**授权码，双方不共享任何邮件凭据。

> 共享邮箱只是测试模式：`python3 scripts/setup.py --mode imap`。它方便同机快速验证，但不是最终产品。

### 0.2 运行

```bash
python3 scripts/run_vault.py --role initiator --port 40000
```

它会提示输入 vault 口令，解密后只把邮箱/授权码/PSK 放进当前进程，然后进入聊天：

- 不传 `--message`：交互式，输入一行发一行；
- 传 `--message "hi"`：发一条就退出，适合测试；
- 两台机器加 `--advertise-host <你的 LAN IP>`。

> **同一台机器跑两个角色**时，产品模式需要两个不同的邮箱；只想快速验证就用共享邮箱测试模式：
>
> ```bash
> # 产品模式（两个邮箱）
> python3 scripts/setup.py --vault state/alice-vault.json
> python3 scripts/setup.py --join "p2p1:..." --vault state/bob-vault.json
> python3 scripts/run_vault.py --vault state/alice-vault.json --role initiator --port 40101
> python3 scripts/run_vault.py --vault state/bob-vault.json   --role responder --port 40102
>
> # 共享邮箱测试模式（一个邮箱，快速）
> python3 scripts/setup.py --mode imap --vault state/test-a.json
> python3 scripts/setup.py --join "p2p1:..." --vault state/test-b.json
> ```
>
> `state/` 已被 `.gitignore` 覆盖。

#### 0.2.1 一条龙：setup 直接进聊天

`setup.py` 现在也会问：

- `Create a new invite (c) or join an existing one (j)?`
  - 选 `j` 时直接粘贴 `p2p1:...`（不再走文件路径）；
- 保存 vault 后问 `Start communication now? [Y/n]`，选 Y 就直接进入聊天。

常用参数：

```bash
python3 scripts/setup.py --start                    # 创建/加入后直接开始
python3 scripts/setup.py --join "p2p1:..." --start  # 直接用邀请码加入
python3 scripts/setup.py --no-start                 # 只配置，不进聊天（旧行为）
python3 scripts/setup.py --start --message "hi"     # 连上后发一条就退出
python3 scripts/setup.py --start --role responder --port 40000 --advertise-host 192.168.1.23
```

- 创建者默认 `initiator`，加入者默认 `responder`；
- 同机两个进程要不同 `--port`（如 40101/40102）；两台机器可都用 40000；
- 等待/新鲜度默认：SMTP `--signal-timeout 600 --replay-window 600`，IMAP `300 / 300`；旧 vault 由 `run_vault.py` 自动补；要更长直接传 `--signal-timeout 900 --replay-window 900`；
- `run_vault.py` 仍然可用，两者共用同一套 vault/env；直接跑 `python -m p2p` 时，这两个值仍是 60/120，需要手动加参数。
- 交互聊天默认 `--keepalive 15`：每 15 秒发 `PING`、对方回 `PONG`，维持 NAT 映射；`0` 关闭。UDP 打洞的映射在 30–60 秒无流量后可能被回收，表现为“刚连上能发、过一会儿双方都收不到”。
- IMAP/SMTP 通道按邮件 **UID 倒序**，优先最新一封 `p2p1/...`；旧会话的候选不会抢在最新候选前。163 的 IMAP 不支持 `SUBJECT "p2p1/"` 时自动回退 `ALL`，再在 Python 里过滤。

### 0.3 授权码到底怎么处理

产品模式（每人一个邮箱）里，授权码**只属于你自己**：

- 你输入自己的授权码；对方输入对方的；
- 它只用于登录你自己的 IMAP/SMTP；
- 它不会放进 invite，也不会发到 P2P 通道；
- 双方的 vault 各自加密保存自己的授权码。

两个秘密：

- **授权码**：本地登录邮件服务器；vault 加密；TLS 传输；
- **P2P PSK**：initiator 自动生成 32 字节随机值；编码进 `p2p1:...` 邀请码，带外传给对方；双方 vault 加密保存；**不需要手输**。

细节：

- **传输中**：IMAP 993 / SMTP 465（或 587 STARTTLS）都是 TLS；邮件服务商在登录时能看到原值（这不是端到端加密）。
- **磁盘上**：vault 用 `scrypt(n=2^15, r=8, p=1)` 派生 key，`ChaCha20-Poly1305` 加密；文件 `0600`；口令不保存。
- **不能哈希**：授权码要原样发给邮件服务器做登录，哈希后就不可用。哈希只适用于“验证口令”，不适用于“要重放的凭据”。
- **内存里**：只在登录时明文存在；不写日志；Python 无法保证可靠擦除。
- **邀请码**：产品模式只含双方邮箱 + PSK，不含授权码。
- **共享邮箱测试模式是例外**：两个人共用一个邮箱，所以都要那个邮箱的授权码；只用于测试，不是产品。
- **替代方案**：Windows Credential Manager / macOS Keychain / Linux Secret Service（`keyring`）可以免口令，但 Linux 无桌面环境时不可用；vault 是跨平台方案。

### 0.4 密钥体系：三个秘密，别混

| 名称 | 作用 | 谁生成 | 要给对方吗 | 存哪 |
|---|---|---|---|---|
| **邮箱授权码** | 登录你自己的 IMAP/SMTP | 邮箱服务商 | **不给**（产品模式）；共享邮箱测试模式才需要共用 | 你的 vault 加密 |
| **P2P PSK** | 认证信令、派生会话密钥 | initiator 的 `setup.py` 自动生成 32B | **要**，通过 invite 带外给 | vault 加密 + invite（0600） |
| **vault 口令** | 解开本地 vault | 用户 | **永远不给** | 不存 |
| **会话密钥** | 加密聊天 | 双方 X25519 现场算 | 不需要，自动 | 只在内存 |

交互流程：

```text
Alice                                      Bob
  │ setup: 自己的邮箱/授权码 + 对方邮箱        │
  │ 生成 PSK                                │
  │ invite(Alice邮箱, Bob邮箱, PSK)          │
  │────────── Signal / QR / USB / 当面 ────>│  ← 这一步就是“外带”
  │                                         │ setup --join：粘贴邀请码
  │                                         │ 输入 Bob 自己的 host/授权码 + vault 口令
  │ SMTP 发信令到 Bob 邮箱（AEAD(PSK, ...)）  │  ← 邮件服务商只看到密文
  │<──────── Bob 读自己收件箱，回信 ─────────>│
  │ X25519 DH → session key                 │
  │<========= 直连 AEAD 聊天 ==============>│
```

**invite 怎么给对方**：

- 同一台机器：复制终端打印的 `p2p1:...`，对方用 `--join` 粘贴；
- 两台机器：Signal / 微信 / 线下把 `p2p1:...` 一行发过去，对方粘贴；**不需要传文件**；
- 两个朋友：Signal / 当面二维码 / 电话读一段短码（当前 invite 是长码，不是短码）；
- **不要用邮件发 invite**。邮件服务器能拿到 PSK，整套认证就破了。

**为什么尴尬**：这是“首次信任”问题，不是本项目的 bug。Signal 有安全号码，SSH 有指纹，WireGuard 有公钥，都要一次带外确认。没有带外通道，就无法对抗主动 MITM；替代方案是 PAKE + 短码（未来工作）或接受 TOFU。产品模式里**只有 PSK 需要带外**；授权码不共享。

**共享邮箱只是测试模式**：

- `--mode imap` 是两个人共用一个邮箱，所以双方都要那个邮箱的授权码；方便同机快速验证，不是最终产品。
- 产品模式（默认 `smtp`）是每人一个邮箱，invite 只含双方邮箱 + PSK，不含授权码。
- 如果一定要共用邮箱，正式做法是邮箱委托/隔离的应用密码，而不是把主授权码发给对方。

### 0.5 非交互：一份配置文件 → setup.py → pair_test.py

不想每次回答提示时，把全部内容写进一个 JSON，setup 直接执行。

```bash
cp setup.example.json setup.local.json
chmod 600 setup.local.json
# 填：mode / 双方邮箱 / host / 授权码 / vault / vault_passphrase / invite（可选备份路径）
python3 scripts/setup.py --config setup.local.json
```

- 字段齐全时**不再问任何问题**；IMAP/SMTP 检查照跑；
- vault 由配置决定；邀请码直接打印在终端，不依赖文件；
- `vault_passphrase` 可以写在配置里，也可以只放环境变量 `P2P_VAULT_PASSPHRASE`；
- 配置里含授权码/口令，按密钥对待：`0600`、不要提交、setup 完删掉。

对方（join）用 `setup.example.join.json`：把 `"join": "p2p1:..."` 换成对方打印的邀请码。

```bash
cp setup.example.join.json setup.bob.local.json
chmod 600 setup.bob.local.json
python3 scripts/setup.py --config setup.bob.local.json
```

然后跑双向测试：

```bash
export P2P_VAULT_PASSPHRASE='vault 口令'    # 两个测试 vault 用同一个
python3 scripts/pair_test.py \
  --alice-vault state/alice-vault.json \
  --bob-vault state/bob-vault.json
```

- 自动起两个 `run_vault.py`，各发一条，检查 `CONNECTED` 和对方消息；
- 输出 `PASS`/`FAIL`，日志在 `state/pair-*/`；
- 两个测试 vault 的 passphrase 必须相同；如果不同，就分别手动起两个 `run_vault.py`，各设自己的 env；
- 共享邮箱测试模式用 `setup.example.imap.json`；产品模式（每人一个邮箱）用 `setup.example.json`；
- 刚实测（126 共享邮箱测试模式）：`setup --config` 两次 + `pair_test` → `PASS`。

---

## 1. 最快的一次验证（开发者模式）

```bash
cd serverless-p2p/email-p2p
python3 -m unittest discover -s tests -v     # 38 tests
python3 scripts/demo_same_machine.py         # PASS, crypto: True
```

同机 demo 每次运行随机生成一个 32 字节 PSK，通过环境变量传给两个子进程（不写盘、不打印），走完整的“STUN + 密钥交换 + 打洞 + 加密聊天”。

### 先探测你的 NAT

```bash
python3 scripts/nat_probe.py
```

- 两个 STUN 返回同一映射端口 → **EIM**，打洞有戏；
- 不同 → **APDM**（对称），无中继基本打不通；
- 都失败 → UDP 可能被封。

它只测 **mapping**，不测 **filtering**。

### netns 跨 NAT（Linux）

```bash
cd serverless-p2p/lab
./real_lab.sh -p cone      # PASS：跨 NAT 加密聊天
./real_lab.sh -p public    # PASS：无 NAT
./real_lab.sh -p sym       # PASS（按预期失败）：E_SYMMETRIC_NAT
```

`real_lab.sh` 启动两个**真实的** `python -m p2p` 进程，各在一个 netns 里，各自后面一个 NAT；WAN 上两个标准 STUN 反射器。结果见 [`../lab/RESULTS.md`](../lab/RESULTS.md)。

---

## 2. 手动跑

### 2.1 同一台机器（明文/加密调试）

```bash
# 终端 1
PYTHONPATH=. python3 -m p2p \
  --name alice --peer bob --port 40101 --role initiator \
  --signal file --shared-dir state/shared --log-dir state/logs

# 终端 2
PYTHONPATH=. python3 -m p2p \
  --name bob --peer alice --port 40102 --role responder \
  --signal file --shared-dir state/shared --log-dir state/logs
```

不设 PSK 时是明文；加密模式先：

```bash
python3 scripts/gen_psk.py --out .env
set -a; . ./.env; set +a
```

两个终端都要 source 同一个 `.env`。

### 2.2 两台机器（明天的实机测试）

先明确一件事：**`FileChannel` 只是本地目录**。两台机器要么用**共享网络目录**（SMB/NFS/Syncthing），要么用 **IMAP 共享邮箱**；否则两台机器各写各的目录，永远收不到对方。

#### 先做邮箱准备（重要）

**只需要开 IMAP，不需要开 POP3。** 代码用 IMAP 读信令；SMTP 只在“互相发邮件”模式里用来发送。

- 在邮箱设置里开启 **IMAP/SMTP 服务**；
- IMAP 用 **993 + SSL**；
- SMTP 用 **465 + SSL**（默认），或 **587 + STARTTLS**（设 `P2P_SMTP_STARTTLS=1`）；
- 用**应用专用密码 / 授权码**，不要用登录主密码；
- 常见情况：
  - Gmail：先开两步验证，再生成“应用专用密码”；
  - QQ / 163：在设置里开启 IMAP/SMTP，使用“授权码”；
  - Outlook / 企业邮箱：可能禁用 basic auth，需要 OAuth2——本工具不支持 OAuth2，换一个支持应用密码的邮箱；
- 先用检查脚本验证，不会打印密码：

```bash
cp .env.example .env.local
chmod 600 .env.local
# 填 P2P_IMAP_HOST / P2P_IMAP_USER / P2P_IMAP_PASSWORD
python3 scripts/check_email.py --imap --env-file .env.local
```

它做四件事：`LOGIN` → `CREATE` 一个临时文件夹 → `APPEND` 一封测试信 → `SEARCH/FETCH` 它 → 删除文件夹。通过会打印 `[PASS]`；失败会打印 `[FAIL]`，按错误信息排查。

126 已实测通过：`LOGIN` → `ID` → `CREATE` → `APPEND` → `SEARCH/FETCH` → `DELETE` 全部 OK。126/163 的 `ID` 命令会自动发送；其他 provider 可能不需要，也可能有别的限制。

**安全**：`.env.local` 已被 `.gitignore` 覆盖；不要把它提交，也不要把密码贴到聊天/issue 里。如果贴过，测试完立刻在邮箱后台 revoke 那个应用密码。

#### 方案 A：IMAP 共享邮箱（仅测试；已在 126 上验证）

```bash
set -a; . ./.env.local; set +a
```

两台机器都用同一个专用邮箱、同一个文件夹：

```bash
export P2P_IMAP_HOST=imap.example.com
export P2P_IMAP_USER=p2p-test@example.com
export P2P_IMAP_PASSWORD='应用专用密码'
export P2P_IMAP_FOLDER=p2p-20261018-a     # 每次测试换一个新文件夹
export P2P_PSK_B64='同一个 32 字节 PSK 的 base64'
```

启动：

```bash
# 机器 1（Linux 或 Windows 都一样）
python3 -m p2p --name alice --peer bob --role initiator \
  --signal imap --advertise-host <本机 LAN IP> \
  --port 40000 --stun stun.l.google.com:19302 --stun stun.cloudflare.com:3478

# 机器 2
python3 -m p2p --name bob --peer alice --role responder \
  --signal imap --advertise-host <本机 LAN IP> \
  --port 40000 --stun stun.l.google.com:19302 --stun stun.cloudflare.com:3478
```

- `--advertise-host` 填本机 LAN IP（Windows `ipconfig`，Linux `ip addr`）。
- 两台机器都要放行 Python 的 UDP 入站（Windows 防火墙选“专用网络”）。
- IMAP 文件夹建议**每次测试新建一个**，避免旧消息被 responder 先读到；旧消息的 `ts` 会被拒，但会拖慢首次建连。
- IMAP 实现是实验性的；先在**同一台机器**开两个进程验证 `--signal imap`，再上两台机器。

#### 方案 B：共享网络目录（更可控，适合先排除 IMAP 问题）

把同一个网络目录挂到两台机器：

```bash
--signal file --shared-dir /mnt/share/p2p-20261018-a
# Windows: --shared-dir \\server\share\p2p-20261018-a
```

其余参数同上。这个模式没有邮件延迟，适合先验证“跨 NAT + 密钥交换”本身。

#### 方案 C：SMTP 每用户邮箱（产品模式；两邮箱端到端待测）

每台机器用自己的邮箱，通过 SMTP 把信令邮件发给对方，再从自己的 INBOX 读：

```bash
export P2P_SMTP_HOST=smtp.example.com
export P2P_SMTP_PORT=465
export P2P_MY_EMAIL=alice@example.com
export P2P_PEER_EMAIL=bob@example.com
export P2P_IMAP_HOST=imap.example.com
export P2P_IMAP_USER=alice@example.com
export P2P_IMAP_PASSWORD='应用专用密码'
export P2P_SMTP_FOLDER=INBOX
export P2P_PSK_B64='同一个 PSK'
```

启动时用 `--signal smtp`。

- 这是**产品模式**：双方各自用自己的邮箱，授权码不共享。
- 已通过 126 的 SMTP 登录检查；两邮箱端到端（126 ↔ 另一个 provider）待测。
- 需要邮箱支持 SMTP over TLS 465，并且有 SPF/DKIM；机器生成的邮件很容易被判垃圾。
- 读信令走 IMAP INBOX，按 `Subject: p2p1/...` 搜索。
- 共享邮箱 `--mode imap` 只用于测试，不要当产品。

#### 明天建议的测试顺序

1. 同一台机器：`demo_same_machine.py`（确认环境）。
2. 同一台机器：两个进程 `--mode imap` 共享邮箱（确认邮件通道；测试模式）。
3. 两台机器 + 同一 LAN：`--signal file` 共享目录（确认跨进程）。
4. 两台机器 + 两个邮箱 + 两个网络：`--signal smtp`（真正的产品模式 + 跨 NAT）。
5. 记录：NAT 分类、`CONNECTED`、`E_SYMMETRIC_NAT`、`E_NO_PATH`、`T_connect`、邮件延迟。

### 2.3 Windows

```powershell
py -3 -m pip install -r requirements.txt
py -3 scripts\demo_same_machine.py
```

或：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\demo_windows.ps1
```

两台机器时：

```powershell
py -3 -m p2p --name alice --peer bob --role initiator `
  --signal imap --advertise-host 192.168.1.23 `
  --port 40000 --stun stun.l.google.com:19302 --stun stun.cloudflare.com:3478
```

设置 PSK（PowerShell）：

```powershell
Get-Content .env | ForEach-Object {
  if ($_ -match '^([^#=]+)=(.*)$') { Set-Item "Env:$($matches[1])" $matches[2] }
}
```

注意：

- 第一次运行会弹 Windows 防火墙，选“专用网络”。
- `--advertise-host` 用 LAN IP；`127.0.0.1` 只适合同机测试。
- Windows 上没有 `unshare`/netns；跨 NAT 用两台机器或同机 demo。
- 代码路径只用 `socket`/`select`/`pathlib`/`tempfile` 等跨平台 API；CI 矩阵包含 `windows-latest`。

**打包 exe（可选，分发用）**

在 Windows 项目根目录：

```powershell
py -3 -m pip install -r requirements-build.txt
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

产出：

- `dist\StrayPigeon-setup.exe`
- `dist\StrayPigeon-run.exe`

目标机器不需要 Python；**构建机器需要** `cryptography` 和 PyInstaller，脚本会装。只打包 `setup`/`run_vault`；`pair_test.py`/`demo_same_machine.py` 会启动 Python 子进程，不打包，测试仍用源码跑。

`--onefile` 首次启动会解压到临时目录，杀软可能误报；要更快/更稳，把脚本里的 `--onefile` 改成 `--onedir`，产出目录整体分发。

---

## 3. 它到底按什么顺序跑

```text
1. bind 一个固定 UDP 端口（整个会话不换）
2. 收集候选：
     host（本机地址）
     + 向两个 STUN 各问一次，得到 srflx 映射
     + 比较两个映射端口：相同 EIM，不同 APDM
3. initiator 生成 sid + 临时 X25519，把候选/公钥/ts/nat 用 PSK 加密后写进通道
4. responder 解密、读 initiator 公钥，生成自己的密钥对，加密回写
5. 双方算 DH，HKDF 出 session key
6. 双方对彼此所有候选打洞：每 200ms 一个 36 字节 P2P1，收到就回
7. 拿到确认地址后，聊天帧用 session key 加密；接收方校验 seq
```

对应代码：

| 步骤 | 代码 |
|---|---|
| 绑定端口 | `p2p/net.py: bind_udp` |
| STUN + NAT 分类 | `p2p/stun.py`、`p2p/session.py: _collect_candidates` |
| 候选/密钥交换 | `p2p/session.py: _send_candidate / _recv_candidate / _derive_session_key` |
| 打洞 | `p2p/punch.py: punch` |
| 加密聊天 | `p2p/session.py: _send_chat / _pump_chat` |
| 信令通道 | `p2p/signal/file.py`、`p2p/signal/imap_store.py` |
| 跨 NAT 测试 | `../lab/real_lab.sh` |

### 3.1 密钥交换到底怎么走

```text
邀请码：32 字节 PSK（带外共享，不进邮件）
        │
A: sid + 临时 X25519 (a_priv, a_pub)
        │  CANDIDATE = AEAD(HKDF(PSK, sid), {candidates, a_pub, ts, nat})
        ▼
B: 解密，生成 (b_priv, b_pub)
        │  CANDIDATE = AEAD(HKDF(PSK, sid), {candidates, b_pub, ts, nat})
        ▼
DH = X25519(a_priv, b_pub) = X25519(b_priv, a_pub)
session_key = HKDF(DH, salt=sid,
                   info="p2p1-data|" + sid + "|" + a_pub + "|" + b_pub)
        │
DATA = seq(8) || nonce(12) || ChaCha20-Poly1305(session_key, text, aad=seq)
```

- **PSK 只认证信令**，不直接加密聊天；
- **公钥在 AEAD 里**，邮件服务器改不了；
- **会话密钥由临时 DH 现场算出来**，从不上邮件；
- **数据层前向保密**：PSK 以后泄露，过去聊天内容解不开；但过去的信令（地址）可能被解密；
- **PSK 泄露 = 可冒充双方**，只能轮换。

---

## 4. 安全：没有硬编码的敏感信息

- **代码里没有任何邮箱、密码、PSK、token。**
- PSK 只从环境变量 `P2P_PSK_B64` 读取（32 字节，base64）。
- IMAP/SMTP 凭据只从 `P2P_IMAP_*` / `P2P_SMTP_*` 读取。
- `.env`、`.env.*`、`*.invite`、`state/`、`logs/` 在 `.gitignore` 里。
- 日志文件权限 `0600`，目录 `0700`；日志里的 `psk/password/secret/token` 字段被 `***` 替换。
- 聊天内容不写日志，只记录字符数。
- demo 每次随机生成 PSK，通过环境变量传给子进程，不落盘、不打印。
- 测试断言 PSK 不出现在 `repr(Config.redacted())` 里。

生成 PSK（不要手写、不要从硬件信息派生）：

```bash
python3 scripts/gen_psk.py --out .env
```

- 用 `secrets.token_bytes(32)`；
- 文件权限 `0600`；
- 不打印密钥；
- 两台机器必须用同一个值，通过二维码/Signal/线下共享，不要用邮件传。

为什么不用 MAC/CPU 序列号/主机名/时间做哈希：

- 熵低、很多是公开的；
- 两台机器各自“算”出来的值不一样，根本对不上；
- 真要硬件保护，把随机 PSK 存进 OS keyring/TPM，而不是从硬件信息“算”；
- `secrets`/`os.urandom` 底层已经是 OS CSPRNG，已经混合硬件熵。

---

## 5. 文件结构

```text
email-p2p/
├── p2p/
│   ├── __main__.py       # CLI
│   ├── config.py         # 参数 + 环境变量
│   ├── keyexchange.py    # X25519 + HKDF + 信令/数据帧 AEAD
│   ├── crypto.py         # 通用 HKDF/AEAD/指纹小工具
│   ├── vault.py          # scrypt + ChaCha20-Poly1305 加密 vault
│   ├── mailcheck.py      # 可复用的 IMAP 能力检查
│   ├── imap_id.py        # 126/163 的 IMAP ID 命令
│   ├── net.py            # bind、地址格式化
│   ├── stun.py           # 最小 STUN 客户端
│   ├── candidate.py      # Candidate + lease
│   ├── punch.py          # P2P1 探测/回包
│   ├── session.py        # 状态机 + STUN + 密钥交换 + 聊天
│   ├── gui/              # PyQt6 GUI（MVP）
│   │   ├── app.py        # vault 解锁 + contact 列表 + 聊天
│   │   └── worker.py     # Session -> Qt signals
│   ├── transport.py      # v0.4 QUIC 占位
│   ├── log.py            # JSONL + 脱敏
│   └── signal/
│       ├── base.py       # Channel 接口
│       ├── file.py       # 共享目录
│       ├── imap_store.py # 共享邮箱（126 已验证）
│       └── smtp_mail.py  # 互相发邮件（实验）
├── tests/                # 13 个测试文件，38 个测试
├── scripts/
│   ├── setup.py          # identity-only 设置（peer 由 contact.py 管）
│   ├── setup_identity.py # setup.py 的实现
│   ├── setup_legacy.py   # 旧 flat create/join（兼容）
│   ├── contact.py        # new/join/list，每个对话一个 contact
│   ├── run_vault.py      # 用 vault + --contact 运行，交互式聊天
│   ├── pair_test.py      # 两个 vault 非交互双向测试
│   ├── check_email.py    # IMAP/SMTP 能力检查
│   ├── demo_same_machine.py
│   ├── demo_windows.ps1
│   ├── nat_probe.py
│   └── gen_psk.py
├── setup.identity.example.json
├── setup.example.json
├── setup.example.join.json
├── setup.example.imap.json
├── requirements.txt
├── requirements-gui.txt  # PyQt6
├── .github/workflows/ci.yml
├── .env.example
└── .gitignore
```

---

## 6. 测试

```bash
cd serverless-p2p/email-p2p
python3 -m unittest discover -s tests -v
```

13 个测试文件、38 个测试：

- `test_config.py`：PSK 只从环境变量来、不出现在日志字段里；
- `test_crypto.py`：HKDF/AEAD 往返、篡改失败；
- `test_keyexchange.py`：ECDH 一致、session key 一致、信令篡改、错误 PSK、数据帧重放/篡改；
- `test_punch.py`：两个本地 socket 互相打洞；
- `test_signal_file.py`：共享目录通道；
- `test_stun.py`：本地假 STUN 服务器解析；
- `test_vault.py`：vault 往返、错误口令、篡改失败、0600 权限；
- `test_runtime_env.py`：vault → 进程环境变量映射；
- `test_session_identity.py`：同邮箱但不同进程实例时，不把对方消息误判成自己的。
- `test_keepalive.py`：`PING`/`PONG` 和 keepalive 线程。
- `test_imap_store.py`：IMAP 按 UID 倒序返回最新邮件；163 的 `SUBJECT` 搜索为空时回退 `ALL`。
- `test_contacts.py`：扁平 vault 迁移成 identity + contact、按 email 选择 contact、多 contact 必须显式选择。
- `test_invite.py`：邀请码往返、无前缀、校验和错误。

跨 NAT 场景：

```bash
cd ../lab
./real_lab.sh -p cone
./real_lab.sh -p public
./real_lab.sh -p sym
```

---

## 7. 下一步

1. **真实网络复测**：163 Windows + 126 Linux，验证 keepalive 后 NAT 映射不超时。
2. **IMAP 真实验证**：同一台机器两个进程 → 两台机器；用新文件夹。
3. **SMTP 真实验证**：用真实邮箱测 `--signal smtp`；需要 SPF/DKIM 和 465 端口。
4. **QUIC**：v0.4；`aioquic`，同一个打洞 socket，临时证书 + 指纹固定。
5. **IPv6 优先**：有全球 IPv6 时跳过打洞。
6. **PAKE/身份密钥/轮换**：如果不想依赖长期 PSK。
7. **论文**：按 [`../docs/PAPER_SKELETON.md`](../docs/PAPER_SKELETON.md) 填实验与图表。

---

## 8. 已知限制

- 不设 PSK 时退回明文；`--advertise-host` 默认 `127.0.0.1`，只适合同机。
- **对称 NAT 无解**：双方 APDM 时返回 `E_SYMMETRIC_NAT`，不重试。
- **NAT 映射会超时**：默认 `--keepalive 15`；如果运营商 NAT 更短，改 5–10 秒；UDP 无重传，丢包就是丢消息。
- **IMAP 测试模式已在 126 验证；SMTP 每用户模式已在 163↔126 实测 `CONNECTED`；163 的 IMAP 不支持 `SUBJECT "p2p1/"`，代码已回退 `ALL` 并在 Python 过滤；其他 provider 可能不同**：IMAP 每次 `recv` 抓整个文件夹，消息多了会慢；SMTP 需要 SPF/DKIM 和 465/587 端口。126 的测试文件夹可能没被删除，去网页端手动删一下。
- **SMTP 投递受垃圾过滤影响**：建议两端用不同 provider（如 126 ↔ 163/QQ）各测一次；机器生成的邮件可能进垃圾箱。
- **共享邮箱测试模式需要双方持有同一个邮箱的授权码**：仅用于测试；产品模式（SMTP）各自用自己的邮箱，授权码不共享。
- **vault 的安全性取决于口令强度**：口令不保存，忘记只能重新 setup；`scrypt` 参数固定，未做硬件绑定。
- **QUIC 是占位**：当前数据走裸 UDP，不保证可靠/有序；丢包会丢消息。
- **信令防重放是基础版**：`ts` + 已见 `sid`；没有 challenge-response，120 s 窗口内的重放仍可能造成 DoS。
- **无身份密钥/无轮换/无 PAKE**：PSK 是唯一信任根；泄露只能手动轮换。
- **不保护元数据**：邮件服务器知道谁在何时通信、消息大小。
- **不防 root/内存取证**。
- **无 IPv6 路径**：目前只有 IPv4 host/srflx。
- **`punch` 有竞态**：打洞循环会读走第一个非 `P2P1` 包；demo 用 0.5 s 延迟规避，正式版要改“分拣员”。
- **Windows 未在本机实跑**：代码路径跨平台，CI 矩阵已配；明天实机确认。
