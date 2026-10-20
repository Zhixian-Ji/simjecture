"""Bounded local document extraction. No source code or document macros execute."""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

MAX_TEXT = 500_000
MAX_PAGES = 400
TEXT_SUFFIXES = {
    ".md",
    ".rst",
    ".txt",
    ".tex",
    ".bib",
    ".py",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
    ".f90",
    ".f",
    ".sh",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".json",
    ".ipynb",
    ".jl",
    ".m",
    ".rs",
    ".js",
    ".ts",
}
DOCUMENT_SUFFIXES = {".pdf", ".pptx", ".docx"}
SPECIAL_NAMES = {"README", "Makefile", "CMakeLists.txt", "Dockerfile"}
CODE_SUFFIXES = {
    ".py",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
    ".f90",
    ".f",
    ".sh",
    ".jl",
    ".m",
    ".rs",
    ".js",
    ".ts",
}


def chunks(text, location=""):
    """Keep lines intact and preserve an addressable parent page/slide."""
    lines = text.splitlines()
    start, pending, size = 1, [], 0
    for number, line in enumerate(lines, 1):
        if pending and size + len(line) > 2500:
            yield {
                "location": f"{location + ' · ' if location else ''}Lines {start}–{number - 1}",
                "text": "\n".join(pending),
            }
            pending, size, start = [], 0, number
        # Pathological generated single lines must not create unbounded tool results.
        for offset in range(0, max(1, len(line)), 2500):
            part = line[offset : offset + 2500]
            pending.append(part)
            size += len(part) + 1
            if size >= 2500:
                yield {
                    "location": f"{location + ' · ' if location else ''}Lines {start}–{number}",
                    "text": "\n".join(pending),
                }
                pending, size, start = [], 0, number + 1
    if pending:
        yield {
            "location": f"{location + ' · ' if location else ''}Lines {start}–{len(lines)}",
            "text": "\n".join(pending),
        }


def xml_text(archive, name):
    info = archive.getinfo(name)
    if info.file_size > 8 * 1024**2:
        raise ValueError("A document XML part exceeds the extraction limit")
    data = archive.read(name)
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ValueError("Document XML entities are not supported")
    root = ET.fromstring(data)
    paragraphs = []
    for paragraph in root.iter():
        if paragraph.tag.rsplit("}", 1)[-1] == "p":
            paragraphs.append(
                " ".join(
                    node.text or ""
                    for node in paragraph.iter()
                    if node.tag.rsplit("}", 1)[-1] == "t"
                )
            )
    return "\n".join(paragraphs)


def document_title(path, sections):
    path = Path(path)
    title = path.stem.replace("_", " ")
    if path.suffix.lower() in {".md", ".rst", ".txt"}:
        heading = next((s["text"].splitlines()[0] for s in sections if s["text"].strip()), "")
        if heading.startswith("# "):
            title = heading[2:180]
    return title


def extract(path):
    path = Path(path)
    suffix = path.suffix.lower()
    sections, warnings = [], []
    remaining = MAX_TEXT

    def add(text, location=""):
        nonlocal remaining
        clean = text.encode("utf-8", errors="replace").decode("utf-8")
        if clean != text:
            warning = "Some characters could not be decoded; inspect the original"
            if warning not in warnings:
                warnings.append(warning)
        text = clean
        if len(text) > remaining and "Text extraction limit reached" not in warnings:
            warnings.append("Text extraction limit reached")
        text = text[:remaining]
        remaining -= len(text)
        sections.extend(chunks(text, location))

    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as error:
            raise ValueError(
                "PDF support needs the knowledge extra: uv sync --extra knowledge"
            ) from error
        reader = PdfReader(path)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("Password-protected PDF")
        if len(reader.pages) > MAX_PAGES:
            warnings.append(f"Only the first {MAX_PAGES} pages were indexed")
        for number, page in enumerate(reader.pages[:MAX_PAGES], 1):
            add(page.extract_text() or "", f"Page {number}")
            if not remaining:
                break
        if not any(section["text"].strip() for section in sections):
            raise ValueError("No readable text; this PDF may need OCR")
    elif suffix in {".pptx", ".docx"}:
        with zipfile.ZipFile(path) as archive:
            if suffix == ".docx":
                add(xml_text(archive, "word/document.xml"))
            else:
                # Follow presentation order, which need not match slide filenames.
                presentation = archive.read("ppt/presentation.xml")
                relationships = archive.read("ppt/_rels/presentation.xml.rels")
                if any(
                    b"<!DOCTYPE" in data or b"<!ENTITY" in data
                    for data in (presentation, relationships)
                ):
                    raise ValueError("Document XML entities are not supported")
                targets = {r.attrib["Id"]: r.attrib["Target"] for r in ET.fromstring(relationships)}
                ids = [n for n in ET.fromstring(presentation).iter() if n.tag.endswith("}sldId")]
                names = set(archive.namelist())
                if len(ids) > MAX_PAGES:
                    warnings.append(f"Only the first {MAX_PAGES} slides were indexed")
                for number, item in enumerate(ids[:MAX_PAGES], 1):
                    key = next(v for k, v in item.attrib.items() if k.endswith("}id"))
                    target = targets[key]
                    name = target.lstrip("/") if target.startswith("/") else "ppt/" + target
                    content = xml_text(archive, name)
                    rel = str(Path(name).parent / "_rels" / (Path(name).name + ".rels"))
                    if rel in names:
                        data = archive.read(rel)
                        if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
                            raise ValueError("Document XML entities are not supported")
                        for relation in ET.fromstring(data):
                            if relation.attrib.get("Type", "").endswith("/notesSlide"):
                                import posixpath

                                note = posixpath.normpath(
                                    posixpath.join(
                                        str(Path(name).parent), relation.attrib["Target"]
                                    )
                                )
                                if note in names:
                                    content += "\nSpeaker notes:\n" + xml_text(archive, note)
                    add(content, f"Slide {number}")
                    if not remaining:
                        break
    else:
        raw = path.read_bytes()
        if b"\x00" in raw:
            raise ValueError("Binary file")
        text = raw.decode("utf-8", errors="replace")
        if suffix == ".ipynb":
            data = json.loads(text)
            # Notebook outputs often contain large encoded images or repeated arrays.
            text = "\n\n".join(
                f"Cell {i + 1} ({cell.get('cell_type', 'text')})\n"
                + "".join(cell.get("source", []))
                for i, cell in enumerate(data.get("cells", []))
            )
        add(text)
    if not sections:
        sections = [{"location": "Document", "text": ""}]
    title = document_title(path, sections)
    kind = (
        "papers"
        if suffix == ".pdf"
        else "slides"
        if suffix == ".pptx"
        else "code"
        if suffix in CODE_SUFFIXES
        else "documents"
    )
    return {"title": title, "kind": kind, "sections": sections, "warning": "; ".join(warnings)}


if __name__ == "__main__":
    # Expensive parsers run outside the HTTP server and have memory/time budgets.
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (1536 * 1024**2, 1536 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (25, 25))
    try:
        print(json.dumps(extract(sys.argv[1])))
    except Exception as error:
        print(json.dumps({"error": str(error) or type(error).__name__}))
        sys.exit(1)
