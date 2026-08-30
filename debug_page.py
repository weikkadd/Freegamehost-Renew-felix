def debug_page(page):
    """Debug: print all page elements"""
    log("=" * 50)
    log("=== PAGE DEBUG INFO ===")
    log(f"URL: {page.url}")
    log(f"Title: {page.title}")
    
    # Get page text
    try:
        text = page.get_text()
        log(f"Page text length: {len(text)}")
        log(f"Page text preview: {text[:300]}")
    except:
        pass
    
    # Find all inputs
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
    
    # Find all buttons
    try:
        buttons = page.eles('button')
        log(f"Found {len(buttons)} buttons:")
        for i, btn in enumerate(buttons):
            log(f"  [{i}] text={repr(btn.text[:30])}, type={btn.attr('type')}")
    except Exception as e:
        log(f"Failed to get buttons: {e}", "WARN")
    
    # Check for reCAPTCHA frames
    try:
        frames = page.get_frames()
        log(f"Found {len(frames)} frames:")
        for i, f in enumerate(frames):
            log(f"  [{i}] url={f.url[:80] if f.url else 'None'}")
    except Exception as e:
        log(f"Failed to get frames: {e}", "WARN")
    
    log("=" * 50)
