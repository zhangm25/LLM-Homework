"""Small local file parser for itinerary attachments.

Phase 1 intentionally avoids provider-specific file APIs: we parse common
office/text files locally, then pass compact text/table context to the existing
LLM pipeline.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

from ..models.plan import FileContext

MAX_TEXT_CHARS = 24_000
MAX_ROWS = 80
MAX_COLS = 12
MAX_FILE_BYTES = 6 * 1024 * 1024


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace")


def _trim(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    text = re.sub(r"\r\n?", "\n", text or "").strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text if len(text) <= limit else text[:limit] + f"\n...[已截断 {len(text) - limit} 字符]"


def _summary(text: str, rows: list[list[str]], warnings: list[str]) -> str:
    bits: list[str] = []
    if rows:
        bits.append(f"解析出 {len(rows)} 行表格")
    if text:
        bits.append(f"解析出约 {len(text)} 字文本")
    if warnings:
        bits.append("；".join(warnings[:2]))
    return "；".join(bits) if bits else "未解析出有效文本"


def _rows_to_text(rows: list[list[str]]) -> str:
    lines = []
    for row in rows[:MAX_ROWS]:
        cells = [c.strip() for c in row[:MAX_COLS] if c and c.strip()]
        if cells:
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def _parse_text(data: bytes) -> tuple[str, list[list[str]], list[str]]:
    return _trim(_decode(data)), [], []


def _parse_csv(data: bytes) -> tuple[str, list[list[str]], list[str]]:
    text = _decode(data)
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample)
    except csv.Error:
        dialect = csv.excel
    rows = []
    for row in csv.reader(io.StringIO(text), dialect):
        rows.append([str(c).strip() for c in row[:MAX_COLS]])
        if len(rows) >= MAX_ROWS:
            break
    warnings = ["CSV 行数较多，仅读取前若干行"] if text.count("\n") + 1 > MAX_ROWS else []
    return _trim(_rows_to_text(rows)), rows, warnings


def _xml_text(node: ET.Element, ns: str) -> str:
    parts = []
    for t in node.iter(f"{{{ns}}}t"):
        if t.text:
            parts.append(t.text)
    return "".join(parts)


def _parse_docx(data: bytes) -> tuple[str, list[list[str]], list[str]]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        raw = zf.read("word/document.xml")
    root = ET.fromstring(raw)
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    blocks = []
    for para in root.iter(f"{{{ns}}}p"):
        text = _xml_text(para, ns).strip()
        if text:
            blocks.append(text)
    return _trim("\n".join(blocks)), [], []


def _xlsx_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    return [_xml_text(si, ns) for si in root.iter(f"{{{ns}}}si")]


def _xlsx_cell_value(cell: ET.Element, shared: list[str], ns: str) -> str:
    cell_type = cell.attrib.get("t")
    value = cell.find(f"{{{ns}}}v")
    inline = cell.find(f"{{{ns}}}is")
    if inline is not None:
        return _xml_text(inline, ns)
    raw = value.text if value is not None and value.text is not None else ""
    if cell_type == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return raw
    return raw


def _parse_xlsx(data: bytes) -> tuple[str, list[list[str]], list[str]]:
    rows: list[list[str]] = []
    warnings: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        shared = _xlsx_shared_strings(zf)
        sheet_names = sorted(
            name for name in zf.namelist()
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )
        ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        for sheet in sheet_names[:3]:
            root = ET.fromstring(zf.read(sheet))
            for row in root.iter(f"{{{ns}}}row"):
                cells = [_xlsx_cell_value(c, shared, ns).strip() for c in row.iter(f"{{{ns}}}c")]
                if any(cells):
                    rows.append(cells[:MAX_COLS])
                if len(rows) >= MAX_ROWS:
                    warnings.append("XLSX 内容较多，仅读取前若干行")
                    break
            if len(rows) >= MAX_ROWS:
                break
    return _trim(_rows_to_text(rows)), rows, warnings


def _parse_pdf(data: bytes) -> tuple[str, list[list[str]], list[str]]:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except Exception:
            return "", [], ["当前环境没有 PDF 文本解析库；请先上传 txt/csv/xlsx/docx，或安装 pypdf 后解析 PDF"]
    reader = PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages[:20]:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            pass
    return _trim("\n".join(parts)), [], []


def parse_attachment(filename: str, data: bytes, content_type: Optional[str] = None) -> FileContext:
    warnings: list[str] = []
    safe_name = Path(filename or "attachment").name
    suffix = Path(safe_name).suffix.lower()
    if len(data) > MAX_FILE_BYTES:
        data = data[:MAX_FILE_BYTES]
        warnings.append("文件较大，仅解析前 6MB")
    try:
        if suffix in {".txt", ".md"}:
            text, rows, extra = _parse_text(data)
            kind = "text"
        elif suffix == ".csv":
            text, rows, extra = _parse_csv(data)
            kind = "table"
        elif suffix == ".docx":
            text, rows, extra = _parse_docx(data)
            kind = "document"
        elif suffix == ".xlsx":
            text, rows, extra = _parse_xlsx(data)
            kind = "spreadsheet"
        elif suffix == ".pdf":
            text, rows, extra = _parse_pdf(data)
            kind = "pdf"
        else:
            text, rows, extra = _parse_text(data)
            kind = "text"
            warnings.append(f"未知文件类型 {suffix or '无扩展名'}，已按纯文本尝试解析")
        warnings.extend(extra)
    except (KeyError, zipfile.BadZipFile, ET.ParseError, UnicodeError) as exc:
        text, rows = "", []
        kind = "unknown"
        warnings.append(f"文件解析失败：{type(exc).__name__}")
    text = _trim(text)
    return FileContext(
        filename=safe_name,
        content_type=content_type,
        kind=kind,
        summary=_summary(text, rows, warnings),
        text=text,
        rows=rows,
        warnings=warnings,
    )
