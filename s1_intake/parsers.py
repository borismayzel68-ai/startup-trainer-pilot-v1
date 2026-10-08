"""Безопасная загрузка файлов из Tilda и парсинг PDF (питч-дек, pypdf — как в тренажёре) / XLSX (финмодель, openpyxl).

Защита:
- только https и только хосты из FILE_HOST_ALLOWLIST (защита от SSRF: ссылку в
  форму может вписать кто угодно);
- редиректы проверяются вручную, каждый хоп — снова по allowlist;
- лимит размера при стриминге, тип определяется по сигнатуре, а не по расширению;
- XLSX: проверка распакованного размера (zip-bomb), read_only-режим openpyxl.
"""
from __future__ import annotations

import io
import logging
import re
import zipfile
from datetime import date, datetime
from urllib.parse import urljoin, urlparse

import httpx
import openpyxl
from pypdf import PdfReader

from .config import Settings
from .models import FileRef, ParsedDoc

log = logging.getLogger(__name__)

SOURCE_BY_KIND = {"pitch_deck": "deck", "fin_model": "model", "evidence": "evidence"}
MAX_UNZIPPED_BYTES = 200 * 1024 * 1024
XLSX_MAX_ROWS_PER_SHEET = 80
XLSX_MAX_COLS = 24

# Канонические метрики финмодели: ключ -> regex по подписи строки (RU/EN)
METRIC_PATTERNS: dict[str, re.Pattern] = {
    "revenue": re.compile(r"\b(revenue|sales|turnover)\b|выручк|доход", re.I),
    "mrr_arr": re.compile(r"\b(mrr|arr)\b", re.I),
    "gross_margin": re.compile(r"gross\s*(margin|profit)|валов", re.I),
    "ebitda": re.compile(r"\bebitda\b", re.I),
    "burn": re.compile(r"\bburn\b|net\s*cash\s*flow|денежный\s*поток|отток", re.I),
    "cash": re.compile(r"\bcash\b(?!\s*flow)|остаток\s*(денежн|средств)|денежные\s*средства", re.I),
    "headcount": re.compile(r"head\s*count|\bfte\b|численност|штат", re.I),
    "customers": re.compile(r"\bcustomers?\b|\bclients?\b|клиент|пользовател|\busers?\b", re.I),
    "cac": re.compile(r"\bcac\b", re.I),
    "ltv": re.compile(r"\bltv\b|\bclv\b", re.I),
}


class FileRejected(Exception):
    pass


# ------------------------------------------------------------- download ----
def _host_allowed(url: str, allowlist: list[str]) -> bool:
    p = urlparse(url)
    if p.scheme != "https" or not p.hostname:
        return False
    host = p.hostname.lower()
    return any(host == a or host.endswith("." + a) for a in allowlist)


def download(url: str, settings: Settings, client: httpx.Client | None = None) -> tuple[bytes, str]:
    """Возвращает (bytes, final_url). Бросает FileRejected."""
    limit = settings.max_file_mb * 1024 * 1024
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(30.0), follow_redirects=False)
    try:
        current = url
        for _ in range(4):  # исходный запрос + до 3 редиректов
            if not _host_allowed(current, settings.file_host_allowlist):
                raise FileRejected(f"host not in allowlist: {urlparse(current).hostname}")
            with client.stream("GET", current) as r:
                if r.status_code in (301, 302, 303, 307, 308):
                    current = urljoin(current, r.headers.get("location", ""))
                    continue
                if r.status_code != 200:
                    raise FileRejected(f"HTTP {r.status_code}")
                declared = int(r.headers.get("content-length") or 0)
                if declared > limit:
                    raise FileRejected(f"file too large: {declared} bytes")
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > limit:
                        raise FileRejected(f"file exceeds {settings.max_file_mb} MB")
                return bytes(buf), current
        raise FileRejected("too many redirects")
    except httpx.HTTPError as e:
        raise FileRejected(f"download error: {e.__class__.__name__}") from e
    finally:
        if own:
            client.close()


def sniff(data: bytes) -> str:
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = set(z.namelist())
                if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED_BYTES:
                    return "zipbomb"
                if "xl/workbook.xml" in names:
                    return "xlsx"
        except zipfile.BadZipFile:
            return "unknown"
    return "unknown"


# ---------------------------------------------------------------- parse ----
def parse_pdf(data: bytes, max_chars: int) -> tuple[str, int, list[str]]:
    warnings: list[str] = []
    parts = []
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            reader.decrypt("")  # пустой пароль — частый случай «защищённых» деков
        except Exception as e:  # noqa: BLE001
            raise FileRejected("PDF зашифрован паролем") from e
    n = len(reader.pages)
    for i, page in enumerate(reader.pages, 1):
        t = (page.extract_text() or "").strip()
        parts.append(f"[Слайд/стр. {i}]\n{t}" if t else f"[Слайд/стр. {i}] (текст не извлечён)")
    text = "\n\n".join(parts)
    extracted = sum(len(p) for p in parts) - n * 20
    if n and extracted / n < 80:
        warnings.append(
            "Мало извлекаемого текста — вероятно, дек экспортирован картинками. "
            "Нужен OCR или текстовый PDF, иначе оценка по деку неполная."
        )
    if len(text) > max_chars:
        text = text[:max_chars]
        warnings.append(f"Текст дека усечён до {max_chars} символов.")
    return text, n, warnings


def _cell_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    if isinstance(v, (datetime, date)):
        return v.strftime("%Y-%m")
    return str(v).strip().replace("\n", " ")[:80]


def parse_xlsx(data: bytes, max_chars: int) -> tuple[str, dict[str, list], list[str], int]:
    warnings: list[str] = []
    metrics: dict[str, list] = {}
    lines: list[str] = []
    formula_cells_without_cache = 0
    formula_cells = 0

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    wb_f = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=False)
    try:
        for ws in wb.worksheets:
            lines.append(f"### Лист: {ws.title}")
            ws_f = wb_f[ws.title]
            for r_idx, (row, row_f) in enumerate(
                zip(
                    ws.iter_rows(max_row=XLSX_MAX_ROWS_PER_SHEET, max_col=XLSX_MAX_COLS, values_only=True),
                    ws_f.iter_rows(max_row=XLSX_MAX_ROWS_PER_SHEET, max_col=XLSX_MAX_COLS, values_only=True),
                ),
                start=1,
            ):
                for v, vf in zip(row, row_f):
                    if isinstance(vf, str) and vf.startswith("="):
                        formula_cells += 1
                    if v is None and isinstance(vf, str) and vf.startswith("="):
                        formula_cells_without_cache += 1
                cells = [_cell_str(v) for v in row]
                if not any(cells):
                    continue
                lines.append(f"R{r_idx}: " + " | ".join(cells).rstrip(" |"))

                label = next((c for c in cells if c and not _is_number(c)), "")
                nums = [v for v in row if isinstance(v, (int, float)) and not isinstance(v, bool)]
                if label and len(nums) >= 2:
                    for key, pat in METRIC_PATTERNS.items():
                        if pat.search(label):
                            metrics.setdefault(key, []).append(
                                {"sheet": ws.title, "row": r_idx, "label": label, "values": nums[:36]}
                            )
                            break
    finally:
        wb.close()
        wb_f.close()

    if formula_cells_without_cache > 10:
        warnings.append(
            f"{formula_cells_without_cache} формул без сохранённых значений — файл сохранён не из Excel "
            "или без пересчёта. Цифры модели могут быть неполными; попросить фаундера пересохранить в Excel."
        )
    if not metrics:
        warnings.append("Не найдено ни одной стандартной метрики (выручка, burn, cash…) по подписям строк.")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars]
        warnings.append(f"Дамп финмодели усечён до {max_chars} символов.")
    return text, metrics, warnings, formula_cells


def _is_number(s: str) -> bool:
    try:
        float(s.replace(",", "").replace(" ", "").replace("%", ""))
        return True
    except ValueError:
        return False


# -------------------------------------------------------------- facade ----
def fetch_and_parse(ref: FileRef, settings: Settings, client: httpx.Client | None = None) -> ParsedDoc:
    name = urlparse(ref.url).path.rstrip("/").split("/")[-1] or ref.url
    doc = ParsedDoc(kind=ref.kind, source=SOURCE_BY_KIND[ref.kind], name=name, url=ref.url)
    try:
        data, _ = download(ref.url, settings, client)
        ftype = sniff(data)
        if ftype == "pdf":
            doc.text, doc.pages, doc.warnings = parse_pdf(data, settings.max_doc_chars)
        elif ftype == "xlsx":
            doc.text, doc.metrics, doc.warnings, doc.formula_cells = parse_xlsx(data, settings.max_doc_chars)
        elif ftype == "zipbomb":
            raise FileRejected("archive expands beyond safe limit")
        else:
            raise FileRejected("unsupported file type (нужен PDF или XLSX)")
        if ref.kind == "pitch_deck" and ftype != "pdf":
            doc.warnings.append("В поле питч-дека загружен не PDF.")
        if ref.kind == "fin_model" and ftype != "xlsx":
            doc.warnings.append("В поле финмодели загружен не XLSX.")
    except FileRejected as e:
        doc.ok = False
        doc.warnings.append(f"Файл не обработан: {e}")
        log.warning("file rejected %s: %s", ref.url, e)
    except Exception as e:  # битый файл не должен ронять весь пайплайн
        doc.ok = False
        doc.warnings.append(f"Ошибка разбора файла: {e.__class__.__name__}")
        log.exception("parse failed %s", ref.url)
    return doc
