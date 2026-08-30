        # Debug page content
        debug_page(page)
        
        # Get raw HTML to inspect
        try:
            html_content = page.html
            log(f"HTML length: {len(html_content)}")
            # Save for inspection
            with open("page_dump.html", "w", encoding="utf-8") as f:
                f.write(html_content)
            log("Saved page_dump.html")
        except:
            pass