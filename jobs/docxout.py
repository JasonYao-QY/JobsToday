# -*- coding: utf-8 -*-
"""Write a letter into a .docx, with nothing but the standard library.

Public: write, paragraphs_of, body_xml, read_paragraphs.

A .docx is a zip of XML parts. When the target already exists it is used as its own template: every part
except `word/document.xml` is copied through byte for byte, and inside that part only the paragraphs
between `<w:body>` and `<w:sectPr>` are replaced. That keeps his margins, fonts, styles and theme, which
is the whole reason to write into a document he already has rather than generate a fresh one. The second
write therefore templates off the first, which is stable: nothing outside the body is ever touched.

Only when there is no file at all does this fall back to a minimal document of its own.

The write is atomic. A letter is overwritten on purpose, but a half-written zip in place of his document
is not something to risk, so the new file is built beside the old one and moved over it in one step; any
failure leaves the old file exactly as it was.
"""

import os
import re
import shutil
import tempfile
import zipfile

__all__ = ["write", "paragraphs_of", "body_xml", "read_paragraphs"]


def paragraphs_of(text):
    """The letter as paragraphs. Blank lines separate; they do not become empty paragraphs of their own,
    because Word spaces paragraphs itself and his document has no blank ones."""
    out = [line.strip() for line in (text or "").replace("\r\n", "\n").split("\n")]
    out = [p for p in out if p]
    if not out:
        raise ValueError("there is no letter to write")
    return out


def _esc(s):
    s = "".join(c for c in s if c >= " " or c == "\t")
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def body_xml(paragraphs):
    return "".join("<w:p><w:r><w:t xml:space=\"preserve\">%s</w:t></w:r></w:p>" % _esc(p)
                   for p in paragraphs)


def _swap_body(document_xml, paragraphs):
    """Replace the paragraphs of an existing word/document.xml, keeping its sectPr and everything around
    it. The section properties carry page size and margins, so they are preserved rather than rebuilt."""
    m = re.search(r"(<w:body\b[^>]*>)(.*)(</w:body>)", document_xml, re.S)
    if not m:
        raise ValueError("not a Word document: no body in word/document.xml")
    head, inner, tail = m.group(1), m.group(2), m.group(3)
    sect = re.search(r"<w:sectPr\b.*?</w:sectPr>", inner, re.S)
    rebuilt = head + body_xml(paragraphs) + (sect.group(0) if sect else "") + tail
    return document_xml[:m.start()] + rebuilt + document_xml[m.end():]


_MINIMAL = {
    "[Content_Types].xml":
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.wordprocessingml.document.main+xml"/></Types>',
    "_rels/.rels":
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="word/document.xml"/></Relationships>',
    "word/_rels/document.xml.rels":
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>',
}

_MIN_DOC = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body>%s<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
            '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="720" '
            'w:footer="720" w:gutter="0"/></w:sectPr></w:body></w:document>')


def write(path, text):
    """Write `text` into the .docx at `path`, keeping that document's formatting. -> the paragraphs written.

    Overwrites: that is what it is for. Atomic, so a failure leaves the previous file untouched.
    """
    paragraphs = paragraphs_of(text)
    path = os.path.expanduser(path)
    folder = os.path.dirname(os.path.abspath(path)) or "."
    if not os.path.isdir(folder):
        raise ValueError("no folder %s" % folder)

    parts = None
    if os.path.exists(path):
        try:
            with zipfile.ZipFile(path) as z:
                if "word/document.xml" not in z.namelist():
                    raise ValueError("not a Word document: %s has no word/document.xml" % path)
                parts = [(i, z.read(i.filename)) for i in z.infolist()]
        except zipfile.BadZipFile:
            raise ValueError("not a Word document: %s is not a zip" % path)

    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".letter-", suffix=".docx")
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
            if parts is None:
                for name, xml in _MINIMAL.items():
                    out.writestr(name, xml)
                out.writestr("word/document.xml", _MIN_DOC % body_xml(paragraphs))
            else:
                for info, data in parts:
                    if info.filename == "word/document.xml":
                        data = _swap_body(data.decode("utf-8"), paragraphs).encode("utf-8")
                    out.writestr(info, data)
        shutil.copystat(path, tmp) if os.path.exists(path) else None
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return paragraphs


def read_paragraphs(path):
    """The text of a .docx, paragraph by paragraph. For checking what was written."""
    with zipfile.ZipFile(os.path.expanduser(path)) as z:
        doc = z.read("word/document.xml").decode("utf-8")
    out = []
    for p in re.findall(r"<w:p\b[^>]*>.*?</w:p>", doc, re.S):
        text = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, re.S))
        text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        if text.strip():
            out.append(text)
    return out
