# 🎮 FGH-Renew   https://panel.freegamehost.xyz/

自动续期 FreeGameHost 服务器，每 4 小时运行一次，支持 reCAPTCHA 自动破解。

## ⭐ 功能特性

- 🔓 **全自动 reCAPTCHA 破解** — 音频识别 + 自动填写，无需人工干预
- 🌐 **智能代理轮换** — 检测到 Google 封锁后自动切换到下一个 v2rayN 代理节点（支持 vless/vmess/trojan/ss/hysteria2/hysteria）
- 📱 **Telegram 通知** — 续期成功/失败均推送消息，附带页面截图
- ⏰ **定时 + 手动运行** — 默认每 4 小时自动执行，也支持手动触发
- 🧹 **自动清理运行记录** — 只保留最近 2 条 Actions 记录，仓库保持清爽
- 🖥️ **虚拟显示无头运行** — 使用 Xvfb，不依赖图形界面

---

## ⚙️ Secrets 配置

在仓库 **Settings → Secrets and variables → Actions** 中添加以下变量：

| Secret | 必填 | 格式 | 说明 |
|--------|:----:|------|------|
| 🔑 `FGH_ACCOUNT` | ✅ | `邮箱,密码` | FreeGameHost 登录账号，例如 `user@example.com,password123` |
| 🌐 `PROXY_URI` | 推荐 | v2rayN 链接 | v2rayN 客户端复制的代理链接（vless/vmess/trojan/ss），支持多个换行分隔。详见下方说明 |
| 📨 `TG_BOT_TOKEN` | 可选 | `数字:字母数字` | Telegram Bot Token，用于推送通知 |
| 📨 `TG_CHAT_ID` | 可选 | `数字` | Telegram Chat ID，接收通知的目标 |

### PROXY_URI 格式说明

`PROXY_URI` 接受 v2rayN 客户端"复制链接"功能导出的链接，支持以下协议：

| 协议 | 链接前缀 | 示例 |
|------|---------|------|
| **VLESS** | `vless://` | `vless://uuid@host:443?type=ws&security=tls&sni=example.com&path=%2F#name` |
| **VMess** | `vmess://` | `vmess://eyJ2IjoiMiIsImFkZCI6...`（base64 编码 JSON） |
| **Trojan** | `trojan://` | `trojan://password@host:443?sni=host.com#name` |
| **Shadowsocks** | `ss://` | `ss://aes-256-gcm:password@host:8388#name` |
| **Hysteria2** | `hysteria2://` 或 `hy2://` | `hysteria2://password@host:443?sni=host.com&insecure=1#name` |
| **Hysteria (v1)** | `hysteria://` | `hysteria://password@host:443?sni=host.com#name` |

**多节点轮换**：用换行符（在 GitHub Secrets 输入框里直接回车）分隔多个链接，reCAPTCHA 被封时自动切换下一个节点。

**如何获取链接**：
- v2rayN 客户端 → 右键节点 → "复制链接"
- Clash Verge → 节点 → "分享 URL"
- 直接从机场订阅里复制单条链接

### 如何获取 Telegram 信息？

1. **Bot Token**：向 [@BotFather](https://t.me/BotFather) 发送 `/newbot` 创建机器人，获得 token
2. **Chat ID**：向 [@userinfobot](https://t.me/userinfobot) 发送任意消息即可获取

---

## 🚀 使用方法

### 方法 1：定时自动运行（默认）

Fork 本仓库并完成 Secrets 配置后，工作流会按以下时间自动执行（UTC 时间）：

```cron
25 */4 * * *   # 每 4 小时执行一次（00:25, 04:25, 08:25, 12:25, 16:25, 20:25）
```

如需修改频率，编辑 `.github/workflows/renew.yml` 中的 `schedule` 部分即可。

常用 cron 示例：
- `25 */4 * * *` — 每 4 小时一次
- `0 0,12 * * *` — 每天 0 点和 12 点（UTC）

### 方法 2：手动触发

1. 进入仓库的 **Actions** 页面
2. 选择 **FGH-Renew** 工作流
3. 点击 **Run workflow** 按钮

---

## 🐛 常见问题

### 1. reCAPTCHA 识别失败怎么办？

脚本内置了音频识别流程，但如果识别率较低，可能是以下原因：
- **网络问题**：Google 语音识别 API 访问不稳定，脚本会自动重试（最多 3 次下载、多次识别）
- **IP 被标记**：频繁操作会导致 Google 展示更难的验证码，脚本会自动切换到下一个 PROXY_URI 节点
- **环境噪音**：建议检查 Actions 日志中的识别结果 `[INFO] 识别结果: [xxxx]`

### 2. IP 被封锁导致无法继续？

当检测到 Google reCAPTCHA 封锁（verify button 长期 disabled）时，脚本会自动调用 `sing-box` 切换到下一个 `PROXY_URI` 节点，重启 Chrome 后重新尝试续期。整个流程最多尝试 **5 次** 代理切换。

### 3. 为什么需要代理？

FreeGameHost 面板嵌入了 Google reCAPTCHA。当短时间内多次尝试验证时，Google 可能会封禁当前 IP。`PROXY_URI` 接受 v2rayN 客户端链接格式，通过 `sing-box` 在本地启动 socks5 代理，Chrome 通过该代理访问面板。被封后自动切换到下一个节点，每个节点都有独立的出口 IP。

### 4. 没有收到 Telegram 通知？

- 确认是否已正确设置 `TG_BOT_TOKEN` 和 `TG_CHAT_ID` 两个 Secret
- 在 Telegram 中先和 Bot 发送 `/start` 激活对话
- 检查 Actions 日志中是否有 `Telegram 通知已发送` 的日志，或者错误信息

### 5. 截图在哪里查看？

每次 Actions 运行结束后，在运行详情页底部 **Artifacts** 区域可以下载 `debug-screenshots-运行编号` 的压缩包，里面包含每次续期的成功/失败截图。

### 6. 登录失败怎么办？

- 检查 `FGH_ACCOUNT` 格式是否正确（`邮箱,密码`，逗号是英文逗号）
- 确认账号密码在浏览器中能否正常登录 panel.freegamehost.xyz
- 查看截图 `01_login_page.png` 和 `02_after_login.png` 确认页面状态

### 7. 运行时间很长是否正常？

正常。每次续期需要启动 Chrome、加载页面、处理验证码、可能还需要切换 IP 并重试，单次通常需 **2-5 分钟**，遇到多次 IP 封锁可能会更久。GitHub Actions 限制最长 6 小时，完全足够。

---

## 🔒 安全建议

- **敏感信息存放**：账号密码、Telegram Token 等信息请严格存储在 GitHub Secrets 中，不要直接写在代码里
- **定期维护**：如果 FreeGameHost 网站更新了页面结构，可能需要更新元素定位逻辑，届时请关注仓库更新
- **Actions 权限**：默认的 `GITHUB_TOKEN` 权限已满足需要，无需额外设置
- **Fork 安全**：建议保持 Fork 仓库同步，以便获取最新的脚本修复

---

## 📄 许可证

MIT License

---

**⚠️ 免责声明**：本脚本仅供学习交流使用，使用者需遵守 FreeGameHost 的服务条款。因使用本脚本造成的任何问题，作者不承担任何责任。
