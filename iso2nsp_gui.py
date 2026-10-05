#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
iso2nsp_gui.py - Графическое приложение (GUI) для конвертации ISO G-кода в CypNest .nsp
с интерактивным 2D-предпросмотром, изменением размеров листа, трансформациями раскроя
и интерактивной таблицей переименования деталей с подсветкой на холсте.

Работает на стандартной библиотеке Python (tkinter), компилируется в standalone .exe.
"""

import os
import sys
import math
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Импорт ядра конвертера
from iso2nsp import (
    IsoParser, Point, Segment, Contour, PartInstance,
    build_part_hierarchy, transform_contour, NspGenerator,
    parts_match_rotation
)


class Iso2NspApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("iso2nsp - Конвертер раскроя ISO G-код -> CypNest .nsp")
        self.geometry("1380x850")
        self.minsize(1050, 680)

        # Данные текущей программы
        self.current_iso_path = None
        self.parser = None
        self.raw_closed_contours = []
        self.raw_open_contours = []

        # Текущее состояние листа и трансформаций
        self.sheet_w = 1500.0
        self.sheet_h = 3000.0
        self.thickness = 6.0
        self.material = "SUS"
        self.plate_name = "Plate"

        self.swap_xy = True  # По умолчанию меняем оси станка на оси листа
        self.rot_deg = 0
        self.flip_x = False
        self.flip_y = True   # По умолчанию раскладка сверху, как в Lantek
        self.offset_x = 0.0
        self.offset_y = 0.0

        # Кэш деталей и мастер-типов
        self.current_parts = []
        self.current_open_cuts = []
        self.master_groups = []  # Список словарей уникальных деталей
        self.selected_group_key = None  # Выбранная деталь для подсветки

        # Словарь пользовательских названий: geom_key -> custom_name
        self.custom_names = {}

        # Параметры навигации по холсту (Zoom & Pan)
        self.zoom_scale = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.drag_start = None

        self._setup_style()
        self._create_layout()

        # Автоматическая загрузка первого тестового файла если доступен
        default_test = r"d:\Пупупу\тест\1118 FE6.iso"
        if os.path.exists(default_test):
            self.load_iso_file(default_test)

    def _setup_style(self):
        self.configure(bg="#16181f")
        self.style = ttk.Style(self)
        self.style.theme_use('clam')

        self.bg_color = "#16181f"
        self.panel_bg = "#21242e"
        self.accent_color = "#00e5ff"
        self.btn_bg = "#2b303d"
        self.text_color = "#e6edf3"

        self.style.configure(".", background=self.panel_bg, foreground=self.text_color, font=("Segoe UI", 9))
        self.style.configure("TLabel", background=self.panel_bg, foreground=self.text_color)
        self.style.configure("Header.TLabel", font=("Segoe UI", 11, "bold"), foreground=self.accent_color)
        self.style.configure("Sub.TLabel", font=("Segoe UI", 8), foreground="#8b949e")

        self.style.configure("TButton", background=self.btn_bg, foreground=self.text_color, borderwidth=0, padding=5)
        self.style.map("TButton", background=[("active", "#383f50"), ("pressed", "#444c60")])

        self.style.configure("Accent.TButton", background="#007acc", foreground="#ffffff", font=("Segoe UI", 9, "bold"), padding=6)
        self.style.map("Accent.TButton", background=[("active", "#0098ff"), ("pressed", "#005999")])

        self.style.configure("Success.TButton", background="#238636", foreground="#ffffff", font=("Segoe UI", 10, "bold"), padding=8)
        self.style.map("Success.TButton", background=[("active", "#2ea043"), ("pressed", "#1b5e20")])

        self.style.configure("TEntry", fieldbackground="#161922", foreground="#ffffff")

        # Настройка Treeview
        self.style.configure(
            "Treeview",
            background="#1a1d26",
            foreground="#e6edf3",
            fieldbackground="#1a1d26",
            rowheight=26,
            font=("Segoe UI", 9)
        )
        self.style.configure("Treeview.Heading", background="#282c38", foreground="#00e5ff", font=("Segoe UI", 9, "bold"))
        self.style.map("Treeview", background=[("selected", "#1f4068")])

    def _create_layout(self):
        # Главный контейнер
        main_frame = tk.Frame(self, bg=self.bg_color)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 1. Левая панель инструментов (Sidebar)
        left_sidebar = tk.Frame(main_frame, bg=self.panel_bg, width=280, padx=12, pady=12)
        left_sidebar.pack(side=tk.LEFT, fill=tk.Y)
        left_sidebar.pack_propagate(False)

        # Заголовок
        ttk.Label(left_sidebar, text="Раскрой ЧПУ -> CypNest", style="Header.TLabel").pack(anchor="w", pady=(0, 2))
        ttk.Label(left_sidebar, text="Конвертер траекторий Lantek ISO", style="Sub.TLabel").pack(anchor="w", pady=(0, 10))

        # Кнопка открытия файла
        btn_open = ttk.Button(left_sidebar, text="📂 Выбрать .ISO файл", style="Accent.TButton", command=self.on_open_file)
        btn_open.pack(fill=tk.X, pady=(0, 4))

        self.lbl_file = ttk.Label(left_sidebar, text="Файл не выбран", wraplength=250, foreground="#abb2bf")
        self.lbl_file.pack(anchor="w", pady=(0, 8))

        ttk.Separator(left_sidebar, orient="horizontal").pack(fill=tk.X, pady=6)

        # Параметры листа
        ttk.Label(left_sidebar, text="Параметры листа", style="Header.TLabel").pack(anchor="w", pady=(0, 4))

        dim_frame = tk.Frame(left_sidebar, bg=self.panel_bg)
        dim_frame.pack(fill=tk.X, pady=2)

        ttk.Label(dim_frame, text="Ширина (X):").grid(row=0, column=0, sticky="w", pady=2)
        self.ent_w = ttk.Entry(dim_frame, width=7)
        self.ent_w.insert(0, str(int(self.sheet_w)))
        self.ent_w.grid(row=0, column=1, sticky="w", padx=4, pady=2)
        ttk.Label(dim_frame, text="мм").grid(row=0, column=2, sticky="w")

        ttk.Label(dim_frame, text="Длина (Y):").grid(row=1, column=0, sticky="w", pady=2)
        self.ent_h = ttk.Entry(dim_frame, width=7)
        self.ent_h.insert(0, str(int(self.sheet_h)))
        self.ent_h.grid(row=1, column=1, sticky="w", padx=4, pady=2)
        ttk.Label(dim_frame, text="мм").grid(row=1, column=2, sticky="w")

        ttk.Label(dim_frame, text="Толщина:").grid(row=2, column=0, sticky="w", pady=2)
        self.ent_t = ttk.Entry(dim_frame, width=7)
        self.ent_t.insert(0, str(self.thickness))
        self.ent_t.grid(row=2, column=1, sticky="w", padx=4, pady=2)
        ttk.Label(dim_frame, text="мм").grid(row=2, column=2, sticky="w")

        ttk.Label(dim_frame, text="Материал:").grid(row=3, column=0, sticky="w", pady=2)
        self.ent_mat = ttk.Entry(dim_frame, width=7)
        self.ent_mat.insert(0, self.material)
        self.ent_mat.grid(row=3, column=1, sticky="w", padx=4, pady=2)

        btn_apply_sheet = ttk.Button(left_sidebar, text="Применить размеры", command=self.on_apply_sheet_params)
        btn_apply_sheet.pack(fill=tk.X, pady=(4, 8))

        ttk.Separator(left_sidebar, orient="horizontal").pack(fill=tk.X, pady=6)

        # Трансформации
        ttk.Label(left_sidebar, text="Трансформации", style="Header.TLabel").pack(anchor="w", pady=(0, 4))

        self.btn_swap = ttk.Button(left_sidebar, text="⤢ Поменять оси (X ↔ Y)", command=self.on_toggle_swap_axes)
        self.btn_swap.pack(fill=tk.X, pady=2)

        rot_frame = tk.Frame(left_sidebar, bg=self.panel_bg)
        rot_frame.pack(fill=tk.X, pady=2)
        ttk.Button(rot_frame, text="⟲ -90°", width=7, command=lambda: self.on_rotate(-90)).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=1)
        ttk.Button(rot_frame, text="⟳ +90°", width=7, command=lambda: self.on_rotate(90)).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=1)
        ttk.Button(rot_frame, text="180°", width=7, command=lambda: self.on_rotate(180)).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=1)

        flip_frame = tk.Frame(left_sidebar, bg=self.panel_bg)
        flip_frame.pack(fill=tk.X, pady=2)
        ttk.Button(flip_frame, text="⇄ Отразить X", width=12, command=self.on_flip_x).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=1)
        ttk.Button(flip_frame, text="⇅ Отразить Y", width=12, command=self.on_flip_y).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=1)

        align_frame = tk.Frame(left_sidebar, bg=self.panel_bg)
        align_frame.pack(fill=tk.X, pady=2)
        ttk.Button(align_frame, text="⌖ Отступ 10мм", width=12, command=self.on_align_margin).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=1)
        ttk.Button(align_frame, text="⊡ Центрировать", width=12, command=self.on_center_sheet).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=1)

        ttk.Separator(left_sidebar, orient="horizontal").pack(fill=tk.X, pady=8)

        # Кнопки экспорта и навигации
        btn_save = ttk.Button(left_sidebar, text="💾 Сохранить .NSP", style="Success.TButton", command=self.on_save_nsp)
        btn_save.pack(fill=tk.X, pady=(2, 6))

        btn_fit = ttk.Button(left_sidebar, text="🔍 Вписать лист в окно", command=self.fit_view)
        btn_fit.pack(fill=tk.X, pady=2)

        # 2. Правая панель - Список и переименование деталей
        right_sidebar = tk.Frame(main_frame, bg=self.panel_bg, width=380, padx=12, pady=12)
        right_sidebar.pack(side=tk.RIGHT, fill=tk.Y)
        right_sidebar.pack_propagate(False)

        ttk.Label(right_sidebar, text="Детали на листе", style="Header.TLabel").pack(anchor="w", pady=(0, 2))
        ttk.Label(right_sidebar, text="Кликните на деталь для подсветки и изменения имени", style="Sub.TLabel").pack(anchor="w", pady=(0, 8))

        # Таблица деталей Treeview с прокруткой
        tree_container = tk.Frame(right_sidebar, bg=self.panel_bg)
        tree_container.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        columns = ("name", "dims", "qty", "holes")
        self.tree_parts = ttk.Treeview(tree_container, columns=columns, show="headings", selectmode="browse")
        self.tree_parts.heading("name", text="Имя детали")
        self.tree_parts.heading("dims", text="Габариты (мм)")
        self.tree_parts.heading("qty", text="Кол-во")
        self.tree_parts.heading("holes", text="Отв.")

        self.tree_parts.column("name", width=130, anchor="w")
        self.tree_parts.column("dims", width=105, anchor="center")
        self.tree_parts.column("qty", width=50, anchor="center")
        self.tree_parts.column("holes", width=45, anchor="center")

        tree_scroll = ttk.Scrollbar(tree_container, orient=tk.VERTICAL, command=self.tree_parts.yview)
        self.tree_parts.configure(yscrollcommand=tree_scroll.set)

        self.tree_parts.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree_parts.bind("<<TreeviewSelect>>", self.on_part_selected)
        self.tree_parts.bind("<Double-1>", lambda e: self.ent_rename.focus_set())

        # Блок быстрого переименования выбранной детали
        rename_box = tk.LabelFrame(right_sidebar, text=" Переименование выбранной детали ", bg=self.panel_bg, fg="#00e5ff", padx=10, pady=8)
        rename_box.pack(fill=tk.X, pady=(0, 4))

        self.lbl_selected_info = ttk.Label(rename_box, text="Выберите деталь в таблице выше", foreground="#8b949e")
        self.lbl_selected_info.pack(anchor="w", pady=(0, 4))

        edit_row = tk.Frame(rename_box, bg=self.panel_bg)
        edit_row.pack(fill=tk.X, pady=2)

        self.ent_rename = ttk.Entry(edit_row, font=("Segoe UI", 10))
        self.ent_rename.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.ent_rename.bind("<Return>", lambda e: self.on_apply_rename())

        btn_apply_name = ttk.Button(edit_row, text="Применить", style="Accent.TButton", command=self.on_apply_rename)
        btn_apply_name.pack(side=tk.RIGHT)

        btn_reset_names = ttk.Button(rename_box, text="Сбросить все имена к стандартным", command=self.on_reset_all_names)
        btn_reset_names.pack(fill=tk.X, pady=(6, 0))

        # 3. Центральная часть - 2D Холст (Canvas)
        canvas_container = tk.Frame(main_frame, bg="#101216")
        canvas_container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(canvas_container, bg="#101216", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # Нижняя информационная строка
        self.status_bar = tk.Frame(canvas_container, bg="#1b1e27", height=28, padx=10)
        self.status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        self.lbl_coords = tk.Label(self.status_bar, text="X: 0.0 мм  |  Y: 0.0 мм", bg="#1b1e27", fg="#8892b0", font=("Consolas", 9))
        self.lbl_coords.pack(side=tk.LEFT)

        self.lbl_legend = tk.Label(
            self.status_bar,
            text="■ Лист (Голубой)   ■ Детали (Зеленый)   ■ Выбранная (Желтый)   ■ Отверстия (Синий)   ■ Резы (Красный)",
            bg="#1b1e27", fg="#a0aec0", font=("Segoe UI", 8)
        )
        self.lbl_legend.pack(side=tk.RIGHT)

        # Привязка событий мыши (Zoom & Pan)
        self.canvas.bind("<ButtonPress-1>", self.on_mouse_down)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<ButtonPress-2>", self.on_mouse_down)
        self.canvas.bind("<B2-Motion>", self.on_mouse_drag)
        self.canvas.bind("<MouseWheel>", self.on_mouse_wheel)  # Windows
        self.canvas.bind("<Button-4>", lambda e: self.on_mouse_wheel_linux(e, 1.2))
        self.canvas.bind("<Button-5>", lambda e: self.on_mouse_wheel_linux(e, 0.8))
        self.canvas.bind("<Motion>", self.on_mouse_move)
        self.bind("<Configure>", lambda e: self.render())

    # ==========================================================================
    # ЗАГРУЗКА И ОБРАБОТКА ДАННЫХ
    # ==========================================================================

    def on_open_file(self):
        filetypes = [("Файлы программ ЧПУ (*.iso;*.cnc;*.nc;*.tap;*.txt)", "*.iso;*.cnc;*.nc;*.tap;*.txt"), ("Все файлы", "*.*")]
        init_dir = r"d:\Пупупу\тест" if os.path.exists(r"d:\Пупупу\тест") else os.getcwd()
        path = filedialog.askopenfilename(title="Выберите файл управляющей программы ЧПУ", filetypes=filetypes, initialdir=init_dir)
        if path:
            self.load_iso_file(path)

    def load_iso_file(self, path):
        try:
            self.current_iso_path = path
            self.parser = IsoParser(path)
            self.parser.parse()

            self.sheet_w = self.parser.sheet_width
            self.sheet_h = self.parser.sheet_height
            self.thickness = self.parser.thickness
            self.material = self.parser.material
            self.plate_name = self.parser.program_name

            # Обновление полей ввода
            self.ent_w.delete(0, tk.END)
            self.ent_w.insert(0, str(int(self.sheet_w)))
            self.ent_h.delete(0, tk.END)
            self.ent_h.insert(0, str(int(self.sheet_h)))
            self.ent_t.delete(0, tk.END)
            self.ent_t.insert(0, str(self.thickness))
            self.ent_mat.delete(0, tk.END)
            self.ent_mat.insert(0, self.material)

            self.lbl_file.config(text=os.path.basename(path), foreground="#00e5ff")

            self.raw_closed_contours = self.parser.closed_contours
            self.raw_open_contours = self.parser.open_contours

            # Сброс пользовательских имен при открытии нового файла
            self.custom_names.clear()
            self.selected_group_key = None

            # По умолчанию для вертикального листа CAM Lantek (W <= H):
            # включаем смену осей и отражение Y, чтобы раскладка сразу ложилась сверху,
            # в точном соответствии с отчетом и чертежом раскроя Lantek.
            if self.sheet_w <= self.sheet_h:
                self.swap_xy = True
                self.flip_y = True
            else:
                self.swap_xy = False
                self.flip_y = False

            self.rot_deg = 0
            self.flip_x = False
            self.offset_x = 0.0
            self.offset_y = 0.0

            self._update_transformed_geometry()
            self.fit_view()

        except Exception as e:
            messagebox.showerror("Ошибка парсинга ISO", f"Не удалось прочитать файл:\n{e}")

    def on_apply_sheet_params(self):
        try:
            self.sheet_w = float(self.ent_w.get())
            self.sheet_h = float(self.ent_h.get())
            self.thickness = float(self.ent_t.get())
            self.material = self.ent_mat.get().strip() or "SUS"
            self._update_transformed_geometry()
            self.render()
        except ValueError:
            messagebox.showwarning("Некорректный ввод", "Пожалуйста, введите корректные числовые значения для размеров листа.")

    def on_toggle_swap_axes(self):
        self.swap_xy = not self.swap_xy
        self._update_transformed_geometry()
        self.render()

    def on_rotate(self, angle_delta):
        self.rot_deg = (self.rot_deg + angle_delta) % 360
        self._update_transformed_geometry()
        self.render()

    def on_flip_x(self):
        self.flip_x = not self.flip_x
        self._update_transformed_geometry()
        self.render()

    def on_flip_y(self):
        self.flip_y = not self.flip_y
        self._update_transformed_geometry()
        self.render()

    def on_align_margin(self, margin=10.0):
        if not self.current_parts and not self.current_open_cuts:
            return
        all_pts = []
        for p in self.current_parts:
            all_pts.extend(p.outer_contour.get_discrete_points())
        for c in self.current_open_cuts:
            all_pts.extend(c.get_discrete_points())

        if all_pts:
            min_x = min(p[0] for p in all_pts)
            min_y = min(p[1] for p in all_pts)
            dx = margin - min_x
            dy = margin - min_y
            self.offset_x += dx
            self.offset_y += dy
            self._update_transformed_geometry()
            self.render()

    def on_center_sheet(self):
        if not self.current_parts and not self.current_open_cuts:
            return
        all_pts = []
        for p in self.current_parts:
            all_pts.extend(p.outer_contour.get_discrete_points())
        for c in self.current_open_cuts:
            all_pts.extend(c.get_discrete_points())

        if all_pts:
            min_x = min(p[0] for p in all_pts)
            max_x = max(p[0] for p in all_pts)
            min_y = min(p[1] for p in all_pts)
            max_y = max(p[1] for p in all_pts)

            nest_w = max_x - min_x
            nest_h = max_y - min_y

            target_x = (self.sheet_w - nest_w) / 2.0
            target_y = (self.sheet_h - nest_h) / 2.0

            dx = target_x - min_x
            dy = target_y - min_y
            self.offset_x += dx
            self.offset_y += dy
            self._update_transformed_geometry()
            self.render()

    def _update_transformed_geometry(self):
        """Пересчет контуров с учетом трансформаций и группировка мастер-деталей (включая поворот)"""
        transformed_closed = []
        for c in self.raw_closed_contours:
            tc = transform_contour(
                c, swap_xy=self.swap_xy, rot_deg=self.rot_deg,
                flip_x=self.flip_x, flip_y=self.flip_y,
                dx=self.offset_x, dy=self.offset_y,
                sheet_w=self.sheet_w, sheet_h=self.sheet_h
            )
            transformed_closed.append(tc)

        transformed_open = []
        for c in self.raw_open_contours:
            tc = transform_contour(
                c, swap_xy=self.swap_xy, rot_deg=self.rot_deg,
                flip_x=self.flip_x, flip_y=self.flip_y,
                dx=self.offset_x, dy=self.offset_y,
                sheet_w=self.sheet_w, sheet_h=self.sheet_h
            )
            transformed_open.append(tc)

        self.current_parts = build_part_hierarchy(transformed_closed)
        self.current_open_cuts = transformed_open

        # Группировка уникальных деталей с распознаванием поворота (0, 90, 180, 270)
        master_list = []
        for part in self.current_parts:
            found_master = None
            found_angle = 0
            for m in master_list:
                ang = parts_match_rotation(m['prototype'], part)
                if ang is not None:
                    found_master = m
                    found_angle = ang
                    break

            if found_master is None:
                bx = part.bbox()
                w = round(bx[2] - bx[0], 2)
                h = round(bx[3] - bx[1], 2)
                num_holes = len(part.holes)
                geom_key = (min(w, h), max(w, h), num_holes)
                def_name = self.custom_names.get(geom_key, f"{self.plate_name}_P{len(master_list) + 1}")

                master_data = {
                    'key': geom_key,
                    'name': def_name,
                    'width': w,
                    'height': h,
                    'holes': num_holes,
                    'prototype': part,
                    'instances': [part]
                }
                master_list.append(master_data)
                part.part_name = def_name
                part.insert_angle_rad = 0.0
            else:
                found_master['instances'].append(part)
                part.part_name = found_master['name']
                part.insert_angle_rad = math.radians(found_angle)

        self.master_groups = sorted(master_list, key=lambda g: g['width'] * g['height'], reverse=True)

        self._refresh_parts_table()

    # ==========================================================================
    # ТАБЛИЦА И ПЕРЕИМЕНОВАНИЕ ДЕТАЛЕЙ
    # ==========================================================================

    def _refresh_parts_table(self):
        """Обновление строк таблицы деталей"""
        for item in self.tree_parts.get_children():
            self.tree_parts.delete(item)

        selected_iid = None
        for idx, g in enumerate(self.master_groups):
            name = g['name']
            dims = f"{g['width']:.1f} x {g['height']:.1f}"
            qty = str(len(g['instances']))
            holes = str(g['holes'])

            iid = f"group_{idx}"
            self.tree_parts.insert("", tk.END, iid=iid, values=(name, dims, qty, holes))

            if self.selected_group_key == g['key']:
                selected_iid = iid

        if selected_iid:
            self.tree_parts.selection_set(selected_iid)
            self.tree_parts.see(selected_iid)
        elif self.master_groups:
            # Выделяем первую строку по умолчанию
            first_iid = "group_0"
            self.tree_parts.selection_set(first_iid)
            self.on_part_selected(None)

    def on_part_selected(self, event):
        sel = self.tree_parts.selection()
        if not sel:
            self.selected_group_key = None
            self.lbl_selected_info.config(text="Выберите деталь в таблице выше")
            self.ent_rename.delete(0, tk.END)
            self.render()
            return

        iid = sel[0]
        idx = int(iid.split("_")[1])
        if idx < len(self.master_groups):
            group = self.master_groups[idx]
            self.selected_group_key = group['key']
            info_text = f"Деталь #{idx+1}: {group['width']:.1f} x {group['height']:.1f} мм ({len(group['instances'])} шт.)"
            self.lbl_selected_info.config(text=info_text, foreground="#00e5ff")

            # Заполняем поле редактирования имени
            self.ent_rename.delete(0, tk.END)
            self.ent_rename.insert(0, group['name'])
            self.ent_rename.select_range(0, tk.END)

            self.render()

    def on_apply_rename(self):
        new_name = self.ent_rename.get().strip()
        if not new_name:
            messagebox.showwarning("Внимание", "Имя детали не может быть пустым.")
            return

        if self.selected_group_key:
            # Обновляем кэш имен
            self.custom_names[self.selected_group_key] = new_name

            # Обновляем деталь и все её экземпляры
            for g in self.master_groups:
                if g['key'] == self.selected_group_key:
                    g['name'] = new_name
                    for part in g['instances']:
                        part.part_name = new_name
                    break

            self._refresh_parts_table()
            self.render()

    def on_reset_all_names(self):
        if not self.master_groups:
            return
        if messagebox.askyesno("Сброс имен", "Сбросить все пользовательские названия деталей к стандартным?"):
            self.custom_names.clear()
            for idx, g in enumerate(self.master_groups):
                def_name = f"{self.plate_name}_P{idx + 1}"
                g['name'] = def_name
                for part in g['instances']:
                    part.part_name = def_name
            self._refresh_parts_table()
            self.render()

    def on_save_nsp(self):
        if not self.current_parts and not self.current_open_cuts:
            messagebox.showwarning("Нет данных", "Сначала откройте файл ISO с траекториями.")
            return

        default_name = f"{self.plate_name}.nsp"
        out_path = filedialog.asksaveasfilename(
            title="Сохранить проект раскроя CypNest",
            defaultextension=".nsp",
            filetypes=[("Проект CypNest", "*.nsp"), ("XML файл", "*.xml")],
            initialfile=default_name
        )
        if not out_path:
            return

        try:
            gen = NspGenerator(
                sheet_width=self.sheet_w,
                sheet_height=self.sheet_h,
                thickness=self.thickness,
                material=self.material,
                plate_name=self.plate_name,
                parts=self.current_parts,
                open_contours=self.current_open_cuts
            )
            xml_text = gen.generate_xml_string()
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(xml_text)
            messagebox.showinfo("Успешно", f"Файл проекта сохранен:\n{out_path}\nВсе назначенные имена деталей успешно записаны!")
        except Exception as e:
            messagebox.showerror("Ошибка сохранения", f"Не удалось сформировать .nsp файл:\n{e}")

    # ==========================================================================
    # ИНТЕРАКТИВНЫЙ 2D РЕНДЕР С ПОДСВЕТКОЙ
    # ==========================================================================

    def fit_view(self):
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw < 50 or ch < 50:
            cw, ch = 700, 750

        margin = 35
        avail_w = cw - 2 * margin
        avail_h = ch - 2 * margin

        scale_x = avail_w / max(1.0, self.sheet_w)
        scale_y = avail_h / max(1.0, self.sheet_h)
        self.zoom_scale = min(scale_x, scale_y)

        draw_w = self.sheet_w * self.zoom_scale
        draw_h = self.sheet_h * self.zoom_scale

        self.pan_x = margin + (avail_w - draw_w) / 2.0
        self.pan_y = margin + (avail_h - draw_h) / 2.0

        self.render()

    def to_screen(self, x, y):
        sx = self.pan_x + x * self.zoom_scale
        sy = self.pan_y + (self.sheet_h - y) * self.zoom_scale
        return sx, sy

    def to_world(self, sx, sy):
        x = (sx - self.pan_x) / self.zoom_scale
        y = self.sheet_h - (sy - self.pan_y) / self.zoom_scale
        return x, y

    def render(self):
        self.canvas.delete("all")

        # 1. Граница и фон листа
        p1 = self.to_screen(0, 0)
        p2 = self.to_screen(self.sheet_w, self.sheet_h)
        sx1, sy2 = p1
        sx2, sy1 = p2

        self.canvas.create_rectangle(sx1, sy1, sx2, sy2, fill="#1c202a", outline="#00e5ff", width=2, dash=(6, 4))

        # Сетка шагом 100 / 500 мм
        grid_step = 500.0 if self.zoom_scale < 0.35 else 100.0
        gx = grid_step
        while gx < self.sheet_w:
            lx1, ly1 = self.to_screen(gx, 0)
            lx2, ly2 = self.to_screen(gx, self.sheet_h)
            self.canvas.create_line(lx1, ly1, lx2, ly2, fill="#252a37", width=1)
            gx += grid_step

        gy = grid_step
        while gy < self.sheet_h:
            lx1, ly1 = self.to_screen(0, gy)
            lx2, ly2 = self.to_screen(self.sheet_w, gy)
            self.canvas.create_line(lx1, ly1, lx2, ly2, fill="#252a37", width=1)
            gy += grid_step

        # Текст габаритов листа
        self.canvas.create_text(
            sx1 + 8, sy1 + 14, text=f"Лист: {self.sheet_w:.0f} x {self.sheet_h:.0f} x {self.thickness:.1f} мм ({self.material})",
            anchor="w", fill="#00e5ff", font=("Segoe UI", 9, "bold")
        )

        # 2. Отрисовка деталей
        for part in self.current_parts:
            # Определяем, подсвечена ли эта деталь
            is_selected = False
            if self.selected_group_key is not None:
                for g in self.master_groups:
                    if g['key'] == self.selected_group_key and part in g['instances']:
                        is_selected = True
                        break

            # Цвета: обычная деталь - темно-зеленый / изумрудный, выбранная - ярко-золотой / желтый
            if is_selected:
                fill_color = "#4d3d14"
                outline_color = "#ffd700"
                outline_width = 2.5
            else:
                fill_color = "#1d382d"
                outline_color = "#00e676"
                outline_width = 1.2

            # Внешний контур
            pts = part.outer_contour.get_discrete_points()
            screen_pts = []
            for px, py in pts:
                screen_pts.extend(self.to_screen(px, py))

            if len(screen_pts) >= 6:
                self.canvas.create_polygon(screen_pts, fill=fill_color, outline=outline_color, width=outline_width)

            # Отверстия
            for hole in part.holes:
                hpts = hole.get_discrete_points()
                h_screen = []
                for hx, hy in hpts:
                    h_screen.extend(self.to_screen(hx, hy))
                if len(h_screen) >= 6:
                    hole_outline = "#ffea00" if is_selected else "#2979ff"
                    self.canvas.create_polygon(h_screen, fill="#1c202a", outline=hole_outline, width=1.2)

        # 3. Разомкнутые резы
        for cut in self.current_open_cuts:
            cpts = cut.get_discrete_points()
            for i in range(len(cpts) - 1):
                x1, y1 = self.to_screen(cpts[i][0], cpts[i][1])
                x2, y2 = self.to_screen(cpts[i+1][0], cpts[i+1][1])
                self.canvas.create_line(x1, y1, x2, y2, fill="#ff1744", width=2, dash=(4, 2))

    # ==========================================================================
    # СОБЫТИЯ МЫШИ (ZOOM & PAN)
    # ==========================================================================

    def on_mouse_down(self, event):
        self.drag_start = (event.x, event.y)

    def on_mouse_drag(self, event):
        if self.drag_start:
            dx = event.x - self.drag_start[0]
            dy = event.y - self.drag_start[1]
            self.pan_x += dx
            self.pan_y += dy
            self.drag_start = (event.x, event.y)
            self.render()

    def on_mouse_wheel(self, event):
        factor = 1.15 if event.delta > 0 else 0.85
        self._zoom_at(event.x, event.y, factor)

    def on_mouse_wheel_linux(self, event, factor):
        self._zoom_at(event.x, event.y, factor)

    def _zoom_at(self, mx, my, factor):
        wx, wy = self.to_world(mx, my)
        self.zoom_scale *= factor
        new_sx, new_sy = self.to_screen(wx, wy)
        self.pan_x += (mx - new_sx)
        self.pan_y += (my - new_sy)
        self.render()

    def on_mouse_move(self, event):
        wx, wy = self.to_world(event.x, event.y)
        self.lbl_coords.config(text=f"X: {wx:7.1f} мм  |  Y: {wy:7.1f} мм  |  Масштаб: {self.zoom_scale:.3f}")


if __name__ == "__main__":
    app = Iso2NspApp()
    app.mainloop()
