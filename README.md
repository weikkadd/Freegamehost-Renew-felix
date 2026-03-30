# 🎮 FGH-Renew

自动续期 FreeGameHost 服务器，每 6 小时运行一次。

## ⚙️ Secrets 配置

在仓库 **Settings → Secrets and variables → Actions** 中添加以下变量：

| Secret | 必填 | 格式 | 说明 |
|--------|:----:|------|------|
| 🔑 `FGH_ACCOUNT` | ✅ | `邮箱,密码` | FreeGameHost 登录账号 |
| 🔐 `PRIVATE_REPO_TOKEN` | ✅ | `ghp_xxxx` | 有 `repo` 权限的 PAT |
| 🌐 `GOST_PROXY` | ➖ | `socks5://user:pass@host:port` | 代理地址，不填则直连 |
| 📨 `TG_BOT` | ➖ | `chat_id,bot_token` | Telegram 推送，不填则跳过 |
