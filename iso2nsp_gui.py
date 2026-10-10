#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
iso2nsp_gui.py - Графическое приложение (GUI) для конвертации ISO G-кода в CypNest .nsp
с поддержкой многолистовых проектов раскроя, тиража листов, интерактивным 2D-предпросмотром,
трансформациями и интерактивной таблицей переименования деталей с подсветкой на холсте.

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
    build_part_hierarchy, transform_contour, compute_part_geom_key,
    PlateData, NestingProject, parts_match_rotation, sort_parts_from_bottom_left
)

APP_VERSION = "2.6.0"
IS_TEST_VERSION = "test" in APP_VERSION.lower()

EMBEDDED_LICENSE_TEXT = """УСЛОВИЯ ИСПОЛЬЗОВАНИЯ И ОТКАЗ ОТ ОТВЕТСТВЕННОСТИ (LICENSE)

1. ОГРАНИЧЕНИЕ ПРАВ ИСПОЛЬЗОВАНИЯ
1.1. Настоящее программное обеспечение (далее — "Программа") создано исключительно в личных целях Разработчика (автора).
1.2. Использование Программы третьими лицами, любыми сотрудниками компании, а также любыми другими юридическими и физическими лицами в коммерческих или производственных целях КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО без явного письменного согласия автора.
1.3. Автор оставляет за собой исключительное и безусловное право в любой момент запретить использование Программы компании или любым третьим лицам. В случае получения устного или письменного требования автора о прекращении использования, компания и любые третьи лица обязаны незамедлительно прекратить эксплуатацию Программы и удалить все её копии. Дальнейшее использование Программы после такого требования является незаконным.

2. ПОЛНЫЙ ОТКАЗ ОТ ОТВЕТСТВЕННОСТИ
2.1. Программа предоставляется на условиях "КАК ЕСТЬ" ("AS IS"). Автор не предоставляет никаких гарантий относительно точности, надежности, производительности или пригодности Программы для каких-либо производственных или коммерческих задач.
2.2. Автор НЕ НЕСЕТ ОТВЕТСТВЕННОСТИ за любые прямые, косвенные, случайные или сопутствующие убытки, возникшие в результате использования или невозможности использования Программы.
2.3. Автор не несет ответственности за:
   - Сбои в производственных процессах;
   - Увеличение объемов брака, дефектов или порчу материалов/оборудования;
   - Финансовые потери, упущенную выгоду или любые иные негативные результаты работы Программы.
2.4. Все риски, связанные с качеством и производительностью Программы, принимают на себя лица или организации, решившие запустить её вопреки запрету, указанному в п. 1.2.

Автор Программы: Мурзянов Виталий Олегович (pururuall)
Дата: 06 октября 2026 года
"""


class Iso2NspApp(tk.Tk):
    def __init__(self):
        super().__init__()

        title_suffix = " [ТЕСТОВАЯ ВЕРСИЯ]" if IS_TEST_VERSION else ""
        self.title(f"iso2nsp v{APP_VERSION}{title_suffix} — Конвертер раскроя ISO -> CypNest .nsp")
        self.geometry("1440x880")
        self.minsize(1120, 700)

        # Модель многолистового проекта
        self.project = NestingProject()
        self.current_plate_idx = -1
        self.master_groups = []  # Типы деталей для активного листа
        self.selected_group_idx = -1  # Индекс выбранной группы деталей
        self.selected_group_key = None  # Выбранная деталь для подсветки

        # Флаг: применять трансформации ко всем листам проекта
        self.var_apply_all = tk.BooleanVar(value=True)

        # Параметры навигации по холсту (Zoom & Pan)
        self.zoom_scale = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.drag_start = None

        self._setup_style()
        self._create_layout()

    @property
    def active_plate(self) -> PlateData:
        if 0 <= self.current_plate_idx < len(self.project.plates):
            return self.project.plates[self.current_plate_idx]
        return None

    def _create_round_btn_image(self, width=17, height=17, radius=4, fill='#444444', border='#333333'):
        """Генерирует 9-patch PhotoImage со скругленными углами в стиле Blender 3D."""
        img = tk.PhotoImage(width=width, height=height)
        rows = []
        transparent_coords = []
        for y in range(height):
            row = []
            for x in range(width):
                cx = radius if x < radius else (width - 1 - radius if x >= width - 1 - radius else x)
                cy = radius if y < radius else (height - 1 - radius if y >= height - 1 - radius else y)
                dist = math.hypot(x - cx, y - cy)
                if dist > radius + 0.35:
                    row.append(fill)
                    transparent_coords.append((x, y))
                elif dist > radius - 0.65:
                    row.append(border)
                else:
                    row.append(fill)
            rows.append('{' + ' '.join(row) + '}')
        img.put(' '.join(rows))
        for tx, ty in transparent_coords:
            img.transparency_set(tx, ty, True)
        return img

    def _register_rounded_btn_style(self, style_name, normal_fill, normal_border,
                                    hover_fill, hover_border,
                                    pressed_fill, pressed_border,
                                    disabled_fill='#2b2b2b', disabled_border='#242424',
                                    fg='#e6e6e6', font=("Segoe UI", 9), padding=4, radius=4):
        """Регистрирует стиль ttk.Button со скругленными углами (9-slice) и состояниями (Blender-кнопка)."""
        img_n = self._create_round_btn_image(width=17, height=17, radius=radius, fill=normal_fill, border=normal_border)
        img_h = self._create_round_btn_image(width=17, height=17, radius=radius, fill=hover_fill, border=hover_border)
        img_p = self._create_round_btn_image(width=17, height=17, radius=radius, fill=pressed_fill, border=pressed_border)
        img_d = self._create_round_btn_image(width=17, height=17, radius=radius, fill=disabled_fill, border=disabled_border)
        self._style_images.extend([img_n, img_h, img_p, img_d])

        elem_name = f"{style_name}.round_border"
        self.style.element_create(
            elem_name, "image", img_n,
            ("disabled", img_d),
            ("pressed", img_p),
            ("active", img_h),
            border=5, sticky="nsew"
        )
        self.style.layout(style_name, [
            (elem_name, {"sticky": "nswe", "children": [
                ("Button.padding", {"sticky": "nswe", "children": [
                    ("Button.label", {"sticky": "nswe"})
                ]})
            ]})
        ])
        self.style.configure(style_name, foreground=fg, font=font, padding=padding)
        self.style.map(style_name, foreground=[("disabled", "#666666")])

    def _setup_style(self):
        self.configure(bg="#1e1e1e")
        self.style = ttk.Style(self)
        self.style.theme_use('clam')

        # Цветовая палитра в стилистике Blender 3D (Dark Theme)
        self.bg_color = "#1e1e1e"        # Основной фон окна (Blender interface base)
        self.panel_bg = "#282828"        # Панели и боковые сайдбары (Blender properties region)
        self.accent_color = "#ea7600"     # Фирменный оранжевый акцент Blender (Active tool / selection)
        self.accent_blue = "#4772b3"      # Вторичный синий акцент Blender (Selection highlight)
        self.btn_bg = "#444444"          # Кнопки по умолчанию (Blender button gray)
        self.text_color = "#e6e6e6"      # Основной текст (Blender clean soft white)
        self.text_muted = "#999999"      # Второстепенные надписи (Blender subtitle gray)

        self.style.configure(".", background=self.panel_bg, foreground=self.text_color, font=("Segoe UI", 9))
        self.style.configure("TLabel", background=self.panel_bg, foreground=self.text_color)
        self.style.configure("Header.TLabel", font=("Segoe UI", 11, "bold"), foreground=self.accent_color)
        self.style.configure("Sub.TLabel", font=("Segoe UI", 8), foreground=self.text_muted)

        self._style_images = []

        # Стандартные кнопки Blender (скругление r=4, графитово-серый с мягким ховером)
        self._register_rounded_btn_style("TButton", "#444444", "#333333", "#545454", "#444444", "#333333", "#222222", padding=4, radius=4)

        # Акцентная кнопка Blender (Фирменный оранжевый, r=4)
        self._register_rounded_btn_style("Accent.TButton", "#ea7600", "#b35b00", "#f5881f", "#c46300", "#c46300", "#8f4800", fg="#ffffff", font=("Segoe UI", 9, "bold"), padding=5, radius=4)

        # Кнопка сохранения проекта (Blender синий / стальной, r=4)
        self._register_rounded_btn_style("Success.TButton", "#3a6ea5", "#2a527c", "#4a7fb8", "#336396", "#2a5785", "#1d4063", fg="#ffffff", font=("Segoe UI", 10, "bold"), padding=7, radius=4)

        # Кнопка экспорта отчета PDF (Blender приглушенный красный / терракот, r=4)
        self._register_rounded_btn_style("Pdf.TButton", "#993d3d", "#732d2d", "#b04747", "#8c3636", "#7a2e2e", "#5c2121", fg="#ffffff", font=("Segoe UI", 9, "bold"), padding=6, radius=4)

        # Опасные действия (Удаление, r=4)
        self._register_rounded_btn_style("Danger.TButton", "#7a2e2e", "#5c2121", "#993d3d", "#732d2d", "#5a1f1f", "#3d1414", fg="#ffffff", padding=4, radius=4)

        # Поля ввода в стиле Blender (темная утопленная область)
        self.style.configure("TEntry", fieldbackground="#161616", foreground="#ffffff")
        self.style.configure("TCheckbutton", background=self.panel_bg, foreground=self.accent_color, font=("Segoe UI", 8, "bold"))
        self.style.configure("TSeparator", background="#383838")

        # Таблицы Treeview в стиле Blender Outliner
        self.style.configure(
            "Treeview",
            background="#202020",
            foreground="#e6e6e6",
            fieldbackground="#202020",
            rowheight=24,
            font=("Segoe UI", 9)
        )
        self.style.configure("Treeview.Heading", background="#323232", foreground=self.accent_color, font=("Segoe UI", 9, "bold"))
        self.style.map("Treeview", background=[("selected", "#3e5573")])

    def _create_layout(self):
        # Главный контейнер
        main_frame = tk.Frame(self, bg=self.bg_color)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # ======================================================================
        # 1. ЛЕВАЯ ПАНЕЛЬ ИНСТРУМЕНТОВ (Sidebar)
        # ======================================================================
        left_sidebar = tk.Frame(main_frame, bg=self.panel_bg, width=320, padx=10, pady=10)
        left_sidebar.pack(side=tk.LEFT, fill=tk.Y)
        left_sidebar.pack_propagate(False)

        # Заголовок и имя проекта
        ttk.Label(left_sidebar, text="Раскрой ЧПУ -> CypNest", style="Header.TLabel").pack(anchor="w")
        ttk.Label(left_sidebar, text="Многолистовые проекты .NSP", style="Sub.TLabel").pack(anchor="w", pady=(0, 6))

        job_frame = tk.Frame(left_sidebar, bg=self.panel_bg)
        job_frame.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(job_frame, text="Заказ / Программа:").pack(anchor="w")
        self.ent_job_name = ttk.Entry(job_frame, font=("Segoe UI", 9, "bold"))
        self.ent_job_name.pack(fill=tk.X, pady=(2, 0))

        ttk.Separator(left_sidebar, orient="horizontal").pack(fill=tk.X, pady=4)

        # Панель "Листы программы"
        header_sheets = tk.Frame(left_sidebar, bg=self.panel_bg)
        header_sheets.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(header_sheets, text="Листы программы", style="Header.TLabel").pack(side=tk.LEFT)
        self.lbl_sheets_count = ttk.Label(header_sheets, text="0 шт.", style="Sub.TLabel")
        self.lbl_sheets_count.pack(side=tk.RIGHT, pady=2)

        # Таблица листов с прокруткой
        sheets_box = tk.Frame(left_sidebar, bg=self.panel_bg)
        sheets_box.pack(fill=tk.BOTH, expand=False, pady=(0, 4))

        cols_s = ("idx", "name", "dims", "qty", "parts")
        self.tree_sheets = ttk.Treeview(sheets_box, columns=cols_s, show="headings", height=6, selectmode="browse")
        self.tree_sheets.heading("idx", text="№")
        self.tree_sheets.heading("name", text="Имя листа")
        self.tree_sheets.heading("dims", text="ШхВ")
        self.tree_sheets.heading("qty", text="Тираж")
        self.tree_sheets.heading("parts", text="Дет.")

        self.tree_sheets.column("idx", width=24, anchor="center")
        self.tree_sheets.column("name", width=110, anchor="w")
        self.tree_sheets.column("dims", width=80, anchor="center")
        self.tree_sheets.column("qty", width=42, anchor="center")
        self.tree_sheets.column("parts", width=40, anchor="center")

        sb_sheets = ttk.Scrollbar(sheets_box, orient=tk.VERTICAL, command=self.tree_sheets.yview)
        self.tree_sheets.configure(yscrollcommand=sb_sheets.set)

        self.tree_sheets.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb_sheets.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_sheets.bind("<<TreeviewSelect>>", self.on_sheet_selected)

        # Кнопки управления листами
        btn_sheets_row = tk.Frame(left_sidebar, bg=self.panel_bg)
        btn_sheets_row.pack(fill=tk.X, pady=(0, 4))

        btn_add = ttk.Button(btn_sheets_row, text="+ Добавить файлы...", style="Accent.TButton", command=self.on_open_files)
        btn_add.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))

        ttk.Button(btn_sheets_row, text="▲", width=3, command=lambda: self.on_move_sheet(-1)).pack(side=tk.LEFT, padx=1)
        ttk.Button(btn_sheets_row, text="▼", width=3, command=lambda: self.on_move_sheet(1)).pack(side=tk.LEFT, padx=1)
        ttk.Button(btn_sheets_row, text="✕", width=3, style="Danger.TButton", command=self.on_remove_sheet).pack(side=tk.LEFT, padx=1)
        ttk.Button(btn_sheets_row, text="⟳", width=3, command=self.on_clear_sheets).pack(side=tk.LEFT, padx=1)

        btn_attach_pdf = ttk.Button(left_sidebar, text="📑 Подгрузить отчеты Lantek (PDF)", command=self.on_attach_pdf)
        btn_attach_pdf.pack(fill=tk.X, pady=(0, 4))

        # Редактирование тиража выбранного листа
        rep_frame = tk.Frame(left_sidebar, bg=self.panel_bg)
        rep_frame.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(rep_frame, text="Тираж выбранного листа (шт):").pack(side=tk.LEFT)
        self.spin_repeat = tk.Spinbox(rep_frame, from_=1, to=9999, width=5, bg="#161616", fg="#ffffff",
                                      buttonbackground=self.btn_bg, insertbackground="#ffffff", command=self.on_repeat_changed)
        self.spin_repeat.pack(side=tk.RIGHT)
        self.spin_repeat.bind("<Return>", lambda e: self.on_repeat_changed())
        self.spin_repeat.bind("<FocusOut>", lambda e: self.on_repeat_changed())

        ttk.Separator(left_sidebar, orient="horizontal").pack(fill=tk.X, pady=4)

        # Параметры активного листа
        self.lbl_active_sheet = ttk.Label(left_sidebar, text="Параметры листа", style="Header.TLabel")
        self.lbl_active_sheet.pack(anchor="w", pady=(0, 2))

        dim_frame = tk.Frame(left_sidebar, bg=self.panel_bg)
        dim_frame.pack(fill=tk.X, pady=2)

        ttk.Label(dim_frame, text="Ширина (X):").grid(row=0, column=0, sticky="w", pady=2)
        self.ent_w = ttk.Entry(dim_frame, width=7)
        self.ent_w.insert(0, "1500")
        self.ent_w.grid(row=0, column=1, sticky="w", padx=4, pady=2)
        ttk.Label(dim_frame, text="мм").grid(row=0, column=2, sticky="w")

        ttk.Label(dim_frame, text="Длина (Y):").grid(row=1, column=0, sticky="w", pady=2)
        self.ent_h = ttk.Entry(dim_frame, width=7)
        self.ent_h.insert(0, "3000")
        self.ent_h.grid(row=1, column=1, sticky="w", padx=4, pady=2)
        ttk.Label(dim_frame, text="мм").grid(row=1, column=2, sticky="w")

        ttk.Label(dim_frame, text="Толщина:").grid(row=2, column=0, sticky="w", pady=2)
        self.ent_t = ttk.Entry(dim_frame, width=7)
        self.ent_t.insert(0, "6.0")
        self.ent_t.grid(row=2, column=1, sticky="w", padx=4, pady=2)
        ttk.Label(dim_frame, text="мм").grid(row=2, column=2, sticky="w")

        ttk.Label(dim_frame, text="Материал:").grid(row=3, column=0, sticky="w", pady=2)
        self.ent_mat = ttk.Entry(dim_frame, width=7)
        self.ent_mat.insert(0, "FE")
        self.ent_mat.grid(row=3, column=1, sticky="w", padx=4, pady=2)

        btn_apply_sheet = ttk.Button(left_sidebar, text="Применить параметры листа", command=self.on_apply_sheet_params)
        btn_apply_sheet.pack(fill=tk.X, pady=(2, 6))

        ttk.Separator(left_sidebar, orient="horizontal").pack(fill=tk.X, pady=4)

        # Трансформации
        trans_head = tk.Frame(left_sidebar, bg=self.panel_bg)
        trans_head.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(trans_head, text="Трансформации", style="Header.TLabel").pack(side=tk.LEFT)
        chk_all = ttk.Checkbutton(trans_head, text="Ко всем листам", variable=self.var_apply_all, style="TCheckbutton")
        chk_all.pack(side=tk.RIGHT)

        rot_frame = tk.Frame(left_sidebar, bg=self.panel_bg)
        rot_frame.pack(fill=tk.X, pady=2)
        ttk.Button(rot_frame, text="⟲ -90°", width=7, command=lambda: self.on_rotate(-90)).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=1)
        ttk.Button(rot_frame, text="⟳ +90°", width=7, command=lambda: self.on_rotate(90)).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=1)
        ttk.Button(rot_frame, text="180°", width=7, command=lambda: self.on_rotate(180)).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=1)

        action_frame = tk.Frame(left_sidebar, bg=self.panel_bg)
        action_frame.pack(fill=tk.X, pady=2)
        ttk.Button(action_frame, text="⇲ В нули (0, 0)", width=12, command=self.on_align_zero).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=1)
        ttk.Button(action_frame, text="⚡ Резка от упоров", width=12, command=self.on_sort_bottom_left).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=1)

        # Быстрое переключение технологической обрезки листа
        self.btn_toggle_cutoff = ttk.Button(left_sidebar, text="✂ Обрезка: отсутствует", command=self.on_toggle_cutoff)
        self.btn_toggle_cutoff.pack(fill=tk.X, pady=(3, 2))

        ttk.Separator(left_sidebar, orient="horizontal").pack(fill=tk.X, pady=6)

        # Сохранение и масштаб
        btn_save = ttk.Button(left_sidebar, text="💾 Сохранить проект (.NSP)", style="Success.TButton", command=self.on_save_nsp)
        btn_save.pack(fill=tk.X, pady=(2, 3))

        btn_pdf = ttk.Button(left_sidebar, text="📄 Сохранить отчет (PDF)", style="Pdf.TButton", command=self.on_save_pdf)
        btn_pdf.pack(fill=tk.X, pady=(0, 4))

        btn_fit = ttk.Button(left_sidebar, text="🔍 Вписать лист в окно", command=self.fit_view)
        btn_fit.pack(fill=tk.X, pady=2)

        # ======================================================================
        # 2. ПРАВАЯ ПАНЕЛЬ - Список и переименование деталей активного листа
        # ======================================================================
        right_sidebar = tk.Frame(main_frame, bg=self.panel_bg, width=380, padx=10, pady=10)
        right_sidebar.pack(side=tk.RIGHT, fill=tk.Y)
        right_sidebar.pack_propagate(False)

        ttk.Label(right_sidebar, text="Детали активного листа", style="Header.TLabel").pack(anchor="w", pady=(0, 2))
        ttk.Label(right_sidebar, text="Кликните на деталь для подсветки и изменения имени", style="Sub.TLabel").pack(anchor="w", pady=(0, 6))

        # Таблица деталей Treeview с прокруткой
        tree_container = tk.Frame(right_sidebar, bg=self.panel_bg)
        tree_container.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

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
        rename_box = tk.LabelFrame(right_sidebar, text=" Переименование выбранной детали ", bg=self.panel_bg, fg=self.accent_color, padx=10, pady=8)
        rename_box.pack(fill=tk.X, pady=(0, 4))

        self.lbl_selected_info = ttk.Label(rename_box, text="Выберите деталь в таблице выше", foreground=self.text_muted)
        self.lbl_selected_info.pack(anchor="w", pady=(0, 4))

        edit_row = tk.Frame(rename_box, bg=self.panel_bg)
        edit_row.pack(fill=tk.X, pady=2)

        self.ent_rename = ttk.Entry(edit_row, font=("Segoe UI", 10))
        self.ent_rename.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.ent_rename.bind("<Return>", lambda e: self.on_apply_rename())

        btn_apply_name = ttk.Button(edit_row, text="Применить", style="Accent.TButton", command=self.on_apply_rename)
        btn_apply_name.pack(side=tk.RIGHT)

        btn_reset_names = ttk.Button(rename_box, text="Сбросить имена к стандартным", command=self.on_reset_all_names)
        btn_reset_names.pack(fill=tk.X, pady=(6, 0))

        # Правовая маркировка об условиях использования (внизу справа)
        self.lbl_lic = tk.Label(
            right_sidebar,
            text="Использование ПО строго по условиям LICENSE (pururuall)",
            bg=self.panel_bg,
            fg=self.text_muted,
            font=("Segoe UI", 8, "underline"),
            cursor="hand2"
        )
        self.lbl_lic.pack(side=tk.BOTTOM, anchor="e", pady=(6, 2))
        self.lbl_lic.bind("<Button-1>", lambda e: self.show_license_dialog())
        self.lbl_lic.bind("<Enter>", lambda e: self.lbl_lic.configure(fg=self.accent_color))
        self.lbl_lic.bind("<Leave>", lambda e: self.lbl_lic.configure(fg=self.text_muted))

        # ======================================================================
        # 3. ЦЕНТРАЛЬНАЯ ЧАСТЬ - 2D Холст (Canvas в стиле Blender 3D Viewport)
        # ======================================================================
        canvas_container = tk.Frame(main_frame, bg="#303030")
        canvas_container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(canvas_container, bg="#303030", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # Нижняя информационная строка
        self.status_bar = tk.Frame(canvas_container, bg="#242424", height=28, padx=10)
        self.status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        self.lbl_coords = tk.Label(self.status_bar, text="X: 0.0 мм  |  Y: 0.0 мм", bg="#242424", fg="#b0b0b0", font=("Consolas", 9))
        self.lbl_coords.pack(side=tk.LEFT)

        self.lbl_legend = tk.Label(
            self.status_bar,
            text="■ Лист (Оранжевый)   ■ Детали (Графит)   ■ Выбранная (Blender Orange)   ■ Отверстия (Синий)   ■ Обрезка (Красный / Пунктир)",
            bg="#242424", fg="#888888", font=("Segoe UI", 8)
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
    # УПРАВЛЕНИЕ ЛИСТАМИ ПРОЕКТА
    # ==========================================================================

    def on_open_files(self):
        filetypes = [
            ("Все файлы раскроя и отчетов (*.iso;*.cnc;*.nc;*.tap;*.txt;*.pdf)", "*.iso;*.cnc;*.nc;*.tap;*.txt;*.pdf"),
            ("Файлы программ ЧПУ (*.iso;*.cnc;*.nc;*.tap;*.txt)", "*.iso;*.cnc;*.nc;*.tap;*.txt"),
            ("Отчеты Lantek (PDF) (*.pdf)", "*.pdf"),
            ("Все файлы (*.*)", "*.*")
        ]
        init_dir = r"d:\Пупупу\пример работ с Lantec" if os.path.exists(r"d:\Пупупу\пример работ с Lantec") else (r"d:\Пупупу\тест" if os.path.exists(r"d:\Пупупу\тест") else os.getcwd())
        paths = filedialog.askopenfilenames(title="Выберите файлы программ ЧПУ или отчеты Lantek PDF", filetypes=filetypes, initialdir=init_dir)
        if paths:
            self.load_mixed_files(list(paths))

    def load_mixed_files(self, paths):
        cnc_paths = [p for p in paths if not p.lower().endswith('.pdf')]
        pdf_paths = [p for p in paths if p.lower().endswith('.pdf')]

        if cnc_paths:
            self.load_iso_files(cnc_paths)

        if pdf_paths:
            self.attach_lantek_pdfs(pdf_paths)

    def on_attach_pdf(self):
        filetypes = [("Отчеты Lantek (PDF) (*.pdf)", "*.pdf"), ("Все файлы", "*.*")]
        init_dir = r"d:\Пупупу\пример работ с Lantec" if os.path.exists(r"d:\Пупупу\пример работ с Lantec") else os.getcwd()
        paths = filedialog.askopenfilenames(title="Выберите PDF-отчеты Lantek для привязки", filetypes=filetypes, initialdir=init_dir)
        if paths:
            self.attach_lantek_pdfs(list(paths))

    def attach_lantek_pdfs(self, pdf_paths):
        if not pdf_paths:
            return
        try:
            from lantek_pdf import parse_lantek_pdf, apply_lantek_reports_to_project
            all_reports = []
            for pf in pdf_paths:
                try:
                    all_reports.extend(parse_lantek_pdf(pf))
                except Exception as ex:
                    messagebox.showwarning("Ошибка парсинга PDF", f"Не удалось прочитать {os.path.basename(pf)}:\n{ex}")

            if not all_reports:
                return

            res = apply_lantek_reports_to_project(self.project, all_reports)

            if res.get('detected_job_name'):
                self.ent_job_name.delete(0, tk.END)
                self.ent_job_name.insert(0, self.project.job_name)

            self._refresh_sheets_table()
            self._update_ui_for_active_sheet()
            self.render()

            msg = (
                f"Обработано отчетов Lantek: {len(all_reports)}\n\n"
                f"• Листов сопоставлено: {res['matched_plates']}\n"
                f"• Деталей переименовано: {res['renamed_parts']}"
            )
            if res.get('detected_job_name'):
                msg += f"\n• Имя заказа: {res['detected_job_name']}"
            messagebox.showinfo("Отчеты Lantek привязаны", msg)
        except ImportError:
            messagebox.showwarning(
                "Требуется pypdf",
                "Для чтения отчетов Lantek PDF требуется библиотека 'pypdf'.\n"
                "Установите ее командой:\npip install pypdf"
            )
        except Exception as e:
            messagebox.showerror("Ошибка привязки PDF", f"Не удалось привязать отчеты:\n{e}")

    def load_iso_files(self, paths):
        if not paths:
            return

        added_count = 0
        first_added_idx = len(self.project.plates)

        for path in paths:
            try:
                parser = IsoParser(path)
                parser.parse()

                plate = PlateData(
                    file_path=path,
                    plate_name=parser.program_name,
                    sheet_width=parser.sheet_width,
                    sheet_height=parser.sheet_height,
                    thickness=parser.thickness,
                    material=parser.material,
                    repeat_count=1,
                    include_cutoff=False
                )
                plate.raw_closed_contours = parser.closed_contours
                plate.raw_open_contours = parser.open_contours

                # По умолчанию для вертикального листа CAM Lantek (W <= H): swap_xy=True, flip_y=True
                if plate.sheet_width <= plate.sheet_height:
                    plate.swap_xy = True
                    plate.flip_y = True
                else:
                    plate.swap_xy = False
                    plate.flip_y = False

                plate.apply_transformations()
                self.project.add_plate(plate)
                added_count += 1
            except Exception as e:
                messagebox.showerror("Ошибка загрузки", f"Не удалось прочитать {os.path.basename(path)}:\n{e}")

        if added_count > 0:
            detected_job = self.project.detect_job_name()
            self.project.job_name = detected_job
            self.ent_job_name.delete(0, tk.END)
            self.ent_job_name.insert(0, detected_job)

            self._refresh_sheets_table()
            self._select_sheet(first_added_idx)

    def on_sheet_selected(self, event):
        sel = self.tree_sheets.selection()
        if not sel:
            return
        iid = sel[0]
        idx = int(iid.split("_")[1])
        if 0 <= idx < len(self.project.plates) and idx != self.current_plate_idx:
            self.current_plate_idx = idx
            self._update_ui_for_active_sheet()
            self.fit_view()

    def _select_sheet(self, idx):
        if 0 <= idx < len(self.project.plates):
            self.current_plate_idx = idx
            iid = f"sheet_{idx}"
            self.tree_sheets.selection_set(iid)
            self.tree_sheets.see(iid)
            self._update_ui_for_active_sheet()
            self.fit_view()

    def _refresh_sheets_table(self, preserve_selection=True):
        cur_idx = self.current_plate_idx
        for item in self.tree_sheets.get_children():
            self.tree_sheets.delete(item)

        for idx, p in enumerate(self.project.plates):
            iid = f"sheet_{idx}"
            dims = f"{p.sheet_width:.0f}x{p.sheet_height:.0f}"
            self.tree_sheets.insert("", tk.END, iid=iid, values=(idx + 1, p.plate_name, dims, p.repeat_count, len(p.parts)))

        self.lbl_sheets_count.config(text=f"{len(self.project.plates)} шт.")

        if preserve_selection and 0 <= cur_idx < len(self.project.plates):
            iid = f"sheet_{cur_idx}"
            self.tree_sheets.selection_set(iid)
            self.tree_sheets.see(iid)

    def on_repeat_changed(self):
        p = self.active_plate
        if not p:
            return
        try:
            val = int(self.spin_repeat.get())
            if val < 1:
                val = 1
            p.repeat_count = val
            self._refresh_sheets_table(preserve_selection=True)
        except ValueError:
            pass

    def on_remove_sheet(self):
        if not self.project.plates:
            return
        if self.current_plate_idx < 0:
            return
        idx = self.current_plate_idx
        self.project.remove_plate(idx)

        new_idx = min(idx, len(self.project.plates) - 1)
        self.current_plate_idx = new_idx
        self._refresh_sheets_table()
        if new_idx >= 0:
            self._select_sheet(new_idx)
        else:
            self._update_ui_for_active_sheet()

    def on_clear_sheets(self):
        if not self.project.plates:
            return
        if messagebox.askyesno("Очистить проект", "Удалить все листы из проекта?"):
            self.project.plates.clear()
            self.current_plate_idx = -1
            self.project.custom_names.clear()
            self._refresh_sheets_table()
            self._update_ui_for_active_sheet()

    def on_move_sheet(self, direction):
        if self.current_plate_idx < 0:
            return
        idx = self.current_plate_idx
        target = idx + direction
        if 0 <= target < len(self.project.plates):
            self.project.move_plate(idx, direction)
            self.current_plate_idx = target
            self._refresh_sheets_table()
            self._select_sheet(target)

    def _update_ui_for_active_sheet(self):
        p = self.active_plate
        if not p:
            self.lbl_active_sheet.config(text="Параметры листа")
            self.ent_w.delete(0, tk.END)
            self.ent_h.delete(0, tk.END)
            self.ent_t.delete(0, tk.END)
            self.ent_mat.delete(0, tk.END)
            self.spin_repeat.delete(0, tk.END)
            self.spin_repeat.insert(0, "1")
            self.master_groups = []
            self.selected_group_key = None
            self._refresh_parts_table()
            self.update_cutoff_button_ui()
            self.update_end_zero_button_ui()
            self.render()
            return

        self.lbl_active_sheet.config(text=f"Лист #{self.current_plate_idx + 1}: {p.plate_name}")
        self.ent_w.delete(0, tk.END)
        self.ent_w.insert(0, str(int(p.sheet_width)))
        self.ent_h.delete(0, tk.END)
        self.ent_h.insert(0, str(int(p.sheet_height)))
        self.ent_t.delete(0, tk.END)
        self.ent_t.insert(0, str(p.thickness))
        self.ent_mat.delete(0, tk.END)
        self.ent_mat.insert(0, p.material)

        self.spin_repeat.delete(0, tk.END)
        self.spin_repeat.insert(0, str(p.repeat_count))

        self._update_master_groups_for_active_plate()
        self._refresh_parts_table()
        self.update_cutoff_button_ui()
        self.update_end_zero_button_ui()
        self.render()

    def on_apply_sheet_params(self):
        p = self.active_plate
        if not p:
            return
        try:
            w = float(self.ent_w.get())
            h = float(self.ent_h.get())
            t = float(self.ent_t.get())
            mat = self.ent_mat.get().strip() or "FE"

            targets = self.project.plates if self.var_apply_all.get() else [p]
            for pl in targets:
                pl.sheet_width = w
                pl.sheet_height = h
                pl.thickness = t
                pl.material = mat
                pl.apply_transformations()

            self._refresh_sheets_table(preserve_selection=True)
            self._update_ui_for_active_sheet()
        except ValueError:
            messagebox.showwarning("Некорректный ввод", "Введите корректные числовые значения габаритов и толщины.")

    # ==========================================================================
    # ТРАНСФОРМАЦИИ РАСКРОЯ
    # ==========================================================================

    def _get_target_plates(self):
        if self.var_apply_all.get():
            return self.project.plates
        return [self.active_plate] if self.active_plate else []

    def on_toggle_swap_axes(self):
        for p in self._get_target_plates():
            p.swap_xy = not p.swap_xy
            p.apply_transformations()
        self._update_ui_for_active_sheet()

    def on_rotate(self, angle_delta):
        for p in self._get_target_plates():
            p.rot_deg = (p.rot_deg + angle_delta) % 360
            p.apply_transformations()
        self._update_ui_for_active_sheet()

    def on_flip_x(self):
        for p in self._get_target_plates():
            p.flip_x = not p.flip_x
            p.apply_transformations()
        self._update_ui_for_active_sheet()

    def on_flip_y(self):
        for p in self._get_target_plates():
            p.flip_y = not p.flip_y
            p.apply_transformations()
        self._update_ui_for_active_sheet()

    def on_align_zero(self):
        """Перемещение всей раскладки в нижний левый угол листа (X=0, Y=0, к упорам)"""
        self.on_align_margin(margin=0.0)

    def on_align_margin(self, margin=10.0):
        """
        Выравнивание раскладки с отступом от левого и нижнего упоров (X=0, Y=0).
        Отступ рассчитывается СТРОГО по габаритам деталей (parts), чтобы
        технологические резы обрезки листа не искажали заданный отступ.
        """
        for p in self._get_target_plates():
            parts_pts = []
            for part in p.parts:
                parts_pts.extend(part.outer_contour.get_discrete_points())
            if not parts_pts:
                for c in p.open_contours:
                    parts_pts.extend(c.get_discrete_points())
            if parts_pts:
                min_x = min(pt[0] for pt in parts_pts)
                min_y = min(pt[1] for pt in parts_pts)
                p.dx += (margin - min_x)
                p.dy += (margin - min_y)
                p.apply_transformations()
        self._update_ui_for_active_sheet()

    def on_center_sheet(self):
        """Центрирование раскладки по центру листа (по габаритам деталей)"""
        for p in self._get_target_plates():
            parts_pts = []
            for part in p.parts:
                parts_pts.extend(part.outer_contour.get_discrete_points())
            if not parts_pts:
                for c in p.open_contours:
                    parts_pts.extend(c.get_discrete_points())
            if parts_pts:
                min_x = min(pt[0] for pt in parts_pts)
                max_x = max(pt[0] for pt in parts_pts)
                min_y = min(pt[1] for pt in parts_pts)
                max_y = max(pt[1] for pt in parts_pts)
                nw = max_x - min_x
                nh = max_y - min_y
                p.dx += ((p.sheet_width - nw) / 2.0 - min_x)
                p.dy += ((p.sheet_height - nh) / 2.0 - min_y)
                p.apply_transformations()
        self._update_ui_for_active_sheet()

    def on_sort_bottom_left(self):
        """Переупорядочивание деталей раскроя от базовых упоров (0,0) - старт резки из нижнего левого угла (синяя стрелка)"""
        target_plates = self._get_target_plates()
        if not target_plates:
            return
        for p in target_plates:
            p.parts = sort_parts_from_bottom_left(p.parts)
        self._update_master_groups_for_active_plate()
        self._refresh_parts_table()
        self.render()
        messagebox.showinfo(
            "Порядок резки оптимизирован",
            "Порядок резки деталей переупорядочен от нижнего левого угла (упоры X=0, Y=0):\n\n"
            "• Станок начнет резку с ближайшей к упорам детали (синяя стрелка);\n"
            "• Траектория оптимизирована снизу вверх без лишних холостых перемещений (G00);\n"
            "• Первая деталь отмечена маркером '▶ Старт #1' на чертеже."
        )

    def on_toggle_cutoff(self):
        p = self.active_plate
        if not p:
            return
        new_val = not getattr(p, 'include_cutoff', False)
        for pl in self._get_target_plates():
            pl.include_cutoff = new_val
        self.update_cutoff_button_ui()
        self.render()

    def update_cutoff_button_ui(self):
        p = self.active_plate
        if not p or not p.raw_open_contours:
            self.btn_toggle_cutoff.config(text="✂ Обрезка: отсутствует", state=tk.DISABLED)
        elif getattr(p, 'include_cutoff', False):
            self.btn_toggle_cutoff.config(text="✂ Обрезка: ВКЛ (пойдет в NSP)", state=tk.NORMAL)
        else:
            self.btn_toggle_cutoff.config(text="✂ Обрезка: ВЫКЛ (в NSP не идет)", state=tk.NORMAL)

    def on_toggle_end_zero(self):
        """Переключение команды возврата станка к нулевым координатам (End 0;0) после завершения резки"""
        p = self.active_plate
        if not p:
            return
        new_val = not getattr(p, 'return_to_zero', False)
        for pl in self._get_target_plates():
            pl.return_to_zero = new_val
        self.project.return_to_zero = new_val
        self.update_end_zero_button_ui()
        self.render()

    def update_end_zero_button_ui(self):
        if not hasattr(self, 'btn_toggle_end_zero'):
            return
        p = self.active_plate
        if not p:
            self.btn_toggle_end_zero.config(text="⌂ End 0;0: ВЫКЛ (возврат)", state=tk.DISABLED)
        elif getattr(p, 'return_to_zero', False):
            self.btn_toggle_end_zero.config(text="⌂ End 0;0: ВКЛ (возврат в 0, 0)", state=tk.NORMAL)
        else:
            self.btn_toggle_end_zero.config(text="⌂ End 0;0: ВЫКЛ (возврат в ноль)", state=tk.NORMAL)

    def show_license_dialog(self):
        """Отображение модального окна с полным текстом условий лицензии (LICENSE)"""
        license_text = ""
        candidate_paths = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "LICENSE.txt"),
            os.path.join(os.getcwd(), "LICENSE.txt"),
        ]
        if hasattr(sys, '_MEIPASS'):
            candidate_paths.insert(0, os.path.join(sys._MEIPASS, "LICENSE.txt"))

        for path in candidate_paths:
            if os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        license_text = f.read()
                    break
                except Exception:
                    pass

        if not license_text:
            license_text = EMBEDDED_LICENSE_TEXT

        dlg = tk.Toplevel(self)
        dlg.title("Лицензионные условия (LICENSE) — iso2nsp")
        dlg.geometry("660x540")
        dlg.minsize(500, 400)
        dlg.configure(bg="#222222")
        dlg.transient(self)
        dlg.grab_set()

        # Центрирование окна
        dlg.update_idletasks()
        rx = self.winfo_x() + max(0, (self.winfo_width() - 660) // 2)
        ry = self.winfo_y() + max(0, (self.winfo_height() - 540) // 2)
        dlg.geometry(f"660x540+{rx}+{ry}")

        # Заголовок
        header = tk.Label(
            dlg,
            text="⚖ Условия использования и отказ от ответственности",
            font=("Segoe UI", 12, "bold"),
            bg="#222222",
            fg=self.accent_color,
            pady=12
        )
        header.pack(fill=tk.X)

        # Текстовое поле с прокруткой
        text_frame = tk.Frame(dlg, bg="#222222", padx=16, pady=4)
        text_frame.pack(fill=tk.BOTH, expand=True)

        txt_widget = tk.Text(
            text_frame,
            wrap=tk.WORD,
            bg="#161616",
            fg="#e6e6e6",
            insertbackground="#ffffff",
            font=("Segoe UI", 10),
            padx=12,
            pady=12,
            relief=tk.FLAT,
            borderwidth=1,
            highlightthickness=1,
            highlightbackground="#3d3d3d"
        )
        scroll = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=txt_widget.yview)
        txt_widget.configure(yscrollcommand=scroll.set)

        txt_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        txt_widget.insert(tk.END, license_text.strip())
        txt_widget.configure(state=tk.DISABLED)

        # Нижняя панель с кнопкой закрытия
        btn_frame = tk.Frame(dlg, bg="#222222", pady=12)
        btn_frame.pack(fill=tk.X)

        btn_close = ttk.Button(btn_frame, text="Закрыть", style="Accent.TButton", command=dlg.destroy)
        btn_close.pack(side=tk.RIGHT, padx=16)
        btn_close.focus_set()

        dlg.bind("<Escape>", lambda e: dlg.destroy())

    # ==========================================================================
    # ТАБЛИЦА И ПЕРЕИМЕНОВАНИЕ ДЕТАЛЕЙ
    # ==========================================================================

    def _update_master_groups_for_active_plate(self):
        p = self.active_plate
        if not p:
            self.master_groups = []
            return

        master_list = []
        for part in p.parts:
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
                geom_key = compute_part_geom_key(part)

                current_name = getattr(part, 'part_name', '')
                c_name = self.project.custom_names.get(geom_key, "")
                if c_name and c_name != "Part":
                    def_name = c_name
                elif current_name and current_name != "Part" and not current_name.startswith(f"{p.plate_name}_P"):
                    def_name = current_name
                    self.project.custom_names[geom_key] = def_name
                else:
                    def_name = f"{p.plate_name}_P{len(master_list) + 1}"

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
        if 0 <= self.selected_group_idx < len(self.master_groups):
            self.selected_group_key = self.master_groups[self.selected_group_idx]['key']
        else:
            self.selected_group_idx = 0 if self.master_groups else -1
            self.selected_group_key = self.master_groups[0]['key'] if self.master_groups else None

    def _refresh_parts_table(self):
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

            if self.selected_group_idx == idx:
                selected_iid = iid

        if selected_iid:
            self.tree_parts.selection_set(selected_iid)
            self.tree_parts.see(selected_iid)
        elif self.master_groups:
            self.selected_group_idx = 0
            first_iid = "group_0"
            self.tree_parts.selection_set(first_iid)
            self.on_part_selected(None)

    def on_part_selected(self, event):
        sel = self.tree_parts.selection()
        if not sel:
            self.selected_group_idx = -1
            self.selected_group_key = None
            self.lbl_selected_info.config(text="Выберите деталь в таблице выше")
            self.ent_rename.delete(0, tk.END)
            self.render()
            return

        iid = sel[0]
        idx = int(iid.split("_")[1])
        if idx < len(self.master_groups):
            self.selected_group_idx = idx
            group = self.master_groups[idx]
            self.selected_group_key = group['key']
            info_text = f"Деталь #{idx+1}: {group['width']:.1f} x {group['height']:.1f} мм ({len(group['instances'])} шт.)"
            self.lbl_selected_info.config(text=info_text, foreground=self.accent_color)

            self.ent_rename.delete(0, tk.END)
            self.ent_rename.insert(0, group['name'])
            self.ent_rename.select_range(0, tk.END)

            self.render()

    def on_apply_rename(self):
        new_name = self.ent_rename.get().strip()
        if not new_name:
            messagebox.showwarning("Внимание", "Имя детали не может быть пустым.")
            return

        if 0 <= self.selected_group_idx < len(self.master_groups):
            group = self.master_groups[self.selected_group_idx]
            group['name'] = new_name
            self.project.custom_names[group['key']] = new_name

            for part in group['instances']:
                part.part_name = new_name

            # Сквозное переименование идентичных деталей на ВСЕХ листах проекта
            for pl in self.project.plates:
                for pt in pl.parts:
                    if compute_part_geom_key(pt) == group['key']:
                        pt.part_name = new_name

            self._refresh_parts_table()
            self.render()

    def on_reset_all_names(self):
        if not self.master_groups:
            return
        if messagebox.askyesno("Сброс имен", "Сбросить пользовательские названия деталей к стандартным?"):
            self.project.custom_names.clear()
            for pl in self.project.plates:
                for pt in pl.parts:
                    pt.part_name = "Part"
            self._update_master_groups_for_active_plate()
            self._refresh_parts_table()
            self.render()

    # ==========================================================================
    # СОХРАНЕНИЕ ПРОЕКТА CYPNEST (.NSP)
    # ==========================================================================

    def on_save_nsp(self):
        if not self.project.plates:
            messagebox.showwarning("Нет данных", "Сначала добавьте файлы управляющих программ ЧПУ в проект.")
            return

        job_name = self.ent_job_name.get().strip() or self.project.detect_job_name()
        self.project.job_name = job_name

        default_name = f"{job_name}.nsp"
        out_path = filedialog.asksaveasfilename(
            title="Сохранить проект раскроя CypNest",
            defaultextension=".nsp",
            filetypes=[("Проект CypNest", "*.nsp"), ("XML файл", "*.xml")],
            initialfile=default_name
        )
        if not out_path:
            return

        try:
            xml_text = self.project.generate_xml_string()
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(xml_text)
            total_parts = sum(len(p.parts) for p in self.project.plates)
            msg = (
                f"Проект раскроя успешно сохранен:\n{out_path}\n\n"
                f"• Листов в проекте: {len(self.project.plates)}\n"
                f"• Всего деталей на листах: {total_parts}\n"
                f"• Имена деталей и тиражи листов сохранены!"
            )
            messagebox.showinfo("Успешно", msg)
        except Exception as e:
            messagebox.showerror("Ошибка сохранения", f"Не удалось сформировать .nsp файл:\n{e}")

    def on_save_pdf(self):
        if not self.project.plates:
            messagebox.showwarning("Нет данных", "Сначала добавьте файлы управляющих программ ЧПУ в проект.")
            return

        job_name = self.ent_job_name.get().strip() or self.project.detect_job_name()
        self.project.job_name = job_name

        single_plate_idx = None
        if len(self.project.plates) > 1:
            ans = messagebox.askyesnocancel(
                "Экспорт отчета в PDF",
                f"В проекте {len(self.project.plates)} листов.\n\n"
                f"Сформировать единый PDF-отчет по ВСЕМ листам проекта ({len(self.project.plates)} стр.)?\n\n"
                f"• Нажмите [Да], чтобы экспортировать ВСЕ листы ({len(self.project.plates)} стр.)\n"
                f"• Нажмите [Нет], чтобы экспортировать ТОЛЬКО текущий лист ({self.active_plate.plate_name})\n"
                f"• Нажмите [Отмена] для отмены действия"
            )
            if ans is None:
                return
            elif ans is False:
                single_plate_idx = self.current_plate_idx

        default_name = f"{job_name}_Отчет.pdf" if single_plate_idx is None else f"{self.active_plate.plate_name}_Отчет.pdf"
        out_path = filedialog.asksaveasfilename(
            title="Сохранить производственный отчет (PDF)",
            defaultextension=".pdf",
            initialfile=default_name,
            filetypes=[("PDF Документы (*.pdf)", "*.pdf"), ("Все файлы (*.*)", "*.*")]
        )
        if not out_path:
            return

        try:
            from pdf_report import generate_pdf_report
            generate_pdf_report(self.project, out_path, single_plate_idx=single_plate_idx)

            num_pages = len(self.project.plates) if single_plate_idx is None else 1
            if messagebox.askyesno(
                "Отчет сформирован",
                f"Производственный PDF-отчет успешно создан!\n\n"
                f"• Файл: {os.path.basename(out_path)}\n"
                f"• Листов в отчете: {num_pages}\n\n"
                f"Открыть созданный PDF-отчет прямо сейчас?"
            ):
                try:
                    os.startfile(out_path)
                except Exception:
                    pass
        except Exception as e:
            messagebox.showerror("Ошибка создания PDF", f"Не удалось сгенерировать PDF-отчет:\n{e}")

    # ==========================================================================
    # ИНТЕРАКТИВНЫЙ 2D РЕНДЕР С ПОДСВЕТКОЙ
    # ==========================================================================

    def fit_view(self):
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw < 50 or ch < 50:
            cw, ch = 700, 750

        p = self.active_plate
        sw = p.sheet_width if p else 1500.0
        sh = p.sheet_height if p else 3000.0

        margin = 35
        avail_w = cw - 2 * margin
        avail_h = ch - 2 * margin

        scale_x = avail_w / max(1.0, sw)
        scale_y = avail_h / max(1.0, sh)
        self.zoom_scale = min(scale_x, scale_y)

        draw_w = sw * self.zoom_scale
        draw_h = sh * self.zoom_scale

        self.pan_x = margin + (avail_w - draw_w) / 2.0
        self.pan_y = margin + (avail_h - draw_h) / 2.0

        self.render()

    def to_screen(self, x, y):
        p = self.active_plate
        sh = p.sheet_height if p else 3000.0
        sx = self.pan_x + x * self.zoom_scale
        sy = self.pan_y + (sh - y) * self.zoom_scale
        return sx, sy

    def to_world(self, sx, sy):
        p = self.active_plate
        sh = p.sheet_height if p else 3000.0
        x = (sx - self.pan_x) / self.zoom_scale
        y = sh - (sy - self.pan_y) / self.zoom_scale
        return x, y

    def render(self):
        self.canvas.delete("all")

        p = self.active_plate
        if not p:
            self.canvas.create_text(
                self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2,
                text="Нажмите '+ Добавить файлы...' для загрузки программ ЧПУ",
                fill="#777777", font=("Segoe UI", 13, "bold")
            )
            return

        sw = p.sheet_width
        sh = p.sheet_height

        # 1. Граница и фон листа (Blender dark workspace)
        p1 = self.to_screen(0, 0)
        p2 = self.to_screen(sw, sh)
        sx1, sy2 = p1
        sx2, sy1 = p2

        self.canvas.create_rectangle(sx1, sy1, sx2, sy2, fill="#222222", outline=self.accent_color, width=2, dash=(6, 4))

        # Сетка шагом 100 / 500 мм (Blender viewport grid)
        grid_step = 500.0 if self.zoom_scale < 0.35 else 100.0
        gx = grid_step
        while gx < sw:
            lx1, ly1 = self.to_screen(gx, 0)
            lx2, ly2 = self.to_screen(gx, sh)
            self.canvas.create_line(lx1, ly1, lx2, ly2, fill="#333333", width=1)
            gx += grid_step

        gy = grid_step
        while gy < sh:
            lx1, ly1 = self.to_screen(0, gy)
            lx2, ly2 = self.to_screen(sw, gy)
            self.canvas.create_line(lx1, ly1, lx2, ly2, fill="#333333", width=1)
            gy += grid_step

        # Текст габаритов листа и тиража (Blender Orange)
        info_text = f"Лист {self.current_plate_idx + 1}/{len(self.project.plates)}: {p.plate_name}  |  {sw:.0f} x {sh:.0f} x {p.thickness:.1f} мм ({p.material})  |  Тираж: {p.repeat_count} шт."
        self.canvas.create_text(
            sx1 + 8, sy1 + 14, text=info_text,
            anchor="w", fill=self.accent_color, font=("Segoe UI", 9, "bold")
        )

        # 2. Отрисовка деталей (Blender solid shading / active orange wireframe)
        selected_instances = set()
        if 0 <= self.selected_group_idx < len(self.master_groups):
            selected_instances = set(self.master_groups[self.selected_group_idx]['instances'])

        for part in p.parts:
            is_selected = (part in selected_instances)

            if is_selected:
                fill_color = "#5c3814"      # Теплый оранжевый оттенок Blender для выбранной детали
                outline_color = "#ea7600"   # Свечение Blender Orange для активного объекта
                outline_width = 2.5
            else:
                fill_color = "#3a4049"      # Графитово-стальной оттенок Blender Solid / Clay
                outline_color = "#8b9cb0"   # Четкий серебристо-стальной контур
                outline_width = 1.2

            pts = part.outer_contour.get_discrete_points()
            screen_pts = []
            for px, py in pts:
                screen_pts.extend(self.to_screen(px, py))

            if len(screen_pts) >= 6:
                self.canvas.create_polygon(screen_pts, fill=fill_color, outline=outline_color, width=outline_width)

            for hole in part.holes:
                hpts = hole.get_discrete_points()
                h_screen = []
                for hx, hy in hpts:
                    h_screen.extend(self.to_screen(hx, hy))
                if len(h_screen) >= 6:
                    hole_outline = "#ea7600" if is_selected else "#4772b3"
                    self.canvas.create_polygon(h_screen, fill="#222222", outline=hole_outline, width=1.2)

        # Маркер первой детали (точка станка / старт резки - Blender Orange)
        if p.parts:
            first_pt = p.parts[0]
            fbx = first_pt.bbox()
            fcx = (fbx[0] + fbx[2]) / 2.0
            fcy = (fbx[1] + fbx[3]) / 2.0
            fsx, fsy = self.to_screen(fcx, fcy)
            self.canvas.create_text(
                fsx, fsy,
                text="▶ Старт #1",
                fill=self.accent_color,
                font=("Segoe UI", 9, "bold")
            )

        # Обозначение физических упоров станка (зеленые штрихи Blender оси на X=0 и Y=0)
        stop_len = 10
        for stop_y in range(100, int(sh), 200):
            sx0, sy0 = self.to_screen(0, stop_y)
            self.canvas.create_line(sx0, sy0, sx0 - stop_len, sy0 + stop_len, fill="#5cb85c", width=2)
        for stop_x in range(100, int(sw), 200):
            sx0, sy0 = self.to_screen(stop_x, 0)
            self.canvas.create_line(sx0, sy0, sx0 - stop_len, sy0 + stop_len, fill="#5cb85c", width=2)

        # Текстовые метки упоров
        u_lx, u_ly = self.to_screen(0, sh / 2)
        self.canvas.create_text(u_lx - 16, u_ly, text="◀ Упор X=0", fill="#5cb85c", font=("Segoe UI", 8, "bold"), angle=90)
        u_bx, u_by = self.to_screen(sw / 2, 0)
        self.canvas.create_text(u_bx, u_by + 16, text="▼ Упор Y=0", fill="#5cb85c", font=("Segoe UI", 8, "bold"))

        # Обозначение команды возврата станка в нули (End 0;0 - Blender Orange)
        if getattr(p, 'return_to_zero', False):
            z_sx, z_sy = self.to_screen(0, 0)
            self.canvas.create_oval(z_sx - 6, z_sy - 6, z_sx + 6, z_sy + 6, fill=self.accent_color, outline="#ffffff", width=1.5)
            self.canvas.create_text(z_sx + 36, z_sy - 12, text="⌂ End (0,0)", fill=self.accent_color, font=("Segoe UI", 8, "bold"))

        # 3. Разомкнутые технологические резы (обрезка листа)
        for cut in p.open_contours:
            cpts = cut.get_discrete_points()
            if not cpts:
                continue
            is_inc = getattr(p, 'include_cutoff', False)
            line_color = "#e05353" if is_inc else "#555555"
            line_dash = () if is_inc else (6, 4)
            line_w = 2 if is_inc else 1.5

            for i in range(len(cpts) - 1):
                x1, y1 = self.to_screen(cpts[i][0], cpts[i][1])
                x2, y2 = self.to_screen(cpts[i+1][0], cpts[i+1][1])
                self.canvas.create_line(x1, y1, x2, y2, fill=line_color, width=line_w, dash=line_dash)

            if not is_inc and len(cpts) >= 2:
                mid_x = (cpts[0][0] + cpts[-1][0]) / 2
                mid_y = (cpts[0][1] + cpts[-1][1]) / 2
                sx, sy = self.to_screen(mid_x, mid_y)
                self.canvas.create_text(
                    sx, sy - 8,
                    text="✂ Обрезка (откл.)",
                    fill=self.text_muted,
                    font=("Segoe UI", 8, "italic")
                )

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
