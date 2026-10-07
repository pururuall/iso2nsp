#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lantek_pdf.py - Парсер производственных PDF-отчетов Lantek CAM
для автоматического импорта спецификаций, имен деталей, тиражей листов
и наименования заказа в iso2nsp.
"""

import os
import re
from typing import List, Dict, Any, Optional

try:
    import pypdf
except ImportError:
    pypdf = None


class LantekSheetReport:
    """Модель данных одного листа из отчета Lantek CAM"""
    def __init__(self):
        self.prog_name: str = ""        # Например "6746 FE14"
        self.job_title: str = ""        # Например "6746 Zhyk.bash moduli 59och FE14 laser"
        self.repeat_count: int = 1      # Тираж (число листов)
        self.sheet_width: float = 1500.0
        self.sheet_height: float = 3000.0
        self.thickness: float = 6.0
        self.parts: List[Dict[str, Any]] = []  # Список словарей деталей


def parse_lantek_pdf(pdf_path: str) -> List[LantekSheetReport]:
    """
    Парсит PDF-отчет раскроя Lantek (поддерживает одностраничные и многостраничные отчеты).
    Возвращает список объектов LantekSheetReport для каждой страницы отчета.
    """
    if not pypdf:
        raise ImportError("Библиотека 'pypdf' не установлена. Установите ее: pip install pypdf")

    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"Файл PDF не найден: {pdf_path}")

    reader = pypdf.PdfReader(pdf_path)
    reports: List[LantekSheetReport] = []

    for page_idx, page in enumerate(reader.pages):
        text = page.extract_text()
        if not text:
            continue

        rep = LantekSheetReport()

        # 1. Поиск имени программы раскроя и габаритов листа
        # Формат в тексте Lantek:
        # \n<ИмяПрограммы>\n<Ширина> <Высота> <Толщина>
        prog_match = re.search(
            r'\n([0-9A-Za-z\-_]+(?:\s+[A-Za-z0-9\-_]+)?)\s*\n\s*(\d{3,5})\s+(\d{3,5})\s+(\d+)',
            text
        )
        if prog_match:
            rep.prog_name = prog_match.group(1).strip()
            rep.sheet_width = float(prog_match.group(2))
            rep.sheet_height = float(prog_match.group(3))
            rep.thickness = float(prog_match.group(4))

        # 2. Имя заказа (Job Name) и количество листов (типа: <Имя>-JOB01xxxx\n1)
        job_match = re.search(r'\n([^\n]+?-JOB\d+)\s*\n\s*(\d+)', text)
        if job_match:
            raw_job = job_match.group(1).strip()
            rep.repeat_count = max(1, int(job_match.group(2)))
            # Очистка времени и суффикса -JOB...
            raw_job = re.sub(r'^\d{2}:\d{2}:\d{2}\.\d{2}\s*', '', raw_job).strip()
            rep.job_title = re.sub(r'-JOB\d+$', '', raw_job).strip()

        # 3. Таблица спецификации деталей
        # Каждая строка заканчивается на размеры и символ 'x':
        # #   Имя детали   Кол-во на листе   Всего в заказе   Отход   Вес   Dim1   Dim2   x
        for line in text.splitlines():
            line_str = line.strip()
            m = re.match(
                r'^(\d+)\s+(.+?)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d\.]+)\s+([\d\.]+)\s+([\d\.]+)\s*x\s*$',
                line_str
            )
            if m:
                num, name, qty, total, scrap, wt, d1, d2 = m.groups()
                clean_name = re.sub(r'\s+', ' ', name).strip()
                # Удаляем возможные артефакты колонок таблицы Lantek
                clean_name = re.sub(r'^(?:0TK|1ED|[A-Z0-9\.\s]+)\s+', '', clean_name) if ('0TK' in clean_name or '1ED' in clean_name) else clean_name
                rep.parts.append({
                    'num': int(num),
                    'name': clean_name.strip(),
                    'qty': int(qty),
                    'total': int(total),
                    'weight': float(wt),
                    'dim1': float(d1),
                    'dim2': float(d2)
                })

        if rep.parts or rep.prog_name:
            reports.append(rep)

    return reports


def apply_lantek_reports_to_project(project, reports: List[LantekSheetReport]) -> Dict[str, Any]:
    """
    Сопоставляет данные отчетов Lantek с листами проекта NestingProject:
    - Обновляет тиражи листов (repeat_count);
    - Устанавливает имя заказа (job_name);
    - Назначает оригинальные человекопонятные имена деталям на листах;
    Возвращает отчет о результатах сопоставления.
    """
    stats = {
        'matched_plates': 0,
        'renamed_parts': 0,
        'detected_job_name': None
    }

    if not project.plates or not reports:
        return stats

    # Обновляем имя заказа из отчета, если оно не было задано вручную
    for r in reports:
        if r.job_title and not project.job_name:
            project.job_name = r.job_title
            stats['detected_job_name'] = r.job_title
            break

    used_reports = set()

    for plate in project.plates:
        # 1. Поиск соответствующего отчета для данного листа
        target_report: Optional[LantekSheetReport] = None
        plate_base = os.path.splitext(os.path.basename(plate.file_path))[0] if plate.file_path else plate.plate_name
        plate_name_clean = plate.plate_name.strip().upper()
        plate_num_prefix = plate_base.split()[0].upper() if plate_base else ""

        # Приоритет А: Точное совпадение имени программы
        for idx, r in enumerate(reports):
            if idx in used_reports:
                continue
            r_prog_clean = r.prog_name.strip().upper()
            if r_prog_clean and (r_prog_clean == plate_name_clean or r_prog_clean == plate_base.upper()):
                target_report = r
                used_reports.add(idx)
                break

        # Приоритет Б: Совпадение по цифровому префиксу файла (например 6746)
        if not target_report and plate_num_prefix:
            for idx, r in enumerate(reports):
                if idx in used_reports:
                    continue
                r_prog_clean = r.prog_name.strip().upper()
                r_job_clean = r.job_title.strip().upper()
                if (plate_num_prefix in r_prog_clean) or (plate_num_prefix in r_job_clean):
                    target_report = r
                    used_reports.add(idx)
                    break

        # Приоритет В: Если ровно 1 лист и 1 отчет
        if not target_report and len(project.plates) == 1 and len(reports) == 1 and 0 not in used_reports:
            target_report = reports[0]
            used_reports.add(0)

        if not target_report:
            continue

        stats['matched_plates'] += 1

        # Обновляем тираж листа
        if target_report.repeat_count > 0:
            plate.repeat_count = target_report.repeat_count

        # 2. Сопоставление и переименование деталей
        if not target_report.parts or not plate.parts:
            continue

        report_parts = sorted(target_report.parts, key=lambda p: (p['qty'], p['dim1'] * p['dim2']), reverse=True)
        iso_parts = plate.parts
        used_iso_indices = set()

        for rp in report_parts:
            rp_min = min(rp['dim1'], rp['dim2'])
            rp_max = max(rp['dim1'], rp['dim2'])
            needed_qty = rp['qty']

            # Ищем детали в ISO с подходящими габаритами
            candidates = []
            for p_idx, ip in enumerate(iso_parts):
                if p_idx in used_iso_indices:
                    continue
                bx = ip.bbox()
                pw = bx[2] - bx[0]
                ph = bx[3] - bx[1]
                ip_min = min(pw, ph)
                ip_max = max(pw, ph)

                # Допуск 2.0 мм на погрешности врезок / округления CAM
                if abs(ip_min - rp_min) <= 2.0 and abs(ip_max - rp_max) <= 2.0:
                    candidates.append(p_idx)

            # Назначаем имя найденным деталям
            take_qty = min(needed_qty, len(candidates))
            for c_idx in candidates[:take_qty]:
                iso_parts[c_idx].part_name = rp['name']
                used_iso_indices.add(c_idx)
                stats['renamed_parts'] += 1

    return stats
