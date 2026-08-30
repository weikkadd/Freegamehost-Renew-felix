import re

# Read the file
with open('renew.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find and add debug code after "log(f"Current URL: {current_url}")"
old_text = '        log(f"Current URL: {current_url}")'
new_text = '''        log(f"Current URL: {current_url}")
        
        # Debug: print all inputs
        try:
            inputs = page.eles('input')
            log(f"Found {len(inputs)} inputs:")
            for i, inp in enumerate(inputs):
                inp_type = inp.attr('type') or 'unknown'
                inp_name = inp.attr('name') or ''
                inp_id = inp.attr('id') or ''
                log(f"  [{i}] type={inp_type}, name={inp_name}, id={inp_id}")
        except Exception as e:
            log(f"Debug inputs failed: {e}", "WARN")'''

if old_text in content:
    content = content.replace(old_text, new_text)
    with open('renew.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("Debug code added successfully")
else:
    print("Pattern not found, trying alternative...")
    # Try to find the line with 'Current URL'
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if 'Current URL' in line and 'log' in line:
            print(f"Found at line {i}: {line}")
            # Insert after this line
            indent = len(line) - len(line.lstrip())
            debug_code = '''
        # Debug: print all inputs
        try:
            inputs = page.eles('input')
            log(f"Found {len(inputs)} inputs:")
            for i, inp in enumerate(inputs):
                inp_type = inp.attr('type') or 'unknown'
                inp_name = inp.attr('name') or ''
                inp_id = inp.attr('id') or ''
                log(f"  [{i}] type={inp_type}, name={inp_name}, id={inp_id}")
        except Exception as e:
            log(f"Debug inputs failed: {e}", "WARN")'''
            lines.insert(i + 1, debug_code)
            content = '\n'.join(lines)
            with open('renew.py', 'w', encoding='utf-8') as f:
                f.write(content)
            print("Debug code added via line insertion")
            break
