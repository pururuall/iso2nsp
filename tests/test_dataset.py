#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_dataset.py - Автоматический валидатор всех файлов раскроя в проекте.
Проверяет:
1. Корректность парсинга ISO/CNC G-кода.
2. Математическую корректность дуг и сегментов.
3. Отсутствие вырожденных и паразитных контуров.
4. Отсутствие вылета деталей за границы листа.
5. Корректность генерации CypNest XML (.nsp).
"""

import os
import sys
import glob
import math
import xml.etree.ElementTree as ET

# Подключаем корневую папку проекта
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import iso2nsp


def validate_file(file_path: str):
    issues = []
    
    # 1. Парсинг файла
    parser = iso2nsp.IsoParser(file_path)
    try:
        parser.parse()
    except Exception as e:
        return False, [f"Ошибка парсинга: {e}"]

    # 2. Проверка дуг на математическую согласованность
    all_contours = parser.closed_contours + parser.open_contours
    for c_idx, c in enumerate(all_contours):
        for s_idx, s in enumerate(c.segments):
            if s.type == 'arc' and s.center:
                r1 = math.hypot(s.start.x - s.center.x, s.start.y - s.center.y)
                r2 = math.hypot(s.end.x - s.center.x, s.end.y - s.center.y)
                if r1 < 1e-3 or r2 < 1e-3:
                    issues.append(f"Вырожденная дуга с нулевым радиусом в контуре #{c_idx}, сегмент #{s_idx}")
                elif abs(r1 - r2) > 1.0:
                    issues.append(f"Несогласованные радиусы дуги (R1={r1:.1f}, R2={r2:.1f}) в контуре #{c_idx}, сегмент #{s_idx}")

    # 3. Трансформация в лист
    plate = iso2nsp.PlateData(
        file_path=file_path,
        plate_name=parser.program_name or os.path.splitext(os.path.basename(file_path))[0],
        sheet_width=parser.sheet_width,
        sheet_height=parser.sheet_height,
        thickness=parser.thickness,
        material=parser.material
    )
    plate.raw_closed_contours = parser.closed_contours
    plate.raw_open_contours = parser.open_contours

    if plate.sheet_width <= plate.sheet_height:
        plate.swap_xy = True
        plate.flip_y = True
    else:
        plate.swap_xy = False
        plate.flip_y = False

    try:
        plate.apply_transformations()
    except Exception as e:
        return False, [f"Ошибка трансформации: {e}"]

    # 4. Проверка габаритов деталей
    margin_tol = 15.0  # технологический допуск кромки
    for p_idx, part in enumerate(plate.parts):
        b = part.bbox()
        if b[0] < -margin_tol or b[2] > plate.sheet_width + margin_tol or \
           b[1] < -margin_tol or b[3] > plate.sheet_height + margin_tol:
            issues.append(f"Деталь #{p_idx} выходит за границы листа: X=[{b[0]:.1f}, {b[2]:.1f}], Y=[{b[1]:.1f}, {b[3]:.1f}] при листе {plate.sheet_width}x{plate.sheet_height}")

    # 5. Проверка открытых контуров (должны быть только отрезными линиями)
    for o_idx, oc in enumerate(plate.open_contours):
        if len(oc.segments) > 2:
            issues.append(f"Подозрительный составной открытый контур #{o_idx}: {len(oc.segments)} сегментов (не похож на отрезную линию)")

    # 6. Проверка сборки XML CypNest
    try:
        proj = iso2nsp.NestingProject()
        proj.add_plate(plate)
        xml_data = proj.generate_xml_string()
        root = ET.fromstring(xml_data)
        if root.tag != 'root':
            issues.append(f"Некорректный корневой тег XML: {root.tag}")
    except Exception as e:
        issues.append(f"Ошибка валидации XML: {e}")

    passed = len(issues) == 0
    return passed, issues


def main():
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    patterns = [
        os.path.join(root_dir, "**", "*.iso"),
        os.path.join(root_dir, "**", "*.cnc"),
    ]
    
    files = []
    for pat in patterns:
        files.extend(glob.glob(pat, recursive=True))

    # Убираем дубликаты
    unique_files = sorted(list(set(os.path.abspath(f) for f in files)))

    print("=" * 80)
    print(f"ВАЛИДАЦИЯ ДАТАСЕТА ISO/CNC -> CypNest NSP ({len(unique_files)} файлов)")
    print("=" * 80)

    all_passed = True
    total_parts = 0

    for path in unique_files:
        rel = os.path.relpath(path, root_dir)
        passed, issues = validate_file(path)
        
        parser = iso2nsp.IsoParser(path)
        parser.parse()
        p_count = len(parser.closed_contours)
        total_parts += p_count

        if passed:
            print(f"[OK] {rel:<35} | {parser.sheet_width:.0f}x{parser.sheet_height:.0f} | контуров: {p_count:<4} | открытых: {len(parser.open_contours)}")
        else:
            all_passed = False
            print(f"[FAIL] {rel:<33} | ОШИБКИ:")
            for iss in issues:
                print(f"       -> {iss}")

    print("=" * 80)
    if all_passed:
        print(f"РЕЗУЛЬТАТ: ВСЕ {len(unique_files)} ФАЙЛОВ УСПЕШНО ПРОШЛИ 100% ПРОВЕРОК! (Обработано контуров: {total_parts})")
    else:
        print("РЕЗУЛЬТАТ: ОБНАРУЖЕНЫ ОШИБКИ!")
    print("=" * 80)
    return 0 if all_passed else 1


if __name__ == '__main__':
    import sys
    sys.exit(main())
