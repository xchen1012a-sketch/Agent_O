"""
Markdown -> HTML -> PDF (via Edge headless)
Self-contained, handles Chinese rendering with proper styling.
"""
import re
import os
import subprocess
import sys
from pathlib import Path

def md_to_html_body(md_text: str) -> str:
    """Convert markdown to HTML body. Simple but covers our use case."""
    lines = md_text.split('\n')
    html_parts = []
    i = 0
    in_table = False
    table_rows = []
    in_list = False

    def close_list():
        nonlocal in_list
        if in_list:
            html_parts.append('</ul>')
            in_list = False

    def flush_table():
        nonlocal in_table, table_rows
        if in_table and table_rows:
            html_parts.append('<table>')
            for idx, row in enumerate(table_rows):
                tag = 'th' if idx == 0 else 'td'
                cells = ''.join(f'<{tag}>{inline(c.strip())}</{tag}>' for c in row)
                html_parts.append(f'<tr>{cells}</tr>')
            html_parts.append('</table>')
        in_table = False
        table_rows = []

    def inline(text: str) -> str:
        # bold
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        # inline code
        text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
        return text

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # horizontal rule
        if stripped == '---':
            close_list()
            flush_table()
            html_parts.append('<hr/>')
            i += 1
            continue

        # headings
        m = re.match(r'^(#{1,6})\s+(.+)$', stripped)
        if m:
            close_list()
            flush_table()
            level = len(m.group(1))
            html_parts.append(f'<h{level}>{inline(m.group(2))}</h{level}>')
            i += 1
            continue

        # blockquote
        if stripped.startswith('>'):
            close_list()
            flush_table()
            content = stripped.lstrip('>').strip()
            html_parts.append(f'<blockquote>{inline(content)}</blockquote>')
            i += 1
            continue

        # table detection: line with | and next line is separator
        if '|' in stripped and stripped.count('|') >= 2:
            # peek for separator line like | --- | --- |
            if not in_table:
                if i + 1 < len(lines) and re.match(r'^\s*\|?[\s\-:|]+\|[\s\-:|]+\s*$', lines[i+1]):
                    close_list()
                    in_table = True
                    cells = [c for c in stripped.strip('|').split('|')]
                    table_rows.append(cells)
                    i += 2  # skip separator
                    continue
            else:
                cells = [c for c in stripped.strip('|').split('|')]
                table_rows.append(cells)
                i += 1
                continue
        else:
            if in_table:
                flush_table()

        # list item
        if re.match(r'^\s*-\s+', line):
            flush_table()
            if not in_list:
                html_parts.append('<ul>')
                in_list = True
            content = re.sub(r'^\s*-\s+', '', line)
            # checkbox
            cb = re.match(r'^\[([ xX])\]\s+(.+)$', content)
            if cb:
                checked = 'checked' if cb.group(1).lower() == 'x' else ''
                html_parts.append(f'<li class="task"><input type="checkbox" disabled {checked}/> {inline(cb.group(2))}</li>')
            else:
                html_parts.append(f'<li>{inline(content)}</li>')
            i += 1
            continue
        else:
            close_list()

        # empty line
        if not stripped:
            i += 1
            continue

        # paragraph
        html_parts.append(f'<p>{inline(stripped)}</p>')
        i += 1

    close_list()
    flush_table()
    return '\n'.join(html_parts)


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
@page {{
  size: A4;
  margin: 18mm 16mm;
}}
* {{ box-sizing: border-box; }}
html, body {{
  font-family: "Microsoft YaHei", "PingFang SC", "Hiragino Sans GB", "Source Han Sans SC", sans-serif;
  font-size: 11pt;
  line-height: 1.7;
  color: #1a1a1a;
  background: #fff;
  margin: 0;
  padding: 0;
}}
h1 {{
  font-size: 22pt;
  text-align: center;
  margin: 0 0 8pt 0;
  padding-bottom: 8pt;
  border-bottom: 3px solid #1a1a1a;
  page-break-after: avoid;
}}
h2 {{
  font-size: 14pt;
  margin: 18pt 0 8pt 0;
  padding: 6pt 10pt;
  background: #f5f5f5;
  border-left: 4px solid #1a1a1a;
  page-break-after: avoid;
  break-inside: avoid;
}}
h3 {{
  font-size: 12pt;
  margin: 14pt 0 6pt 0;
  color: #333;
  page-break-after: avoid;
}}
p {{
  margin: 6pt 0;
  text-align: justify;
}}
strong {{
  color: #c0392b;
  font-weight: 700;
}}
blockquote {{
  margin: 8pt 0;
  padding: 8pt 12pt;
  background: #fffbea;
  border-left: 3px solid #f1c40f;
  color: #5d4e00;
  font-size: 10pt;
  font-style: normal;
}}
hr {{
  border: none;
  border-top: 1px dashed #bbb;
  margin: 14pt 0;
}}
ul {{
  margin: 6pt 0;
  padding-left: 20pt;
}}
li {{
  margin: 3pt 0;
}}
li.task {{
  list-style: none;
  margin-left: -16pt;
}}
li.task input {{
  margin-right: 6pt;
}}
table {{
  width: 100%;
  border-collapse: collapse;
  margin: 8pt 0;
  font-size: 10pt;
  page-break-inside: avoid;
}}
th, td {{
  border: 1px solid #ccc;
  padding: 6pt 8pt;
  text-align: left;
  vertical-align: top;
}}
th {{
  background: #f0f0f0;
  font-weight: 700;
}}
code {{
  background: #f4f4f4;
  padding: 1pt 4pt;
  border-radius: 3px;
  font-family: Consolas, monospace;
  font-size: 9.5pt;
}}
/* Avoid orphan headings */
h2, h3 {{
  break-inside: avoid;
}}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def find_edge():
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def main():
    md_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"C:\Users\ahua\Desktop\Agent_O\答辩10问-口播话术.md")
    out_pdf = md_path.with_suffix('.pdf')
    out_html = md_path.with_suffix('.html')

    md_text = md_path.read_text(encoding='utf-8')
    # title from first h1
    title_match = re.search(r'^#\s+(.+)$', md_text, re.MULTILINE)
    title = title_match.group(1) if title_match else md_path.stem

    body = md_to_html_body(md_text)
    html = HTML_TEMPLATE.format(title=title, body=body)
    out_html.write_text(html, encoding='utf-8')
    print(f"HTML written: {out_html}")

    edge = find_edge()
    if not edge:
        print("ERROR: Edge not found")
        sys.exit(1)

    # use file:// URL with absolute path
    file_url = out_html.resolve().as_uri()
    cmd = [
        edge,
        "--headless=new",
        "--disable-gpu",
        f"--print-to-pdf={out_pdf}",
        "--no-margins",
        "--print-to-pdf-no-header",
        file_url,
    ]
    print("Running Edge headless...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        print("STDERR:", result.stderr)
        sys.exit(1)
    print(f"PDF written: {out_pdf}")


if __name__ == "__main__":
    main()
