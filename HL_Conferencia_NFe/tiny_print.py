from __future__ import annotations

import json
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

from reportlab.lib.colors import black
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas


CM = 72 / 2.54
CSS_TO_PT = 72 / 96
SUMATRA_URL = "https://www.sumatrapdfreader.org/dl/rel/3.6.1/SumatraPDF-3.6.1-64.zip"
SUMATRA_SHA256 = "719F689B34F47BE8CA105CE8484948474DAFDE0E106BAB599E4A89326070C3D0"
LABEL_PRINT_SETTINGS = "disable-auto-rotation,fit,paper=40mm x 25mm"
RUNTIME_LOCK = threading.Lock()
PRINT_LOCK = threading.Lock()
FONT_LOCK = threading.Lock()
LABEL_FONTS: tuple[str, str] | None = None
DEFAULT_MODEL = {
    "source": "Tiny ERP - Configuracoes de etiquetas / Produto",
    "captured_at": "2026-09-18",
    "name": "Etiqueta de produto",
    "width_cm": 4.0,
    "height_cm": 2.5,
    "description": {"top_px": 1.98438, "left_px": 1, "width_px": 150, "height_px": 39, "font_pt": 8},
    "sku": {"top_px": 43.9688, "left_px": 0, "width_px": 151, "height_px": 29, "font_pt": 14},
    "location": {"top_px": 72.9844, "left_px": 1, "width_px": 150, "height_px": 29, "font_pt": 10},
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _install_sumatra_archive(archive_data: bytes, target: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(archive_data)) as archive:
        member = next(name for name in archive.namelist() if name.lower().endswith(".exe"))
        temporary = target.with_suffix(".tmp")
        with archive.open(member) as source, temporary.open("wb") as destination:
            shutil.copyfileobj(source, destination)
    if _sha256(temporary) != SUMATRA_SHA256:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("o arquivo de impressao nao passou na verificacao de integridade")
    temporary.replace(target)


def ensure_print_runtime() -> Path:
    runtime_dir = Path(os.getenv("LOCALAPPDATA", Path.home())) / "HL_Conferencia_NFe" / "runtime"
    target = runtime_dir / "SumatraPDF.exe"
    with RUNTIME_LOCK:
        if target.is_file() and _sha256(target) == SUMATRA_SHA256:
            return target

        runtime_dir.mkdir(parents=True, exist_ok=True)
        bundle_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        for bundled_archive in (
            bundle_dir / "SumatraPDF-3.6.1-64.zip",
            bundle_dir / "assets" / "SumatraPDF-3.6.1-64.zip",
        ):
            if bundled_archive.is_file():
                _install_sumatra_archive(bundled_archive.read_bytes(), target)
                return target

        candidates = [
            Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "Label Print Agent" / "SumatraPDF.exe",
            Path.cwd() / "SumatraPDF.exe",
        ]
        for candidate in candidates:
            if candidate.is_file() and _sha256(candidate) == SUMATRA_SHA256:
                temporary = target.with_suffix(".tmp")
                shutil.copy2(candidate, temporary)
                temporary.replace(target)
                return target

        try:
            request = Request(SUMATRA_URL, headers={"User-Agent": "HL-Conferencia-NFe/1.0"})
            with urlopen(request, timeout=90) as response:
                _install_sumatra_archive(response.read(), target)
            return target
        except Exception as error:
            target.unlink(missing_ok=True)
            raise RuntimeError(
                "Nao foi possivel preparar o modulo de impressao. Verifique a internet e tente novamente. "
                f"Detalhe: {error}"
            ) from error


def list_printers() -> tuple[list[str], str]:
    try:
        import win32print

        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        names = sorted({item[2] for item in win32print.EnumPrinters(flags)})
        default = win32print.GetDefaultPrinter()
        return names, default
    except Exception:
        return [], ""


def _label_fonts() -> tuple[str, str]:
    global LABEL_FONTS
    with FONT_LOCK:
        if LABEL_FONTS:
            return LABEL_FONTS
        fonts_dir = Path(os.getenv("WINDIR", r"C:\Windows")) / "Fonts"
        regular = fonts_dir / "arial.ttf"
        bold = fonts_dir / "arialbd.ttf"
        if regular.is_file() and bold.is_file():
            pdfmetrics.registerFont(TTFont("HLArial", str(regular)))
            pdfmetrics.registerFont(TTFont("HLArial-Bold", str(bold)))
            LABEL_FONTS = ("HLArial", "HLArial-Bold")
        else:
            LABEL_FONTS = ("Helvetica", "Helvetica-Bold")
        return LABEL_FONTS


def _fit_text(canvas: Canvas, text: str, max_width: float, max_height: float, font: str, size: float) -> list[tuple[str, float]]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        attempt = f"{current} {word}".strip()
        if current and stringWidth(attempt, font, size) > max_width:
            lines.append(current)
            current = word
        else:
            current = attempt
    if current:
        lines.append(current)
    line_height = size * 1.12
    max_lines = max(1, int(max_height // line_height))
    lines = lines[:max_lines]
    if len(lines) == max_lines and words and " ".join(lines) != text:
        while lines[-1] and stringWidth(lines[-1] + "...", font, size) > max_width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "..."
    return [(line, line_height) for line in lines]


def render_tiny_label_pdf(target: Path, description: str, sku: str, location: str, quantity: int) -> None:
    model = DEFAULT_MODEL
    width = model["width_cm"] * CM
    height = model["height_cm"] * CM
    canvas = Canvas(str(target), pagesize=(width, height), pageCompression=1)
    canvas.setFillColor(black)

    def centered(text: str, config: dict, font: str) -> None:
        size = float(config["font_pt"])
        top = config["top_px"] * CSS_TO_PT
        left = config["left_px"] * CSS_TO_PT
        box_width = config["width_px"] * CSS_TO_PT
        box_height = config["height_px"] * CSS_TO_PT
        lines = _fit_text(canvas, text, box_width, box_height, font, size)
        y = height - top - size
        for line, line_height in lines:
            canvas.setFont(font, size)
            canvas.drawCentredString(left + box_width / 2, y, line)
            y -= line_height

    regular_font, bold_font = _label_fonts()
    centered(description, model["description"], regular_font)
    centered(sku, model["sku"], bold_font)
    centered(f"LOCAL: {location}".strip(), model["location"], regular_font)
    canvas.showPage()
    canvas.save()


def print_pdf(pdf_path: Path, printer: str, quantity: int = 1) -> dict:
    executable = ensure_print_runtime()
    copies = max(1, int(quantity))
    settings = f"{copies}x,{LABEL_PRINT_SETTINGS}" if copies > 1 else LABEL_PRINT_SETTINGS
    command = [
        str(executable),
        "-print-to",
        printer,
        "-print-settings",
        settings,
        "-silent",
        str(pdf_path),
    ]
    with PRINT_LOCK:
        completed = subprocess.run(
            command,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=180,
            check=False,
        )
    if completed.returncode:
        raise RuntimeError(f"Falha ao enviar etiquetas para a impressora (codigo {completed.returncode}).")

    def cleanup() -> None:
        time.sleep(90)
        try:
            pdf_path.unlink(missing_ok=True)
        except OSError:
            pass

    threading.Thread(target=cleanup, daemon=True).start()
    return {"printer": printer, "file": str(pdf_path), "runtime": str(executable), "copies": copies}


def create_temp_label(description: str, sku: str, location: str, quantity: int) -> Path:
    folder = Path(tempfile.gettempdir()) / "HL_Conferencia_NFe" / "labels"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"tiny_{sku}_{int(time.time() * 1000)}.pdf"
    render_tiny_label_pdf(target, description, sku, location, quantity)
    return target
