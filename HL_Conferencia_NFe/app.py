from __future__ import annotations

import io
import json
import mimetypes
import os
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pypdfium2 as pdfium
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

try:
    from .pdf_parser import parse_invoice
    from .tiny_client import TinyClient
    from .tiny_print import create_temp_label, ensure_print_runtime, list_printers, print_pdf
except ImportError:
    from pdf_parser import parse_invoice
    from tiny_client import TinyClient
    from tiny_print import create_temp_label, ensure_print_runtime, list_printers, print_pdf


HOST = "127.0.0.1"
PORT = int(os.getenv("HL_CONFERENCIA_PORT", "8770"))
APP_VERSION = "7.1"
SERVER_INSTANCE = f"{os.getpid()}-{time.time_ns()}"
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APP_DIR = BUNDLE_ROOT
ROOT_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
STATIC_DIR = BUNDLE_ROOT / "conference_static" if (BUNDLE_ROOT / "conference_static").is_dir() else APP_DIR / "static"
DATA_DIR = Path(os.getenv("LOCALAPPDATA", Path.home())) / "HL_Conferencia_NFe"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "conferencia.sqlite3"
RENDER_DIR = DATA_DIR / "render"
RENDER_DIR.mkdir(exist_ok=True)
EXPORT_DIR = DATA_DIR / "exports"
EXPORT_DIR.mkdir(exist_ok=True)
DEFAULT_WATCH = Path.home() / "Desktop" / "ETIQUETAS"
DB_LOCK = threading.RLock()
TINY = TinyClient(APP_DIR, DATA_DIR) if getattr(sys, "frozen", False) else TinyClient(ROOT_DIR)
AUTH_BRIDGE = None
RESTART_REQUESTED = threading.Event()


class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    request_queue_size = 128


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def init_db() -> None:
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS notes (
            id TEXT PRIMARY KEY, sha256 TEXT UNIQUE, filename TEXT, path TEXT, access_key TEXT,
            number TEXT, pages INTEGER, mtime REAL, parsed_at TEXT, error TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, note_id TEXT NOT NULL, item_index INTEGER NOT NULL,
            page INTEGER, sku TEXT, description TEXT, expected REAL, counted REAL, checked INTEGER DEFAULT 0,
            bbox TEXT, last_action_at TEXT, operator TEXT, printed_count INTEGER DEFAULT 0,
            UNIQUE(note_id, item_index)
        );
        CREATE TABLE IF NOT EXISTS operators (name TEXT PRIMARY KEY, last_used_at TEXT);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, note_id TEXT, item_id INTEGER, action TEXT,
            value TEXT, operator TEXT, created_at TEXT, undone_at TEXT
        );
        INSERT OR IGNORE INTO operators(name, last_used_at) VALUES ('Anderson', NULL), ('Luiz', NULL);
        """)
        action_columns = {row[1] for row in db.execute("PRAGMA table_info(actions)")}
        if "undone_at" not in action_columns:
            db.execute("ALTER TABLE actions ADD COLUMN undone_at TEXT")
        db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('watch_dir',?)", (str(DEFAULT_WATCH),))
        printers, default = list_printers()
        db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('printer',?)", (default,))


def setting(key: str, default: str = "") -> str:
    with connect() as db:
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return str(row["value"]) if row else default


def save_setting(key: str, value: str) -> None:
    with connect() as db:
        db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def scan_folder() -> dict:
    watch_dir = Path(setting("watch_dir", str(DEFAULT_WATCH)))
    watch_dir.mkdir(parents=True, exist_ok=True)
    files = [path for path in watch_dir.glob("*.pdf") if "_labels" not in path.stem.lower()]
    parsed = 0
    errors = []
    for path in files:
        try:
            mtime = path.stat().st_mtime
            with connect() as db:
                known = db.execute("SELECT id FROM notes WHERE path=? AND mtime=?", (str(path), mtime)).fetchone()
            if known:
                continue
            data = parse_invoice(path)
            with DB_LOCK, connect() as db:
                duplicate = db.execute("SELECT id FROM notes WHERE sha256=?", (data["sha256"],)).fetchone()
                if duplicate:
                    continue
                db.execute(
                    "INSERT OR REPLACE INTO notes(id,sha256,filename,path,access_key,number,pages,mtime,parsed_at,error) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (data["id"], data["sha256"], data["filename"], data["path"], data["key"], data["number"], data["pages"], mtime, now(), ""),
                )
                for index, item in enumerate(data["items"]):
                    db.execute(
                        "INSERT OR IGNORE INTO items(note_id,item_index,page,sku,description,expected,counted,bbox) VALUES(?,?,?,?,?,?,?,?)",
                        (data["id"], index, item["page"], item["sku"], item["description"], item["expected"], item["expected"], json.dumps(item["bbox"])),
                    )
            parsed += 1
        except Exception as error:
            errors.append(f"{path.name}: {error}")
            traceback.print_exc()
    return {"files": len(files), "parsed": parsed, "errors": errors}


def watcher() -> None:
    while True:
        scan_folder()
        time.sleep(3)


def note_summary(row: sqlite3.Row) -> dict:
    total = row["total_items"] or 0
    checked = row["checked_items"] or 0
    expected = row["expected_units"] or 0
    counted = row["counted_units"] or 0
    return {
        "id": row["id"], "filename": row["filename"], "number": row["number"],
        "pages": row["pages"], "total_items": total, "checked_items": checked,
        "expected_units": expected, "counted_units": counted,
        "progress": round((checked / total * 100) if total else 0, 1),
        "updated_at": row["updated_at"] or row["parsed_at"], "error": row["error"],
    }


def list_notes() -> list[dict]:
    with connect() as db:
        rows = db.execute("""
            SELECT n.*, COUNT(i.id) total_items, SUM(i.checked) checked_items,
                   SUM(i.expected) expected_units, SUM(i.counted) counted_units,
                   MAX(i.last_action_at) updated_at
            FROM notes n LEFT JOIN items i ON i.note_id=n.id
            GROUP BY n.id ORDER BY COALESCE(updated_at,n.parsed_at) DESC
        """).fetchall()
    return [note_summary(row) for row in rows]


def get_note(note_id: str) -> dict | None:
    with connect() as db:
        note = db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        if not note:
            return None
        items = db.execute("SELECT * FROM items WHERE note_id=? ORDER BY item_index", (note_id,)).fetchall()
        last = db.execute("SELECT * FROM actions WHERE note_id=? ORDER BY id DESC LIMIT 1", (note_id,)).fetchone()
    return {
        "id": note["id"], "filename": note["filename"], "number": note["number"],
        "access_key": note["access_key"], "pages": note["pages"],
        "items": [{
            "id": item["id"], "page": item["page"], "sku": item["sku"],
            "description": item["description"], "expected": item["expected"],
            "counted": item["counted"], "checked": bool(item["checked"]),
            "bbox": json.loads(item["bbox"]), "last_action_at": item["last_action_at"],
            "operator": item["operator"], "printed_count": item["printed_count"],
        } for item in items],
        "last_action": dict(last) if last else None,
    }


def render_page(note_id: str, page_number: int) -> Path:
    target = RENDER_DIR / f"{note_id}_{page_number}.webp"
    if target.is_file():
        return target
    with connect() as db:
        row = db.execute("SELECT path FROM notes WHERE id=?", (note_id,)).fetchone()
    if not row:
        raise FileNotFoundError(note_id)
    document = pdfium.PdfDocument(row["path"])
    try:
        page = document[page_number - 1]
        image = page.render(scale=2.0).to_pil()
        image.save(target, "WEBP", quality=88, method=4)
    finally:
        document.close()
    return target


def _item_status(item: sqlite3.Row) -> tuple[str, tuple[float, float, float]]:
    if not item["checked"]:
        return "PENDENTE", (0.50, 0.48, 0.52)
    mismatch = float(item["counted"]) != float(item["expected"])
    if mismatch:
        return "DIVERGENTE", (0.95, 0.58, 0.12)
    return "CONFERIDO", (0.10, 0.66, 0.43)


def _summary_pdf(note: sqlite3.Row, items: list[sqlite3.Row]) -> bytes:
    stream = io.BytesIO()
    canvas = Canvas(stream, pagesize=A4, pageCompression=1)
    width, height = A4
    attention = [item for item in items if _item_status(item)[0] != "CONFERIDO"]
    expected = sum(float(item["expected"]) for item in items)
    counted = sum(float(item["counted"]) for item in items)
    checked = sum(1 for item in items if item["checked"])
    exact = sum(1 for item in items if _item_status(item)[0] == "CONFERIDO")
    divergent = sum(1 for item in items if _item_status(item)[0] == "DIVERGENTE")
    pending = sum(1 for item in items if _item_status(item)[0] == "PENDENTE")

    def header(page_number: int) -> float:
        canvas.setFillColorRGB(0.12, 0.08, 0.16)
        canvas.rect(0, height - 86, width, 86, stroke=0, fill=1)
        canvas.setFillColorRGB(1, 1, 1)
        canvas.setFont("Helvetica-Bold", 17)
        canvas.drawString(34, height - 39, f"RELATORIO DE CONFERENCIA - NF {note['number'] or 'SEM NUMERO'}")
        canvas.setFont("Helvetica", 8)
        canvas.drawString(34, height - 57, f"Arquivo: {note['filename']}")
        canvas.drawRightString(width - 34, height - 57, f"Resumo {page_number}")
        return height - 112

    def metric(x: float, label: str, value: str, color: tuple[float, float, float]) -> None:
        canvas.setFillColorRGB(*color)
        canvas.roundRect(x, y - 42, 118, 42, 4, stroke=0, fill=1)
        canvas.setFillColorRGB(1, 1, 1)
        canvas.setFont("Helvetica-Bold", 15)
        canvas.drawString(x + 9, y - 20, value)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(x + 9, y - 33, label)

    page_number = 1
    y = header(page_number)
    metric(34, "ITENS CONFERIDOS", f"{checked}/{len(items)}", (0.23, 0.22, 0.25))
    metric(160, "EXATOS", str(exact), (0.10, 0.66, 0.43))
    metric(286, "DIVERGENTES", str(divergent), (0.95, 0.58, 0.12))
    metric(412, "PENDENTES", str(pending), (0.50, 0.48, 0.52))
    y -= 64
    canvas.setFillColorRGB(0.18, 0.15, 0.21)
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawString(34, y, f"Unidades contadas: {counted:g} de {expected:g}   |   Saldo: {counted - expected:+g}")
    canvas.setFont("Helvetica", 8)
    canvas.setFillColorRGB(0.42, 0.38, 0.46)
    canvas.drawRightString(width - 34, y, f"Gerado em {datetime.now().astimezone().strftime('%d/%m/%Y %H:%M')}")
    y -= 26
    canvas.setFillColorRGB(0.12, 0.08, 0.16)
    canvas.setFont("Helvetica-Bold", 11)
    canvas.drawString(34, y, "ITENS QUE EXIGEM ATENCAO")
    y -= 18

    if not attention:
        canvas.setFillColorRGB(0.10, 0.66, 0.43)
        canvas.setFont("Helvetica-Bold", 12)
        canvas.drawString(34, y - 10, "Nenhuma pendencia ou divergencia encontrada.")
    for item in attention:
        if y < 72:
            canvas.showPage()
            page_number += 1
            y = header(page_number)
        status, color = _item_status(item)
        canvas.setFillColorRGB(0.97, 0.96, 0.98)
        canvas.roundRect(34, y - 37, width - 68, 34, 3, stroke=0, fill=1)
        canvas.setFillColorRGB(*color)
        canvas.rect(34, y - 37, 4, 34, stroke=0, fill=1)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(45, y - 16, str(item["sku"])[:34])
        canvas.drawRightString(width - 42, y - 16, f"{status}  {float(item['counted']):g}/{float(item['expected']):g}")
        canvas.setFillColorRGB(0.35, 0.31, 0.39)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(45, y - 29, str(item["description"] or "")[:104])
        y -= 42

    canvas.save()
    return stream.getvalue()


def create_final_pdf(note_id: str, output_dir: Path = EXPORT_DIR) -> Path:
    with connect() as db:
        note = db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        items = db.execute("SELECT * FROM items WHERE note_id=? ORDER BY item_index", (note_id,)).fetchall()
    if not note:
        raise FileNotFoundError(note_id)
    source_path = Path(note["path"])
    if not source_path.is_file():
        raise FileNotFoundError(source_path)

    reader = PdfReader(str(source_path))
    writer = PdfWriter()
    by_page: dict[int, list[sqlite3.Row]] = {}
    for item in items:
        by_page.setdefault(int(item["page"]), []).append(item)

    for page_number, page in enumerate(reader.pages, start=1):
        page_width = float(page.mediabox.width)
        page_height = float(page.mediabox.height)
        marked_items = [item for item in by_page.get(page_number, []) if item["checked"]]
        if not marked_items:
            writer.add_page(page)
            continue
        overlay_stream = io.BytesIO()
        overlay = Canvas(overlay_stream, pagesize=(page_width, page_height), pageCompression=1)
        for item in marked_items:
            bbox = json.loads(item["bbox"])
            scale_x = page_width / float(bbox["page_width"])
            scale_y = page_height / float(bbox["page_height"])
            x = float(bbox["x0"]) * scale_x
            y = page_height - float(bbox["bottom"]) * scale_y
            box_width = (float(bbox["x1"]) - float(bbox["x0"])) * scale_x
            box_height = (float(bbox["bottom"]) - float(bbox["top"])) * scale_y
            status, color = _item_status(item)
            overlay.saveState()
            overlay.setFillColorRGB(*color)
            overlay.setStrokeColorRGB(*color)
            overlay.setFillAlpha(0.16 if status == "CONFERIDO" else 0.42)
            overlay.setStrokeAlpha(0.85)
            overlay.setLineWidth(1.2)
            overlay.rect(x, y, box_width, box_height, stroke=1, fill=1)
            overlay.restoreState()
        overlay.save()
        overlay_stream.seek(0)
        page.merge_page(PdfReader(overlay_stream).pages[0])
        writer.add_page(page)

    for page in PdfReader(io.BytesIO(_summary_pdf(note, items))).pages:
        writer.add_page(page)
    writer.add_metadata({"/Title": f"Conferencia NF {note['number']}", "/Producer": "HL Conferencia NFe"})

    output_dir.mkdir(parents=True, exist_ok=True)
    number = "".join(character for character in str(note["number"] or note_id) if character.isalnum() or character in "-_")
    target = output_dir / f"NF_{number}_conferencia_final.pdf"
    temporary = target.with_suffix(".tmp")
    with temporary.open("wb") as stream:
        writer.write(stream)
    temporary.replace(target)
    return target


def update_item(note_id: str, item_id: int, payload: dict) -> dict:
    action = payload.get("action")
    operator = str(payload.get("operator") or "").strip()
    stamp = now()
    with DB_LOCK, connect() as db:
        item = db.execute("SELECT * FROM items WHERE id=? AND note_id=?", (item_id, note_id)).fetchone()
        if not item:
            raise KeyError(item_id)
        if action == "count":
            before = float(item["counted"])
            value = max(0, float(payload.get("value", item["counted"])))
            db.execute("UPDATE items SET counted=?,last_action_at=?,operator=? WHERE id=?", (value, stamp, operator, item_id))
        elif action == "toggle":
            before = int(item["checked"])
            value = 0 if item["checked"] else 1
            db.execute("UPDATE items SET checked=?,last_action_at=?,operator=? WHERE id=?", (value, stamp, operator, item_id))
        else:
            raise ValueError("Acao invalida")
        change = json.dumps({"before": before, "after": value}, ensure_ascii=False)
        db.execute("INSERT INTO actions(note_id,item_id,action,value,operator,created_at) VALUES(?,?,?,?,?,?)", (note_id, item_id, action, change, operator, stamp))
        if operator:
            db.execute("INSERT INTO operators(name,last_used_at) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET last_used_at=excluded.last_used_at", (operator, stamp))
    return get_note(note_id)


def _action_value(value: str) -> dict:
    try:
        parsed = json.loads(value or "")
        return parsed if isinstance(parsed, dict) else {"after": parsed}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"after": value}


def action_history(note_id: str, limit: int = 200) -> list[dict]:
    with connect() as db:
        rows = db.execute("""
            SELECT a.*, i.sku, i.description
            FROM actions a LEFT JOIN items i ON i.id=a.item_id
            WHERE a.note_id=? ORDER BY a.id DESC LIMIT ?
        """, (note_id, limit)).fetchall()
    return [{
        "id": row["id"], "sku": row["sku"] or "", "description": row["description"] or "",
        "action": row["action"], "value": _action_value(row["value"]),
        "operator": row["operator"] or "", "created_at": row["created_at"],
        "undone": bool(row["undone_at"]),
    } for row in rows]


def undo_last_action(note_id: str, operator: str) -> dict:
    stamp = now()
    with DB_LOCK, connect() as db:
        action = db.execute("""
            SELECT a.*, i.expected, i.checked, i.counted
            FROM actions a JOIN items i ON i.id=a.item_id
            WHERE a.note_id=? AND a.action IN ('toggle','count') AND a.undone_at IS NULL
            ORDER BY a.id DESC LIMIT 1
        """, (note_id,)).fetchone()
        if not action:
            raise ValueError("Nao ha conferencia ou quantidade para desfazer.")
        values = _action_value(action["value"])
        if "before" in values:
            previous = values["before"]
        elif action["action"] == "toggle":
            previous = 0 if str(values.get("after")) in ("1", "1.0", "True", "true") else 1
        else:
            older = db.execute("""
                SELECT value FROM actions WHERE note_id=? AND item_id=? AND action='count'
                AND id<? AND undone_at IS NULL ORDER BY id DESC LIMIT 1
            """, (note_id, action["item_id"], action["id"])).fetchone()
            previous = _action_value(older["value"]).get("after") if older else action["expected"]
        if action["action"] == "toggle":
            db.execute("UPDATE items SET checked=?,last_action_at=?,operator=? WHERE id=?", (int(float(previous)), stamp, operator, action["item_id"]))
        else:
            db.execute("UPDATE items SET counted=?,last_action_at=?,operator=? WHERE id=?", (float(previous), stamp, operator, action["item_id"]))
        db.execute("UPDATE actions SET undone_at=? WHERE id=?", (stamp, action["id"]))
        undo_value = json.dumps({"action_id": action["id"], "restored": previous}, ensure_ascii=False)
        db.execute("INSERT INTO actions(note_id,item_id,action,value,operator,created_at) VALUES(?,?,?,?,?,?)", (note_id, action["item_id"], "undo", undo_value, operator, stamp))
    return {"note": get_note(note_id), "undone_action": action["action"], "item_id": action["item_id"]}


def reset_note_progress(note_id: str, operator: str) -> dict:
    stamp = now()
    with DB_LOCK, connect() as db:
        note = db.execute("SELECT id FROM notes WHERE id=?", (note_id,)).fetchone()
        if not note:
            raise KeyError(note_id)
        total = db.execute("SELECT COUNT(*) total FROM items WHERE note_id=?", (note_id,)).fetchone()["total"]
        db.execute("""
            UPDATE items SET counted=expected, checked=0, printed_count=0,
                   last_action_at=?, operator=? WHERE note_id=?
        """, (stamp, operator, note_id))
        value = json.dumps({"items": total, "counted": "expected", "checked": 0, "printed_count": 0}, ensure_ascii=False)
        db.execute("INSERT INTO actions(note_id,item_id,action,value,operator,created_at) VALUES(?,NULL,'reset',?,?,?)", (note_id, value, operator, stamp))
    return {"note": get_note(note_id), "reset_items": total}


def print_item(note_id: str, item_id: int, payload: dict) -> dict:
    operator = str(payload.get("operator") or "").strip()
    dry_run = bool(payload.get("dry_run"))
    with connect() as db:
        item = db.execute("SELECT * FROM items WHERE id=? AND note_id=?", (item_id, note_id)).fetchone()
    if not item:
        raise KeyError(item_id)
    quantity = max(1, int(round(float(item["counted"]))))
    product = None
    tiny_error = ""
    try:
        product = TINY.product_by_sku(item["sku"])
    except Exception as error:
        tiny_error = str(error)
        if not dry_run:
            raise RuntimeError("Tiny nao autorizado ou indisponivel. Conclua a autorizacao aberta no navegador e tente novamente.") from error
    if not product and not dry_run:
        raise RuntimeError(f"SKU {item['sku']} nao encontrado no cadastro de produtos do Tiny.")
    description = (product or {}).get("description") or item["description"] or item["sku"]
    location = (product or {}).get("location") or ""
    pdf = create_temp_label(description, item["sku"], location, quantity)
    printer = str(payload.get("printer") or setting("printer")).strip()
    if not printer:
        raise RuntimeError("Escolha uma impressora nas configuracoes.")
    result = {"printer": printer, "quantity": quantity, "preview": f"/api/labels/{pdf.name}", "tiny_error": tiny_error}
    if not dry_run:
        result.update(print_pdf(pdf, printer, quantity))
    if not dry_run:
        with DB_LOCK, connect() as db:
            stamp = now()
            db.execute("UPDATE items SET printed_count=printed_count+?,last_action_at=?,operator=? WHERE id=?", (quantity, stamp, operator, item_id))
            db.execute("INSERT INTO actions(note_id,item_id,action,value,operator,created_at) VALUES(?,?,?,?,?,?)", (note_id, item_id, "print", str(quantity), operator, stamp))
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = "HLConferencia/1.0"

    def log_message(self, fmt, *args):
        return

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path, download_name: str = "", inline: bool = True):
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        if download_name:
            disposition = "inline" if inline else "attachment"
            self.send_header("Content-Disposition", f'{disposition}; filename="{download_name}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def payload(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            if path == "/api/health":
                return self.send_json({"ok": True, "version": APP_VERSION, "instance": SERVER_INSTANCE, "watch_dir": setting("watch_dir"), "time": now()})
            if path == "/api/notes":
                return self.send_json({"notes": list_notes()})
            if path == "/api/settings":
                printers, default = list_printers()
                with connect() as db:
                    operators = [dict(row) for row in db.execute("SELECT * FROM operators ORDER BY COALESCE(last_used_at,'') DESC,name")]
                return self.send_json({"watch_dir": setting("watch_dir"), "printer": setting("printer", default), "printers": printers, "operators": operators})
            if path == "/api/tiny":
                try:
                    TINY.ensure_token()
                    return self.send_json({"authorized": True})
                except Exception as error:
                    return self.send_json({"authorized": False, "authorize_url": "http://127.0.0.1:8766/autorizar-tiny", "error": str(error)})
            if path.startswith("/api/notes/"):
                parts = path.strip("/").split("/")
                note_id = parts[2]
                if len(parts) == 3:
                    note = get_note(note_id)
                    return self.send_json(note or {"error": "Nota nao encontrada"}, 200 if note else 404)
                if len(parts) == 4 and parts[3] == "history":
                    return self.send_json({"history": action_history(note_id)})
                if len(parts) == 4 and parts[3] == "final.pdf":
                    target = create_final_pdf(note_id)
                    download = parse_qs(parsed.query).get("download") == ["1"]
                    return self.send_file(target, target.name, inline=not download)
                if len(parts) == 5 and parts[3] == "pages":
                    return self.send_file(render_page(note_id, int(parts[4])))
            if path.startswith("/api/labels/"):
                target = Path(os.getenv("TEMP", ".")) / "HL_Conferencia_NFe" / "labels" / Path(path).name
                if target.is_file():
                    return self.send_file(target)
            static_path = STATIC_DIR / ("index.html" if path == "/" else path.lstrip("/"))
            if static_path.is_file() and STATIC_DIR in static_path.resolve().parents:
                return self.send_file(static_path)
            self.send_json({"error": "Nao encontrado"}, 404)
        except Exception as error:
            traceback.print_exc()
            self.send_json({"error": str(error)}, 500)

    def do_POST(self):
        try:
            path = unquote(urlparse(self.path).path)
            payload = self.payload()
            if path == "/api/restart":
                RESTART_REQUESTED.set()
                self.send_json({"ok": True, "instance": SERVER_INSTANCE})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if path == "/api/scan":
                return self.send_json(scan_folder())
            if path == "/api/settings":
                if "watch_dir" in payload:
                    save_setting("watch_dir", str(payload["watch_dir"]))
                if "printer" in payload:
                    save_setting("printer", str(payload["printer"]))
                return self.send_json({"ok": True})
            if path == "/api/operators":
                name = str(payload.get("name") or "").strip()
                if not name:
                    raise ValueError("Informe o nome do operador")
                with connect() as db:
                    db.execute("INSERT OR IGNORE INTO operators(name,last_used_at) VALUES(?,?)", (name, now()))
                return self.send_json({"ok": True})
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[0:2] == ["api", "notes"] and parts[3] == "reset":
                return self.send_json(reset_note_progress(parts[2], str(payload.get("operator") or "").strip()))
            if len(parts) == 4 and parts[0:2] == ["api", "notes"] and parts[3] == "undo":
                return self.send_json(undo_last_action(parts[2], str(payload.get("operator") or "").strip()))
            if len(parts) == 5 and parts[0:2] == ["api", "notes"] and parts[3] == "items":
                note_id, item_id = parts[2], int(parts[4])
                if payload.get("action") == "print":
                    return self.send_json(print_item(note_id, item_id, payload))
                return self.send_json(update_item(note_id, item_id, payload))
            self.send_json({"error": "Nao encontrado"}, 404)
        except KeyError:
            self.send_json({"error": "Item nao encontrado"}, 404)
        except Exception as error:
            traceback.print_exc()
            self.send_json({"error": str(error)}, 500)


class TinyAuthHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[TINY AUTH] {fmt % args}")

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/autorizar-tiny":
            self.send_response(302)
            self.send_header("Location", TINY.authorization_url())
            self.end_headers()
            return
        if parsed.path == "/callback":
            query = parse_qs(parsed.query)
            code = (query.get("code") or [""])[0]
            error = (query.get("error_description") or query.get("error") or [""])[0]
            try:
                if error or not code:
                    raise RuntimeError(error or "Callback do Tiny sem codigo")
                TINY.exchange_code(code)
                message = "Autorizacao Tiny concluida. Pode fechar esta aba."
                status = 200
            except Exception as exc:
                message = f"Falha na autorizacao Tiny: {exc}"
                status = 500
            body = message.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


def start_auth_bridge() -> bool:
    global AUTH_BRIDGE
    try:
        AUTH_BRIDGE = AppServer((HOST, 8766), TinyAuthHandler)
        threading.Thread(target=AUTH_BRIDGE.serve_forever, daemon=True).start()
        return True
    except OSError:
        return False


def prepare_print_runtime() -> None:
    try:
        ensure_print_runtime()
    except Exception:
        traceback.print_exc()


def schedule_restart() -> None:
    if getattr(sys, "frozen", False):
        environment = os.environ.copy()
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        creation_flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(
            [sys.executable, *sys.argv[1:]],
            cwd=Path(sys.executable).resolve().parent,
            env=environment,
            creationflags=creation_flags,
            close_fds=True,
        )
        return
    command = [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]
    subprocess.Popen(command, cwd=Path(__file__).resolve().parent, close_fds=True)


def main(open_browser: bool = True, start_auth: bool = True) -> None:
    init_db()
    threading.Thread(target=prepare_print_runtime, daemon=True).start()
    scan_folder()
    threading.Thread(target=watcher, daemon=True).start()
    server = AppServer((HOST, PORT), Handler)
    bridge_started = start_auth_bridge() if start_auth else False
    url = f"http://{HOST}:{PORT}/"
    print(f"HL Conferencia NFe em {url}")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    if start_auth and os.getenv("HL_SKIP_TINY_AUTH") != "1":
        try:
            TINY.ensure_token()
        except Exception:
            if bridge_started:
                threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:8766/autorizar-tiny")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if AUTH_BRIDGE:
            AUTH_BRIDGE.shutdown()
            AUTH_BRIDGE.server_close()
        if RESTART_REQUESTED.is_set():
            schedule_restart()


if __name__ == "__main__":
    main()
