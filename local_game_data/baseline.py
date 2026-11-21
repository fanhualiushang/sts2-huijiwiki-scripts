"""Read Huiji's two-sheet tabx XLSX download without changing the workbook."""
from pathlib import Path, PurePosixPath
import re
from zipfile import ZipFile
import xml.etree.ElementTree as ET


NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _column(address):
    letters = re.match(r"[A-Z]+", address)
    if not letters:
        raise ValueError(f"Invalid XLSX cell address: {address}")
    result = 0
    for char in letters[0]:
        result = result * 26 + ord(char) - ord("A") + 1
    return result - 1


def read_tabx_xlsx(path):
    path = Path(path)
    with ZipFile(path) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = ["".join(t.text or "" for t in node.findall(".//s:t", NS))
                      for node in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("s:si", NS)]
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {r.get("Id"): r.get("Target") for r in relationships}
        sheets = {}
        for sheet in workbook.findall("s:sheets/s:sheet", NS):
            target = targets[sheet.get("{" + REL + "}id")]
            filename = target.lstrip("/") if target.startswith("/") else str(PurePosixPath("xl") / target)
            rows = []
            document = ET.fromstring(archive.read(filename))
            for row in document.findall("s:sheetData/s:row", NS):
                values = []
                for cell in row.findall("s:c", NS):
                    column = _column(cell.get("r", ""))
                    while len(values) <= column:
                        values.append(None)
                    value_node = cell.find("s:v", NS)
                    value = value_node.text if value_node is not None else None
                    if cell.get("t") == "s":
                        value = shared[int(value)] if value is not None else None
                    elif cell.get("t") == "inlineStr":
                        value = "".join(t.text or "" for t in cell.findall(".//s:t", NS))
                    elif cell.get("t") == "b":
                        value = "True" if value == "1" else "False"
                    if cell.find("s:f", NS) is not None and value_node is None:
                        raise ValueError(f"XLSX formula has no cached value: {path.name} {sheet.get('name')}!{cell.get('r')}")
                    values[column] = value
                if any(value is not None for value in values):
                    rows.append(values)
            sheets[sheet.get("name")] = rows
    if "data" not in sheets or not sheets["data"]:
        raise ValueError(f"Huiji tabx workbook must have a non-empty data sheet: {path}")
    headers = sheets["data"][0]
    if any(not isinstance(h, str) or not h for h in headers) or len(set(headers)) != len(headers) or "id" not in headers:
        raise ValueError(f"Invalid tabx data headers in {path}")
    fields = {name: {"name": name, "type": "string", "title": {"en": name, "zh": ""}} for name in headers}
    if sheets.get("fields"):
        keys = sheets["fields"][0]
        for row in sheets["fields"][1:]:
            info = dict(zip(keys, row))
            name = info.get("name")
            if name in fields:
                fields[name] = {"name": name, "type": info.get("type") or "string",
                                "title": {"en": info.get("title_en") or name, "zh": info.get("title_zh") or ""}}
    data = []
    for row in sheets["data"][1:]:
        if len(row) > len(headers) and any(v is not None for v in row[len(headers):]):
            raise ValueError(f"Data row exceeds tabx headers in {path}")
        data.append(row[:len(headers)] + [None] * max(0, len(headers) - len(row)))
    return {"schema": {"fields": [fields[h] for h in headers]}, "data": data, "_baseline_source": str(path.resolve())}
