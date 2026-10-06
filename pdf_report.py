#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pdf_report.py - Генератор производственных PDF-отчетов по раскрою (стиль Lantek CAM)
для iso2nsp / CypNest.

Формирует страницы формата А4:
1. Шапка заказа (Заказ, Лист, Ссылка, Дата, ЧПУ, Тираж).
2. Векторный чертёж раскроя листа с контурами деталей, отверстиями и номерами деталей.
3. Сводная таблица параметров листа (Станок, Материал, Вес листа, Использование %, Габариты).
4. Таблица спецификации деталей (№, Имя, Кол-во на листе, Всего в заказе, Вес, Размеры).
"""

import os
import math
import datetime
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors

from iso2nsp import NestingProject, PlateData, PartInstance, compute_part_geom_key, parts_match_rotation


def register_fonts():
    """Регистрация TrueType шрифтов с поддержкой кириллицы (Windows/Linux)"""
    candidates = [
        ("Arial", "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
        ("SegoeUI", "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf"),
        ("Calibri", "C:/Windows/Fonts/calibri.ttf", "C:/Windows/Fonts/calibrib.ttf"),
        ("DejaVu", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    for name, reg, bold in candidates:
        if os.path.exists(reg):
            try:
                pdfmetrics.registerFont(TTFont("AppFont", reg))
                b_path = bold if os.path.exists(bold) else reg
                pdfmetrics.registerFont(TTFont("AppFont-Bold", b_path))
                return "AppFont", "AppFont-Bold"
            except Exception:
                continue
    return "Helvetica", "Helvetica-Bold"


def calculate_density(material_str: str) -> float:
    """Плотность материала в г/см3 (или т/м3) для расчета веса стали/нержавейки/алюминия"""
    mat = (material_str or "").upper()
    if "AL" in mat:
        return 2.70
    elif "SS" in mat or "12X18" in mat or "INOX" in mat:
        return 7.93
    elif "CU" in mat or "BR" in mat:
        return 8.90
    return 7.85  # Конструкционная сталь (FE)


def generate_pdf_report(project: NestingProject, output_path: str, single_plate_idx: int = None) -> str:
    """
    Генерирует PDF-отчет по заказу раскроя.
    Если single_plate_idx задан, генерирует отчет только по указанному листу.
    Иначе — многостраничный отчет по всем листам проекта.
    """
    if not project.plates:
        raise ValueError("В проекте нет листов для генерации отчета.")

    font_reg, font_bold = register_fonts()
    c = canvas.Canvas(output_path, pagesize=A4)
    page_w, page_h = A4  # 595.28 x 841.89

    # Подготавливаем глобальную библиотеку деталей для подсчета общего количества (Сумма)
    all_masters = project.deduplicate_global_master_parts()
    global_qty_map = {m['key']: m['total_qty'] for m in all_masters}

    plates_to_export = [project.plates[single_plate_idx]] if single_plate_idx is not None else project.plates
    total_pages = len(plates_to_export)

    for page_num, plate in enumerate(plates_to_export, 1):
        _draw_plate_page(
            c=c,
            project=project,
            plate=plate,
            page_w=page_w,
            page_h=page_h,
            page_num=page_num,
            total_pages=total_pages,
            global_qty_map=global_qty_map,
            font_reg=font_reg,
            font_bold=font_bold
        )
        c.showPage()

    c.save()
    return output_path


def _draw_plate_page(c: canvas.Canvas, project: NestingProject, plate: PlateData,
                     page_w: float, page_h: float, page_num: int, total_pages: int,
                     global_qty_map: dict, font_reg: str, font_bold: str):
    """Отрисовка одной страницы А4 по образцу отчета Lantek"""
    margin = 24.0
    box_w = page_w - 2 * margin  # 547.28
    box_h = page_h - 2 * margin  # 793.89
    left = margin
    bottom = margin
    top = page_h - margin
    right = page_w - margin

    # 1. Внешняя рамка листа чертежа
    c.setLineWidth(1.2)
    c.setStrokeColor(colors.black)
    c.rect(left, bottom, box_w, box_h, stroke=1, fill=0)

    # =========================================================================
    # ШАПКА ДОКУМЕНТА (HEADER) - Lantek style
    # =========================================================================
    header_h = 75.0
    header_y = top - header_h
    c.line(left, header_y, right, header_y)

    # Логотип / Блок слева
    logo_w = 90.0
    c.line(left + logo_w, header_y, left + logo_w, top)
    
    # Стилизованный логотип iso2nsp
    c.setFillColor(colors.HexColor("#16181f"))
    c.rect(left + 2, header_y + 2, logo_w - 4, header_h - 4, stroke=0, fill=1)
    c.setFillColor(colors.HexColor("#00e5ff"))
    c.setFont(font_bold, 13)
    c.drawCentredString(left + logo_w / 2, header_y + 44, "iso2nsp")
    c.setFillColor(colors.HexColor("#e6edf3"))
    c.setFont(font_reg, 7)
    c.drawCentredString(left + logo_w / 2, header_y + 30, "Lantek -> CypNest")
    c.setFillColor(colors.HexColor("#00e676"))
    c.setFont(font_bold, 7)
    c.drawCentredString(left + logo_w / 2, header_y + 16, "EXPERT REPORT")

    # Текстовые поля шапки справа от логотипа
    hx = left + logo_w + 6
    row_h = header_h / 4.0

    # Разделительные горизонтальные линии строк шапки
    for r in range(1, 4):
        c.setLineWidth(0.6)
        c.setStrokeColor(colors.HexColor("#cccccc"))
        c.line(left + logo_w, top - r * row_h, right, top - r * row_h)

    # Строка 1: Ссылка и Количество
    c.setFillColor(colors.black)
    c.setFont(font_bold, 13)
    dims_str = f"{plate.sheet_width:.0f}x{plate.sheet_height:.0f}x{plate.thickness:.1f}"
    c.drawString(hx, top - 15, "Ссылка")
    c.setFont(font_bold, 12)
    c.drawString(hx + 80, top - 15, dims_str)

    c.setFont(font_bold, 11)
    repeat_str = f"Количество: {plate.repeat_count} шт."
    c.drawRightString(right - 8, top - 15, repeat_str)

    # Строка 2: Работа (Job Name)
    c.setFont(font_bold, 9)
    c.drawString(hx, top - row_h - 13, "Работа:")
    c.setFont(font_reg, 9)
    job_display = project.job_name or "Nesting Job"
    c.drawString(hx + 55, top - row_h - 13, job_display)

    # Строка 3: Лист (Габариты в рамке + Дата/Время)
    c.setFont(font_bold, 9)
    c.drawString(hx, top - 2 * row_h - 13, "Лист:")
    
    # Мини-рамка для габаритов листа
    dim_box_x = hx + 55
    dim_box_w = 190
    c.setLineWidth(0.8)
    c.rect(dim_box_x, top - 2 * row_h - 17, dim_box_w, 16, stroke=1, fill=0)
    c.setFont(font_reg, 9)
    dim_detail_text = f"{plate.sheet_width:.0f}   x   {plate.sheet_height:.0f}   x   {plate.thickness:.1f} мм"
    c.drawCentredString(dim_box_x + dim_box_w / 2, top - 2 * row_h - 13, dim_detail_text)

    # Дата и время формирования отчета
    now_str = datetime.datetime.now().strftime("%d.%m.%Y  %H:%M:%S")
    c.setFont(font_reg, 8)
    c.drawRightString(right - 8, top - 2 * row_h - 13, now_str)

    # Строка 4: CNC (Имя программы + Номер листа из N)
    c.setFont(font_bold, 9)
    c.drawString(hx, top - 3 * row_h - 13, "CNC:")
    c.setFont(font_bold, 9)
    c.drawString(hx + 55, top - 3 * row_h - 13, plate.plate_name)

    page_str = f"Лист {page_num} из {total_pages}"
    c.drawRightString(right - 8, top - 3 * row_h - 13, page_str)

    # =========================================================================
    # ТАБЛИЦА СПЕЦИФИКАЦИИ ДЕТАЛЕЙ (НИЗ СТРАНИЦЫ)
    # =========================================================================
    # Группируем детали текущего листа для таблицы и нумерации
    plate_groups = _group_plate_parts(plate)
    
    table_bottom = bottom + 6
    table_h = 240.0
    table_top = table_bottom + table_h
    c.setLineWidth(1.0)
    c.setStrokeColor(colors.black)
    c.line(left, table_top, right, table_top)

    # Колонки таблицы
    col_w = [25.0, 185.0, 50.0, 55.0, 50.0, 55.0, 127.28]
    col_x = [left]
    for w in col_w:
        col_x.append(col_x[-1] + w)

    # Заголовок таблицы
    th_h = 16.0
    c.setFillColor(colors.HexColor("#f4f4f4"))
    c.rect(left, table_top - th_h, box_w, th_h, stroke=0, fill=1)
    c.line(left, table_top - th_h, right, table_top - th_h)

    headers = ["#", "Ссылка / Имя детали", "Лист", "Сумма", "Брак", "Вес (кг)", "Размеры (мм)"]
    c.setFillColor(colors.black)
    c.setFont(font_bold, 8)
    for i, h_text in enumerate(headers):
        if i == 0:
            c.drawCentredString((col_x[i] + col_x[i+1]) / 2, table_top - 11, h_text)
        elif i in (1, 6):
            c.drawString(col_x[i] + 4, table_top - 11, h_text)
        else:
            c.drawCentredString((col_x[i] + col_x[i+1]) / 2, table_top - 11, h_text)

    # Строки деталей
    row_y = table_top - th_h
    row_h_item = 14.5
    density = calculate_density(plate.material)

    total_parts_sheet = 0
    total_weight_sheet = 0.0

    for idx, g in enumerate(plate_groups):
        if row_y - row_h_item < table_bottom:
            break
        row_y -= row_h_item

        # Чередование цвета фона строк
        if idx % 2 == 1:
            c.setFillColor(colors.HexColor("#fafafa"))
            c.rect(left, row_y, box_w, row_h_item, stroke=0, fill=1)

        c.setLineWidth(0.4)
        c.setStrokeColor(colors.HexColor("#e0e0e0"))
        c.line(left, row_y, right, row_y)

        c.setFillColor(colors.black)
        c.setFont(font_reg, 8)

        # Вычисление веса детали
        part_area_cm2 = g['net_area'] / 100.0  # мм2 в см2
        part_vol_cm3 = part_area_cm2 * (plate.thickness / 10.0)
        part_weight_kg = (part_vol_cm3 * density) / 1000.0
        
        qty_on_sheet = len(g['instances'])
        total_parts_sheet += qty_on_sheet
        total_weight_sheet += part_weight_kg * qty_on_sheet

        # Общий тираж в проекте
        sum_qty = global_qty_map.get(g['key'], qty_on_sheet * plate.repeat_count)

        part_num = str(idx + 1)
        dims_str = f"{g['width']:.1f}  x  {g['height']:.1f}"

        c.drawCentredString((col_x[0] + col_x[1]) / 2, row_y + 4, part_num)
        c.drawString(col_x[1] + 4, row_y + 4, g['name'][:32])
        c.drawCentredString((col_x[2] + col_x[3]) / 2, row_y + 4, str(qty_on_sheet))
        c.drawCentredString((col_x[3] + col_x[4]) / 2, row_y + 4, str(sum_qty))
        c.drawCentredString((col_x[4] + col_x[5]) / 2, row_y + 4, "0")
        c.drawCentredString((col_x[5] + col_x[6]) / 2, row_y + 4, f"{part_weight_kg:.3f}")
        c.drawString(col_x[6] + 6, row_y + 4, dims_str)

    # Заполняем оставшиеся строки таблицы разлиновкой (стиль Lantek)
    empty_idx = len(plate_groups)
    while row_y - row_h_item >= table_bottom:
        row_y -= row_h_item
        if empty_idx % 2 == 1:
            c.setFillColor(colors.HexColor("#fafafa"))
            c.rect(left, row_y, box_w, row_h_item, stroke=0, fill=1)
        c.setLineWidth(0.4)
        c.setStrokeColor(colors.HexColor("#e0e0e0"))
        c.line(left, row_y, right, row_y)
        empty_idx += 1

    # Вертикальные разделители колонок таблицы
    c.setLineWidth(0.5)
    c.setStrokeColor(colors.HexColor("#cccccc"))
    for cx in col_x[1:-1]:
        c.line(cx, table_bottom, cx, table_top)

    # =========================================================================
    # СВОДНЫЕ ПАРАМЕТРЫ ЛИСТА (ПАРАМЕТРЫ МЕЖДУ ЧЕРТЕЖОМ И ТАБЛИЦЕЙ)
    # =========================================================================
    params_bottom = table_top + 4
    params_h = 56.0
    params_top = params_bottom + params_h
    
    c.setLineWidth(0.8)
    c.setStrokeColor(colors.black)
    c.rect(left, params_bottom, box_w, params_h, stroke=1, fill=0)

    # Расчет использования площади листа и веса
    sheet_area = plate.sheet_width * plate.sheet_height
    total_parts_area = sum(g['net_area'] * len(g['instances']) for g in plate_groups)
    utilization_pct = min(100.0, max(0.0, (total_parts_area / max(1.0, sheet_area)) * 100.0))
    remnant_pct = 100.0 - utilization_pct

    # Вес заготовки листа
    sheet_vol_cm3 = (plate.sheet_width / 10.0) * (plate.sheet_height / 10.0) * (plate.thickness / 10.0)
    sheet_weight_kg = (sheet_vol_cm3 * density) / 1000.0

    # Габариты занятой области
    all_pts = []
    for part in plate.parts:
        all_pts.extend(part.outer_contour.get_discrete_points())
    max_part_x = max(pt[0] for pt in all_pts) if all_pts else plate.sheet_width
    max_part_y = max(pt[1] for pt in all_pts) if all_pts else plate.sheet_height

    # Разделитель пополам по ширине
    mid_x = left + box_w / 2.0
    c.line(mid_x, params_bottom, mid_x, params_top)

    p_row_h = params_h / 4.0
    for r in range(1, 4):
        c.setLineWidth(0.4)
        c.setStrokeColor(colors.HexColor("#dddddd"))
        c.line(left, params_bottom + r * p_row_h, right, params_bottom + r * p_row_h)

    # Левая колонка параметров
    c.setFillColor(colors.black)
    c.setFont(font_bold, 8)
    c.drawString(left + 8, params_top - 11, "Станок:")
    c.drawString(left + 8, params_top - p_row_h - 11, "Материал:")
    c.drawString(left + 8, params_top - 2 * p_row_h - 11, "Деталей на листе:")
    c.drawString(left + 8, params_top - 3 * p_row_h - 11, "Остаток (%):")

    c.setFont(font_reg, 8)
    c.drawString(left + 95, params_top - 11, "CypNest / Laser / Plasma")
    c.drawString(left + 95, params_top - p_row_h - 11, f"{plate.material} (s={plate.thickness:.1f} мм)")
    c.drawString(left + 95, params_top - 2 * p_row_h - 11, f"{len(plate.parts)} шт.")
    c.drawString(left + 95, params_top - 3 * p_row_h - 11, f"{remnant_pct:.2f} %")

    # Правая колонка параметров
    c.setFont(font_bold, 8)
    c.drawString(mid_x + 8, params_top - 11, "Вес листа:")
    c.drawString(mid_x + 8, params_top - p_row_h - 11, "Занято X:")
    c.drawString(mid_x + 8, params_top - 2 * p_row_h - 11, "Занято Y:")
    c.drawString(mid_x + 8, params_top - 3 * p_row_h - 11, "Использование (%):")

    c.setFont(font_reg, 8)
    c.drawString(mid_x + 105, params_top - 11, f"{sheet_weight_kg:.2f}  кг")
    c.drawString(mid_x + 105, params_top - p_row_h - 11, f"{max_part_x:.1f}  мм")
    c.drawString(mid_x + 105, params_top - 2 * p_row_h - 11, f"{max_part_y:.1f}  мм")
    c.setFont(font_bold, 8)
    c.setFillColor(colors.HexColor("#006622") if utilization_pct >= 70 else colors.black)
    c.drawString(mid_x + 105, params_top - 3 * p_row_h - 11, f"{utilization_pct:.2f} %")
    c.setFillColor(colors.black)

    # =========================================================================
    # ВЕКТОРНЫЙ ЧЕРТЁЖ ЛИСТА РАСКРОЯ (CENTER DRAWING)
    # =========================================================================
    draw_box_bottom = params_top + 6
    draw_box_top = header_y - 6
    draw_box_h = draw_box_top - draw_box_bottom
    draw_box_w = box_w

    # Масштабирование листа для центрирования в области чертежа
    pad = 12.0
    avail_w = draw_box_w - 2 * pad
    avail_h = draw_box_h - 2 * pad

    scale = min(avail_w / max(1.0, plate.sheet_width), avail_h / max(1.0, plate.sheet_height))
    sheet_draw_w = plate.sheet_width * scale
    sheet_draw_h = plate.sheet_height * scale

    ox = left + (draw_box_w - sheet_draw_w) / 2.0
    oy = draw_box_bottom + (draw_box_h - sheet_draw_h) / 2.0

    # 1. Рамка заготовки листа (пунктир или тонкая линия)
    c.setLineWidth(0.8)
    c.setStrokeColor(colors.HexColor("#777777"))
    c.rect(ox, oy, sheet_draw_w, sheet_draw_h, stroke=1, fill=0)

    # Карта номеров деталей по прототипам групп
    part_to_num = {}
    for g_idx, g in enumerate(plate_groups):
        for inst in g['instances']:
            part_to_num[id(inst)] = g_idx + 1

    # 2. Отрисовка деталей
    c.setLineWidth(0.6)
    c.setStrokeColor(colors.black)

    for part in plate.parts:
        # Внешний контур детали
        pts = part.outer_contour.get_discrete_points(arc_steps=16)
        if len(pts) >= 2:
            p_path = c.beginPath()
            p_path.moveTo(ox + pts[0][0] * scale, oy + pts[0][1] * scale)
            for pt in pts[1:]:
                p_path.lineTo(ox + pt[0] * scale, oy + pt[1] * scale)
            p_path.close()
            c.setStrokeColor(colors.HexColor("#222222"))
            c.drawPath(p_path, stroke=1, fill=0)

        # Отверстия детали
        for hole in part.holes:
            h_pts = hole.get_discrete_points(arc_steps=16)
            if len(h_pts) >= 2:
                h_path = c.beginPath()
                h_path.moveTo(ox + h_pts[0][0] * scale, oy + h_pts[0][1] * scale)
                for pt in h_pts[1:]:
                    h_path.lineTo(ox + pt[0] * scale, oy + pt[1] * scale)
                h_path.close()
                c.setStrokeColor(colors.HexColor("#444444"))
                c.drawPath(h_path, stroke=1, fill=0)

        # Номер детали по центру
        bx = part.bbox()
        cx = (bx[0] + bx[2]) / 2.0
        cy = (bx[1] + bx[3]) / 2.0
        part_num = part_to_num.get(id(part), "")
        
        # Рисуем номер, если деталь достаточно крупная в масштабе
        part_px_w = (bx[2] - bx[0]) * scale
        part_px_h = (bx[3] - bx[1]) * scale
        if min(part_px_w, part_px_h) >= 6.0 and part_num:
            c.setFillColor(colors.HexColor("#333333"))
            c.setFont(font_bold, 6 if min(part_px_w, part_px_h) < 14 else 7.5)
            c.drawCentredString(ox + cx * scale, oy + cy * scale - 2.5, str(part_num))

    # 3. Открытые резы листа (отрезные линии) и штриховка делового отхода
    c.setLineWidth(0.8)
    c.setStrokeColor(colors.HexColor("#b71c1c"))
    c.setDash(4, 2)
    for cut in plate.open_contours:
        c_pts = cut.get_discrete_points()
        if len(c_pts) >= 2:
            c_path = c.beginPath()
            c_path.moveTo(ox + c_pts[0][0] * scale, oy + c_pts[0][1] * scale)
            for pt in c_pts[1:]:
                c_path.lineTo(ox + pt[0] * scale, oy + pt[1] * scale)
            c.drawPath(c_path, stroke=1, fill=0)

            # Если это прямая отрезная линия через лист по ширине (Y = const)
            if abs(c_pts[0][1] - c_pts[-1][1]) < 2.0 and abs(abs(c_pts[0][0] - c_pts[-1][0]) - plate.sheet_width) < 15.0:
                cut_y = (c_pts[0][1] + c_pts[-1][1]) / 2.0
                part_ys = [p.bbox()[1] for p in plate.parts]
                if part_ys:
                    min_py = min(part_ys)
                    if min_py > cut_y:  # Детали сверху, отход снизу
                        rem_y1, rem_y2 = 0.0, cut_y
                    else:  # Детали снизу, отход сверху
                        rem_y1, rem_y2 = cut_y, plate.sheet_height

                    rx1 = ox
                    ry1 = oy + rem_y1 * scale
                    rw = sheet_draw_w
                    rh = (rem_y2 - rem_y1) * scale

                    if rh > 10.0:
                        c.saveState()
                        clip_p = c.beginPath()
                        clip_p.rect(rx1, ry1, rw, rh)
                        c.clipPath(clip_p, stroke=0)
                        c.setLineWidth(0.35)
                        c.setStrokeColor(colors.HexColor("#777777"))
                        c.setDash()
                        d = rx1 - rh
                        while d < rx1 + rw + rh:
                            c.line(d, ry1, d + rh, ry1 + rh)
                            d += 9.0
                        c.restoreState()

                        c.setFillColor(colors.HexColor("#333333"))
                        c.setFont(font_reg, 7.5)
                        rem_text = f"{plate.sheet_width:.0f}x{rem_y2 - rem_y1:.0f}x{plate.thickness:.1f}  REMNANT"
                        c.drawCentredString(rx1 + rw / 2.0, ry1 + rh / 2.0 - 2, rem_text)

    c.setDash()  # сброс пунктира


def _group_plate_parts(plate: PlateData) -> list:
    """Группировка деталей одного листа по геометрии с вычислением чистой площади"""
    master_list = []
    for part in plate.parts:
        found_master = None
        for m in master_list:
            ang = parts_match_rotation(m['prototype'], part)
            if ang is not None:
                found_master = m
                break

        if found_master is None:
            bx = part.bbox()
            w = round(bx[2] - bx[0], 2)
            h = round(bx[3] - bx[1], 2)
            geom_key = compute_part_geom_key(part)

            # Чистая площадь = площадь внешнего контура минус площадь отверстий
            outer_area = abs(part.outer_contour.signed_area())
            holes_area = sum(abs(hole.signed_area()) for hole in part.holes)
            net_area = max(0.0, outer_area - holes_area)

            def_name = part.part_name if (part.part_name and part.part_name != "Part") else f"{plate.plate_name}_P{len(master_list) + 1}"

            master_data = {
                'key': geom_key,
                'name': def_name,
                'width': w,
                'height': h,
                'net_area': net_area,
                'prototype': part,
                'instances': [part]
            }
            master_list.append(master_data)
        else:
            found_master['instances'].append(part)

    # Сортировка групп по габаритам (площади) убывающе
    return sorted(master_list, key=lambda g: g['width'] * g['height'], reverse=True)
