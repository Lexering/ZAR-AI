"""
LibreOffice + PDF tools for ZAR.

- Convert between Word / Excel / PowerPoint / OpenDocument / PDF / HTML / TXT / CSV / images
- PDF -> Word (editable) with pdf2docx, falling back to LibreOffice
- Merge, split, extract pages, rotate, compress, protect, unlock PDFs, PDF <-> images
- Open a file in LibreOffice to edit it, print it, install LibreOffice

Results are saved in Documents\\ZAR and opened when ready.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

try:
    from actions import documents as _docs
except Exception:  # pragma: no cover
    import documents as _docs  # type: ignore

EXTRA_EXTS = {".doc", ".odt", ".xlsx", ".xls", ".ods", ".ppt", ".pptx", ".odp", ".html", ".htm",
              ".png", ".jpg", ".jpeg", ".epub", ".rtf", ".csv", ".txt", ".pdf", ".docx"}
_docs.DOC_EXTS.update(EXTRA_EXTS)       # lets use_document / find_document see these files too

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
LO_FORMATS = {"pdf", "docx", "doc", "odt", "rtf", "txt", "html", "xlsx", "xls", "ods", "csv",
              "pptx", "ppt", "odp", "epub", "png", "jpg"}
_INSTALLING = threading.Event()


# ─────────────────────────── helpers ───────────────────────────
def soffice_path() -> str | None:
    for p in (shutil.which("soffice"), shutil.which("soffice.exe"),
              r"C:\Program Files\LibreOffice\program\soffice.exe",
              r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
              os.path.expandvars(r"%LOCALAPPDATA%\Programs\LibreOffice\program\soffice.exe")):
        if p and Path(p).exists():
            return str(p)
    return None


_SPEAK = None


def _need_lo() -> str:
    exe = soffice_path()
    if not exe:
        msg = install(_SPEAK)          # install it automatically the first time it's needed
        raise RuntimeError(f"LibreOffice wasn't installed. {msg} Ask me again when it's ready.")
    return exe


def _resolve(file: str | None, current: str | None = None) -> Path:
    f = (file or "").strip().strip('"')
    if not f and current:
        return Path(current)
    found = _docs.find_document(f) if f else (Path(current) if current else _docs.find_document(""))
    if not found or not Path(found).exists():
        raise FileNotFoundError(f"I can't find a file matching '{file}'.")
    return Path(found)


def _resolve_many(files) -> list[Path]:
    if isinstance(files, str):
        p = Path(files.strip().strip('"'))
        if p.is_dir():
            return sorted(x for x in p.iterdir() if x.suffix.lower() == ".pdf")
        files = [x for x in re.split(r"[;,\n]| y | and ", files) if x.strip()]
    return [_resolve(x) for x in files]


def _out(stem: str, ext: str) -> Path:
    return _docs.unique_path(_docs.output_dir(), _docs.safe_name(stem, "file"), ext)


def _pages(spec: str, total: int) -> list[int]:
    """'1-3,5,8-' -> zero-based page indexes."""
    spec = (spec or "").replace(" ", "")
    if not spec:
        return list(range(total))
    out: list[int] = []
    for part in spec.split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            a = int(a) if a else 1
            b = int(b) if b else total
            out.extend(range(a - 1, min(b, total)))
        else:
            n = int(part)
            if 1 <= n <= total:
                out.append(n - 1)
    return out


def _lo_convert(src: Path, fmt: str, outdir: Path, infilter: str | None = None) -> Path:
    exe = _need_lo()
    profile = Path(tempfile.gettempdir()) / "alberto_lo_profile"
    cmd = [exe, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--norestore", "--nologo"]
    if infilter:
        cmd.append(f"--infilter={infilter}")
    cmd += ["--convert-to", fmt, "--outdir", str(outdir), str(src)]
    flags = 0x08000000 if os.name == "nt" else 0          # CREATE_NO_WINDOW
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=240, creationflags=flags)
    produced = outdir / f"{src.stem}.{fmt.split(':')[0]}"
    if not produced.exists():
        raise RuntimeError(f"LibreOffice couldn't convert it. {(r.stderr or r.stdout or '').strip()[:200]}")
    return produced


def _fitz():
    import fitz  # PyMuPDF
    return fitz


# ─────────────────────────── actions ───────────────────────────
def convert(src: Path, to: str) -> Path:
    to = to.lower().lstrip(".")
    if to in ("word",): to = "docx"
    if to in ("excel",): to = "xlsx"
    if to in ("powerpoint", "ppt"): to = "pptx"
    if to in ("image", "images", "jpeg"): to = "png" if to != "jpeg" else "jpg"
    ext = src.suffix.lower()

    if ext == ".pdf" and to in ("png", "jpg"):
        return pdf_to_images(src, to)
    if ext in IMAGE_EXTS and to == "pdf":
        return images_to_pdf([src])
    if ext == ".pdf" and to in ("docx", "doc", "odt", "rtf"):
        out = _out(src.stem, ".docx")
        try:
            from pdf2docx import Converter
            cv = Converter(str(src)); cv.convert(str(out)); cv.close()
            if to == "docx":
                return out
            src = out                                  # then docx -> odt/rtf/doc via LibreOffice
        except ImportError:
            with tempfile.TemporaryDirectory() as td:
                tmp = _lo_convert(src, "docx:MS Word 2007 XML", Path(td), infilter="writer_pdf_import")
                shutil.move(str(tmp), out)
            if to == "docx":
                return out
            src = out
    if ext == ".pdf" and to == "txt":
        doc = _fitz().open(str(src))
        out = _out(src.stem, ".txt")
        out.write_text("\n\n".join(p.get_text() for p in doc), encoding="utf-8")
        return out
    if to not in LO_FORMATS:
        raise ValueError(f"I can't convert to '{to}'. Try pdf, docx, xlsx, pptx, odt, html, txt, csv or png.")
    with tempfile.TemporaryDirectory() as td:
        tmp = _lo_convert(src, to, Path(td))
        out = _out(src.stem, tmp.suffix)
        shutil.move(str(tmp), out)
    return out


def merge_pdfs(files: list[Path], name: str = "") -> Path:
    fitz = _fitz()
    if len(files) < 2:
        raise ValueError("I need at least two files to merge.")
    merged = fitz.open()
    for f in files:
        if f.suffix.lower() != ".pdf":
            f = convert(f, "pdf")
        with fitz.open(str(f)) as d:
            merged.insert_pdf(d)
    out = _out(name or f"{files[0].stem}_merged", ".pdf")
    merged.save(str(out), garbage=3, deflate=True)
    return out


def extract_pages(src: Path, pages: str) -> Path:
    fitz = _fitz()
    doc = fitz.open(str(src))
    idx = _pages(pages, doc.page_count)
    if not idx:
        raise ValueError("Those pages don't exist in the PDF.")
    new = fitz.open()
    for i in idx:
        new.insert_pdf(doc, from_page=i, to_page=i)
    out = _out(f"{src.stem}_pages_{pages or 'all'}", ".pdf")
    new.save(str(out), garbage=3, deflate=True)
    return out


def split_pdf(src: Path) -> Path:
    fitz = _fitz()
    doc = fitz.open(str(src))
    folder = _docs.output_dir() / _docs.safe_name(f"{src.stem}_split")
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(doc.page_count):
        page = fitz.open()
        page.insert_pdf(doc, from_page=i, to_page=i)
        page.save(str(folder / f"{src.stem}_p{i + 1:03d}.pdf"))
    return folder


def rotate_pdf(src: Path, degrees: int = 90, pages: str = "") -> Path:
    fitz = _fitz()
    doc = fitz.open(str(src))
    for i in _pages(pages, doc.page_count):
        p = doc[i]
        p.set_rotation((p.rotation + int(degrees)) % 360)
    out = _out(f"{src.stem}_rotated", ".pdf")
    doc.save(str(out))
    return out


def compress_pdf(src: Path) -> Path:
    fitz = _fitz()
    doc = fitz.open(str(src))
    try:                                   # PyMuPDF >= 1.24: downsample images
        doc.rewrite_images(dpi_threshold=150, dpi_target=110, quality=70)
    except Exception:
        pass
    out = _out(f"{src.stem}_small", ".pdf")
    doc.save(str(out), garbage=4, deflate=True, clean=True)
    return out


def protect_pdf(src: Path, password: str) -> Path:
    if not password:
        raise ValueError("Tell me the password to use.")
    fitz = _fitz()
    doc = fitz.open(str(src))
    out = _out(f"{src.stem}_protected", ".pdf")
    doc.save(str(out), encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw=password, user_pw=password,
             permissions=int(fitz.PDF_PERM_PRINT | fitz.PDF_PERM_COPY))
    return out


def unlock_pdf(src: Path, password: str) -> Path:
    fitz = _fitz()
    doc = fitz.open(str(src))
    if doc.needs_pass and not doc.authenticate(password or ""):
        raise ValueError("Wrong password for that PDF.")
    out = _out(f"{src.stem}_unlocked", ".pdf")
    doc.save(str(out))
    return out


def pdf_to_images(src: Path, fmt: str = "png", dpi: int = 150) -> Path:
    fitz = _fitz()
    doc = fitz.open(str(src))
    folder = _docs.output_dir() / _docs.safe_name(f"{src.stem}_images")
    folder.mkdir(parents=True, exist_ok=True)
    for i, page in enumerate(doc):
        page.get_pixmap(dpi=dpi).save(str(folder / f"{src.stem}_p{i + 1:03d}.{fmt}"))
    return folder


def images_to_pdf(files: list[Path], name: str = "") -> Path:
    fitz = _fitz()
    new = fitz.open()
    for f in files:
        with fitz.open(str(f)) as img:
            rect = img[0].rect
            pdf_bytes = img.convert_to_pdf()
        with fitz.open("pdf", pdf_bytes) as one:
            page = new.new_page(width=rect.width, height=rect.height)
            page.show_pdf_page(rect, one, 0)
    out = _out(name or f"{files[0].stem}_images", ".pdf")
    new.save(str(out), garbage=3, deflate=True)
    return out


def pdf_info(src: Path) -> str:
    fitz = _fitz()
    doc = fitz.open(str(src))
    words = sum(len(p.get_text().split()) for p in doc) if not doc.needs_pass else 0
    size = src.stat().st_size / 1024 / 1024
    return (f"{src.name}: {doc.page_count} pages, {size:.1f} MB, about {words} words"
            f"{', password protected' if doc.needs_pass else ''}.")


def open_in_libreoffice(src: Path) -> str:
    exe = _need_lo()
    subprocess.Popen([exe, "--norestore", str(src)])
    return f"Opened {src.name} in LibreOffice."


def print_file(src: Path) -> str:
    exe = _need_lo()
    subprocess.Popen([exe, "--headless", "-p", str(src)])
    return f"Sent {src.name} to the default printer."


def install(speak=None) -> str:
    if soffice_path():
        return "LibreOffice is already installed."
    if _INSTALLING.is_set():
        return "I'm already installing LibreOffice, give it a few minutes."
    if not shutil.which("winget"):
        import webbrowser
        webbrowser.open("https://www.libreoffice.org/download/download/")
        return "winget isn't available, so I opened the LibreOffice download page."

    def _run():
        _INSTALLING.set()
        try:
            subprocess.run(["winget", "install", "-e", "--id", "TheDocumentFoundation.LibreOffice",
                            "--silent", "--accept-package-agreements", "--accept-source-agreements"],
                           capture_output=True, text=True, timeout=1800,
                           creationflags=0x08000000 if os.name == "nt" else 0)
            msg = ("LibreOffice is installed and ready." if soffice_path()
                   else "The LibreOffice install didn't finish. Try again or install it from libreoffice.org.")
        except Exception as e:
            msg = f"The LibreOffice install failed: {e}"
        finally:
            _INSTALLING.clear()
        if speak:
            speak(f"[SISTEMA] Aviso: {msg} Díselo al usuario en una frase.")

    threading.Thread(target=_run, daemon=True).start()
    return "Installing LibreOffice in the background. It takes a few minutes; I'll tell you when it's done."


# ─────────────────────────── tool entry point ───────────────────────────
def office_tool(parameters: dict, player=None, speak=None, current_file: str | None = None) -> str:
    global _SPEAK
    _SPEAK = speak
    a = parameters or {}
    action = (a.get("action") or "").lower().strip()
    file = a.get("file", "")
    try:
        if action == "install":
            return install(speak)
        if action == "status":
            p = soffice_path()
            return f"LibreOffice is installed ({p})." if p else "LibreOffice is not installed."
        if action == "merge":
            out = merge_pdfs(_resolve_many(a.get("files") or file), a.get("name", ""))
        elif action == "images_to_pdf":
            out = images_to_pdf(_resolve_many(a.get("files") or file), a.get("name", ""))
        else:
            src = _resolve(file, current_file)
            if action == "convert":
                out = convert(src, a.get("to", "pdf"))
            elif action == "extract_pages":
                out = extract_pages(src, a.get("pages", ""))
            elif action == "split":
                out = split_pdf(src)
            elif action == "rotate":
                out = rotate_pdf(src, int(a.get("degrees", 90) or 90), a.get("pages", ""))
            elif action == "compress":
                out = compress_pdf(src)
            elif action == "protect":
                out = protect_pdf(src, a.get("password", ""))
            elif action == "unlock":
                out = unlock_pdf(src, a.get("password", ""))
            elif action == "to_images":
                out = pdf_to_images(src, a.get("to", "png") if a.get("to") in ("png", "jpg") else "png")
            elif action == "info":
                return pdf_info(src)
            elif action == "open":
                return open_in_libreoffice(src)
            elif action == "print":
                return print_file(src)
            else:
                return f"Unknown action '{action}'."
        if a.get("open_result", True):
            _docs.open_file(out)
        return f"Done. Saved to {out}"
    except Exception as e:
        return f"Office/PDF error: {e}"


TOOL_DECLARATION = {
    "name": "office_pdf",
    "description": (
        "LibreOffice and PDF tools. Use for ANY request to convert documents (Word, Excel, PowerPoint, "
        "OpenDocument, PDF, HTML, TXT, CSV, images), turn a PDF into editable Word, merge / join PDFs, "
        "split a PDF, extract or rotate pages, compress a PDF, add or remove a PDF password, PDF to images "
        "or images to PDF, PDF info (pages, words), open a file in LibreOffice to edit it, print a file, "
        "or install LibreOffice. If the user doesn't name a file, it uses the current document. "
        "Results are saved in Documents\\ZAR and opened."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description":
                       "convert | merge | split | extract_pages | rotate | compress | protect | unlock | "
                       "to_images | images_to_pdf | info | open | print | install | status"},
            "file": {"type": "STRING", "description": "Path, words of the file name, or 'último'. Empty = current document"},
            "files": {"type": "STRING", "description": "For merge / images_to_pdf: file names or paths separated by commas, or a folder"},
            "to": {"type": "STRING", "description": "Target format for convert: pdf, docx, odt, xlsx, ods, csv, pptx, odp, html, txt, rtf, epub, png, jpg"},
            "pages": {"type": "STRING", "description": "Pages like '1-3,5' (extract_pages / rotate)"},
            "degrees": {"type": "INTEGER", "description": "Rotation: 90, 180 or 270"},
            "password": {"type": "STRING", "description": "Password for protect / unlock"},
            "name": {"type": "STRING", "description": "Optional output file name"},
        },
        "required": ["action"],
    },
}
