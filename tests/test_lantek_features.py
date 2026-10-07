#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_lantek_features.py - Тестирование новых функций:
1. Отключение и включение технологической обрезки листа (include_cutoff) в .nsp
2. Автоматический парсинг отчетов Lantek PDF и привязка к проекту раскроя
"""

import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from iso2nsp import IsoParser, PlateData, NestingProject, convert_iso_to_nsp
from lantek_pdf import parse_lantek_pdf, apply_lantek_reports_to_project


def test_cutoff_toggle():
    print("[TEST 1] Проверка управления обрезкой листа (include_cutoff)...")
    iso_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "samples", "1168 FE6.iso")
    if not os.path.exists(iso_file):
        print("  [SKIP] Файл samples/1168 FE6.iso не найден")
        return True

    parser = IsoParser(iso_file)
    parser.parse()
    assert len(parser.open_contours) > 0, "В файле 1168 FE6.iso должны быть открытые контуры обрезки"

    # А. По умолчанию include_cutoff=False (обрезка НЕ идет в NSP)
    plate_no_cutoff = PlateData(
        file_path=iso_file,
        plate_name="TestNoCutoff",
        sheet_width=parser.sheet_width,
        sheet_height=parser.sheet_height,
        repeat_count=1,
        include_cutoff=False
    )
    plate_no_cutoff.raw_closed_contours = parser.closed_contours
    plate_no_cutoff.raw_open_contours = parser.open_contours
    plate_no_cutoff.apply_transformations()

    proj_no_cut = NestingProject("NoCutJob")
    proj_no_cut.add_plate(plate_no_cutoff)
    xml_no_cut = proj_no_cut.generate_xml_string()

    root_no_cut = ET.fromstring(xml_no_cut)
    plate_node_no = root_no_cut.find('PlateNode')
    shapes_no = plate_node_no.find('shapes')
    direct_polylines_no = [child for child in shapes_no if child.tag == 'PolylineNode']
    assert len(direct_polylines_no) == 0, f"Ожидалось 0 линий обрезки в NSP при include_cutoff=False, получено {len(direct_polylines_no)}"
    print("  [OK] include_cutoff=False: открытые линии обрезки корректно исключены из NSP")

    # Б. include_cutoff=True (обрезка идет в NSP)
    plate_with_cutoff = PlateData(
        file_path=iso_file,
        plate_name="TestWithCutoff",
        sheet_width=parser.sheet_width,
        sheet_height=parser.sheet_height,
        repeat_count=1,
        include_cutoff=True
    )
    plate_with_cutoff.raw_closed_contours = parser.closed_contours
    plate_with_cutoff.raw_open_contours = parser.open_contours
    plate_with_cutoff.apply_transformations()

    proj_with_cut = NestingProject("WithCutJob")
    proj_with_cut.add_plate(plate_with_cutoff)
    xml_with_cut = proj_with_cut.generate_xml_string()

    root_with_cut = ET.fromstring(xml_with_cut)
    plate_node_with = root_with_cut.find('PlateNode')
    shapes_with = plate_node_with.find('shapes')
    direct_polylines_with = [child for child in shapes_with if child.tag == 'PolylineNode']
    assert len(direct_polylines_with) == len(parser.open_contours), "При include_cutoff=True линии обрезки должны присутствовать в NSP"
    print("  [OK] include_cutoff=True: открытые линии обрезки успешно перенесены в NSP")
    return True


def test_lantek_pdf_matching():
    print("\n[TEST 2] Проверка авто-парсинга Lantek PDF и сопоставления с ISO...")
    lantek_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "пример работ с Lantec")
    if not os.path.exists(lantek_dir):
        print("  [SKIP] Папка 'пример работ с Lantec' не найдена")
        return True

    iso_files = sorted([os.path.join(lantek_dir, f) for f in os.listdir(lantek_dir) if f.lower().endswith('.iso')])
    pdf_files = sorted([os.path.join(lantek_dir, f) for f in os.listdir(lantek_dir) if f.lower().endswith('.pdf')])

    assert len(iso_files) == 8, f"Ожидалось 8 файлов ISO, найдено {len(iso_files)}"
    assert len(pdf_files) == 8, f"Ожидалось 8 файлов PDF, найдено {len(pdf_files)}"

    # 1. Проверяем парсинг всех 8 отчетов
    all_reports = []
    for pdf_path in pdf_files:
        reports = parse_lantek_pdf(pdf_path)
        assert len(reports) >= 1, f"Не удалось распарсить отчет {os.path.basename(pdf_path)}"
        all_reports.extend(reports)
    print(f"  [OK] Успешно распарсено {len(all_reports)} страниц отчетов Lantek")

    # 2. Создаем многолистовой проект из 8 ISO и сопоставляем
    project = NestingProject()
    for iso_path in iso_files:
        parser = IsoParser(iso_path)
        parser.parse()
        plate = PlateData(
            file_path=iso_path,
            plate_name=parser.program_name,
            sheet_width=parser.sheet_width,
            sheet_height=parser.sheet_height,
            thickness=parser.thickness,
            material=parser.material,
            include_cutoff=False
        )
        plate.raw_closed_contours = parser.closed_contours
        plate.raw_open_contours = parser.open_contours
        plate.swap_xy = True
        plate.flip_y = True
        plate.apply_transformations()
        project.add_plate(plate)

    res = apply_lantek_reports_to_project(project, all_reports)
    print(f"  [OK] Сопоставлено листов: {res['matched_plates']} из {len(project.plates)}")
    print(f"  [OK] Присвоено имен деталям: {res['renamed_parts']}")
    print(f"  [OK] Распознано имя заказа: '{res['detected_job_name']}'")

    assert res['matched_plates'] == 8, f"Все 8 листов должны быть сопоставлены, получено {res['matched_plates']}"
    assert res['renamed_parts'] > 0, "Детали должны быть переименованы"

    # Проверяем, что в сгенерированном NSP появились человекопонятные имена деталей (например А1187)
    xml_data = project.generate_xml_string()
    assert "А1187" in xml_data or "1187" in xml_data, "В проекте NSP должны присутствовать имена деталей из PDF"
    print("  [OK] Проект NSP успешно содержит имена деталей из Lantek PDF")
    return True


def test_multisheet_cross_sheet_naming():
    print("\n[TEST 3] Проверка многолистового сопоставления и сквозных имен деталей (1849 FE6, 10 листов)...")
    import glob
    base_dir = os.path.dirname(os.path.dirname(__file__))
    iso_files = sorted(glob.glob(os.path.join(base_dir, "**", "*1849*.iso"), recursive=True))
    pdf_files = glob.glob(os.path.join(base_dir, "**", "*1849*.pdf"), recursive=True)

    if not iso_files or not pdf_files:
        print("  [SKIP] Файлы 1849 не найдены")
        return True

    assert len(iso_files) == 10, f"Ожидалось 10 файлов 1849 FE6, найдено {len(iso_files)}"
    pdf_path = pdf_files[0]

    reports = parse_lantek_pdf(pdf_path)
    assert len(reports) == 10, f"Ожидалось 10 страниц отчета в PDF 1849, получено {len(reports)}"
    # Проверяем, что имена программ на каждой странице определены корректно
    for r in reports:
        assert r.prog_name.startswith("1849 FE6"), f"Неверное имя программы: {r.prog_name}"
    print(f"  [OK] Все 10 страниц PDF успешно распарсены с точными именами программ (например '{reports[0].prog_name}')")

    project = NestingProject()
    for iso_path in iso_files:
        parser = IsoParser(iso_path)
        parser.parse()
        plate = PlateData(
            file_path=iso_path,
            plate_name=os.path.splitext(os.path.basename(iso_path))[0],
            sheet_width=parser.sheet_width,
            sheet_height=parser.sheet_height,
            thickness=parser.thickness,
            material=parser.material,
            include_cutoff=False
        )
        plate.raw_closed_contours = parser.closed_contours
        plate.raw_open_contours = parser.open_contours
        plate.swap_xy = True
        plate.flip_y = True
        plate.apply_transformations()
        project.add_plate(plate)

    # Применяем отчеты Lantek
    res = apply_lantek_reports_to_project(project, reports)
    print(f"  [OK] Сопоставлено листов: {res['matched_plates']} из {len(project.plates)}")
    print(f"  [OK] Переименовано деталей: {res['renamed_parts']}")

    assert res['matched_plates'] == 10, "Все 10 листов должны быть сопоставлены"

    # Проверяем, что каждый лист имеет правильный тираж
    plate_02 = [p for p in project.plates if "02" in p.plate_name][0]
    plate_01 = [p for p in project.plates if "01" in p.plate_name][0]
    assert plate_02.repeat_count == 3, f"Лист 02 должен иметь тираж 3, получено {plate_02.repeat_count}"
    assert plate_01.repeat_count == 1, f"Лист 01 должен иметь тираж 1, получено {plate_01.repeat_count}"
    print("  [OK] Тиражи листов из PDF корректно привязаны (Лист 02 = 3 шт, Лист 01 = 1 шт)")

    # Проверяем, что НИ ОДНА деталь на всех 10 листах не осталась безымянной ('Part')
    total_parts = sum(len(p.parts) for p in project.plates)
    unnamed = []
    for pl in project.plates:
        for pt in pl.parts:
            if pt.part_name == "Part" or pt.part_name.startswith(f"{pl.plate_name}_P"):
                unnamed.append((pl.plate_name, pt))
    assert len(unnamed) == 0, f"Найдено {len(unnamed)} безымянных деталей из {total_parts}!"
    print(f"  [OK] Все {total_parts} деталей на всех 10 листах успешно получили имена из спецификации Lantek!")

    # Проверяем генерацию XML
    xml_str = project.generate_xml_string()
    assert "1184" in xml_str, "Имена деталей должны присутствовать в NSP"
    print("  [OK] Проект NSP сгенерирован успешно со всеми деталями и именами")
    return True


if __name__ == "__main__":
    t1 = test_cutoff_toggle()
    t2 = test_lantek_pdf_matching()
    t3 = test_multisheet_cross_sheet_naming()
    if t1 and t2 and t3:
        print("\n" + "="*60)
        print("ВСЕ ТЕСТЫ НОВЫХ ФУНКЦИЙ УСПЕШНО ПРОЙДЕНЫ (100% PASS)!")
        print("="*60)
    else:
        sys.exit(1)
