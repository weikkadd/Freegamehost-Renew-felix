#!/usr/bin/env python3
"""
FreeGameHost.xyz 自动续期脚本
使用 SeleniumBase 模拟浏览器登录并点击 Renew 按钮。
环境变量:
  FGH_ACCOUNT  — 必填，格式: email,password
  GOST_PROXY   — 可选，格式: socks5://user:pass@host:port
  TG_BOT       — 可选，格式: chat_id,bot_token
"""

import os
import sys
import time
import json
import urllib.request
import urllib.parse
from seleniumbase import SB

PANEL_URL = "https://panel.freegamehost.xyz"


def log(msg):
    print(f"[FGH-Renew] {msg}", flush=True)


def send_tg(chat_id, bot_token, text):
    """发送 Telegram 通知"""
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }).encode()
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            log(f"Telegram 通知已发送: {resp.status}")
    except Exception as e:
        log(f"Telegram 通知发送失败: {e}")


def parse_env(name, sep=","):
    """解析环境变量，返回各部分"""
    val = os.environ.get(name, "").strip()
    if not val:
        return None
    parts = [p.strip() for p in val.split(sep)]
    return parts if len(parts) > 1 else parts[0]


def main():
    # ── 解析账号 ──
    account = parse_env("FGH_ACCOUNT")
    if not account or len(account) < 2:
        log("错误: FGH_ACCOUNT 未设置或格式不正确（应为 email,password）")
        sys.exit(1)

    email, password = account[0], account[1]
    log(f"账号: {email}")

    # ── 解析 Telegram ──
    tg = parse_env("TG_BOT")
    tg_chat_id = tg_bot_token = None
    if tg and len(tg) >= 2:
        tg_chat_id, tg_bot_token = tg[0], tg[1]

    # ── 代理设置 ──
    proxy = os.environ.get("GOST_PROXY", "").strip()
    proxy_arg = None
    if proxy:
        proxy_arg = proxy
        log(f"使用代理: {proxy.split('@')[-1] if '@' in proxy else proxy}")

    # ── 启动浏览器 ──
    with SB(
        browser="chrome",
        headless=True,
        proxy=proxy_arg,
        gui=False,
    ) as sb:

        success = False
        error_msg = ""

        try:
            # ── 第一步：登录 ──
            log("正在打开登录页面...")
            sb.open(f"{PANEL_URL}/auth/login")
            time.sleep(3)

            # 截图用于调试
            sb.save_screenshot("01_login_page.png")

            # 检查是否已在登录页
            if not sb.is_element_present('input[name="username"]'):
                # Pterodactyl 有时用 email 字段
                if sb.is_element_present('input[name="email"]'):
                    sb.type('input[name="email"]', email)
                else:
                    log("找不到用户名/邮箱输入框")
                    sb.save_screenshot("error_no_input.png")
                    error_msg = "找不到登录输入框"
                    raise Exception(error_msg)
            else:
                sb.type('input[name="username"]', email)

            # 输入密码
            if sb.is_element_present('input[name="password"]'):
                sb.type('input[name="password"]', password)
            elif sb.is_element_present('input[type="password"]'):
                sb.type('input[type="password"]', password)
            else:
                log("找不到密码输入框")
                sb.save_screenshot("error_no_password.png")
                error_msg = "找不到密码输入框"
                raise Exception(error_msg)

            # 点击登录按钮
            login_selectors = [
                'button[type="submit"]',
                'button:contains("Login")',
                'button:contains("Sign")',
                '#login-button',
                '.btn-primary',
            ]

            clicked = False
            for sel in login_selectors:
                try:
                    if sb.is_element_present(sel):
                        sb.click(sel)
                        clicked = True
                        log("已点击登录按钮")
                        break
                except Exception:
                    continue

            if not clicked:
                # 尝试按回车
                sb.send_keys('input[type="password"]', "\n")
                log("尝试按回车登录")

            # 等待页面跳转
            time.sleep(8)
            sb.save_screenshot("02_after_login.png")

            # 检查是否登录成功（URL 变化或出现仪表盘元素）
            current_url = sb.get_current_url()
            log(f"登录后 URL: {current_url}")

            if "auth/login" in current_url:
                log("登录失败：仍在登录页面")
                sb.save_screenshot("error_login_failed.png")
                error_msg = "登录失败，可能账号密码错误"
                raise Exception(error_msg)

            log("登录成功！")

            # ── 第二步：前往仪表盘 ──
            log("正在打开仪表盘...")
            sb.open(f"{PANEL_URL}/")
            time.sleep(5)
            sb.save_screenshot("03_dashboard.png")

            # ── 第三步：点击 Renew 按钮 ──
            # FreeGameHost 定制面板有 Renew 按钮
            # 尝试多种可能的选择器
            renew_selectors = [
                'button:contains("Renew")',
                'a:contains("Renew")',
                '[class*="renew"]',
                '[id*="renew"]',
                'button:contains("Extend")',
                'a:contains("Extend")',
            ]

            renewed = False
            for sel in renew_selectors:
                try:
                    if sb.is_element_present(sel):
                        sb.click(sel)
                        renewed = True
                        log(f"点击续期按钮成功: {sel}")
                        break
                except Exception:
                    continue

            if not renewed:
                # 尝试遍历所有按钮找 Renew 文本
                try:
                    buttons = sb.find_elements("button")
                    for btn in buttons:
                        text = btn.text.strip().lower()
                        if "renew" in text or "extend" in text:
                            sb.click(btn)
                            renewed = True
                            log(f"通过文本匹配点击续期按钮: {text}")
                            break
                except Exception as e:
                    log(f"遍历按钮失败: {e}")

            if not renewed:
                # 尝试链接
                try:
                    links = sb.find_elements("a")
                    for link in links:
                        text = link.text.strip().lower()
                        if "renew" in text or "extend" in text:
                            sb.click(link)
                            renewed = True
                            log(f"通过文本匹配点击续期链接: {text}")
                            break
                except Exception as e:
                    log(f"遍历链接失败: {e}")

            time.sleep(5)
            sb.save_screenshot("04_after_renew.png")

            if renewed:
                log("✅ 续期成功！")
                success = True
            else:
                log("⚠️ 未找到续期按钮，可能已续期或页面结构变化")
                error_msg = "未找到续期按钮"
                # 检查是否有服务器列表，逐个进入续期
                try:
                    server_links = sb.find_elements('a[href*="/server/"]')
                    if server_links:
                        log(f"发现 {len(server_links)} 个服务器，尝试逐个续期...")
                        for i, sl in enumerate(server_links):
                            try:
                                href = sl.get_attribute("href")
                                log(f"进入服务器 {i+1}: {href}")
                                sb.open(href)
                                time.sleep(3)
                                sb.save_screenshot(f"server_{i+1}.png")

                                # 在服务器页面找 Renew 按钮
                                found = False
                                for sel in renew_selectors:
                                    try:
                                        if sb.is_element_present(sel):
                                            sb.click(sel)
                                            found = True
                                            log(f"服务器 {i+1} 续期成功")
                                            time.sleep(3)
                                            break
                                    except Exception:
                                        continue

                                if not found:
                                    # 遍历按钮
                                    btns = sb.find_elements("button")
                                    for btn in btns:
                                        t = btn.text.strip().lower()
                                        if "renew" in t or "extend" in t:
                                            sb.click(btn)
                                            found = True
                                            log(f"服务器 {i+1} 续期成功（文本匹配）")
                                            time.sleep(3)
                                            break

                                if not found:
                                    log(f"服务器 {i+1} 未找到续期按钮")
                            except Exception as e:
                                log(f"服务器 {i+1} 续期失败: {e}")

                        success = True
                        log("所有服务器遍历完成")
                except Exception as e:
                    log(f"服务器遍历失败: {e}")
                    error_msg = f"续期失败: {e}"

        except Exception as e:
            error_msg = str(e)
            log(f"❌ 出错: {error_msg}")
            try:
                sb.save_screenshot("error_exception.png")
            except Exception:
                pass

    # ── 发送 Telegram 通知 ──
    if tg_chat_id and tg_bot_token:
        if success:
            msg = "✅ <b>FreeGameHost 续期成功</b>\n账号已自动续期。"
        else:
            msg = f"❌ <b>FreeGameHost 续期失败</b>\n错误: {error_msg}"
        send_tg(tg_chat_id, tg_bot_token, msg)

    if success:
        log("完成！")
        sys.exit(0)
    else:
        log(f"失败: {error_msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()
