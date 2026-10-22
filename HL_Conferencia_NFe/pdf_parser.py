from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

import pdfplumber


FOOTER_MARKERS = ("DADOS ADICIONAIS", "CALCULO DO ISSQN", "CÁLCULO DO ISSQN")


class UnsupportedInvoiceLayoutError(ValueError):
    pass


def _plain(value: str) -> str:
    value = value.replace("�", "")
    return "".join(
        char for char in unicodedata.normalize("NFKD", value.upper())
        if not unicodedata.combining(char)
    )


def _number(value: str) -> float | None:
    value = value.strip().replace(".", "").replace(",", ".")
    try:
        return float(value)
    except ValueError:
        return None


def _rows(words: list[dict], tolerance: float = 2.2) -> list[dict]:
    rows: list[dict] = []
    for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
        target = None
        for row in reversed(rows[-5:]):
            if abs(row["top"] - word["top"]) <= tolerance:
                target = row
                break
        if target is None:
            rows.append({"top": word["top"], "words": [word]})
        else:
            target["words"].append(word)
            target["top"] = sum(item["top"] for item in target["words"]) / len(target["words"])
    for row in rows:
        row["words"].sort(key=lambda item: item["x0"])
        row["text"] = " ".join(item["text"] for item in row["words"])
    return rows


def _header_columns(row: dict) -> dict | None:
    words = row["words"]
    normalized = [_plain(item["text"]) for item in words]
    joined = " ".join(normalized)
    if not (("COD" in joined or "C DIGO" in joined) and "PROD" in joined):
        return None
    quantity_indices = [i for i, text in enumerate(normalized) if text.startswith("QTD") or text.startswith("QUANT")]
    if not quantity_indices:
        return None
    desc_indices = [i for i, text in enumerate(normalized) if text.startswith("DESCRI")]
    ncm_indices = [i for i, text in enumerate(normalized) if text.startswith("NCM")]
    if not desc_indices or not ncm_indices:
        return None
    return {
        "left": max(0.0, words[0]["x0"] - 4),
        "desc": words[desc_indices[0]]["x0"],
        "ncm": words[ncm_indices[0]]["x0"],
        "qty": words[quantity_indices[0]]["x0"],
        "top": row["top"],
    }


def _find_quantity(words: list[dict], qty_x: float) -> dict | None:
    candidates = []
    for word in words:
        number = _number(word["text"])
        if number is not None and abs(word["x0"] - qty_x) <= 38:
            candidates.append((abs(word["x0"] - qty_x), word, number))
    if not candidates:
        return None
    _, word, number = min(candidates, key=lambda item: item[0])
    return {"word": word, "value": number}


def _extract_page_items(page, page_number: int) -> list[dict]:
    words = page.extract_words(x_tolerance=1, y_tolerance=2, keep_blank_chars=False)
    rows = _rows(words)
    columns = None
    header_index = -1
    for index, row in enumerate(rows):
        columns = _header_columns(row)
        if columns:
            header_index = index
            break
    if not columns:
        return []

    footer_top = page.height - 20
    for row in rows[header_index + 1:]:
        plain = _plain(row["text"])
        if any(marker in plain for marker in FOOTER_MARKERS):
            footer_top = row["top"]
            break

    starts: list[dict] = []
    for row in rows[header_index + 1:]:
        if row["top"] >= footer_top:
            break
        table_words = [word for word in row["words"] if word["x0"] < columns["ncm"] - 12]
        if not table_words:
            continue
        quantity = _find_quantity(row["words"], columns["qty"])
        if not quantity:
            continue
        sku = table_words[0]["text"].strip()
        if not re.search(r"[A-Za-z0-9]", sku) or len(sku) > 40:
            continue
        starts.append({"row": row, "sku": sku, "quantity": quantity})

    items = []
    for index, start in enumerate(starts):
        top = max(columns["top"] + 2, start["row"]["top"] - 2)
        bottom = starts[index + 1]["row"]["top"] - 2 if index + 1 < len(starts) else footer_top - 2
        description_words = []
        sku_right = start["row"]["words"][0]["x1"]
        for row in rows:
            if row["top"] < start["row"]["top"] - 1 or row["top"] >= bottom:
                continue
            description_words.extend(
                word for word in row["words"]
                if word["x0"] >= sku_right + 2 and word["x0"] < columns["ncm"] - 12
            )
        description = " ".join(word["text"] for word in description_words)
        description = re.sub(r"\s+", " ", description).strip()
        qty_word = start["quantity"]["word"]
        items.append({
            "page": page_number,
            "sku": start["sku"],
            "description": description,
            "expected": start["quantity"]["value"],
            "bbox": {
                "x0": columns["left"],
                "top": top,
                "x1": min(page.width, qty_word["x1"] + 5),
                "bottom": max(top + 8, bottom),
                "page_width": float(page.width),
                "page_height": float(page.height),
            },
        })
    return items


# New invoice layouts can be added here without changing storage or UI code.
PAGE_ITEM_EXTRACTORS = (("danfe-generic-columns", _extract_page_items),)


def _extract_items(page, page_number: int) -> tuple[list[dict], str]:
    for layout_name, extractor in PAGE_ITEM_EXTRACTORS:
        items = extractor(page, page_number)
        if items:
            return items, layout_name
    return [], ""


def parse_invoice(path: Path) -> dict:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pdfplumber.open(path) as pdf:
        page_texts = [page.extract_text(x_tolerance=2, y_tolerance=3) or "" for page in pdf.pages]
        items = []
        layouts = set()
        for page_number, page in enumerate(pdf.pages, start=1):
            page_items, layout = _extract_items(page, page_number)
            items.extend(page_items)
            if layout:
                layouts.add(layout)

    if not items:
        raise UnsupportedInvoiceLayoutError(
            f"Layout de DANFE ainda nao reconhecido em {path.name}. "
            "O PDF foi preservado para adicionar um novo extrator."
        )

    digits = re.sub(r"\D", "", path.stem)
    key_match = re.search(r"\d{44}", digits)
    if not key_match:
        key_match = re.search(r"(?:\d[ .-]*){44}", "\n".join(page_texts))
    key = re.sub(r"\D", "", key_match.group(0)) if key_match else ""
    invoice_number = str(int(key[25:34])) if len(key) == 44 else ""
    return {
        "id": digest[:24],
        "sha256": digest,
        "filename": path.name,
        "path": str(path),
        "key": key,
        "number": invoice_number,
        "pages": len(page_texts),
        "layouts": sorted(layouts),
        "items": items,
    }
