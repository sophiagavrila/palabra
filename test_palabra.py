"""Tests for palabra — comment anchor protection."""

import json
import os
import zipfile
from io import BytesIO

import pytest
from lxml import etree

from palabra.protect import protect_file, protect_document_xml

WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
XML_NS = "http://www.w3.org/XML/1998/namespace"

NSMAP = {"w": WORD_NS}


def _make_docx(path: str):
    """Create a minimal valid .docx with two comments."""
    # -- [Content_Types].xml --
    ct_root = etree.Element("Types", xmlns=CT_NS)
    etree.SubElement(ct_root, "Default", Extension="rels", ContentType="application/vnd.openxmlformats-package.relationships+xml")
    etree.SubElement(ct_root, "Default", Extension="xml", ContentType="application/xml")
    etree.SubElement(ct_root, "Override", PartName="/word/document.xml",
                     ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml")
    etree.SubElement(ct_root, "Override", PartName="/word/comments.xml",
                     ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml")
    ct_bytes = etree.tostring(ct_root, xml_declaration=True, encoding="UTF-8", standalone=True)

    # -- _rels/.rels --
    rels_root = etree.Element("Relationships", xmlns=REL_NS)
    etree.SubElement(rels_root, "Relationship", Id="rId1",
                     Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
                     Target="word/document.xml")
    rels_bytes = etree.tostring(rels_root, xml_declaration=True, encoding="UTF-8", standalone=True)

    # -- word/_rels/document.xml.rels --
    doc_rels_root = etree.Element("Relationships", xmlns=REL_NS)
    etree.SubElement(doc_rels_root, "Relationship", Id="rId1",
                     Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments",
                     Target="comments.xml")
    doc_rels_bytes = etree.tostring(doc_rels_root, xml_declaration=True, encoding="UTF-8", standalone=True)

    # -- word/document.xml with two commented spans --
    doc_root = etree.Element(f"{{{WORD_NS}}}document", nsmap=NSMAP)
    body = etree.SubElement(doc_root, f"{{{WORD_NS}}}body")

    # Paragraph 1 with comment 0
    p1 = etree.SubElement(body, f"{{{WORD_NS}}}p")
    etree.SubElement(p1, f"{{{WORD_NS}}}commentRangeStart", {f"{{{WORD_NS}}}id": "0"})
    r1 = etree.SubElement(p1, f"{{{WORD_NS}}}r")
    t1 = etree.SubElement(r1, f"{{{WORD_NS}}}t")
    t1.text = "Hello world"
    etree.SubElement(p1, f"{{{WORD_NS}}}commentRangeEnd", {f"{{{WORD_NS}}}id": "0"})
    ref_run1 = etree.SubElement(p1, f"{{{WORD_NS}}}r")
    etree.SubElement(ref_run1, f"{{{WORD_NS}}}commentReference", {f"{{{WORD_NS}}}id": "0"})

    # Paragraph 2 with comment 1
    p2 = etree.SubElement(body, f"{{{WORD_NS}}}p")
    etree.SubElement(p2, f"{{{WORD_NS}}}commentRangeStart", {f"{{{WORD_NS}}}id": "1"})
    r2 = etree.SubElement(p2, f"{{{WORD_NS}}}r")
    t2 = etree.SubElement(r2, f"{{{WORD_NS}}}t")
    t2.text = "Second comment target"
    etree.SubElement(p2, f"{{{WORD_NS}}}commentRangeEnd", {f"{{{WORD_NS}}}id": "1"})
    ref_run2 = etree.SubElement(p2, f"{{{WORD_NS}}}r")
    etree.SubElement(ref_run2, f"{{{WORD_NS}}}commentReference", {f"{{{WORD_NS}}}id": "1"})

    doc_bytes = etree.tostring(doc_root, xml_declaration=True, encoding="UTF-8", standalone=True)

    # -- word/comments.xml --
    comments_root = etree.Element(f"{{{WORD_NS}}}comments", nsmap=NSMAP)
    c0 = etree.SubElement(comments_root, f"{{{WORD_NS}}}comment", {
        f"{{{WORD_NS}}}id": "0",
        f"{{{WORD_NS}}}author": "Alice",
        f"{{{WORD_NS}}}date": "2025-01-15T10:00:00Z",
    })
    c0p = etree.SubElement(c0, f"{{{WORD_NS}}}p")
    c0r = etree.SubElement(c0p, f"{{{WORD_NS}}}r")
    c0t = etree.SubElement(c0r, f"{{{WORD_NS}}}t")
    c0t.text = "First comment"

    c1 = etree.SubElement(comments_root, f"{{{WORD_NS}}}comment", {
        f"{{{WORD_NS}}}id": "1",
        f"{{{WORD_NS}}}author": "Bob",
        f"{{{WORD_NS}}}date": "2025-01-15T11:00:00Z",
    })
    c1p = etree.SubElement(c1, f"{{{WORD_NS}}}p")
    c1r = etree.SubElement(c1p, f"{{{WORD_NS}}}r")
    c1t = etree.SubElement(c1r, f"{{{WORD_NS}}}t")
    c1t.text = "Second comment"

    comments_bytes = etree.tostring(comments_root, xml_declaration=True, encoding="UTF-8", standalone=True)

    # Assemble the zip
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", ct_bytes)
        zf.writestr("_rels/.rels", rels_bytes)
        zf.writestr("word/_rels/document.xml.rels", doc_rels_bytes)
        zf.writestr("word/document.xml", doc_bytes)
        zf.writestr("word/comments.xml", comments_bytes)


def _read_doc_xml(docx_path: str) -> etree._Element:
    with zipfile.ZipFile(docx_path, "r") as zf:
        return etree.fromstring(zf.read("word/document.xml"))


class TestProtect:
    def test_protect_wraps_both_anchors(self, tmp_path):
        docx_path = str(tmp_path / "test.docx")
        _make_docx(docx_path)

        total, newly_wrapped, already_protected = protect_file(docx_path)

        assert total == 2
        assert newly_wrapped == 2
        assert already_protected == 0

        # Verify SDT wrappers exist
        root = _read_doc_xml(docx_path)
        sdts = root.findall(f".//{{{WORD_NS}}}sdt")
        assert len(sdts) == 2

        # Check sdtLocked on both
        for sdt in sdts:
            lock = sdt.find(f".//{{{WORD_NS}}}lock")
            assert lock is not None
            assert lock.get(f"{{{WORD_NS}}}val") == "sdtLocked"

        # Check tags
        tags = [sdt.find(f".//{{{WORD_NS}}}tag").get(f"{{{WORD_NS}}}val") for sdt in sdts]
        assert "comment-anchor-0" in tags
        assert "comment-anchor-1" in tags

        # Check preserved space run
        for sdt in sdts:
            content = sdt.find(f"{{{WORD_NS}}}sdtContent")
            first_run = content[0]
            assert first_run.tag == f"{{{WORD_NS}}}r"
            t = first_run.find(f"{{{WORD_NS}}}t")
            assert t.text == " "
            assert t.get(f"{{{XML_NS}}}space") == "preserve"

    def test_sidecar_json_created(self, tmp_path):
        docx_path = str(tmp_path / "test.docx")
        _make_docx(docx_path)

        protect_file(docx_path)

        sidecar_path = str(tmp_path / "test-comments-snapshot.json")
        assert os.path.isfile(sidecar_path)

        with open(sidecar_path) as f:
            data = json.load(f)

        assert len(data) == 2

        ids = {entry["id"] for entry in data}
        assert ids == {"0", "1"}

        authors = {entry["author"] for entry in data}
        assert "Alice" in authors
        assert "Bob" in authors

        comments = {entry["comment_text"] for entry in data}
        assert "First comment" in comments
        assert "Second comment" in comments

    def test_idempotent_second_run(self, tmp_path):
        docx_path = str(tmp_path / "test.docx")
        _make_docx(docx_path)

        # First run
        protect_file(docx_path)

        # Read the protected XML to compare later
        root_after_first = _read_doc_xml(docx_path)
        xml_after_first = etree.tostring(root_after_first)

        # Second run
        total, newly_wrapped, already_protected = protect_file(docx_path)

        assert total == 2
        assert newly_wrapped == 0
        assert already_protected == 2

        # XML should be unchanged
        root_after_second = _read_doc_xml(docx_path)
        xml_after_second = etree.tostring(root_after_second)
        assert xml_after_first == xml_after_second

    def test_activity_log_written(self, tmp_path):
        docx_path = str(tmp_path / "test.docx")
        _make_docx(docx_path)

        protect_file(docx_path)

        from palabra.protect import LOG_FILE
        assert LOG_FILE.is_file()
        content = LOG_FILE.read_text()
        assert docx_path in content
