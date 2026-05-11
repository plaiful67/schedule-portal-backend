#!/usr/bin/env python3
"""Split emoji characters into their own runs with Apple Color Emoji font.

Word renders emoji glyphs correctly only when the run containing them has a
font that provides color emoji glyphs. Arial — the base font used throughout
these handouts — does not, so 📅 and 🎥 come out as tofu boxes.

Sebastian views these handouts on macOS (Word for Mac + Pages), so we target
Apple Color Emoji specifically: it's installed by default on every Mac and
contains all the emoji glyphs used in these templates. If the file is later
opened on Windows, Word will fall back to Segoe UI Emoji automatically for
any glyph the named font can't resolve. We don't try to specify both at once
because OOXML's <w:rFonts> only allows one font per slot.

Rather than change the font on existing runs (which would change how the
surrounding Spanish/English text looks), this script splits any run that
contains an emoji into multiple runs at the emoji boundary. Emoji runs get an
rFonts override pointing at Apple Color Emoji; the surrounding text keeps its
original Arial font.

This is idempotent — running it on an already-fixed file updates the font
string but does not re-split runs.
"""
import re
import sys
import zipfile
import shutil
import os
from pathlib import Path

# Emoji codepoints used in the templates. Keep this list narrow — we only
# split for characters we know render poorly in Arial. Dashes and variation
# selectors stay as-is with the surrounding text.
EMOJI_CHARS = {'📅', '🎥', '⚠', '✅'}
VARIATION_SELECTOR = '\ufe0f'  # keeps the preceding char as "emoji presentation"

EMOJI_RFONTS = (
    '<w:rFonts w:ascii="Apple Color Emoji" w:hAnsi="Apple Color Emoji" '
    'w:cs="Apple Color Emoji" w:eastAsia="Apple Color Emoji"/>'
)


def is_emoji_char(ch):
    return ch in EMOJI_CHARS or ch == VARIATION_SELECTOR


def split_text_to_segments(text):
    """Split a string into alternating (is_emoji, segment) chunks."""
    segments = []
    cur = ''
    cur_emoji = None
    for ch in text:
        e = is_emoji_char(ch)
        if cur_emoji is None:
            cur_emoji = e
            cur = ch
        elif e == cur_emoji:
            cur += ch
        else:
            segments.append((cur_emoji, cur))
            cur_emoji = e
            cur = ch
    if cur:
        segments.append((cur_emoji, cur))
    return segments


def rewrite_run(match):
    """Rewrite a <w:r>…</w:r> run if it contains emoji chars.

    We replace the run with a sequence of runs, one per segment, sharing the
    same <w:rPr> except that emoji segments get an EMOJI_RFONTS override
    prepended (it must come first inside <w:rPr> per OOXML schema).
    """
    run_xml = match.group(0)
    # Find the <w:t ...>…</w:t> inside the run
    t_match = re.search(r'(<w:t[^>]*>)([^<]*)(</w:t>)', run_xml, re.DOTALL)
    if not t_match:
        return run_xml
    t_open, text, t_close = t_match.groups()
    if not any(ch in text for ch in EMOJI_CHARS):
        return run_xml  # nothing to do — only trigger on real emoji

    # Extract <w:rPr>…</w:rPr> (optional) and everything before <w:t>
    rpr_match = re.search(r'<w:rPr>(.*?)</w:rPr>', run_xml, re.DOTALL)
    rpr_inner = rpr_match.group(1) if rpr_match else ''

    # If the existing rPr already starts with an rFonts element, strip it —
    # we'll re-add the base font for text segments and Segoe UI Emoji for
    # emoji segments. This keeps things idempotent.
    base_rfonts_match = re.search(r'<w:rFonts[^/]*/>', rpr_inner)
    base_rfonts = base_rfonts_match.group(0) if base_rfonts_match else ''
    rpr_rest = re.sub(r'<w:rFonts[^/]*/>', '', rpr_inner, count=1)

    segments = split_text_to_segments(text)
    out_runs = []
    for is_emoji, seg in segments:
        fonts = EMOJI_RFONTS if is_emoji else base_rfonts
        rpr = f'<w:rPr>{fonts}{rpr_rest}</w:rPr>' if (fonts or rpr_rest) else ''
        # preserve whitespace
        t_attr = ' xml:space="preserve"' if seg != seg.strip() or not seg else ''
        # escape XML-special chars
        seg_esc = seg.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        out_runs.append(f'<w:r>{rpr}<w:t xml:space="preserve">{seg_esc}</w:t></w:r>')
    return ''.join(out_runs)


def fix_docx(path):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    changed_runs = 0
    with zipfile.ZipFile(path, 'r') as zin:
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == 'word/document.xml':
                    xml = data.decode('utf-8')
                    # Match each <w:r>…</w:r> run, non-greedy
                    def repl(m):
                        nonlocal changed_runs
                        new = rewrite_run(m)
                        if new != m.group(0):
                            changed_runs += 1
                        return new
                    xml = re.sub(r'<w:r\b[^>]*>(?:(?!</w:r>).)*?</w:r>',
                                 repl, xml, flags=re.DOTALL)
                    data = xml.encode('utf-8')
                zout.writestr(item, data)
    shutil.move(str(tmp), str(path))
    print(f"  {path}: split {changed_runs} emoji-containing runs")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        # Default: fix all 4 DOCX templates
        targets = [
            'templates/standard.en.docx',
            'templates/standard.es.docx',
            'templates/infant.en.docx',
            'templates/infant.es.docx',
        ]
    else:
        targets = sys.argv[1:]
    for t in targets:
        fix_docx(t)
