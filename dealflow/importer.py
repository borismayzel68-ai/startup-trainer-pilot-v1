"""MOS-3: импорт dealflow из CSV-выгрузок реестров. Анализ (dry-run) не касается БД; запись — только apply().
Формат колонок: docs/IMPORT_FORMAT.md. Статус не выдумывается: спорные строки остаются в отчёте."""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from dealflow.status import (ZONE_ACTIVE, ZONE_INBOX, InvalidTransition, change_status,
                             create_startup, map_core, map_zone, validate_transition)

SOURCES = ("migration_plan", "core_dashboard", "books")
STATUS_SOURCES = ("migration_plan", "core_dashboard")
WATCHLIST = ("VC-028", "VC-085", "VC-103", "VC-104", "VC-170", "VC-176", "VC-182")
KNOWN_ZONES = (ZONE_INBOX, ZONE_ACTIVE)

# ключ -> допустимые заголовки (в нижнем регистре)
_COLUMNS = {
    "vc_id": ("vc_id", "vc id", "id"),
    "name": ("name", "название", "startup"),
    "stage": ("stage", "status", "этап", "статус", "текущий статус/этап"),
    "zone": ("zone", "зона"),
    "link": ("link", "ссылка", "drive_link", "url"),
    "screening": ("screening",),
    "investment_view": ("investment_view",),
    "process_action": ("process_action",),
}
_REQUIRED = {
    "migration_plan": ("vc_id", "name", "zone"),
    "core_dashboard": ("vc_id", "name", "screening"),
    "books": ("vc_id", "name"),
}
_ID = r"([A-Za-z0-9_-]+)"
_HOST = r"^https://(?:drive|docs)\.google\.com/"
_RE_FILE = re.compile(_HOST + r"(?:[a-z]+/)*?(?:file|document|spreadsheets|presentation)/d/" + _ID)
_RE_FOLDER = re.compile(_HOST + r"(?:drive/(?:u/\d+/)?)?folders/" + _ID)
_RE_QUERY = re.compile(_HOST + r"[^?#]*\?(?:[^#]*&)?id=" + _ID)


class ImportFormatError(ValueError):
    pass


def extract_drive_id(link: str) -> Optional[tuple[str, str]]:
    """(id, 'file'|'folder') из ссылки Drive; иначе None. Сама ссылка нигде не хранится."""
    s = (link or "").strip()
    m = _RE_FOLDER.match(s)
    if m:
        return m.group(1), "folder"
    m = _RE_FILE.match(s)
    if m:
        return m.group(1), "file"
    m = _RE_QUERY.match(s)
    if m:
        return m.group(1), "file"
    return None


@dataclass
class Row:
    source: str
    line: int
    vc_id: str
    name: str
    status: Optional[str] = None
    needs_review: bool = False
    note: str = ""
    zone: str = ""
    stage: str = ""
    drive: Optional[tuple[str, str]] = None
    bad_link: bool = False


def _header_map(source: str, fieldnames) -> dict[str, str]:
    have = {(h or "").strip().lower(): h for h in (fieldnames or [])}
    out = {}
    for key, aliases in _COLUMNS.items():
        for a in aliases:
            if a in have:
                out[key] = have[a]
                break
    missing = [k for k in _REQUIRED[source] if k not in out]
    if missing:
        raise ImportFormatError(f"{source}: нет колонок {missing}")
    return out


def read_source(source: str, path) -> list[Row]:
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        hm = _header_map(source, rd.fieldnames)
        for rec in rd:
            def g(k):
                return (rec.get(hm[k]) or "").strip() if k in hm else ""
            r = Row(source, rd.line_num, g("vc_id").upper(), " ".join(g("name").split()),
                    zone=g("zone"), stage=g("stage"))
            if source == "migration_plan":
                m = map_zone(r.zone, r.stage)
                r.status, r.needs_review, r.note = m.status, m.needs_review, m.note
            elif source == "core_dashboard":
                m = map_core(r.vc_id, g("screening"), g("investment_view"), g("process_action"))
                r.status, r.needs_review, r.note = m.status, m.needs_review, m.note
            link = g("link")
            if link:
                r.drive = extract_drive_id(link)
                r.bad_link = r.drive is None
            rows.append(r)
    return rows


def _norm(name: str) -> str:
    return " ".join(name.casefold().split())


def analyze(paths: dict) -> dict:
    """paths: source -> путь к CSV (или None). Чистая функция: БД не трогает. Возвращает JSON-совместимый отчёт."""
    rows: list[Row] = []
    counts = {}
    for src in SOURCES:
        if paths.get(src):
            rs = read_source(src, paths[src])
            counts[src] = len(rs)
            rows += rs
        else:
            counts[src] = None
    no_id = [{"source": r.source, "line": r.line, "name": r.name} for r in rows if not r.vc_id]
    by_id: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        if r.vc_id:
            by_id[r.vc_id].append(r)

    duplicates, conflicts, review, no_status, bad_link_ids, clean = [], [], [], [], [], {}
    for vid, rs in sorted(by_id.items()):
        per_src = defaultdict(list)
        for r in rs:
            per_src[r.source].append(r.line)
        dup = {s: ls for s, ls in per_src.items() if len(ls) > 1}
        blocked = False
        if dup:
            duplicates.append({"vc_id": vid, "lines": dup})
            blocked = True
        names = {_norm(r.name) for r in rs if r.name}
        sts = {r.status for r in rs if r.status and not r.needs_review}
        c = {}
        if len(names) > 1:
            c["names"] = sorted({r.name for r in rs if r.name})
        if len(sts) > 1:
            c["statuses"] = sorted(sts)
        if c:
            conflicts.append({"vc_id": vid, **c})
            blocked = True
        if any(r.bad_link for r in rs):
            bad_link_ids.append(vid)
            blocked = True
        flagged = [r for r in rs if r.needs_review]
        if flagged:
            review.append({"vc_id": vid, "notes": sorted({r.note for r in flagged})})
            blocked = True
        if not any(r.source in STATUS_SOURCES for r in rs):
            no_status.append(vid)
            blocked = True
        if not blocked:
            clean[vid] = rs

    unknown_zones = sorted({r.zone for r in rows if r.source == "migration_plan" and r.zone not in KNOWN_ZONES})
    bad_links = [{"source": r.source, "line": r.line, "vc_id": r.vc_id} for r in rows if r.bad_link]
    dup_ids = {d["vc_id"] for d in duplicates}
    conf_ids = {c["vc_id"] for c in conflicts}
    watch = {}
    for vid in WATCHLIST:
        if vid not in by_id:
            watch[vid] = "нет в выгрузках"
        elif vid in dup_ids:
            watch[vid] = "ДУБЛЬ" + (" + КОНФЛИКТ" if vid in conf_ids else "")
        elif vid in conf_ids:
            watch[vid] = "КОНФЛИКТ"
        else:
            watch[vid] = "ок" if vid in clean else "требует проверки"
    plan = []
    for vid, rs in clean.items():
        st_rows = [r for r in rs if r.source in STATUS_SOURCES]
        docs = sorted({r.drive for r in rs if r.drive})
        plan.append({"vc_id": vid, "name": rs[0].name, "status": st_rows[0].status,
                     "stage": next((r.stage for r in rs if r.source == "migration_plan"), ""),
                     "drive": [list(d) for d in docs]})
    return {
        "rows_by_source": counts,
        "unique_ids": len(by_id),
        "rows_without_id": no_id,
        "duplicates": duplicates,
        "conflicts": conflicts,
        "needs_review": review,
        "no_status_source": no_status,
        "unknown_zones": unknown_zones,
        "bad_links": bad_links,
        "bad_link_ids": bad_link_ids,
        "watchlist": watch,
        "to_apply": plan,
        "blocked_ids": sorted(set(by_id) - set(clean)),
    }


@dataclass
class ApplyResult:
    created: int = 0
    status_changed: int = 0
    unchanged: int = 0
    documents_added: int = 0
    skipped_transitions: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)  # существующие стартапы не из импорта: статус не тронут


def apply(conn, report: dict) -> ApplyResult:
    """Одна транзакция. Блокированные ID не пишутся. Повторный вызов ничего не меняет."""
    res = ApplyResult()
    with conn.transaction():
        for item in report["to_apply"]:
            row = conn.execute("SELECT id, current_status::text, source FROM startups WHERE vc_id=%s FOR UPDATE",
                               (item["vc_id"],)).fetchone()
            if row is None:
                sid = create_startup(conn, item["vc_id"], item["name"], stage=item["stage"] or None,
                                     source="import", status=item["status"], reason="import")
                res.created += 1
            else:
                sid, cur, src = row
                if cur == item["status"]:
                    res.unchanged += 1
                elif src != "import":
                    res.conflicts.append({"vc_id": item["vc_id"], "current": cur, "csv": item["status"],
                                          "source": src})
                    continue
                else:
                    try:
                        validate_transition(cur, item["status"])
                    except InvalidTransition as e:
                        res.skipped_transitions.append({"vc_id": item["vc_id"], "reason": str(e)})
                    else:
                        change_status(conn, sid, item["status"], reason="import")
                        res.status_changed += 1
            for drive_id, kind in item["drive"]:
                kind = f"drive_{kind}"
                if conn.execute("SELECT 1 FROM documents WHERE startup_id=%s AND drive_file_id=%s AND kind=%s",
                                (sid, drive_id, kind)).fetchone() is None:
                    conn.execute("INSERT INTO documents (startup_id, kind, title, drive_file_id) "
                                 "VALUES (%s,%s,%s,%s)", (sid, kind, item["name"], drive_id))
                    res.documents_added += 1
    return res


def render_markdown(rep: dict, applied: Optional[ApplyResult] = None) -> str:
    L = ["# Отчёт импорта dealflow", "", f"Режим: {'APPLY' if applied else 'DRY-RUN (БД не изменялась)'}", "",
         "## Строк по источникам"]
    L += [f"- {k}: {v if v is not None else 'не задан'}" for k, v in rep["rows_by_source"].items()]
    L += [f"- уникальных ID: {rep['unique_ids']}",
          f"- к записи: {len(rep['to_apply'])}, заблокировано: {len(rep['blocked_ids'])}", "",
          "## Контрольные ID"]
    L += [f"- {k}: {v}" for k, v in rep["watchlist"].items()]
    L += ["", f"## Дубликаты ID ({len(rep['duplicates'])})"]
    L += [f"- {d['vc_id']}: строки {d['lines']}" for d in rep["duplicates"]]
    L += ["", f"## Конфликты между источниками ({len(rep['conflicts'])})"]
    L += [f"- {c['vc_id']}: " + "; ".join(f"{k}={v}" for k, v in c.items() if k != "vc_id")
          for c in rep["conflicts"]]
    L += ["", f"## Требуют ручной проверки ({len(rep['needs_review'])})"]
    L += [f"- {r['vc_id']}: {'; '.join(r['notes'])}" for r in rep["needs_review"]]
    L += ["", f"## Нет источника статуса ({len(rep['no_status_source'])})"]
    L += [f"- {v}" for v in rep["no_status_source"]]
    L += ["", f"## Строки без ID ({len(rep['rows_without_id'])})"]
    L += [f"- {r['source']}:{r['line']} {r['name']}" for r in rep["rows_without_id"]]
    L += ["", f"## Неизвестные зоны ({len(rep['unknown_zones'])})"] + [f"- {z}" for z in rep["unknown_zones"]]
    L += ["", f"## Нераспознанные ссылки ({len(rep['bad_links'])}) — ID заблокированы"]
    L += [f"- {b['source']}:{b['line']} {b['vc_id']}" for b in rep["bad_links"]]
    if applied:
        L += ["", "## Результат apply", f"- создано: {applied.created}",
              f"- статус изменён: {applied.status_changed}", f"- без изменений: {applied.unchanged}",
              f"- документов добавлено: {applied.documents_added}",
              f"- переходов пропущено: {len(applied.skipped_transitions)}",
              f"- конфликтов с записями не из импорта: {len(applied.conflicts)}"]
        L += [f"  - КОНФЛИКТ {c['vc_id']}: в БД {c['current']} (source={c['source']}), в CSV {c['csv']}; статус не изменён"
              for c in applied.conflicts]
        L += [f"  - {s['vc_id']}: {s['reason']}" for s in applied.skipped_transitions]
    return "\n".join(L) + "\n"
