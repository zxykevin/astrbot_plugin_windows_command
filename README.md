# astrbot_plugin_windows_command

AstrBot 4.29.4 插件。让大肥鱼通过**已经连接的 SSH 反向隧道**，在一台受信任的 Windows 电脑上执行 PowerShell。

插件不创建、重连或管理隧道；它只连接 AstrBot/VPS 本机的 loopback 端口。远端命令固定使用 `powershell.exe`（或 `pwsh.exe`）的 `-EncodedCommand` 模式，不经过 AstrBot 主机的 shell。

## 安全默认值

- `execution_enabled=false`，默认不执行任何命令。
- `authorized_sender_ids=[]`，默认没有用户有权限。
- 只接受真实平台消息；拒绝 cron、主动聊天和合成事件。
- `ssh_host` 只接受 loopback IP，不能借此插件连接任意网络主机。
- 只支持 SSH 私钥认证，禁用密码、键盘交互和 SSH agent 的其他密钥。
- 强制 `StrictHostKeyChecking=yes` 和专用 `known_hosts`，不会自动信任新主机。
- 同一时刻只执行一条命令，并限制命令长度、执行时间和返回大小。

PowerShell 本身具有完整的 Windows 用户权限。启用本插件等于把该 Windows 账户的命令执行权交给白名单用户和 AstrBot 使用的模型。应为 Windows OpenSSH 创建权限受限的专用账户，不要使用管理员账户。

## 网络拓扑

假设 Windows OpenSSH Server 监听 `127.0.0.1:22`，Windows 主动连接 VPS，并在 VPS loopback 上建立反向转发：

```powershell
ssh.exe -NT `
  -o ExitOnForwardFailure=yes `
  -o ServerAliveInterval=30 `
  -o ServerAliveCountMax=3 `
  -R 127.0.0.1:2222:127.0.0.1:22 `
  codex@VPS_HOST
```

VPS 上的链路是：

```text
AstrBot plugin -> 127.0.0.1:2222 -> existing SSH tunnel -> Windows OpenSSH:22
```

请先在 VPS 上确认端口只监听 `127.0.0.1`/`::1`，而不是 `0.0.0.0`。

## 准备 Windows 登录密钥

使用一对**专用于 VPS -> Windows** 的 Ed25519 密钥。私钥只保存在 VPS；公钥加入 Windows 专用账户的 `authorized_keys`。不要复用 Windows -> VPS 建立隧道时的密钥。

先通过隧道人工核对并记录 Windows 主机公钥。例如在 VPS 上：

```bash
ssh-keyscan -p 2222 127.0.0.1
```

不要直接信任 `ssh-keyscan` 的结果。请在 Windows 本机将其与 OpenSSH 主机公钥指纹核对。核对后把公钥写成专用别名形式：

```text
feiyu-windows ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA...
```

保存为例如 `/opt/astrbot/secrets/feiyu-windows-known_hosts`。建议私钥与 known_hosts 均仅允许 AstrBot 运行用户读取。

人工验证：

```bash
ssh -T -p 2222 \
  -i /opt/astrbot/secrets/id_feiyu_windows \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=/opt/astrbot/secrets/feiyu-windows-known_hosts \
  -o HostKeyAlias=feiyu-windows \
  WINDOWS_USER@127.0.0.1 \
  'powershell.exe -NoProfile -NonInteractive -Command "$PSVersionTable.PSVersion"'
```

## AstrBot 配置

安装插件后至少设置：

```json
{
  "execution_enabled": true,
  "authorized_sender_ids": ["你的QQ号"],
  "ssh_host": "127.0.0.1",
  "ssh_port": 2222,
  "ssh_user": "WINDOWS_USER",
  "identity_file": "/opt/astrbot/secrets/id_feiyu_windows",
  "known_hosts_file": "/opt/astrbot/secrets/feiyu-windows-known_hosts",
  "host_key_alias": "feiyu-windows"
}
```

确认 AstrBot 容器或服务环境里能执行 `ssh`。若不在 `PATH`，将 `ssh_executable` 配成绝对路径。

插件向模型注册一个工具：

- `windows_powershell(command, timeout_seconds?)`

## 固定安全动作与陪伴主动触达

v0.2.0 增加 `windows_safe_action`，只允许以下固定动作：

- 读取在线、连续运行、键鼠空闲和电池状态；
- 发送 Windows 通知中心消息；
- 把正文写成系统桌面目录中的 `大肥鱼给你的信-*.txt`；
- 播放/暂停、上一首、下一首三个媒体键；
- 在独立开关允许时，安排带倒计时的关机或取消关机。

安全动作不接收路径、URL 或源代码，也没有删除、下载、安装和启动任意程序的动作。
原始 `windows_powershell` 仍只接受授权用户的真实平台消息，定时、主动聊天和合成事件继续拒绝。

可选的主动模式只读 Private Companion 的运行时数据库：当一条已送达的主动私聊超过设定时间仍没有后续用户输入时，向电脑推送同一句话；模型可在独立每日限额内判断是否同时写成桌面信。夜间用机规则只读取固定状态动作，先提醒，再按独立开关决定是否进入关机倒计时。

返回 JSON，包含 `exit_code`、`stdout`、`stderr` 和截断标记。非零退出码会以 `ok=false` 返回，但仍保留输出用于诊断。

## 安装

将整个 `astrbot_plugin_windows_command` 目录打包或推送到独立 Git 仓库，再通过 AstrBot 插件管理页安装。不要把 SSH 私钥、known_hosts、VPS 地址或生产配置提交到仓库。

## 本地测试

```powershell
cd astrbot_plugin_windows_command
python -m unittest discover -s tests -v
```

单元测试不连接真实 Windows，也不会执行命令。
