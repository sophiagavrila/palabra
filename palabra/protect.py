"""Core OOXML manipulation — protect comment anchors with locked SDT wrappers."""

import json
import os
import zipfile
import tempfile
import shutil
from copy import deepcopy
from datetime import datetime
from io import BytesIO
from pathlib import Path

from lxml import etree

WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"

NSMAP = {"w": WORD_NS}

LOG_DIR = Path.home() / ".palabra"
LOG_FILE = LOG_DIR / "activity.log"


def _log_activity(filepath: str) -> None:
    """Append a line to ~/.palabra/activity.log before modifying a file."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"[{ts}] protect {filepath}\n")


def _parse_comments_xml(zip_bytes: bytes) -> dict:
    """Parse word/comments.xml and return a dict keyed by comment id."""
    with zipfile.ZipFile(BytesIO(zip_bytes), "r") as zf:
        if "word/comments.xml" not in zf.namelist():
            return {}
        comments_xml = zf.read("word/comments.xml")

    root = etree.fromstring(comments_xml)
    comments = {}
    for c in root.findall(f"{{{WORD_NS}}}comment"):
        cid = c.get(f"{{{WORD_NS}}}id")
        author = c.get(f"{{{WORD_NS}}}author", "")
        date = c.get(f"{{{WORD_NS}}}date", "")
        # Collect all text within the comment
        texts = []
        for t in c.iter(f"{{{WORD_NS}}}t"):
            if t.text:
                texts.append(t.text)
        comments[cid] = {
            "id": cid,
            "author": author,
            "date": date,
            "comment_text": " ".join(texts),
        }
    return comments


def _get_anchor_text(nodes: list, ns: str) -> str:
    """Extract text content from runs between commentRangeStart and commentRangeEnd."""
    texts = []
    for node in nodes:
        for t in node.iter(f"{{{ns}}}t"):
            if t.text:
                texts.append(t.text)
    return "".join(texts)


def _already_wrapped(start_el: etree._Element) -> bool:
    """Check if this commentRangeStart is already inside a matching SDT wrapper."""
    parent = start_el.getparent()
    if parent is None:
        return False
    # Walk up to find an sdt ancestor
    el = parent
    while el is not None:
        if el.tag == f"{{{WORD_NS}}}sdtContent":
            sdt = el.getparent()
            if sdt is not None and sdt.tag == f"{{{WORD_NS}}}sdt":
                sdt_pr = sdt.find(f"{{{WORD_NS}}}sdtPr", NSMAP)
                if sdt_pr is not None:
                    tag_el = sdt_pr.find(f"{{{WORD_NS}}}tag", NSMAP)
                    if tag_el is not None:
                        tag_val = tag_el.get(f"{{{WORD_NS}}}val", "")
                        cid = start_el.get(f"{{{WORD_NS}}}id")
                        if tag_val == f"comment-anchor-{cid}":
                            return True
        el = el.getparent()
    return False


def protect_document_xml(doc_xml: bytes) -> tuple[bytes, int, int, int, dict]:
    """
    Protect comment anchors in document.xml.

    Returns: (modified_xml_bytes, total_anchors, newly_wrapped, already_protected, anchor_info)
    anchor_info maps comment id -> {"anchor_text": str, "paragraph_index": int}
    """
    root = etree.fromstring(doc_xml)

    # Build paragraph index map
    all_paragraphs = list(root.iter(f"{{{WORD_NS}}}p"))
    para_index_map = {id(p): i for i, p in enumerate(all_paragraphs)}

    # Find all commentRangeStart elements
    starts = list(root.iter(f"{{{WORD_NS}}}commentRangeStart"))
    total = len(starts)
    newly_wrapped = 0
    already_protected = 0
    anchor_info = {}

    for start_el in starts:
        cid = start_el.get(f"{{{WORD_NS}}}id")
        if cid is None:
            continue

        if _already_wrapped(start_el):
            already_protected += 1
            # Still collect anchor info from existing wrapper
            anchor_info[cid] = _collect_anchor_info_from_wrapped(start_el, para_index_map)
            continue

        # Find the matching commentRangeEnd in the same parent
        parent = start_el.getparent()
        if parent is None:
            continue

        # Collect nodes between start and end (inclusive of start, end, and commentReference)
        children = list(parent)
        start_idx = children.index(start_el)

        # Find commentRangeEnd with matching id
        end_idx = None
        for i in range(start_idx + 1, len(children)):
            child = children[i]
            if (child.tag == f"{{{WORD_NS}}}commentRangeEnd"
                    and child.get(f"{{{WORD_NS}}}id") == cid):
                end_idx = i
                break

        if end_idx is None:
            # End marker not in same parent — skip
            continue

        # Also grab commentReference run right after commentRangeEnd
        ref_idx = end_idx
        if end_idx + 1 < len(children):
            next_el = children[end_idx + 1]
            if next_el.tag == f"{{{WORD_NS}}}r":
                ref = next_el.find(f"{{{WORD_NS}}}commentReference")
                if ref is not None and ref.get(f"{{{WORD_NS}}}id") == cid:
                    ref_idx = end_idx + 1

        # Nodes to wrap: start_idx through ref_idx inclusive
        nodes_to_wrap = children[start_idx:ref_idx + 1]

        # Get paragraph index
        p_idx = para_index_map.get(id(parent), -1)

        # Get anchor text from runs between start and end
        inner_nodes = children[start_idx + 1:end_idx]
        anchor_text = _get_anchor_text(inner_nodes, WORD_NS)

        anchor_info[cid] = {"anchor_text": anchor_text, "paragraph_index": p_idx}

        # Build SDT wrapper
        sdt = etree.Element(f"{{{WORD_NS}}}sdt", nsmap=NSMAP)
        sdt_pr = etree.SubElement(sdt, f"{{{WORD_NS}}}sdtPr")
        lock = etree.SubElement(sdt_pr, f"{{{WORD_NS}}}lock")
        lock.set(f"{{{WORD_NS}}}val", "sdtLocked")
        tag = etree.SubElement(sdt_pr, f"{{{WORD_NS}}}tag")
        tag.set(f"{{{WORD_NS}}}val", f"comment-anchor-{cid}")
        sdt_content = etree.SubElement(sdt, f"{{{WORD_NS}}}sdtContent")

        # Add preserved space run first
        space_run = etree.SubElement(sdt_content, f"{{{WORD_NS}}}r")
        space_t = etree.SubElement(space_run, f"{{{WORD_NS}}}t")
        space_t.set(f"{{{XML_NS}}}space", "preserve")
        space_t.text = " "

        # Move original nodes into sdtContent
        insert_pos = list(parent).index(nodes_to_wrap[0])
        for node in nodes_to_wrap:
            parent.remove(node)
            sdt_content.append(node)

        parent.insert(insert_pos, sdt)
        newly_wrapped += 1

    modified_xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    return modified_xml, total, newly_wrapped, already_protected, anchor_info


def _collect_anchor_info_from_wrapped(start_el, para_index_map):
    """Collect anchor info for an already-wrapped comment."""
    cid = start_el.get(f"{{{WORD_NS}}}id")
    parent = start_el.getparent()  # sdtContent
    if parent is None:
        return {"anchor_text": "", "paragraph_index": -1}

    # Find paragraph ancestor
    p_idx = -1
    el = parent
    while el is not None:
        if el.tag == f"{{{WORD_NS}}}p":
            p_idx = para_index_map.get(id(el), -1)
            break
        el = el.getparent()

    # Get text from sibling runs between start and end
    children = list(parent)
    try:
        si = children.index(start_el)
    except ValueError:
        return {"anchor_text": "", "paragraph_index": p_idx}

    end_i = None
    for i in range(si + 1, len(children)):
        if (children[i].tag == f"{{{WORD_NS}}}commentRangeEnd"
                and children[i].get(f"{{{WORD_NS}}}id") == cid):
            end_i = i
            break

    inner = children[si + 1:end_i] if end_i else []
    anchor_text = _get_anchor_text(inner, WORD_NS)
    return {"anchor_text": anchor_text, "paragraph_index": p_idx}


def protect_file(filepath: str) -> tuple[int, int, int]:
    """
    Protect comment anchors in a .docx file. Overwrites in place.

    Returns: (total_anchors, newly_wrapped, already_protected)
    """
    filepath = os.path.abspath(filepath)

    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    # Read the entire zip into memory
    with open(filepath, "rb") as f:
        zip_bytes = f.read()

    # Parse comments from word/comments.xml
    comments = _parse_comments_xml(zip_bytes)

    # Read and process document.xml
    with zipfile.ZipFile(BytesIO(zip_bytes), "r") as zf:
        if "word/document.xml" not in zf.namelist():
            raise ValueError("Not a valid .docx file — word/document.xml not found")
        doc_xml = zf.read("word/document.xml")

    modified_xml, total, newly_wrapped, already_protected, anchor_info = protect_document_xml(doc_xml)

    # Log activity before writing
    _log_activity(filepath)

    if newly_wrapped > 0:
        # Rewrite the zip, replacing only word/document.xml
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".docx", dir=os.path.dirname(filepath))
        try:
            with zipfile.ZipFile(BytesIO(zip_bytes), "r") as zf_in:
                with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf_out:
                    for item in zf_in.infolist():
                        if item.filename == "word/document.xml":
                            zf_out.writestr(item, modified_xml)
                        else:
                            zf_out.writestr(item, zf_in.read(item.filename))
            shutil.move(tmp_path, filepath)
        except Exception:
            # Clean up temp file on error, leave original untouched
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
        finally:
            try:
                os.close(tmp_fd)
            except OSError:
                pass

    # Write sidecar JSON
    sidecar = []
    for cid, info in anchor_info.items():
        entry = {
            "id": cid,
            "author": comments.get(cid, {}).get("author", ""),
            "date": comments.get(cid, {}).get("date", ""),
            "comment_text": comments.get(cid, {}).get("comment_text", ""),
            "anchor_text": info["anchor_text"],
            "paragraph_index": info["paragraph_index"],
        }
        sidecar.append(entry)

    sidecar_path = os.path.splitext(filepath)[0] + "-comments-snapshot.json"
    with open(sidecar_path, "w") as f:
        json.dump(sidecar, f, indent=2)

    return total, newly_wrapped, already_protected
