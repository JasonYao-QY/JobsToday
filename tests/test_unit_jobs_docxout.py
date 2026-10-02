"""Unit: jobs/docxout.py -- writing a letter into a .docx with nothing but the standard library.

A .docx is a zip of XML parts. The document he already keeps carries his margins, fonts and styles, so
the text is swapped inside it rather than a new file being generated: every part except word/document.xml
comes through byte for byte, and inside that part only the paragraphs change.
"""
import os
import shutil
import tempfile
import unittest
import zipfile

os.environ.setdefault("TODAY_HOME", tempfile.mkdtemp(prefix="today-unit-"))
import tests._support  # noqa: E402,F401
from jobs import docxout  # noqa: E402

LETTER = "Dear Hiring Team,\n\nI am applying for the role.\n\nIt went well.\n\nQingyuan Yao"


class Paragraphs(unittest.TestCase):
    def test_blank_lines_separate_rather_than_add_paragraphs(self):
        self.assertEqual(docxout.paragraphs_of(LETTER),
                         ["Dear Hiring Team,", "I am applying for the role.", "It went well.",
                          "Qingyuan Yao"])

    def test_empty_text_is_refused(self):
        for bad in ("", "   ", "\n\n"):
            with self.assertRaises(ValueError):
                docxout.paragraphs_of(bad)

    def test_xml_special_characters_survive(self):
        p = docxout.paragraphs_of('R&D <at> "Smith & Co"')
        xml = docxout.body_xml(p)
        self.assertIn("&amp;", xml)
        self.assertIn("&lt;at&gt;", xml)
        self.assertNotIn("<at>", xml)


class Writing(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "Cover Letter.docx")

    def test_a_new_file_is_a_valid_docx_word_can_open(self):
        docxout.write(self.path, LETTER)
        with zipfile.ZipFile(self.path) as z:
            self.assertIsNone(z.testzip())
            names = set(z.namelist())
        for need in ("[Content_Types].xml", "_rels/.rels", "word/document.xml"):
            self.assertIn(need, names)
        self.assertEqual(docxout.read_paragraphs(self.path), docxout.paragraphs_of(LETTER))

    def test_an_existing_file_keeps_everything_but_its_text(self):
        docxout.write(self.path, "Old letter.\n\nQingyuan Yao")
        with zipfile.ZipFile(self.path) as z:
            before = {n: z.read(n) for n in z.namelist()}
        docxout.write(self.path, LETTER)
        with zipfile.ZipFile(self.path) as z:
            after = {n: z.read(n) for n in z.namelist()}
        self.assertEqual(sorted(before), sorted(after))
        for n in before:
            if n != "word/document.xml":
                self.assertEqual(before[n], after[n], n)
        self.assertEqual(docxout.read_paragraphs(self.path), docxout.paragraphs_of(LETTER))

    def test_the_page_setup_of_the_template_survives(self):
        """sectPr carries his margins and page size; losing it would reformat the letter."""
        docxout.write(self.path, "First.")
        with zipfile.ZipFile(self.path) as z:
            doc = z.read("word/document.xml").decode("utf-8")
        marker = '<w:pgMar w:top="1440"'
        self.assertIn(marker, doc)
        docxout.write(self.path, LETTER)
        with zipfile.ZipFile(self.path) as z:
            self.assertIn(marker, z.read("word/document.xml").decode("utf-8"))

    def test_a_failed_write_leaves_the_old_file_alone(self):
        docxout.write(self.path, "Old letter.\n\nQingyuan Yao")
        with open(self.path, "rb") as f:
            good = f.read()
        with self.assertRaises(ValueError):
            docxout.write(self.path, "   ")
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), good)

    def test_a_template_that_is_not_a_zip_is_refused_by_name(self):
        with open(self.path, "w") as f:
            f.write("this is not a docx")
        with self.assertRaises(ValueError) as e:
            docxout.write(self.path, LETTER)
        self.assertIn("not a Word document", str(e.exception))
        with open(self.path) as f:
            self.assertEqual(f.read(), "this is not a docx")
