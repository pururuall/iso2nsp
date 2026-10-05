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
        if pts:
            pts.append(pts[0])
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

            elif 'M51' in clean_line or 'G100' in clean_line:
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
# 5. ГЕНЕРАТОР XML ФОРМАТА CYPNEST (.NSP)
# ==============================================================================

class NspGenerator:
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
        root = ET.Element('root', {
            'V2': '1.0.0.48168',
            'ShowModel': '2',
            'GAinfo': '2DCut',
            'NormalMaterial': self.material
        })

        ET.SubElement(root, 'StopPos')
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

        # 1. Группировка одинаковых деталей в эталоны CutPieceNode
        master_parts = self._deduplicate_master_parts()

        for master in master_parts:
            self._build_cut_piece_node(root, master)

        # 2. Размещение на листе PlateNode
        self._build_plate_node(root)

        # Pretty-print
        rough_string = ET.tostring(root, encoding='utf-8')
        reparsed = minidom.parseString(rough_string)
        return reparsed.toprettyxml(indent="  ", encoding="utf-8").decode('utf-8')

    def _deduplicate_master_parts(self):
        """
        Группировка одинаковых деталей на листе, включая повернутые копии (0, 90, 180, 270 град).
        Каждая уникальная геометрия переносится в локальные координаты (0,0)
        и становится мастер-шаблоном CutPieceNode.
        """
        master_list = []

        for idx, part in enumerate(self.parts):
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
                master_name = part.part_name if (part.part_name and part.part_name != "Part") else f"{self.plate_name}_P{len(master_list) + 1}"

                new_master = {
                    'guid': master_guid,
                    'name': master_name,
                    'width': w,
                    'height': h,
                    'count': 1,
                    'prototype': part
                }
                master_list.append(new_master)
                part.master_guid = master_guid
                part.part_name = master_name
                part.insert_angle_rad = 0.0
            else:
                found_master['count'] += 1
                part.master_guid = found_master['guid']
                part.part_name = found_master['name']
                part.insert_angle_rad = math.radians(found_angle)

        return master_list

    def _build_cut_piece_node(self, parent_elem, master_info):
        """Создание узла библиотеки эталонов в локальных координатах (0, 0)"""
        proto = master_info['prototype']
        bx = proto.bbox()
        offset_x, offset_y = bx[0], bx[1]

        cut_piece = ET.SubElement(parent_elem, 'CutPieceNode')
        ET.SubElement(cut_piece, 'CutPieceInfo', {
            'Guid': master_info['guid'],
            'material': self.material,
            'partHeight': f"{master_info['height']:.3f}",
            'partName': master_info['name'],
            'partProcessNum': str(master_info['count']),
            'totalnum': str(master_info['count']),
            'partThickness': f"{self.thickness:.1f}",
            'partWidth': f"{master_info['width']:.3f}",
            'nestRotateStep': '90',
            'nestPartFitType': '2'
        })

        shapes = ET.SubElement(cut_piece, 'shapes', {'PartClr': '-13645824'})

        # Сначала отверстия с layer="I" в локальных координатах
        for hole in proto.holes:
            self._append_polyline(shapes, hole, layer="I", offset_x=offset_x, offset_y=offset_y)

        # Внешний периметр детали
        self._append_polyline(shapes, proto.outer_contour, layer=None, offset_x=offset_x, offset_y=offset_y)

    def _build_plate_node(self, parent_elem):
        """Создание узла листа с экземплярами деталей в абсолютных координатах"""
        plate = ET.SubElement(parent_elem, 'PlateNode')
        plate_guid = str(uuid.uuid4())

        # Расчет коэффициента использования
        parts_area = sum(abs(p.outer_contour.signed_area()) for p in self.parts)
        sheet_area = max(1.0, self.sheet_width * self.sheet_height)
        utilization = min(1.0, parts_area / sheet_area)

        ET.SubElement(plate, 'PlateNodeInfo', {
            'EdgeDistance': '10',
            'Guid': plate_guid,
            'Height': f"{self.sheet_height:.0f}",
            'Material': self.material,
            'PlateName': self.plate_name,
            'Thickness': f"{self.thickness:.1f}",
            'UtilizationRate': f"{utilization:.4f}",
            'Width': f"{self.sheet_width:.0f}"
        })

        shapes = ET.SubElement(plate, 'shapes')

        # Экземпляры деталей в абсолютных координатах листа
        for part in self.parts:
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

        # Разомкнутые технологические резы
        for open_cut in self.open_contours:
            self._append_polyline(shapes, open_cut, layer=None)

        # Позиции вставки деталей PlateProfilesInfo
        prof_info = ET.SubElement(plate, 'PlateProfilesInfo')
        for part in self.parts:
            bx = part.bbox()
            ET.SubElement(prof_info, 'PlateLocationInfo', {
                'Guid': part.master_guid,
                'PartInsertAngles': f"{part.insert_angle_rad:.7f}",
                'PartInsertX': f"{bx[0]:.3f}",
                'PartInsertY': f"{bx[1]:.3f}"
            })

        # Граница листа PlateBoardPnts
        board_pnts = ET.SubElement(plate, 'PlateBoardPnts')
        pnt1s = ET.SubElement(board_pnts, 'PlateBoardPnt1s')
        ET.SubElement(pnt1s, 'PlateBoardPnt')
        ET.SubElement(pnt1s, 'PlateBoardPnt', {'x': f"{self.sheet_width:.0f}"})
        ET.SubElement(pnt1s, 'PlateBoardPnt', {'x': f"{self.sheet_width:.0f}", 'y': f"{self.sheet_height:.0f}"})
        ET.SubElement(pnt1s, 'PlateBoardPnt', {'y': f"{self.sheet_height:.0f}"})
        ET.SubElement(pnt1s, 'PlateBoardPnt')

    def _append_polyline(self, parent_elem, contour: Contour, layer=None, offset_x=0.0, offset_y=0.0):
        """
        Сериализация контура в <PolylineNode> с коэффициентами прогиба bul:
        bul = tan(theta / 4)
        """
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

                # Проверка: полная окружность 360 градусов
                if seg.start.dist(seg.end) < 1e-4:
                    bul_val = "1" if seg.direction == 'G03' else "-1"
                    # Узел 1: начальная точка
                    ET.SubElement(plist, 'endNode', {'x': f"{sx:.4f}", 'y': f"{sy:.4f}", 'bul': bul_val})
                    # Узел 2: диаметрально противоположная точка (2*cx - sx, 2*cy - sy)
                    opp_x = 2.0 * cx - sx
                    opp_y = 2.0 * cy - sy
                    ET.SubElement(plist, 'endNode', {'x': f"{opp_x:.4f}", 'y': f"{opp_y:.4f}", 'bul': bul_val})
                    # originShape CircleNode
                    origin_circle = (cx, cy, r)
                else:
                    # Стандартная дуга
                    a1 = math.atan2(sy - cy, sx - cx)
                    a2 = math.atan2(ey - cy, ex - cx)
                    if seg.direction == 'G03':  # CCW
                        sweep = a2 - a1
                        while sweep <= 0: sweep += 2.0 * math.pi
                        bulge = math.tan(sweep / 4.0)
                    else:  # G02 CW
                        sweep = a1 - a2
                        while sweep <= 0: sweep += 2.0 * math.pi
                        bulge = -math.tan(sweep / 4.0)

                    ET.SubElement(plist, 'endNode', {
                        'x': f"{sx:.4f}",
                        'y': f"{sy:.4f}",
                        'bul': f"{bulge:.6f}"
                    })

        # Замыкающий узел полилинии
        last_seg = contour.segments[-1]
        last_x = last_seg.end.x - offset_x
        last_y = last_seg.end.y - offset_y
        ET.SubElement(plist, 'endNode', {'x': f"{last_x:.4f}", 'y': f"{last_y:.4f}"})

        # Если была полная окружность, сохраняем аналитический originShape
        if origin_circle:
            orig = ET.SubElement(poly, 'originShape')
            circle = ET.SubElement(orig, 'CircleNode')
            ET.SubElement(circle, 'CommonProperty')
            ET.SubElement(circle, 'infos', {
                'x': f"{origin_circle[0]:.4f}",
                'y': f"{origin_circle[1]:.4f}",
                'r': f"{origin_circle[2]:.4f}"
            })


# ==============================================================================
# 6. КОНВЕРТЕР ВЫСОКОГО УРОВНЯ
# ==============================================================================

def convert_iso_to_nsp(input_path: str, output_path: str,
                       orientation='auto', swap_axes=None, rotate_deg=0,
                       flip_x=False, flip_y=False,
                       sheet_size=None, thickness=None, material="SUS"):
    """
    Полный пайплайн конвертации ISO -> NSP с учетом ориентации и трансформаций
    """
    parser = IsoParser(input_path)
    parser.parse()

    # Переопределение параметров если заданы
    if sheet_size:
        sw, sh = map(float, sheet_size.lower().split('x'))
        parser.sheet_width, parser.sheet_height = sw, sh
    if thickness:
        parser.thickness = float(thickness)
    if material:
        parser.material = material

    # Определение необходимости смены осей и отражения Y
    # В CAM Lantek для вертикального листа (W <= H):
    # X станка идет вдоль длины листа, а ноль станка соответствует верхнему краю листа в Lantek.
    # Правильное соответствие: X_sheet = Y_iso, Y_sheet = H_sheet - X_iso (swap_xy=True, flip_y=True).
    auto_swap = False
    auto_flip_y = False
    if orientation == 'auto':
        if parser.sheet_width <= parser.sheet_height:
            auto_swap = True
            auto_flip_y = True
    elif orientation == 'vertical':
        if parser.sheet_width > parser.sheet_height:
            parser.sheet_width, parser.sheet_height = parser.sheet_height, parser.sheet_width
        auto_swap = True
        auto_flip_y = True
    elif orientation == 'horizontal':
        if parser.sheet_width < parser.sheet_height:
            parser.sheet_width, parser.sheet_height = parser.sheet_height, parser.sheet_width

    do_swap = swap_axes if swap_axes is not None else auto_swap
    do_flip_y = flip_y if flip_y is not None else auto_flip_y

    # Трансформация контуров
    transformed_closed = []
    for c in parser.closed_contours:
        tc = transform_contour(c, swap_xy=do_swap, rot_deg=rotate_deg, flip_x=flip_x, flip_y=do_flip_y,
                               sheet_w=parser.sheet_width, sheet_h=parser.sheet_height)
        transformed_closed.append(tc)

    transformed_open = []
    for c in parser.open_contours:
        tc = transform_contour(c, swap_xy=do_swap, rot_deg=rotate_deg, flip_x=flip_x, flip_y=do_flip_y,
                               sheet_w=parser.sheet_width, sheet_h=parser.sheet_height)
        transformed_open.append(tc)

    # Построение иерархии деталей (внешний периметр + отверстия)
    parts = build_part_hierarchy(transformed_closed)

    # Генерация XML
    gen = NspGenerator(
        sheet_width=parser.sheet_width,
        sheet_height=parser.sheet_height,
        thickness=parser.thickness,
        material=parser.material,
        plate_name=parser.program_name,
        parts=parts,
        open_contours=transformed_open
    )

    xml_text = gen.generate_xml_string()

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(xml_text)

    print(f"[OK] Конвертация завершена: {output_path}")
    print(f"     Лист: {parser.sheet_width:.0f}x{parser.sheet_height:.0f} мм, Деталей: {len(parts)}, Резов: {len(transformed_open)}")


# ==============================================================================
# 7. CLI ТОЧКА ВХОДА
# ==============================================================================

def main():
    arg_parser = argparse.ArgumentParser(
        description="iso2nsp: Конвертер G-кода ISO ЧПУ в проект раскроя CypNest .nsp"
    )
    arg_parser.add_argument("input", help="Входной файл .iso")
    arg_parser.add_argument("-o", "--output", help="Выходной файл .nsp (по умолчанию: {имя}.nsp)")
    arg_parser.add_argument("--orientation", choices=['auto', 'vertical', 'horizontal'], default='auto',
                            help="Ориентация листа (по умолчанию: auto)")
    arg_parser.add_argument("--swap-axes", action="store_true", help="Принудительно поменять оси X и Y местами")
    arg_parser.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=0,
                            help="Доворот всей карты раскроя (град)")
    arg_parser.add_argument("--flip-x", action="store_true", help="Отразить раскрой по горизонтали")
    arg_parser.add_argument("--flip-y", action="store_true", help="Отразить раскрой по вертикали")
    arg_parser.add_argument("--sheet-size", help="Размер листа в формате WxH (например: 1500x3000)")
    arg_parser.add_argument("--thickness", type=float, help="Толщина листа в мм")
    arg_parser.add_argument("--material", default="SUS", help="Марка материала (по умолчанию: SUS)")

    args = arg_parser.parse_args()

    out_file = args.output if args.output else os.path.splitext(args.input)[0] + ".nsp"

    convert_iso_to_nsp(
        input_path=args.input,
        output_path=out_file,
        orientation=args.orientation,
        swap_axes=args.swap_axes if args.swap_axes else None,
        rotate_deg=args.rotate,
        flip_x=args.flip_x,
        flip_y=args.flip_y,
        sheet_size=args.sheet_size,
        thickness=args.thickness,
        material=args.material
    )


if __name__ == "__main__":
    main()
