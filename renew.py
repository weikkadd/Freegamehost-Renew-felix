#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FreeGameHost.xyz Auto Renew Script
Uses DrissionPage + reCAPTCHA audio recognition
"""

import os
import sys
import time
import random
import html
import signal
import requests
import tempfile
from datetime import datetime
from xvfbwrapper import Xvfb
from DrissionPage import ChromiumPage, ChromiumOptions

try:
    import speech_recognition as sr
    from pydub import AudioSegment
except ImportError:
    print("[WARN] speech_recognition/pydub not installed")
    sr = None

PANEL_URL = "https://panel.freegamehost.xyz"
MAX_CAPTCHA_ATTEMPTS = 10
SCREENSHOT_DIR = "output/screenshots"
# Hard caps to prevent the script from hanging on Google's slow responses
RELOAD_TIMEOUT_S = 15          # Max total time for reload_challenge to complete
SWITCH_AUDIO_TIMEOUT_S = 12    # Max total time for switch_to_audio
GET_AUDIO_URL_TIMEOUT_S = 6    # Max total time for get_audio_url polling
ATTEMPT_TIMEOUT_S = 60         # Max time for a single solve_recaptcha attempt
SOLVE_TOTAL_TIMEOUT_S = 480    # Max total time for solve_recaptcha (8 min)


def log(msg, level="INFO"):
    prefix = {"INFO": "[FGH-Renew]", "WARN": "[WARN]", "ERROR": "[ERROR]"}.get(level, "[FGH-Renew]")
    print(f"{prefix} {msg}", flush=True)


def send_tg(token, chat_id, text):
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        r = requests.post(url, data=data, timeout=30)
        r.raise_for_status()
        log("Telegram notification sent")
        return True
    except Exception as e:
        log(f"Telegram notification failed: {e}", "ERROR")
        return False


def mask_url(url):
    import re
    return re.sub(r'(\?i=)([^&]{1})([^&]*)', r'\1\2***', url)


def get_expire_time(page):
    selectors = ['#expireDate', 'text:Expires in:', 'text:Deletes on:']
    for sel in selectors:
        try:
            ele = page.ele(sel, timeout=2)
            if ele:
                return ele.text.strip()
        except:
            continue
    return "Unknown"


def fill_login_form(page, email, password):
    """Fill login form using JS injection (SPA compatible)"""
    log("Filling login info...")
    
    result = page.run_js(f"""
        const inputs = document.querySelectorAll('input');
        let usernameFilled = false;
        let passwordFilled = false;
        
        inputs.forEach(inp => {{
            const type = inp.type || '';
            const placeholder = (inp.placeholder || '').toLowerCase();
            
            if (!usernameFilled && (type === 'email' || type === 'text' || placeholder.includes('email') || placeholder.includes('user'))) {{
                inp.value = '{email}';
                inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                usernameFilled = true;
            }} else if (!passwordFilled && type === 'password') {{
                inp.value = '{password}';
                inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                passwordFilled = true;
            }}
        }});
        
        return {{username: usernameFilled, password: passwordFilled}};
    """)
    
    log(f"JS fill result: {result}")
    return result.get('username', False) and result.get('password', False)


def debug_page(page):
    """Debug: print all page elements"""
    log("=" * 50)
    log("=== PAGE DEBUG INFO ===")
    log(f"URL: {page.url}")
    log(f"Title: {page.title}")
    
    try:
        html_content = page.html
        log(f"HTML length: {len(html_content)}")
    except:
        pass
    
    try:
        inputs = page.eles('input')
        log(f"Found {len(inputs)} inputs:")
        for i, inp in enumerate(inputs):
            inp_type = inp.attr('type') or 'unknown'
            inp_name = inp.attr('name') or ''
            inp_id = inp.attr('id') or ''
            log(f"  [{i}] type={inp_type}, name={inp_name}, id={inp_id}")
    except Exception as e:
        log(f"Failed to get inputs: {e}", "WARN")
    
    try:
        buttons = page.eles('button')
        log(f"Found {len(buttons)} buttons:")
        for i, btn in enumerate(buttons):
            log(f"  [{i}] text={repr(btn.text[:30])}, type={btn.attr('type')}")
    except Exception as e:
        log(f"Failed to get buttons: {e}", "WARN")
    
    try:
        frames = page.get_frames()
        log(f"Found {len(frames)} frames:")
        for i, f in enumerate(frames):
            log(f"  [{i}] url={f.url[:80] if f.url else 'None'}")
    except Exception as e:
        log(f"Failed to get frames: {e}", "WARN")
    
    log("=" * 50)


# reCAPTCHA functions
def find_recaptcha_frame(page, kind):
    try:
        for frame in page.get_frames():
            url = frame.url or ""
            if "recaptcha" in url and kind in url:
                return frame
    except:
        pass
    return None


def is_recaptcha_solved(page):
    try:
        for frame in page.get_frames():
            try:
                token = frame.run_js("return document.querySelector(\"textarea[name='g-recaptcha-response']\")?.value")
                if token and len(token) > 30:
                    return True
            except:
                pass
    except:
        pass
    anchor = find_recaptcha_frame(page, "anchor")
    if anchor:
        try:
            checked = anchor.run_js("return document.querySelector('#recaptcha-anchor')?.getAttribute('aria-checked') === 'true'")
            if checked:
                return True
        except:
            pass
    return False


def is_blocked(page):
    bframe = find_recaptcha_frame(page, "bframe")
    if not bframe:
        return False
    try:
        return bool(bframe.run_js("""
            const h = document.querySelector('.rc-doscaptcha-header-text');
            if (h && h.textContent.toLowerCase().includes('try again later')) return true;
            const e = document.querySelector('.rc-audiochallenge-error-message');
            if (e && e.offsetParent !== null) return true;
            return false;
        """))
    except:
        return False


def wait_for_frame_ready(frame, timeout=20):
    """Wait for a frame's document to reach readyState=complete."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            ready = frame.run_js("return document.readyState")
            if ready == "complete":
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def is_invisible_recaptcha(page):
    """Detect invisible reCAPTCHA by checking anchor iframe src for size=invisible."""
    try:
        for frame in page.get_frames():
            url = frame.url or ""
            if "recaptcha" in url and "anchor" in url and "size=invisible" in url:
                return True
    except Exception:
        pass
    # Also check the iframe element's src attribute on the page itself
    try:
        iframe_ele = page.ele('xpath://iframe[contains(@src,"recaptcha/api2/anchor")]', timeout=1)
        if iframe_ele:
            src = iframe_ele.attr('src') or ''
            if 'size=invisible' in src:
                return True
    except Exception:
        pass
    return False


def try_invoke_grecaptcha_execute(page):
    """For invisible reCAPTCHA, programmatically invoke grecaptcha.execute() to trigger challenge."""
    try:
        page.run_js("""
            if (typeof grecaptcha !== 'undefined') {
                try {
                    // Try to execute the first widget (typical case)
                    if (grecaptcha.execute) grecaptcha.execute();
                    // Some sites use widget-list API
                    if (grecaptcha.widgets && grecaptcha.widgets.length > 0) {
                        try { grecaptcha.execute(grecaptcha.widgets[0]); } catch(e) {}
                    }
                } catch(e) {}
            }
        """)
        return True
    except Exception as e:
        log(f"grecaptcha.execute() failed: {e}", "WARN")
        return False


def click_recaptcha_checkbox(page):
    """Click reCAPTCHA checkbox (visible mode only). For invisible mode, this is a no-op."""
    if is_invisible_recaptcha(page):
        log("Invisible reCAPTCHA detected — no checkbox to click, invoking execute() instead")
        try_invoke_grecaptcha_execute(page)
        time.sleep(2)
        return

    anchor = find_recaptcha_frame(page, "anchor")
    if not anchor:
        for _ in range(60):
            anchor = find_recaptcha_frame(page, "anchor")
            if anchor:
                break
            time.sleep(1)
    if not anchor:
        raise RuntimeError("reCAPTCHA anchor frame not found")

    # Wait for the anchor frame's document to be fully loaded
    log("Waiting for reCAPTCHA anchor frame content to load...")
    if not wait_for_frame_ready(anchor, timeout=20):
        log("Anchor frame did not reach readyState=complete within 20s", "WARN")

    # Diagnostic: log what's inside the anchor frame
    try:
        body_snippet = anchor.run_js(
            "return document.body ? document.body.innerHTML.substring(0, 300) : 'no body'"
        ) or ""
        log(f"Anchor frame body preview: {body_snippet[:200]}")
    except Exception as e:
        log(f"Could not read anchor frame content: {e}", "WARN")

    # Try multiple selectors for checkbox
    checkbox_selectors = [
        '#recaptcha-anchor',
        '[role="checkbox"]',
        '.recaptcha-checkbox-border',
        '.rc-anchor-checkbox',
        '.recaptcha-checkbox',
        '#recaptcha-anchor.recaptcha-checkbox',
    ]

    checkbox = None
    for sel in checkbox_selectors:
        try:
            checkbox = anchor.ele(sel, timeout=3)
            if checkbox:
                log(f"Found checkbox using selector: {sel}")
                break
        except Exception:
            continue

    if not checkbox:
        raise RuntimeError("reCAPTCHA checkbox not found (visible mode but no checkbox element)")

    page.actions.move_to(checkbox, duration=random.uniform(0.4, 1.0))
    time.sleep(random.uniform(0.2, 0.5))
    try:
        checkbox.click()
    except Exception:
        checkbox.click(by_js=True)
    time.sleep(3)
    if is_blocked(page):
        raise Exception("IP blocked by Google reCAPTCHA")


def switch_to_audio(page):
    bframe = find_recaptcha_frame(page, "bframe")
    if not bframe:
        return False
    try:
        input_box = bframe.ele('#audio-response', timeout=1)
        if input_box and input_box.states.is_displayed:
            return True
    except:
        pass
    for _ in range(3):
        try:
            audio_btn = bframe.ele('#recaptcha-audio-button', timeout=3)
            if audio_btn:
                try:
                    audio_btn.click()
                except:
                    audio_btn.click(by_js=True)
                time.sleep(3)
                if is_blocked(page):
                    raise Exception("IP blocked after clicking audio button")
                input_box = bframe.ele('#audio-response', timeout=1)
                if input_box and input_box.states.is_displayed:
                    return True
        except Exception as e:
            if "blocked" in str(e).lower():
                raise
            pass
        try:
            bframe.run_js("document.querySelector('#recaptcha-audio-button')?.click();")
            time.sleep(3)
            if is_blocked(page):
                raise Exception("IP blocked after JS click")
            input_box = bframe.ele('#audio-response', timeout=1)
            if input_box and input_box.states.is_displayed:
                return True
        except Exception as e:
            if "blocked" in str(e).lower():
                raise
            pass
        time.sleep(2)
    return False


def is_audio_mode(page):
    bframe = find_recaptcha_frame(page, "bframe")
    if not bframe:
        return False
    try:
        input_box = bframe.ele('#audio-response', timeout=1)
        return bool(input_box and input_box.states.is_displayed)
    except:
        return False


def get_audio_url(page):
    """Get audio URL from bframe. Capped at GET_AUDIO_URL_TIMEOUT_S seconds."""
    bframe = find_recaptcha_frame(page, "bframe")
    if not bframe:
        return None
    start = time.time()
    while time.time() - start < GET_AUDIO_URL_TIMEOUT_S:
        try:
            link = bframe.ele('.rc-audiochallenge-tdownload-link', timeout=1)
            if link:
                href = link.attr('href')
                if href and len(href) > 10:
                    return html.unescape(href)
            link = bframe.ele('.rc-audiochallenge-ndownload-link', timeout=1)
            if link:
                href = link.attr('href')
                if href and len(href) > 10:
                    return html.unescape(href)
            audio = bframe.ele('#audio-source', timeout=1)
            if audio:
                src = audio.attr('src')
                if src and len(src) > 10:
                    return html.unescape(src)
        except Exception:
            pass
        time.sleep(0.5)
    return None


def reload_challenge(page):
    """Reload audio challenge. Returns True if audio URL changed after reload.
    
    Each click strategy is verified by checking if the audio URL changed immediately after.
    If a strategy didn't actually work (URL stayed same), we try the next strategy.
    This avoids the failure mode where DrissionPage's native .click() returns success
    but the click was actually intercepted/no-op in the cross-origin iframe.
    """
    bframe = find_recaptcha_frame(page, "bframe")
    if not bframe:
        log("reload_challenge: no bframe", "WARN")
        return False

    old_url = get_audio_url(page)
    log(f"reload_challenge: old audio URL = {(old_url or 'None')[:80]}")
    func_start = time.time()
    
    # CRITICAL: Wait for reload button to be enabled.
    # After clicking Verify, Google disables ALL bframe buttons for 1-3s while it
    # processes the answer server-side. Clicking a disabled button is silently ignored.
    # We must wait for the button to become enabled before clicking.
    log("reload_challenge: waiting for reload button to be enabled...")
    btn_ready = False
    while time.time() - func_start < 8:  # max 8s wait
        try:
            state = bframe.run_js("""
                const btn = document.querySelector('#recaptcha-reload-button');
                if (!btn) return 'gone';
                if (btn.disabled) return 'disabled';
                // Also check the disabled CSS class (Google uses both)
                if (btn.className.includes('rc-button-disabled')) return 'disabled-css';
                return 'enabled';
            """)
            if state == 'enabled':
                btn_ready = True
                log(f"reload_challenge: reload button enabled after {time.time()-func_start:.1f}s")
                break
            elif state in ('disabled', 'disabled-css'):
                # Still disabled, keep waiting
                pass
            elif state == 'gone':
                log("reload_challenge: reload button disappeared!", "WARN")
                break
        except Exception:
            pass
        time.sleep(0.3)
    
    if not btn_ready:
        log(f"reload_challenge: reload button still disabled after 8s, trying anyway", "WARN")
    
    # Diagnostic: dump bframe HTML structure to find the actual reload button
    try:
        buttons_html = bframe.run_js("""
            const btns = document.querySelectorAll('button');
            return Array.from(btns).map(b => ({
                id: b.id,
                cls: b.className,
                title: b.title || '',
                ariaLabel: b.getAttribute('aria-label') || '',
                text: b.textContent.trim().substring(0, 30),
                disabled: b.disabled,
                rect: JSON.stringify({
                    x: Math.round(b.getBoundingClientRect().x),
                    y: Math.round(b.getBoundingClientRect().y),
                    w: Math.round(b.getBoundingClientRect().width),
                    h: Math.round(b.getBoundingClientRect().height),
                    visible: b.offsetParent !== null
                })
            }));
        """)
        if buttons_html:
            log(f"reload_challenge: bframe buttons = {buttons_html}")
    except Exception as e:
        log(f"reload_challenge: failed to dump buttons: {e}", "WARN")

    def click_strategies():
        """Generator yielding (name, fn) pairs of click strategies."""
        # Strategy 1: JS click on the element by ID — try this FIRST, more reliable in cross-origin iframe
        yield ('js-id', lambda: bframe.run_js(
            "const btn = document.querySelector('#recaptcha-reload-button'); "
            "if (btn) { btn.click(); return true; } return false;"
        ))
        # Strategy 2: synthetic MouseEvent with element center coordinates
        yield ('js-mouseevent', lambda: bframe.run_js("""
            const btn = document.querySelector('#recaptcha-reload-button');
            if (!btn) return false;
            const rect = btn.getBoundingClientRect();
            const evt = new MouseEvent('click', {
                bubbles: true, cancelable: true, view: window,
                clientX: rect.left + rect.width / 2,
                clientY: rect.top + rect.height / 2
            });
            btn.dispatchEvent(evt);
            return true;
        """))
        # Strategy 3: full pointer event sequence (pointerdown + mousedown + pointerup + mouseup + click)
        yield ('js-pointer', lambda: bframe.run_js("""
            const btn = document.querySelector('#recaptcha-reload-button');
            if (!btn) return false;
            const rect = btn.getBoundingClientRect();
            const opts = {
                bubbles: true, cancelable: true, view: window,
                clientX: rect.left + rect.width / 2,
                clientY: rect.top + rect.height / 2,
                button: 0
            };
            btn.dispatchEvent(new PointerEvent('pointerdown', opts));
            btn.dispatchEvent(new MouseEvent('mousedown', opts));
            btn.dispatchEvent(new PointerEvent('pointerup', opts));
            btn.dispatchEvent(new MouseEvent('mouseup', opts));
            btn.dispatchEvent(new MouseEvent('click', opts));
            return true;
        """))
        # Strategy 4: DrissionPage native click (LAST resort, since DP click in cross-origin iframe is often no-op)
        yield ('dp-native', lambda: _try_dp_click(bframe, '#recaptcha-reload-button'))
        # Strategy 5: try by aria-label/title (in case ID is wrong)
        yield ('js-aria', lambda: bframe.run_js(
            'const btn = document.querySelector(\'button[aria-label*="eload"], button[title*="eload"]\'); '
            'if (btn) { btn.click(); return true; } return false;'
        ))
        # Strategy 6: try clicking on the reload button via parent (some recaptchas wrap it)
        yield ('js-parent', lambda: bframe.run_js("""
            const btn = document.querySelector('#recaptcha-reload-button');
            if (!btn) return false;
            // Find clickable parent
            let target = btn;
            for (let i = 0; i < 3; i++) {
                target = target.parentElement;
                if (!target) break;
                if (target.onclick || target.getAttribute('role') === 'button') {
                    target.click();
                    return true;
                }
            }
            // Last resort: directly call the recaptcha JS API
            try {
                if (typeof ___grecaptcha_cfg !== 'undefined') {
                    // Force a new challenge by reloading
                    const count = ___grecaptcha_cfg.count || 0;
                    return false;
                }
            } catch (e) {}
            return false;
        """))

    # Try each strategy; verify by checking if URL changed within 1.5s after click
    # HARD CAP: stop trying new strategies once RELOAD_TIMEOUT_S elapsed
    for name, fn in click_strategies():
        if time.time() - func_start > RELOAD_TIMEOUT_S:
            log(f"reload_challenge: hit total timeout ({RELOAD_TIMEOUT_S}s), bailing", "WARN")
            break
        try:
            result = fn()
            if not result:
                log(f"reload_challenge: strategy '{name}' returned False (button not found)", "WARN")
                continue
            log(f"reload_challenge: clicked via strategy '{name}', verifying...")
            
            # Verify by polling for URL change (max 2 seconds per strategy)
            changed = False
            for i in range(8):
                time.sleep(0.25)
                if time.time() - func_start > RELOAD_TIMEOUT_S:
                    log(f"reload_challenge: hit total timeout during verify, bailing", "WARN")
                    break
                new_url = get_audio_url(page)
                # URL changed = reload worked
                # OR: URL became None = challenge mode changed (image mode), which also means reload "happened"
                #     — but we want a NEW audio URL, so None is bad
                if new_url and new_url != old_url:
                    changed = True
                    log(f"reload_challenge: ✓ strategy '{name}' worked, URL changed after {(i+1)*0.25:.2f}s")
                    break
                if not new_url and old_url:
                    # URL disappeared — challenge probably switched to image mode
                    log(f"reload_challenge: strategy '{name}' caused URL to disappear (challenge mode changed?)", "WARN")
                    # Try to switch back to audio
                    time.sleep(1)
                    if switch_to_audio(page):
                        time.sleep(2)
                        newer_url = get_audio_url(page)
                        if newer_url and newer_url != old_url:
                            log(f"reload_challenge: ✓ strategy '{name}' + switch_to_audio worked")
                            changed = True
                            break
                    break  # break out of inner poll loop
            
            if changed:
                # Wait a bit more for the new audio to fully load
                time.sleep(random.uniform(0.5, 1.0))
                return True
            else:
                log(f"reload_challenge: ✗ strategy '{name}' did not change URL, trying next...", "WARN")
        except Exception as e:
            log(f"reload_challenge: strategy '{name}' error: {e}", "WARN")
            continue

    log("reload_challenge: ALL strategies failed to change audio URL", "WARN")
    return False


def _try_dp_click(bframe, selector):
    """Try DrissionPage native click with visibility check. Returns True if click succeeded."""
    try:
        btn = bframe.ele(selector, timeout=1)
        if not btn:
            return False
        # Check visibility
        try:
            if hasattr(btn, 'states') and not btn.states.is_displayed:
                return False
        except Exception:
            pass
        try:
            btn.click()
            return True
        except Exception:
            try:
                btn.click(by_js=True)
                return True
            except Exception:
                return False
    except Exception:
        return False


def fill_and_verify(page, text):
    """Fill audio response and click Verify, then wait for Google to process the response.
    
    After clicking Verify, Google disables ALL buttons in the bframe for 1-3 seconds
    while it processes the response server-side. If we try to reload immediately after,
    the reload button click is silently ignored (button is disabled).
    
    This function waits up to 10s for the verify button to become enabled again
    (or for the challenge to be solved, or for the audio URL to be cleared).
    """
    bframe = find_recaptcha_frame(page, "bframe")
    if not bframe:
        return False
    try:
        input_box = bframe.ele('#audio-response', timeout=2)
        if not input_box:
            return False
        input_box.click()
        input_box.clear()
        input_box.input(text)
    except Exception:
        return False
    time.sleep(random.uniform(0.5, 1.5))
    
    # Click Verify (use JS click as fallback if native click fails)
    try:
        verify_btn = bframe.ele('#recaptcha-verify-button', timeout=2)
        if verify_btn:
            try:
                verify_btn.click()
            except Exception:
                try:
                    verify_btn.click(by_js=True)
                except Exception:
                    bframe.run_js("document.querySelector('#recaptcha-verify-button')?.click();")
    except Exception:
        pass
    
    # CRITICAL: Wait for Google to process the verify response
    # After clicking Verify, all bframe buttons become disabled for 1-3s
    # while Google checks our answer server-side. We must wait for:
    # 1. reCAPTCHA solved → return immediately (best case)
    # 2. Verify button re-enabled → Google rejected our answer, can proceed
    # 3. Verify button text changes to 'Skip' → indicates too many wrong attempts
    log("fill_and_verify: waiting for Google to process verify response...")
    start = time.time()
    while time.time() - start < 10:
        # Best case: solved
        if is_recaptcha_solved(page):
            log(f"fill_and_verify: ✓ reCAPTCHA solved after {time.time()-start:.1f}s")
            return True
        # Check verify button state
        try:
            state = bframe.run_js("""
                const btn = document.querySelector('#recaptcha-verify-button');
                if (!btn) return 'gone';
                if (btn.disabled) return 'disabled';
                return 'enabled';
            """)
            if state == 'enabled':
                # Button re-enabled = Google processed our answer (and we were wrong)
                elapsed = time.time() - start
                log(f"fill_and_verify: verify button re-enabled after {elapsed:.1f}s (answer rejected)")
                # Give DOM a moment to settle
                time.sleep(0.5)
                return False  # answer was wrong, caller should reload
            # else still disabled — keep waiting
        except Exception:
            pass
        time.sleep(0.25)
    
    # Timeout: we don't know what happened, just return and let caller handle
    log("fill_and_verify: timed out waiting for verify button state (10s)", "WARN")
    return False


def download_audio(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.google.com/",
    }
    urls = [url]
    if "recaptcha.net" in url:
        urls.append(url.replace("recaptcha.net", "www.google.com"))
    elif "google.com" in url:
        urls.append(url.replace("www.google.com", "recaptcha.net"))
    for audio_url in urls:
        try:
            r = requests.get(audio_url, headers=headers, timeout=30)
            r.raise_for_status()
            if len(r.content) < 1000:
                continue
            path = tempfile.mktemp(suffix=".mp3")
            with open(path, "wb") as f:
                f.write(r.content)
            return path
        except:
            pass
    return None


def recognize_audio(mp3_path):
    """Recognize audio captcha using multiple strategies for better accuracy."""
    if sr is None:
        return None
    wav_path = mp3_path.replace(".mp3", ".wav")
    try:
        audio_seg = AudioSegment.from_mp3(mp3_path)
        # Diagnostic: log audio info
        duration_s = len(audio_seg) / 1000.0
        # Use dBFS to check if audio is essentially silent
        dbfs = audio_seg.dBFS
        log(f"recognize_audio: audio duration={duration_s:.2f}s, dBFS={dbfs:.1f}, channels={audio_seg.channels}, frame_rate={audio_seg.frame_rate}")
        # Strip silence from the beginning/end and boost low audio
        audio_seg = audio_seg.strip_silence(silence_thresh=-40, silence_len=100)
        # Normalize to -10 dBFS so Google API can hear it well
        if audio_seg.dBFS < -20:
            log(f"recognize_audio: audio is quiet ({audio_seg.dBFS:.1f} dBFS), boosting")
            target_dbfs = -10
            gain = target_dbfs - audio_seg.dBFS
            audio_seg = audio_seg.apply_gain(gain)
        audio_seg.export(wav_path, format="wav")
    except Exception as e:
        log(f"recognize_audio: MP3->WAV conversion failed: {e}", "ERROR")
        return None

    results = []
    try:
        recognizer = sr.Recognizer()
        # Try multiple strategies to improve recognition accuracy
        strategies = [
            {"name": "dynamic-0.5s", "dynamic_energy": True, "adjust_duration": 0.5, "show_all": False},
            {"name": "fixed-300", "dynamic_energy": False, "adjust_duration": 1.0, "show_all": False},
            {"name": "show-all-best", "dynamic_energy": True, "adjust_duration": 0.3, "show_all": True},
            {"name": "dynamic-0.1s", "dynamic_energy": True, "adjust_duration": 0.1, "show_all": False},
        ]
        for strat in strategies:
            try:
                with sr.AudioFile(wav_path) as src:
                    if strat["dynamic_energy"]:
                        recognizer.dynamic_energy_threshold = True
                        recognizer.adjust_for_ambient_noise(src, duration=strat["adjust_duration"])
                    else:
                        recognizer.dynamic_energy_threshold = False
                        recognizer.energy_threshold = 300  # fixed threshold for quiet audio
                    audio_data = recognizer.record(src)
                    if strat["show_all"]:
                        alternatives = recognizer.recognize_google(audio_data, show_all=True)
                        if alternatives:
                            alts = alternatives.get('alternative', [])
                            for alt_i, alt in enumerate(alts[:3]):
                                log(f"  alt[{alt_i}]: '{alt.get('transcript', '')}' (conf={alt.get('confidence', 0):.2f})")
                            # Pick the alternative with highest confidence
                            best = None
                            best_conf = 0
                            for alt in alts:
                                conf = alt.get('confidence', 0)
                                if conf >= best_conf and alt.get('transcript'):
                                    best = alt['transcript']
                                    best_conf = conf
                            if best:
                                results.append((best, best_conf))
                                continue
                    else:
                        text = recognizer.recognize_google(audio_data)
                        if text:
                            results.append((text, 0.5))
                            log(f"  strategy '{strat['name']}' result: '{text}'")
                            continue
            except sr.UnknownValueError:
                log(f"  strategy '{strat['name']}' UnknownValueError", "WARN")
                continue
            except sr.RequestError as e:
                log(f"recognize_audio: Google API error: {e}", "WARN")
                continue
            except Exception as e:
                log(f"recognize_audio: strategy '{strat['name']}' error: {e}", "WARN")
                continue
    finally:
        try:
            os.remove(wav_path)
        except Exception:
            pass

    if not results:
        return None
    # Sort by confidence (descending) and return the best transcript
    results.sort(key=lambda x: x[1], reverse=True)
    # Normalize: lowercase, strip, collapse whitespace
    best_text = ' '.join(results[0][0].lower().split())
    log(f"recognize_audio: best of {len(results)} strategies: '{best_text}' (conf={results[0][1]:.2f})")
    return best_text


def solve_recaptcha(page):
    """Solve reCAPTCHA — supports both visible (checkbox) and invisible modes."""
    start = time.time()
    while time.time() - start < 15:
        if find_recaptcha_frame(page, "anchor"):
            break
        time.sleep(1)
    else:
        raise RuntimeError("reCAPTCHA load timeout")

    # Detect mode early
    invisible = is_invisible_recaptcha(page)
    log(f"reCAPTCHA mode: {'INVISIBLE' if invisible else 'VISIBLE'}")

    # For invisible mode, the challenge bframe appears after the form submit triggers grecaptcha.execute().
    # Give Google some time to render the challenge before we start probing.
    if invisible:
        log("Waiting up to 15s for invisible reCAPTCHA challenge bframe to appear...")
        bframe_start = time.time()
        while time.time() - bframe_start < 15:
            if is_recaptcha_solved(page):
                log("reCAPTCHA solved silently (no challenge)")
                return True
            if is_blocked(page):
                raise Exception("IP blocked by Google reCAPTCHA")
            # If audio mode is already active, the challenge is ready
            if is_audio_mode(page):
                log("Audio challenge already visible")
                break
            # If bframe exists and shows any visible content, that's also good enough
            bframe = find_recaptcha_frame(page, "bframe")
            if bframe:
                try:
                    has_content = bframe.run_js("""
                        const el = document.querySelector('.rc-audiochallenge, .rc-imageselect-instructions, #audio-response');
                        return !!(el && el.offsetParent !== null);
                    """)
                    if has_content:
                        log("bframe challenge visible")
                        break
                except Exception:
                    pass
            # Try invoking execute() periodically (in case the form's submit handler didn't fire)
            try_invoke_grecaptcha_execute(page)
            time.sleep(2)
        else:
            log("bframe did not appear within 15s; proceeding to retry loop", "WARN")

    dl_fails = 0
    last_text = None
    same_text_streak = 0
    solve_start = time.time()
    for i in range(MAX_CAPTCHA_ATTEMPTS):
        # HARD CAP: bail if total solve time exceeded
        if time.time() - solve_start > SOLVE_TOTAL_TIMEOUT_S:
            log(f"solve_recaptcha: total timeout ({SOLVE_TOTAL_TIMEOUT_S}s) exceeded, giving up", "ERROR")
            break
        attempt_start = time.time()
        log(f"--- Attempt {i+1}/{MAX_CAPTCHA_ATTEMPTS} ---")
        if is_recaptcha_solved(page):
            return True
        if is_blocked(page):
            raise Exception("IP blocked by Google reCAPTCHA")

        if i == 0:
            if invisible:
                # Invisible: re-trigger execute() in case the challenge hasn't fired yet
                try_invoke_grecaptcha_execute(page)
                time.sleep(2)
            else:
                click_recaptcha_checkbox(page)
                time.sleep(2)
            if is_recaptcha_solved(page):
                return True

        if not is_audio_mode(page):
            if not switch_to_audio(page):
                time.sleep(3)
                if not switch_to_audio(page):
                    if invisible:
                        # Invisible: try execute again, then wait longer for challenge
                        try_invoke_grecaptcha_execute(page)
                        time.sleep(3)
                    else:
                        click_recaptcha_checkbox(page)
                        time.sleep(3)
                    continue
            time.sleep(random.uniform(2, 4))

        if is_blocked(page):
            raise Exception("Audio mode detected IP blocked")

        audio_url = get_audio_url(page)
        if not audio_url:
            log("No audio URL found, reloading challenge")
            reload_challenge(page)
            continue
        # Log full audio URL for accurate comparison (the p= parameter changes per challenge)
        # Truncate only for very long URLs
        if len(audio_url) > 120:
            log(f"Audio URL: {audio_url[:80]}...{audio_url[-40:]}")
        else:
            log(f"Audio URL: {audio_url}")
        # Log a short hash for quick diff
        log(f"Audio URL hash: {hash(audio_url) % 100000:05d}")

        mp3 = download_audio(audio_url)
        if not mp3:
            dl_fails += 1
            if dl_fails >= 3:
                raise RuntimeError("Audio download failed 3 times")
            log("Audio download failed, reloading challenge", "WARN")
            reload_challenge(page)
            time.sleep(random.uniform(3, 6))
            continue
        dl_fails = 0

        text = recognize_audio(mp3)
        try:
            os.remove(mp3)
        except Exception:
            pass
        if not text:
            log("Recognition returned empty, reloading challenge", "WARN")
            reload_challenge(page)
            time.sleep(3)
            continue

        log(f"Recognition result: [{text}]")

        # HARD CAP per-attempt: if a single attempt takes > ATTEMPT_TIMEOUT_S, skip to next
        if time.time() - attempt_start > ATTEMPT_TIMEOUT_S:
            log(f"Attempt {i+1}: hit per-attempt timeout ({ATTEMPT_TIMEOUT_S}s), skipping to next", "WARN")
            # Force a reload to try to recover
            try:
                reload_challenge(page)
            except Exception:
                pass
            continue

        # Detect if we're stuck on the same audio (Google returned identical result)
        if text == last_text:
            same_text_streak += 1
            log(f"Same recognition result as last attempt (streak={same_text_streak})", "WARN")
            if same_text_streak >= 2:
                # Force hard reset: trigger full execute() to start a completely new challenge cycle
                log("Hard reset: invoking grecaptcha.execute() to force fresh challenge", "WARN")
                try_invoke_grecaptcha_execute(page)
                time.sleep(3)
                # After hard reset, we may need to re-enter audio mode
                if not is_audio_mode(page):
                    switch_to_audio(page)
                last_text = None
                same_text_streak = 0
                continue
        else:
            same_text_streak = 0
        last_text = text

        # fill_and_verify now waits for Google to process the answer (1-3s typically)
        # and returns True if solved, False if answer was rejected
        verified = fill_and_verify(page, text)
        if verified:
            return True
        # Brief delay before reload (randomize to avoid pattern detection)
        time.sleep(random.uniform(1.5, 2.5))

        # Reload and verify it actually changed the audio URL — try up to 3 times
        reload_ok = False
        for retry_idx in range(3):
            if reload_challenge(page):
                reload_ok = True
                break
            log(f"Reload retry {retry_idx+1}/3 failed, retrying...", "WARN")
            time.sleep(random.uniform(2, 4))
        
        if not reload_ok:
            log("All 3 reload attempts failed to change audio URL", "WARN")
            # Last resort: trigger execute() to start fresh
            try_invoke_grecaptcha_execute(page)
            time.sleep(3)
            if not is_audio_mode(page):
                switch_to_audio(page)
            time.sleep(random.uniform(1, 2))
        else:
            time.sleep(random.uniform(1.5, 3))

    raise RuntimeError("Max captcha attempts reached")


def capture_screenshot(page, filename):
    try:
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        page.get_screenshot(path=filename)
        return filename
    except Exception as e:
        log(f"Screenshot failed: {e}", "WARN")
        return None


def main():
    account = os.environ.get("FGH_ACCOUNT", "").strip()
    if not account or "," not in account:
        log("ERROR: FGH_ACCOUNT not set or invalid format", "ERROR")
        sys.exit(1)

    email, password = account.split(",", 1)
    email = email.strip()
    password = password.strip()
    log(f"Account: {email}")
    log(f"Password length: {len(password)}")

    tg_token = os.environ.get("TG_BOT_TOKEN", "").strip()
    tg_chat_id = os.environ.get("TG_CHAT_ID", "").strip()

    vdisplay = Xvfb(width=1920, height=1080, colordepth=24)
    vdisplay.start()

    success = False
    error_msg = ""

    try:
        co = ChromiumOptions()
        co.set_browser_path('/usr/bin/google-chrome')
        co.set_argument('--no-sandbox')
        co.set_argument('--disable-dev-shm-usage')
        co.set_argument('--disable-gpu')
        co.set_argument('--disable-setuid-sandbox')
        co.set_argument('--disable-software-rasterizer')
        co.set_argument('--disable-extensions')
        co.set_argument('--no-first-run')
        co.set_argument('--no-default-browser-check')
        co.set_argument('--window-size=1920,1080')
        co.set_argument('--log-level=3')
        co.headless(False)

        page = ChromiumPage(co)

        page.run_js("""
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                if (parameter === 37445) return 'Intel Inc.';
                if (parameter === 37446) return 'Intel(R) UHD Graphics 630';
                return getParameter.apply(this, [parameter]);
            };
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
        """)

        log("Opening login page...")
        page.get(f"{PANEL_URL}/auth/login")
        time.sleep(15)
        page.get_screenshot(f"{SCREENSHOT_DIR}/01_login_page.png")
        
        debug_page(page)
        fill_login_form(page, email, password)

        log("Clicking login button...")
        page.run_js('document.querySelector("button[type=submit]")?.click()')

        time.sleep(10)
        current_url = page.url
        log(f"After login URL: {current_url}")
        page.get_screenshot(f"{SCREENSHOT_DIR}/02_after_login.png")

        if "login" in current_url.lower():
            if find_recaptcha_frame(page, "anchor"):
                log("reCAPTCHA detected, starting solve...")
                try:
                    solve_recaptcha(page)
                    log("reCAPTCHA solved")
                    time.sleep(3)
                    page.run_js('document.querySelector("button[type=submit]")?.click()')
                    time.sleep(10)
                    current_url = page.url
                except Exception as e:
                    error_msg = f"reCAPTCHA solve failed: {e}"
                    log(f"ERROR: {error_msg}", "ERROR")
                    page.get_screenshot(f"{SCREENSHOT_DIR}/error_captcha.png")
                    raise

            if "login" in page.url.lower():
                error_msg = "Login failed: check username/password"
                page.get_screenshot(f"{SCREENSHOT_DIR}/error_login_failed.png")
                raise Exception(error_msg)

        log("Login successful!")

        log("Opening dashboard...")
        page.get(f"{PANEL_URL}/")
        time.sleep(5)
        page.get_screenshot(f"{SCREENSHOT_DIR}/03_dashboard.png")

        server_links = page.eles('a[href*="/server/renew"]')
        if not server_links:
            server_links = page.eles('a[href*="/server/"]')

        if server_links:
            log(f"Found {len(server_links)} servers, starting renew...")
            for i, link in enumerate(server_links):
                try:
                    href = link.attr('href')
                    url = href if href.startswith('http') else f"{PANEL_URL}{href}"
                    log(f"Server {i+1}: {mask_url(url)}")
                    page.get(url)
                    time.sleep(5)
                    page.get_screenshot(f"{SCREENSHOT_DIR}/server_{i+1}_page.png")

                    old_expire = get_expire_time(page)
                    log(f"Server {i+1} expires: {old_expire}")

                    renew_btn = None
                    try:
                        renew_btn = page.ele('xpath://button[contains(text(), "Renew server")]', timeout=3)
                    except:
                        pass
                    if not renew_btn:
                        try:
                            renew_btn = page.ele('xpath://button[contains(text(), "Renew")]', timeout=3)
                        except:
                            pass
                    if not renew_btn:
                        buttons = page.eles('button')
                        for btn in buttons:
                            if "Renew" in (btn.text or ""):
                                renew_btn = btn
                                break

                    if renew_btn:
                        try:
                            renew_btn.click()
                        except:
                            renew_btn.click(by_js=True)
                        log(f"Server {i+1} clicked renew")
                    else:
                        log(f"Server {i+1} no renew button found", "WARN")
                        continue

                    time.sleep(5)

                    if find_recaptcha_frame(page, "anchor"):
                        log(f"Server {i+1} reCAPTCHA detected")
                        try:
                            solve_recaptcha(page)
                            log(f"Server {i+1} reCAPTCHA solved")
                            time.sleep(3)
                        except Exception as e:
                            log(f"Server {i+1} reCAPTCHA failed: {e}", "ERROR")
                            continue

                    confirm_btn = None
                    try:
                        confirm_btn = page.ele('xpath://button[normalize-space(text())="Renew"]', timeout=5)
                    except:
                        pass
                    if confirm_btn:
                        try:
                            confirm_btn.click()
                        except:
                            confirm_btn.click(by_js=True)
                        log(f"Server {i+1} confirmed renew")
                    else:
                        log(f"Server {i+1} no confirm button found", "WARN")
                        continue

                    time.sleep(10)
                    new_expire = get_expire_time(page)
                    page.get_screenshot(f"{SCREENSHOT_DIR}/server_{i+1}_result.png")

                    if new_expire != old_expire and new_expire != "Unknown":
                        log(f"Server {i+1} renewed: {old_expire} -> {new_expire}")
                        success = True
                    else:
                        log(f"Server {i+1} may already renewed", "WARN")
                        success = True

                except Exception as e:
                    log(f"Server {i+1} renew failed: {e}", "ERROR")
                    page.get_screenshot(f"{SCREENSHOT_DIR}/server_{i+1}_error.png")
        else:
            log("No server renewal links found, trying buttons...", "WARN")
            renew_buttons = page.eles('button')
            found_renew = False
            for btn in renew_buttons:
                if "Renew" in (btn.text or ""):
                    try:
                        btn.click()
                        found_renew = True
                        log("Clicked Renew button")
                        break
                    except:
                        pass
            if found_renew:
                time.sleep(10)
                page.get_screenshot(f"{SCREENSHOT_DIR}/04_after_click_renew.png")
                success = True

    except Exception as e:
        error_msg = str(e)
        log(f"ERROR: {error_msg}", "ERROR")
        try:
            page.get_screenshot(f"{SCREENSHOT_DIR}/error_exception.png")
        except:
            pass

    finally:
        try:
            page.quit()
        except:
            pass
        vdisplay.stop()

    # Telegram notification
    if tg_token and tg_chat_id:
        if success:
            msg = "SUCCESS: FreeGameHost renewed!\nCheck screenshots for details."
        else:
            msg = f"FAILED: FreeGameHost renewal failed\nError: {error_msg[:200]}"
        send_tg(tg_token, tg_chat_id, msg)

    if success:
        log("Done!")
        sys.exit(0)
    else:
        log(f"Failed: {error_msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()
