#!/usr/bin/env python3
"""把 .md 檔案轉成 HTML，再用 macOS cupsfilter 轉成 PDF。"""
import sys
import os
import subprocess
import tempfile
import markdown

CSS = """
@page { size: A4; margin: 1.8cm 1.5cm; }
body {
  font-family: -apple-system, "Helvetica Neue", "PingFang TC", "Heiti TC", sans-serif;
  font-size: 10.5pt;
  line-height: 1.55;
  color: #1a1a1a;
}
h1 { font-size: 22pt; border-bottom: 2px solid #333; padding-bottom: 0.3em; margin-top: 1.2em; }
h2 { font-size: 16pt; border-bottom: 1px solid #999; padding-bottom: 0.2em; margin-top: 1.5em; }
h3 { font-size: 13pt; margin-top: 1.3em; }
h4 { font-size: 11pt; margin-top: 1em; }
code { font-family: Menlo, Consolas, monospace; font-size: 9pt; background: #f4f4f4; padding: 1px 4px; border-radius: 3px; }
pre { background: #f4f4f4; padding: 0.8em; border-radius: 4px; overflow-x: auto; font-size: 8.5pt; line-height: 1.35; }
pre code { background: transparent; padding: 0; }
table { border-collapse: collapse; width: 100%; margin: 0.8em 0; font-size: 9.5pt; }
th, td { border: 1px solid #bbb; padding: 5px 8px; text-align: left; vertical-align: top; }
th { background: #ececec; font-weight: bold; }
blockquote { border-left: 3px solid #888; padding-left: 0.8em; color: #555; margin: 0.6em 0; }
hr { border: none; border-top: 1px solid #ccc; margin: 1.5em 0; }
ul, ol { padding-left: 1.6em; }
a { color: #0c5; text-decoration: none; }
"""

def md_to_pdf(md_path: str, pdf_path: str):
    with open(md_path, 'r', encoding='utf-8') as f:
        md_text = f.read()

    html_body = markdown.markdown(
        md_text,
        extensions=['tables', 'fenced_code', 'toc', 'attr_list', 'sane_lists']
    )
    html_full = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<title>{os.path.basename(md_path)}</title>
<style>{CSS}</style>
</head>
<body>
{html_body}
</body>
</html>
"""

    with tempfile.NamedTemporaryFile(suffix='.html', delete=False, mode='w', encoding='utf-8') as tmp:
        tmp.write(html_full)
        tmp_path = tmp.name

    chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
    abs_pdf = os.path.abspath(pdf_path)
    try:
        result = subprocess.run(
            [
                chrome,
                '--headless=new',
                '--disable-gpu',
                '--no-pdf-header-footer',
                f'--print-to-pdf={abs_pdf}',
                f'file://{tmp_path}',
            ],
            capture_output=True, timeout=120
        )
        if not os.path.exists(abs_pdf):
            print("Chrome headless 失敗：", file=sys.stderr)
            print(result.stderr.decode('utf-8', errors='replace'), file=sys.stderr)
            sys.exit(1)
        print(f"✓ {pdf_path}（{os.path.getsize(abs_pdf)/1024:.1f} KB）")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("用法：md_to_pdf.py <input.md> <output.pdf>")
        sys.exit(1)
    md_to_pdf(sys.argv[1], sys.argv[2])
