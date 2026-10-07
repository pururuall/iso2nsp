#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
iso2nsp.py - Конвертер G-кода ISO (Lantek / ЧПУ плазмы и лазера) в проект раскроя CypNest .nsp

Разработано строго с использованием стандартной библиотеки Python:
math, re, uuid, xml.etree.ElementTree, xml.dom.minidom, argparse, os, sys.
"""

import os
import sys
import re
import math
import uuid
import argparse
import xml.etree.ElementTree as ET
from xml.dom import minidom


# ==============================================================================
# 1. ГЕОМЕТРИЧЕСКИЕ СТРУКТУРЫ ДАННЫХ
# ==============================================================================

class Point:
    __slots__ = ('x', 'y')

    def __init__(self, x: float, y: float):
        self.x = float(x)
        self.y = float(y)

    def dist(self, other: 'Point') -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def __repr__(self):
        return f"Point({self.x:.3f}, {self.y:.3f})"


class Segment:
    """
    Сегмент траектории:
    type: 'line' или 'arc'
    direction: 'G01', 'G02' (CW), 'G03' (CCW)
    center: Point (для дуги, абсолютные координаты I, J)
    """
    def __init__(self, start: Point, end: Point, seg_type='line', direction='G01', center: Point = None):
        self.start = start
        self.end = end
        self.type = seg_type
        self.direction = direction
        self.center = center

    def copy(self):
        return Segment(
            Point(self.start.x, self.start.y),
            Point(self.end.x, self.end.y),
            self.type,
            self.direction,
            Point(self.center.x, self.center.y) if self.center else None
        )


class Contour:
    def __init__(self):
        self.segments = []
        self.is_closed = False
        self.is_hole = False
        self.parent_part = None

    def add_segment(self, seg: Segment):
        self.segments.append(seg)

    @property
    def start_point(self) -> Point:
        return self.segments[0].start if self.segments else Point(0, 0)

    @property
    def end_point(self) -> Point:
        return self.segments[-1].end if self.segments else Point(0, 0)

    def check_closed(self, tol=1.0) -> bool:
        if not self.segments:
            self.is_closed = False
            return False
        dist = self.start_point.dist(self.end_point)
        self.is_closed = dist <= tol
        return self.is_closed

    def get_discrete_points(self, arc_steps=12):
        """Аппроксимация контура точками для PIP (проверки принадлежности полигону) и bbox"""
        pts = []
        for seg in self.segments:
            if seg.type == 'line':
                pts.append((seg.start.x, seg.start.y))
            elif seg.type == 'arc' and seg.center:
                cx, cy = seg.center.x, seg.center.y
                r = math.hypot(seg.start.x - cx, seg.start.y - cy)
                a1 = math.atan2(seg.start.y - cy, seg.start.x - cx)
                a2 = math.atan2(seg.end.y - cy, seg.end.x - cx)
                
                # Полная окружность
                if seg.start.dist(seg.end) < 1e-4:
                    sweep = 2.0 * math.pi
                elif seg.direction == 'G03':  # CCW
                    sweep = a2 - a1
                    while sweep <= 0: sweep += 2.0 * math.pi
                else:  # G02 CW
                    sweep = a1 - a2
                    while sweep <= 0: sweep += 2.0 * math.pi

                steps = max(4, int(arc_steps * (sweep / (2.0 * math.pi))))
                for step in range(steps):
                    t = step / float(steps)
                    if seg.direction == 'G03':
                        ang = a1 + sweep * t
                    else:
                        ang = a1 - sweep * t
                    pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
        if self.segments:
            pts.append((self.segments[-1].end.x, self.segments[-1].end.y))
        return pts

    def bbox(self):
        pts = self.get_discrete_points()
        if not pts:
            return (0, 0, 0, 0)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))

    def signed_area(self) -> float:
        """Площадь полигона (положительная для CCW, отрицательная для CW)"""
        pts = self.get_discrete_points()
        if len(pts) < 3:
            return 0.0
        area = 0.0
        for i in range(len(pts) - 1):
            area += pts[i][0] * pts[i+1][1] - pts[i+1][0] * pts[i][1]
        return area * 0.5


# ==============================================================================
# 2. МАТЕМАТИКА ТРАНСФОРМАЦИЙ
# ==============================================================================

def transform_point(p: Point, swap_xy=False, rot_deg=0, flip_x=False, flip_y=False,
                    dx=0.0, dy=0.0, sheet_w=1500.0, sheet_h=3000.0) -> Point:
    x, y = p.x, p.y

    # 1. Смена осей X <-> Y
    if swap_xy:
        x, y = y, x

    # 2. Отражение по горизонтали / вертикали
    if flip_x:
        x = sheet_w - x
    if flip_y:
        y = sheet_h - y

    # 3. Вращение относительно (0, 0) или центра
    if rot_deg == 90:
        # 90 CW: (x, y) -> (y, -x) -> сдвиг в габарит
        x, y = y, sheet_w - x
    elif rot_deg == 180:
        x, y = sheet_w - x, sheet_h - y
    elif rot_deg == 270:
        x, y = sheet_h - y, x

    # 4. Смещение
    x += dx
    y += dy

    return Point(x, y)


def transform_contour(contour: Contour, swap_xy=False, rot_deg=0, flip_x=False, flip_y=False,
                      dx=0.0, dy=0.0, sheet_w=1500.0, sheet_h=3000.0) -> Contour:
    """
    Трансформация контура с математически строгим пересчетом направлений дуг.
    Отражения (flip_x, flip_y, swap_xy) имеют отрицательный детерминант,
    поэтому направление обхода дуг инвертируется (G02 <-> G03).
    """
    # Считаем количество отражений (каждое инвертирует ориентацию дуги)
    reflections = 0
    if swap_xy: reflections += 1
    if flip_x:  reflections += 1
    if flip_y:  reflections += 1
    invert_arc_dir = (reflections % 2 == 1)

    new_contour = Contour()
    new_contour.is_closed = contour.is_closed
    new_contour.is_hole = contour.is_hole
    new_contour.source_order = getattr(contour, 'source_order', 0)

    for seg in contour.segments:
        new_start = transform_point(seg.start, swap_xy, rot_deg, flip_x, flip_y, dx, dy, sheet_w, sheet_h)
        new_end = transform_point(seg.end, swap_xy, rot_deg, flip_x, flip_y, dx, dy, sheet_w, sheet_h)
        new_center = transform_point(seg.center, swap_xy, rot_deg, flip_x, flip_y, dx, dy, sheet_w, sheet_h) if seg.center else None

        new_dir = seg.direction
        if seg.type == 'arc' and invert_arc_dir:
            new_dir = 'G02' if seg.direction == 'G03' else 'G03'

        new_contour.add_segment(Segment(new_start, new_end, seg.type, new_dir, new_center))

    return new_contour


# ==============================================================================
# 3. ПАРСЕР G-КОДА (Lantek ISO)
# ==============================================================================

class IsoParser:
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.sheet_width = 1500.0
        self.sheet_height = 3000.0
        self.thickness = 4.0
        self.material = "SUS"
        self.cut_type = "Laser"
        self.program_name = os.path.splitext(os.path.basename(filepath))[0]
        self.closed_contours = []
        self.open_contours = []

    def parse(self):
        with open(self.filepath, 'r', encoding='latin1', errors='replace') as f:
            lines = [line.strip() for line in f]

        self._parse_header(lines)
        self._parse_trajectories(lines)

    def _parse_header(self, lines):
        # 0. Автоопределение материала и толщины из имени файла (например, "1118 FE6", "1849 FE6 01", "5377 SS4")
        fname = os.path.basename(self.filepath)
        mat_re = re.search(r'\b(FE|SS|SUS|AL|ST|CR)\s*(\d+(?:\.\d+)?)\b', fname, re.IGNORECASE)
        if mat_re:
            self.material = mat_re.group(1).upper()
            self.thickness = float(mat_re.group(2))

        # 1. Поиск строки ;LAMIERA Laser [Width] x [Height] x [Thickness]
        lamiera_re = re.compile(r';\s*(?:LAMIERA|SHEET)\s*(\w+)?\s*(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)(?:\s*x\s*(\d+(?:\.\d+)?))?', re.IGNORECASE)
        # 2. Поиск параметров станка P21 (X max), P23 (Y max)
        p21_re = re.compile(r'P21\s*=\s*(\d+(?:\.\d+)?)')
        p23_re = re.compile(r'P23\s*=\s*(\d+(?:\.\d+)?)')
        p10_re = re.compile(r'P10\s*=\s*(\d+(?:\.\d+)?)')  # высота прошивки / толщина

        found_lamiera = False
        p21_val, p23_val = None, None

        for line in lines[:50]:
            m = lamiera_re.search(line)
            if m:
                if m.group(1):
                    self.cut_type = m.group(1).strip()
                w = float(m.group(2))
                h = float(m.group(3))
                # В шапке Lantek: Width x Height x Thickness
                self.sheet_width = w
                self.sheet_height = h
                if m.group(4):
                    self.thickness = float(m.group(4))
                found_lamiera = True
                break

            m21 = p21_re.search(line)
            if m21: p21_val = float(m21.group(1))
            m23 = p23_re.search(line)
            if m23: p23_val = float(m23.group(1))

        if not found_lamiera and p21_val and p23_val:
            # Если комментария шапки нет, берем из параметров P
            self.sheet_width = p23_val
            self.sheet_height = p21_val

    def _parse_trajectories(self, lines):
        """Парсинг модальных перемещений между M50 и M51"""
        curr_x, curr_y = 0.0, 0.0
        modal_motion = 'G00'
        in_cut = False
        curr_contour = None

        x_re = re.compile(r'X\s*([-+]?\d*(?:\.\d+)?)', re.IGNORECASE)
        y_re = re.compile(r'Y\s*([-+]?\d*(?:\.\d+)?)', re.IGNORECASE)
        i_re = re.compile(r'I\s*([-+]?\d*(?:\.\d+)?)', re.IGNORECASE)
        j_re = re.compile(r'J\s*([-+]?\d*(?:\.\d+)?)', re.IGNORECASE)

        for line in lines:
            # Очистка комментария в строке
            clean_line = line.split(';')[0].strip()
            if not clean_line:
                continue

            # Команды включения/выключения дуги
            if 'M50' in clean_line:
                in_cut = True
                curr_contour = Contour()
                # Продолжаем с текущей позиции

            elif any(m in clean_line for m in ('M51', 'M56', 'M05', 'G100', 'M02', 'M30')):
                if in_cut and curr_contour and curr_contour.segments:
                    curr_contour.source_order = len(self.closed_contours) + len(self.open_contours)
                    if curr_contour.check_closed():
                        self.closed_contours.append(curr_contour)
                    else:
                        self.open_contours.append(curr_contour)
                in_cut = False
                curr_contour = None

            # Определение модального кода движения
            tokens = clean_line.upper().split()
            for tok in tokens:
                if tok in ('G00', 'G0', 'G1', 'G01', 'G2', 'G02', 'G3', 'G03'):
                    modal_motion = tok
                    if modal_motion == 'G0': modal_motion = 'G00'
                    elif modal_motion == 'G1': modal_motion = 'G01'
                    elif modal_motion == 'G2': modal_motion = 'G02'
                    elif modal_motion == 'G3': modal_motion = 'G03'

            # Извлечение координат X, Y
            mx = x_re.search(clean_line)
            my = y_re.search(clean_line)
            mi = i_re.search(clean_line)
            mj = j_re.search(clean_line)

            new_x = float(mx.group(1)) if mx and mx.group(1) != '' else curr_x
            new_y = float(my.group(1)) if my and my.group(1) != '' else curr_y

            # Если мы в блоке реза и есть движение
            if in_cut and curr_contour is not None:
                start_p = Point(curr_x, curr_y)
                end_p = Point(new_x, new_y)

                if modal_motion in ('G02', 'G03'):
                    # I и J — абсолютные координаты центра
                    cx = float(mi.group(1)) if mi and mi.group(1) != '' else curr_x
                    cy = float(mj.group(1)) if mj and mj.group(1) != '' else curr_y
                    r_start = math.hypot(start_p.x - cx, start_p.y - cy)
                    r_end = math.hypot(end_p.x - cx, end_p.y - cy)
                    # Строгая математическая валидация дуги: радиусы должны быть положительными и совпадать (допуск 1 мм)
                    if r_start > 1e-3 and r_end > 1e-3 and abs(r_start - r_end) <= 1.0:
                        seg = Segment(start_p, end_p, seg_type='arc', direction=modal_motion, center=Point(cx, cy))
                        curr_contour.add_segment(seg)
                elif modal_motion == 'G01':
                    if start_p.dist(end_p) > 1e-4:
                        seg = Segment(start_p, end_p, seg_type='line', direction='G01')
                        curr_contour.add_segment(seg)
                elif modal_motion == 'G00':
                    # Быстрое перемещение внутри реза (если встретится)
                    pass

            curr_x = new_x
            curr_y = new_y


# ==============================================================================
# 4. ТОПОЛОГИЯ ДЕТАЛЕЙ (POINT-IN-POLYGON & HIERARCHY)
# ==============================================================================

def point_in_polygon(x: float, y: float, poly_pts) -> bool:
    """Ray casting algorithm: тест нахождения точки внутри замкнутого контура"""
    inside = False
    n = len(poly_pts)
    if n < 3:
        return False
    p1x, p1y = poly_pts[0]
    for i in range(1, n):
        p2x, p2y = poly_pts[i]
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xinters:
                        inside = not inside
        p1x, p1y = p2x, p2y
    return inside


class PartInstance:
    """Экземпляр детали на листе (внешний периметр + вложенные отверстия)"""
    def __init__(self, outer_contour: Contour):
        self.outer_contour = outer_contour
        self.holes = []
        self.guid = str(uuid.uuid4())
        self.master_guid = None
        self.part_name = "Part"
        self.insert_angle_rad = 0.0

    def add_hole(self, hole_contour: Contour):
        hole_contour.is_hole = True
        self.holes.append(hole_contour)

    def bbox(self):
        return self.outer_contour.bbox()


def rotate_points_canonical(pts, ang_deg):
    """Поворот точек вокруг (0,0) и сдвиг в локальный ноль (0,0)"""
    rad = math.radians(ang_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    rot = [(x * cos_a - y * sin_a, x * sin_a + y * cos_a) for x, y in pts]
    min_x = min(p[0] for p in rot)
    min_y = min(p[1] for p in rot)
    return [(p[0] - min_x, p[1] - min_y) for p in rot]


def parts_match_rotation(part_a: PartInstance, part_b: PartInstance, tol=1.2):
    """
    Проверяет, является ли деталь part_b повернутой копией part_a (на 0, 90, 180 или 270 градусов).
    Возвращает угол поворота в градусах (0, 90, 180, 270) или None, если геометрии не совпадают.
    """
    if len(part_a.holes) != len(part_b.holes):
        return None
    area_a = abs(part_a.outer_contour.signed_area())
    area_b = abs(part_b.outer_contour.signed_area())
    if abs(area_a - area_b) / max(1.0, area_a) > 0.03:
        return None

    pts_a = rotate_points_canonical(part_a.outer_contour.get_discrete_points(), 0)
    raw_pts_b = part_b.outer_contour.get_discrete_points()

    for ang in (0, 90, 180, 270):
        pts_b_rot = rotate_points_canonical(raw_pts_b, ang)
        if len(pts_a) != len(pts_b_rot):
            continue
        all_close = True
        for pa in pts_a:
            if not any(math.hypot(pa[0] - pb[0], pa[1] - pb[1]) < tol for pb in pts_b_rot):
                all_close = False
                break
        if all_close:
            return ang
    return None


def compute_part_geom_key(part: PartInstance):
    """
    Уникальный геометрический ключ детали (инвариантный к поворотам на 90/180/270).
    Включает габариты, площадь внешнего контура, количество и площади отверстий.
    """
    bx = part.bbox()
    w = round(bx[2] - bx[0], 1)
    h = round(bx[3] - bx[1], 1)
    area = round(abs(part.outer_contour.signed_area()), 0)
    num_holes = len(part.holes)
    holes_sig = tuple(sorted(round(abs(hole.signed_area()), 0) for hole in part.holes))
    return (min(w, h), max(w, h), area, num_holes, holes_sig)


def build_part_hierarchy(closed_contours):
    """
    Классификация замкнутых контуров на внешние периметры и внутренние отверстия.
    Контур является отверстием, если он лежит внутри другого замкнутого контура.
    Порядок следования деталей и отверстий сохраняется в точности по исходной резке ISO.
    """
    if not closed_contours:
        return []

    # Предварительно вычисляем дискретные полигоны, площади и bbox
    contour_data = []
    for idx, c in enumerate(closed_contours):
        pts = c.get_discrete_points()
        area = abs(c.signed_area())
        bx = c.bbox()
        contour_data.append({
            'contour': c,
            'pts': pts,
            'area': area,
            'bbox': bx,
            'source_order': getattr(c, 'source_order', idx)
        })

    # Сортируем по убыванию площади ТОЛЬКО для топологической проверки вложенности:
    # большие контуры проверяются первыми
    contour_data.sort(key=lambda d: d['area'], reverse=True)

    parts = []
    assigned_as_hole = set()

    for i in range(len(contour_data)):
        if i in assigned_as_hole:
            continue

        c_outer = contour_data[i]['contour']
        part = PartInstance(c_outer)
        outer_pts = contour_data[i]['pts']
        outer_bbox = contour_data[i]['bbox']

        # Проверяем остальные контуры меньшей площади: не лежат ли они внутри этого?
        for j in range(i + 1, len(contour_data)):
            if j in assigned_as_hole:
                continue

            inner_bbox = contour_data[j]['bbox']
            # Быстрый тест по bbox
            if (inner_bbox[0] >= outer_bbox[0] - 0.1 and
                inner_bbox[1] >= outer_bbox[1] - 0.1 and
                inner_bbox[2] <= outer_bbox[2] + 0.1 and
                inner_bbox[3] <= outer_bbox[3] + 0.1):

                # Тест точки контура (берем первую точку внутреннего контура)
                test_pt = contour_data[j]['pts'][0]
                if point_in_polygon(test_pt[0], test_pt[1], outer_pts):
                    part.add_hole(contour_data[j]['contour'])
                    assigned_as_hole.add(j)

        # Сортируем отверстия внутри детали в порядке их вырезания в ISO
        part.holes.sort(key=lambda h: getattr(h, 'source_order', 0))

        # Хронологический индекс детали в УП (по первому срезанному элементу - отверстию или контуру)
        all_orders = [getattr(c_outer, 'source_order', 0)] + [getattr(h, 'source_order', 0) for h in part.holes]
        part.source_order = min(all_orders)

        parts.append(part)

    # Восстанавливаем строгий хронологический порядок резки из ISO файла
    parts.sort(key=lambda p: getattr(p, 'source_order', 0))

    return parts


# ==============================================================================
# 5. МОДЕЛЬ ДАННЫХ И ГЕНЕРАТОР XML CYPNEST (.NSP)
# ==============================================================================

class PlateData:
    """Модель данных одного листа программы раскроя"""
    def __init__(self, file_path: str, plate_name: str = "",
                 sheet_width: float = 1500.0, sheet_height: float = 3000.0,
                 thickness: float = 6.0, material: str = "FE",
                 repeat_count: int = 1, include_cutoff: bool = False):
        self.file_path = file_path
        self.plate_name = plate_name or (os.path.splitext(os.path.basename(file_path))[0] if file_path else "Plate")
        self.sheet_width = sheet_width
        self.sheet_height = sheet_height
        self.thickness = thickness
        self.material = material
        self.repeat_count = max(1, int(repeat_count))
        self.include_cutoff = include_cutoff  # По умолчанию False: обрезка листа не переносится в NSP
        self.guid = str(uuid.uuid4())

        # Исходные контуры (в координатах станка)
        self.raw_closed_contours = []
        self.raw_open_contours = []

        # Трансформированные детали и открытые резы
        self.parts = []
        self.open_contours = []

        # Параметры трансформации
        self.swap_xy = True
        self.rot_deg = 0
        self.flip_x = False
        self.flip_y = True
        self.dx = 0.0
        self.dy = 0.0

    def apply_transformations(self):
        """Пересчет геометрии с учетом трансформаций листа и сборка топологии деталей"""
        transformed_closed = [
            transform_contour(c, swap_xy=self.swap_xy, rot_deg=self.rot_deg,
                              flip_x=self.flip_x, flip_y=self.flip_y,
                              dx=self.dx, dy=self.dy,
                              sheet_w=self.sheet_width, sheet_h=self.sheet_height)
            for c in self.raw_closed_contours
        ]
        transformed_open = [
            transform_contour(c, swap_xy=self.swap_xy, rot_deg=self.rot_deg,
                              flip_x=self.flip_x, flip_y=self.flip_y,
                              dx=self.dx, dy=self.dy,
                              sheet_w=self.sheet_width, sheet_h=self.sheet_height)
            for c in self.raw_open_contours
        ]
        self.parts = build_part_hierarchy(transformed_closed)
        self.open_contours = transformed_open


class NestingProject:
    """Модель многолистового проекта раскроя (Nesting Job)"""
    def __init__(self, job_name: str = ""):
        self.job_name = job_name
        self.plates = []  # list[PlateData]
        self.custom_names = {}  # geom_key -> custom_name

    def add_plate(self, plate: PlateData):
        self.plates.append(plate)
        if not self.job_name:
            self.job_name = self.detect_job_name()

    def remove_plate(self, idx: int):
        if 0 <= idx < len(self.plates):
            del self.plates[idx]
            if self.plates:
                self.job_name = self.detect_job_name()

    def move_plate(self, idx: int, direction: int):
        target = idx + direction
        if 0 <= target < len(self.plates) and 0 <= idx < len(self.plates):
            self.plates[idx], self.plates[target] = self.plates[target], self.plates[idx]

    def detect_job_name(self) -> str:
        """Автоопределение имени заказа по общему префиксу имен файлов"""
        if not self.plates:
            return "NestingJob"
        names = [p.plate_name for p in self.plates]
        if len(names) == 1:
            return names[0]
        prefix = os.path.commonprefix(names).strip()
        clean = re.sub(r'[\s_\-#]+$|\s+\d+$', '', prefix).strip()
        clean = re.sub(r'[\s_\-#]+$', '', clean).strip()
        return clean if len(clean) >= 3 else names[0]

    def deduplicate_global_master_parts(self):
        """
        Глобальная дедупликация деталей по всем листам проекта (с учетом поворотов 90/180/270).
        Суммарный тираж вычисляется как sum(count_on_plate * plate.repeat_count).
        """
        master_list = []
        for plate in self.plates:
            for part in plate.parts:
                found_master = None
                found_angle = 0
                for m in master_list:
                    ang = parts_match_rotation(m['prototype'], part)
                    if ang is not None:
                        found_master = m
                        found_angle = ang
                        break

                if found_master is None:
                    master_guid = str(uuid.uuid4())
                    bx = part.bbox()
                    w = round(bx[2] - bx[0], 2)
                    h = round(bx[3] - bx[1], 2)
                    geom_key = compute_part_geom_key(part)

                    def_name = self.custom_names.get(
                        geom_key,
                        part.part_name if (part.part_name and part.part_name != "Part") else f"{self.job_name}_P{len(master_list) + 1}"
                    )

                    new_master = {
                        'guid': master_guid,
                        'name': def_name,
                        'key': geom_key,
                        'width': w,
                        'height': h,
                        'thickness': plate.thickness,
                        'material': plate.material,
                        'prototype': part,
                        'instances': [part],
                        'total_qty': plate.repeat_count
                    }
                    master_list.append(new_master)
                    part.master_guid = master_guid
                    part.part_name = def_name
                    part.insert_angle_rad = 0.0
                else:
                    found_master['instances'].append(part)
                    found_master['total_qty'] += plate.repeat_count
                    part.master_guid = found_master['guid']
                    part.part_name = found_master['name']
                    part.insert_angle_rad = math.radians(found_angle)

        return master_list

    def _append_polyline(self, parent_elem, contour: Contour, layer=None, offset_x=0.0, offset_y=0.0):
        poly = ET.SubElement(parent_elem, 'PolylineNode')
        common_prop_attr = {'tlayerb': '0'}
        if layer:
            common_prop_attr['layer'] = layer
        ET.SubElement(poly, 'CommonProperty', common_prop_attr)
        plist = ET.SubElement(poly, 'pListAndBulges')
        origin_circle = None

        for seg in contour.segments:
            sx = seg.start.x - offset_x
            sy = seg.start.y - offset_y
            ex = seg.end.x - offset_x
            ey = seg.end.y - offset_y

            if seg.type == 'line':
                ET.SubElement(plist, 'endNode', {'x': f"{sx:.4f}", 'y': f"{sy:.4f}"})
            elif seg.type == 'arc' and seg.center:
                cx = seg.center.x - offset_x
                cy = seg.center.y - offset_y
                r = math.hypot(sx - cx, sy - cy)

                if seg.start.dist(seg.end) < 1e-4:
                    bul_val = "1" if seg.direction == 'G03' else "-1"
                    ET.SubElement(plist, 'endNode', {'x': f"{sx:.4f}", 'y': f"{sy:.4f}", 'bul': bul_val})
                    opp_x = 2.0 * cx - sx
                    opp_y = 2.0 * cy - sy
                    ET.SubElement(plist, 'endNode', {'x': f"{opp_x:.4f}", 'y': f"{opp_y:.4f}", 'bul': bul_val})
                    origin_circle = (cx, cy, r)
                else:
                    a1 = math.atan2(sy - cy, sx - cx)
                    a2 = math.atan2(ey - cy, ex - cx)
                    if seg.direction == 'G03':
                        sweep = a2 - a1
                        while sweep <= 0: sweep += 2.0 * math.pi
                        bulge = math.tan(sweep / 4.0)
                    else:
                        sweep = a1 - a2
                        while sweep <= 0: sweep += 2.0 * math.pi
                        bulge = -math.tan(sweep / 4.0)

                    ET.SubElement(plist, 'endNode', {
                        'x': f"{sx:.4f}",
                        'y': f"{sy:.4f}",
                        'bul': f"{bulge:.6f}"
                    })

        last_seg = contour.segments[-1]
        last_x = last_seg.end.x - offset_x
        last_y = last_seg.end.y - offset_y
        ET.SubElement(plist, 'endNode', {'x': f"{last_x:.4f}", 'y': f"{last_y:.4f}"})

        if origin_circle:
            orig = ET.SubElement(poly, 'originShape')
            circle = ET.SubElement(orig, 'CircleNode')
            ET.SubElement(circle, 'CommonProperty')
            ET.SubElement(circle, 'infos', {
                'x': f"{origin_circle[0]:.4f}",
                'y': f"{origin_circle[1]:.4f}",
                'r': f"{origin_circle[2]:.4f}"
            })

    def generate_xml_string(self) -> str:
        first_plate_guid = self.plates[0].guid if self.plates else str(uuid.uuid4())
        project_material = self.plates[0].material if self.plates else "FE"
        root = ET.Element('root', {
            'V2': '1.0.0.48168',
            'ShowModel': '3',
            'ShowItemGuid': first_plate_guid,
            'GAinfo': '2DCut',
            'NormalMaterial': project_material
        })

        ET.SubElement(root, 'StopPos', {'x': '10', 'y': '10'})
        ET.SubElement(root, 'NestParam', {
            'MinComnEdgeLen': '1',
            'NestCombType': '1',
            'NestTimeYT': '1',
            'inRotateAngle': '90',
            'nestAllGrid': 'true',
            'nestAngle': '0',
            'nestDir': '1',
            'nestDis': '5',
            'nestDisBoard': '10',
            'nestNextGen': 'true',
            'resolution': '0.5'
        })

        grp = ET.SubElement(root, 'GroupNode')
        ET.SubElement(grp, 'CommonProperty', {'PartClr': '-1', 'partNsetInfo': '-1'})
        ET.SubElement(grp, 'shapes')

        # 1. Библиотека эталонов CutPieceNode
        master_parts = self.deduplicate_global_master_parts()
        for master in master_parts:
            proto = master['prototype']
            bx = proto.bbox()
            offset_x, offset_y = bx[0], bx[1]

            cut_piece = ET.SubElement(root, 'CutPieceNode')
            ET.SubElement(cut_piece, 'CutPieceInfo', {
                'Guid': master['guid'],
                'selected': 'true',
                'partName': master['name'],
                'partProcessNum': str(master['total_qty']),
                'totalnum': str(master['total_qty']),
                'material': master['material'],
                'nestRotateStep': '90',
                'nestPartFitType': '2',
                'partThickness': f"{master['thickness']:.1f}",
                'partWidth': f"{master['width']:.3f}",
                'partHeight': f"{master['height']:.3f}"
            })
            shapes = ET.SubElement(cut_piece, 'shapes', {'PartClr': '-13645824'})
            for hole in proto.holes:
                self._append_polyline(shapes, hole, layer="I", offset_x=offset_x, offset_y=offset_y)
            self._append_polyline(shapes, proto.outer_contour, layer=None, offset_x=offset_x, offset_y=offset_y)

        # 2. Список листов PlateNode
        for plate in self.plates:
            plate_node = ET.SubElement(root, 'PlateNode')
            parts_area = sum(abs(p.outer_contour.signed_area()) for p in plate.parts)
            sheet_area = max(1.0, plate.sheet_width * plate.sheet_height)
            utilization = min(1.0, parts_area / sheet_area)

            ET.SubElement(plate_node, 'PlateNodeInfo', {
                'EdgeDistance': '10',
                'Guid': plate.guid,
                'Height': f"{plate.sheet_height:.0f}",
                'Material': plate.material,
                'PlateName': plate.plate_name,
                'Thickness': f"{plate.thickness:.1f}",
                'Width': f"{plate.sheet_width:.0f}",
                'UtilizationRate': f"{utilization:.4f}",
                'PlateProcessNum': str(plate.repeat_count),
                'TotalNum': str(plate.repeat_count),
                'selected': 'true'
            })

            shapes = ET.SubElement(plate_node, 'shapes')
            for part in plate.parts:
                part_node = ET.SubElement(shapes, 'PartNode')
                ET.SubElement(part_node, 'CommonProperty', {
                    'PartClr': '-8413185',
                    'partGuid': part.master_guid,
                    'partName': part.part_name,
                    'partNsetInfo': '-1'
                })
                part_shapes = ET.SubElement(part_node, 'shapes')
                for hole in part.holes:
                    self._append_polyline(part_shapes, hole, layer="I")
                self._append_polyline(part_shapes, part.outer_contour, layer=None)

            if getattr(plate, 'include_cutoff', False):
                for open_cut in plate.open_contours:
                    self._append_polyline(shapes, open_cut, layer=None)

            prof_info = ET.SubElement(plate_node, 'PlateProfilesInfo')
            for part in plate.parts:
                bx = part.bbox()
                ET.SubElement(prof_info, 'PlateLocationInfo', {
                    'Guid': part.master_guid,
                    'PartInsertAngles': f"{part.insert_angle_rad:.7f}",
                    'PartInsertX': f"{bx[0]:.3f}",
                    'PartInsertY': f"{bx[1]:.3f}"
                })

            board_pnts = ET.SubElement(plate_node, 'PlateBoardPnts')
            pnt1s = ET.SubElement(board_pnts, 'PlateBoardPnt1s')
            ET.SubElement(pnt1s, 'PlateBoardPnt')
            ET.SubElement(pnt1s, 'PlateBoardPnt', {'x': f"{plate.sheet_width:.0f}"})
            ET.SubElement(pnt1s, 'PlateBoardPnt', {'x': f"{plate.sheet_width:.0f}", 'y': f"{plate.sheet_height:.0f}"})
            ET.SubElement(pnt1s, 'PlateBoardPnt', {'y': f"{plate.sheet_height:.0f}"})
            ET.SubElement(pnt1s, 'PlateBoardPnt')

        rough_string = ET.tostring(root, encoding='utf-8')
        reparsed = minidom.parseString(rough_string)
        return reparsed.toprettyxml(indent="  ", encoding="utf-8").decode('utf-8')


class NspGenerator:
    """Генератор XML для обратной совместимости с однолистовым API"""
    def __init__(self, sheet_width: float, sheet_height: float, thickness: float,
                 material: str, plate_name: str, parts, open_contours):
        self.sheet_width = sheet_width
        self.sheet_height = sheet_height
        self.thickness = thickness
        self.material = material
        self.plate_name = plate_name
        self.parts = parts
        self.open_contours = open_contours

    def generate_xml_string(self) -> str:
        plate = PlateData(
            file_path="",
            plate_name=self.plate_name,
            sheet_width=self.sheet_width,
            sheet_height=self.sheet_height,
            thickness=self.thickness,
            material=self.material,
            repeat_count=1
        )
        plate.parts = self.parts
        plate.open_contours = self.open_contours

        project = NestingProject(job_name=self.plate_name)
        project.add_plate(plate)
        return project.generate_xml_string()



# ==============================================================================
# 6. КОНВЕРТЕР ВЫСОКОГО УРОВНЯ
# ==============================================================================

def convert_iso_to_nsp(input_paths, output_path: str,
                       job_name=None,
                       orientation='auto', swap_axes=None, rotate_deg=0,
                       flip_x=False, flip_y=False,
                       sheet_size=None, thickness=None, material=None,
                       repeats=None, pdf_output=None,
                       include_cutoff=False, lantek_pdfs=None):
    """
    Пайплайн конвертации одного или нескольких ISO/CNC файлов в проект CypNest .nsp
    """
    if isinstance(input_paths, str):
        raw_paths = [input_paths]
    else:
        raw_paths = list(input_paths)

    cnc_paths = [p for p in raw_paths if not p.lower().endswith('.pdf')]
    pdf_list = [p for p in raw_paths if p.lower().endswith('.pdf')]
    if lantek_pdfs:
        pdf_list.extend(lantek_pdfs)

    project = NestingProject(job_name=job_name or "")

    for idx, path in enumerate(cnc_paths):
        parser = IsoParser(path)
        parser.parse()

        sw, sh = parser.sheet_width, parser.sheet_height
        if sheet_size:
            sw, sh = map(float, sheet_size.lower().split('x'))
        th = float(thickness) if thickness is not None else parser.thickness
        mat = material if material is not None else parser.material
        rep = repeats[idx] if (repeats and idx < len(repeats)) else 1

        auto_swap = False
        auto_flip_y = False
        if orientation == 'auto':
            if sw <= sh:
                auto_swap = True
                auto_flip_y = True
        elif orientation == 'vertical':
            if sw > sh:
                sw, sh = sh, sw
            auto_swap = True
            auto_flip_y = True
        elif orientation == 'horizontal':
            if sw < sh:
                sw, sh = sh, sw

        do_swap = swap_axes if swap_axes is not None else auto_swap
        do_flip_y = flip_y if flip_y is not None else auto_flip_y

        plate = PlateData(
            file_path=path,
            plate_name=parser.program_name,
            sheet_width=sw,
            sheet_height=sh,
            thickness=th,
            material=mat,
            repeat_count=rep,
            include_cutoff=include_cutoff
        )
        plate.raw_closed_contours = parser.closed_contours
        plate.raw_open_contours = parser.open_contours
        plate.swap_xy = do_swap
        plate.rot_deg = rotate_deg
        plate.flip_x = flip_x
        plate.flip_y = do_flip_y
        plate.apply_transformations()

        project.add_plate(plate)

    # Применение метаданных из отчетов Lantek PDF, если они переданы
    if pdf_list:
        try:
            from lantek_pdf import parse_lantek_pdf, apply_lantek_reports_to_project
            all_reports = []
            for pf in pdf_list:
                all_reports.extend(parse_lantek_pdf(pf))
            res = apply_lantek_reports_to_project(project, all_reports)
            if res.get('matched_plates'):
                print(f"[OK] Подвязано отчетов Lantek PDF: {res['matched_plates']}, переименовано деталей: {res['renamed_parts']}")
        except ImportError:
            print("[WARN] Для чтения отчетов Lantek PDF требуется библиотека pypdf: pip install pypdf")
        except Exception as e:
            print(f"[WARN] Ошибка при чтении отчетов Lantek PDF: {e}")

    if job_name:
        project.job_name = job_name
    else:
        project.job_name = project.detect_job_name()

    xml_text = project.generate_xml_string()

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(xml_text)

    total_parts = sum(len(p.parts) for p in project.plates)
    print(f"[OK] Конвертация завершена: {output_path}")
    print(f"     Проект '{project.job_name}': Листов: {len(project.plates)}, Всего деталей: {total_parts}")

    if pdf_output:
        try:
            from pdf_report import generate_pdf_report
            generate_pdf_report(project, pdf_output)
            print(f"[OK] Сформирован производственный PDF-отчет: {pdf_output}")
        except ImportError:
            print("[WARN] Для генерации PDF-отчетов требуется библиотека reportlab: pip install reportlab")
        except Exception as e:
            print(f"[ERROR] Не удалось создать PDF-отчет: {e}")


# ==============================================================================
# 7. CLI ТОЧКА ВХОДА
# ==============================================================================

def main():
    arg_parser = argparse.ArgumentParser(
        description="iso2nsp: Конвертер G-кода ISO ЧПУ в проект раскроя CypNest .nsp"
    )
    arg_parser.add_argument("inputs", nargs="+", help="Один или несколько входных файлов .iso / .cnc / .pdf")
    arg_parser.add_argument("-o", "--output", help="Выходной файл .nsp (по умолчанию: {имя_заказа}.nsp)")
    arg_parser.add_argument("--job-name", help="Наименование программы / заказа")
    arg_parser.add_argument("--repeats", nargs="*", type=int, help="Тираж для каждого листа (например, 1 2 1)")
    arg_parser.add_argument("--orientation", choices=['auto', 'vertical', 'horizontal'], default='auto',
                            help="Ориентация листа (по умолчанию: auto)")
    arg_parser.add_argument("--swap-axes", action="store_true", help="Принудительно поменять оси X и Y местами")
    arg_parser.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=0,
                            help="Доворот всей карты раскроя (град)")
    arg_parser.add_argument("--flip-x", action="store_true", help="Отразить раскрой по горизонтали")
    arg_parser.add_argument("--flip-y", action="store_true", help="Отразить раскрой по вертикали")
    arg_parser.add_argument("--sheet-size", help="Размер листа в формате WxH (например: 1500x3000)")
    arg_parser.add_argument("--thickness", type=float, help="Толщина листа в мм")
    arg_parser.add_argument("--material", help="Марка материала (например: FE, SS, AL)")
    arg_parser.add_argument("--pdf", nargs="?", const="auto", help="Сформировать производственный PDF-отчет (стиль Lantek CAM)")
    arg_parser.add_argument("--include-cutoff", action="store_true", help="Включить перенос линий обрезки листа в NSP (по умолчанию: отключено)")
    arg_parser.add_argument("--lantek-pdf", nargs="*", help="Файлы отчетов Lantek PDF для автоматического назначения имен и тиражей")

    args = arg_parser.parse_args()

    if args.output:
        out_file = args.output
    elif len(args.inputs) == 1:
        out_file = os.path.splitext(args.inputs[0])[0] + ".nsp"
    else:
        names = [os.path.splitext(os.path.basename(f))[0] for f in args.inputs if not f.lower().endswith('.pdf')]
        if not names:
            names = [os.path.splitext(os.path.basename(f))[0] for f in args.inputs]
        prefix = re.sub(r'[\s_\-#]+$', '', os.path.commonprefix(names).strip())
        out_file = (prefix if len(prefix) >= 3 else names[0]) + ".nsp"

    pdf_out = None
    if args.pdf:
        pdf_out = os.path.splitext(out_file)[0] + ".pdf" if args.pdf == "auto" else args.pdf

    convert_iso_to_nsp(
        input_paths=args.inputs,
        output_path=out_file,
        job_name=args.job_name,
        orientation=args.orientation,
        swap_axes=args.swap_axes if args.swap_axes else None,
        rotate_deg=args.rotate,
        flip_x=args.flip_x,
        flip_y=args.flip_y,
        sheet_size=args.sheet_size,
        thickness=args.thickness,
        material=args.material,
        repeats=args.repeats,
        pdf_output=pdf_out,
        include_cutoff=args.include_cutoff,
        lantek_pdfs=args.lantek_pdf
    )


if __name__ == "__main__":
    main()
