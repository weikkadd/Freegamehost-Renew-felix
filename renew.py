#!/usr/bin/env python3
"""
FreeGameHost.xyz 自动续期脚本
使用 SeleniumBase 模拟浏览器登录并点击 Renew 按钮。
环境变量:
  FGH_ACCOUNT  — 必填，格式: email,password
  BROWSER_PROXY — 可选，浏览器使用的代理
  GOST_PROXY   — 可选，上游代理
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


def clean_proxy(proxy_str):
    """清理代理字符串，去除 #label 后缀"""
    if not proxy_str:
        return None
    clean = proxy_str.split('#')[0].strip()
    return clean if clean else None


def main():
    # ── 解析账号 ──
    account = parse_env("FGH_ACCOUNT")
    if not account or len(account) < 2:
        log("错误: FGH_ACCOUNT 未设置或格式不正确（应为 email,password）")
        sys.exit(1)

    email = account[0]
    password = account[1]
    log(f"账号: {email}")
    log(f"密码长度: {len(password)}")

    # ── 解析 Telegram ──
    tg = parse_env("TG_BOT")
    tg_chat_id = tg_bot_token = None
    if tg and len(tg) >= 2:
        tg_chat_id, tg_bot_token = tg[0], tg[1]

    # ── 代理设置 ──
    browser_proxy_env = os.environ.get("BROWSER_PROXY", None)
    if browser_proxy_env is not None and browser_proxy_env.strip() == "":
        proxy_arg = None
        log("⚠️ BROWSER_PROXY 已设为空，使用直连模式")
    elif browser_proxy_env:
        proxy_arg = browser_proxy_env.strip()
        log(f"使用代理: {proxy_arg}")
    else:
        raw_gost = os.environ.get("GOST_PROXY", "").strip() or None
        proxy_arg = clean_proxy(raw_gost) if raw_gost else None
        if proxy_arg:
            log(f"使用代理: {proxy_arg}")
        else:
            log("⚠️ 未配置代理，使用直连模式")

    # ── 启动浏览器 ──
    sb_kwargs = dict(
        browser="chrome",
        headless=True,
    )
    if proxy_arg:
        sb_kwargs["proxy"] = proxy_arg

    with SB(**sb_kwargs) as sb:

        success = False
        error_msg = ""

        try:
            # ── 第一步：登录 ──
            log("正在打开登录页面...")
            sb.open(f"{PANEL_URL}/auth/login")
            time.sleep(8)  # 等待 JS 完全加载

            # 截图用于调试
            sb.save_screenshot("01_login_page.png")
            log(f"当前URL: {sb.get_current_url()}")

            # 获取页面 HTML 片段用于调试
            try:
                body_html = sb.get_text("body")
                log(f"页面文本长度: {len(body_html)}")
                # 打印包含错误/失败/invalid 的行
                lines = body_html.split('\n')
                for line in lines:
                    line_lower = line.lower()
                    if any(kw in line_lower for kw in ['error', 'fail', 'invalid', '错误', '失败', 'invalid']):
                        log(f"发现关键行: {line.strip()[:200]}")
            except Exception as e:
                log(f"获取页面文本失败: {e}")

            # ── 诊断所有表单元素 ──
            log("=== 所有表单元素 ===")
            all_inputs = sb.find_elements("input")
            log(f"共 {len(all_inputs)} 个 input:")
            csrf_token = None
            for i, inp in enumerate(all_inputs):
                try:
                    name = inp.get_attribute("name")
                    id_attr = inp.get_attribute("id")
                    type_attr = inp.get_attribute("type") or "text"
                    value = inp.get_attribute("value")
                    placeholder = inp.get_attribute("placeholder")
                    log(f"  [{i}] type={type_attr}, name={name}, id={id_attr}, placeholder={placeholder}")
                    # 检查 CSRF token
                    if name and ('csrf' in name.lower() or 'token' in name.lower()):
                        csrf_token = value
                        log(f"  ⚠️ 发现可能的 CSRF token: {value[:20] if value else 'None'}...")
                except Exception:
                    pass

            all_buttons = sb.find_elements("button")
            log(f"共 {len(all_buttons)} 个 button:")
            for i, btn in enumerate(all_buttons):
                try:
                    text = btn.text.strip()[:50]
                    btn_type = btn.get_attribute("type")
                    log(f"  [{i}] text='{text}', type={btn_type}")
                except Exception:
                    pass

            # ── 尝试登录 ──
            logged_in = False
            attempts = 0

            while not logged_in and attempts < 2:
                attempts += 1
                log(f"\n=== 登录尝试 {attempts} ===")

                # 重新定位元素（防止页面刷新导致元素丢失）
                try:
                    user_input = sb.wait_for_element('input[name="username"]', timeout=5)
                    pass_input = sb.wait_for_element('input[name="password"]', timeout=5)
                    log("✅ 找到登录表单")
                except Exception:
                    # 尝试其他选择器
                    try:
                        user_input = sb.wait_for_element('input[type="text"]', timeout=5)
                        pass_input = sb.wait_for_element('input[type="password"]', timeout=5)
                        log("✅ 通过类型找到输入框")
                    except Exception:
                        log("❌ 找不到登录输入框")
                        sb.save_screenshot("error_no_form.png")
                        break

                # 清空并填入
                try:
                    user_input.clear()
                    user_input.send_keys(email)
                    log(f"已填入用户名: {email}")
                except Exception as e:
                    log(f"填入用户名失败: {e}")

                try:
                    pass_input.clear()
                    pass_input.send_keys(password)
                    log(f"已填入密码 (长度{len(password)})")
                except Exception as e:
                    log(f"填入密码失败: {e}")

                # 点击登录
                try:
                    sb.click('button[type="submit"]')
                    log("已点击 LOGIN 按钮")
                except Exception:
                    try:
                        sb.click('button:contains("LOGIN")')
                        log("已点击 LOGIN 按钮 (第二种选择器)")
                    except Exception as e:
                        log(f"点击登录按钮失败: {e}")
                        sb.save_screenshot("error_click_fail.png")
                        break

                # 等待跳转
                log("等待登录响应...")
                time.sleep(12)

                current_url = sb.get_current_url()
                log(f"登录后URL: {current_url}")

                if "auth/login" not in current_url and "login" not in current_url:
                    log("✅ 登录成功！")
                    logged_in = True
                    sb.save_screenshot("02_logged_in.png")
                else:
                    # 检查错误消息
                    log("\n=== 检查错误消息 ===")
                    error_found = False

                    # 方法1: 查找 alert/error 类元素
                    for selector in ['.alert', '.error', '.invalid', '[class*="error"]',
                                    '[class*="invalid"]', '.text-danger', '.bg-danger',
                                    '.flash-error', '.notification-error']:
                        try:
                            el = sb.wait_for_element(selector, timeout=2)
                            text = el.text.strip()
                            if text:
                                log(f"发现错误 [{selector}]: {text[:200]}")
                                error_found = True
                                break
                        except Exception:
                            continue

                    # 方法2: 在 body 文本中搜索
                    if not error_found:
                        try:
                            body_text = sb.get_text("body")
                            lines = body_text.split('\n')
                            for line in lines:
                                line = line.strip()
                                if len(line) > 3 and any(kw in line.lower() for kw in
                                    ['错误', '失败', 'invalid', 'error', 'wrong', 'incorrect', '无效']):
                                    log(f"发现错误文本: {line[:200]}")
                                    error_found = True
                                    break
                        except Exception:
                            pass

                    if not error_found:
                        log("未检测到明确错误消息，但仍在登录页面")

                    sb.save_screenshot(f"03_login_attempt{attempts}_failed.png")

                    # 如果第二次尝试仍然失败，放弃
                    if attempts >= 2:
                        break

            if not logged_in:
                error_msg = "登录失败：账号密码错误或需要额外验证"
                sb.save_screenshot("error_login_final.png")
                raise Exception(error_msg)

            # ── 第二步：前往仪表盘 ──
            log("正在打开仪表盘...")
            sb.open(f"{PANEL_URL}/")
            time.sleep(5)
            sb.save_screenshot("04_dashboard.png")

            # ── 第三步：点击 Renew 按钮 ──
            renew_selectors = [
                'button:contains("Renew")',
                'a:contains("Renew")',
                '[class*="renew"]',
                '[id*="renew"]',
                'button:contains("Extend")',
                'a:contains("Extend")',
                'button:contains("续费")',
                'a:contains("续费")',
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
                try:
                    buttons = sb.find_elements("button")
                    for btn in buttons:
                        text = btn.text.strip().lower()
                        if "renew" in text or "extend" in text or "续费" in text:
                            sb.click(btn)
                            renewed = True
                            log(f"通过文本匹配点击续期: {text}")
                            break
                except Exception as e:
                    log(f"遍历按钮失败: {e}")

            if not renewed:
                try:
                    links = sb.find_elements("a")
                    for link in links:
                        text = link.text.strip().lower()
                        if "renew" in text or "extend" in text or "续费" in text:
                            sb.click(link)
                            renewed = True
                            log(f"通过文本匹配点击续期链接: {text}")
                            break
                except Exception as e:
                    log(f"遍历链接失败: {e}")

            time.sleep(5)
            sb.save_screenshot("05_after_renew.png")

            if renewed:
                log("✅ 续期成功！")
                success = True
            else:
                log("⚠️ 未找到续期按钮，可能已续期或页面结构变化")
                error_msg = "未找到续期按钮"
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
                                    btns = sb.find_elements("button")
                                    for btn in btns:
                                        t = btn.text.strip().lower()
                                        if "renew" in t or "extend" in t or "续费" in t:
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
