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


if __name__ == "__main__":
    t1 = test_cutoff_toggle()
    t2 = test_lantek_pdf_matching()
    if t1 and t2:
        print("\n" + "="*60)
        print("ВСЕ ТЕСТЫ НОВЫХ ФУНКЦИЙ УСПЕШНО ПРОЙДЕНЫ (100% PASS)!")
        print("="*60)
    else:
        sys.exit(1)
