import os
import sys
import time
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from subprocess import CalledProcessError, SubprocessError
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

from disk_erase import get_disk_serial, is_ssd
from utils import get_disk_list, get_base_disk
from disk_partition import partition_disk
from disk_format import format_disk
from log_handler import (
    log_info,
    log_error,
    log_erase_operation,
    session_start,
    session_end,
)
from admin_interface import open_admin_panel
from stats_manager import get_wipe_count
from disk_operations import get_active_disk, process_disk
from config_manager import get_passes


class DiskEraserGUI:
    _REFRESH_INTERVAL_MS = 3000

    _BG = '#0b1220'
    _BG_ELEVATED = '#111b2e'
    _SURFACE = '#14233c'
    _SURFACE2 = '#1a2d4c'
    _SURFACE3 = '#21375c'
    _BORDER = '#27456f'
    _BORDER_SOFT = '#1c3556'
    _TEXT = '#edf4ff'
    _TEXT_DIM = '#9bb4d1'
    _TEXT_FAINT = '#6f87a4'
    _ACCENT = '#0b84ff'
    _ACCENT2 = '#39a0ff'
    _ACCENT_SOFT = '#123252'
    _DANGER = '#ef5350'
    _WARNING = '#f5b342'
    _SUCCESS = '#21c17a'
    _SSD_COLOR = '#4dd7ff'
    _HDD_COLOR = '#f0bf5a'

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("e-Broyeur - Effacement sécurisé de disques")
        self.root.geometry("1280x820")
        self.root.minsize(1120, 720)
        self.root.attributes("-fullscreen", True)
        self.root.configure(bg=self._BG)

        self.disk_vars: Dict[str, tk.BooleanVar] = {}
        self.filesystem_var = tk.StringVar(value="ext4")
        self.passes_var = tk.StringVar(value=str(get_passes()))
        self.erase_method_var = tk.StringVar(value="overwrite")
        self.crypto_fill_var = tk.StringVar(value="random")
        self.label_mode_var = tk.StringVar(value="none")  # "none"|"preserve"|"custom"
        self.custom_label_var = tk.StringVar(value="")
        self.partition_table_var = tk.StringVar(value="mbr")  # "mbr"|"gpt"
        self.disks: List[Dict[str, str]] = []
        self.disk_progress: Dict[str, float] = {}
        self.active_disk = get_active_disk()

        self.active_drive_logged = False
        # Ensemble des disques actuellement occupés par une opération (effacement
        # OU formatage), partagé entre tous les lots lancés en parallèle.
        self._erasing_devs: set = set()
        # Verrou protégeant les accès concurrents à _erasing_devs depuis les
        # différents threads d'effacement/formatage lancés en parallèle.
        self._busy_lock = threading.Lock()
        self._disk_row_cache: Dict[str, dict] = {}
        self._disk_rows: Dict[str, dict] = {}
        self._progress_phase_var = tk.StringVar(value="En attente")
        self._progress_detail_var = tk.StringVar(value="Aucune opération en cours")
        self._progress_stats_var = tk.StringVar(value="0 disque sélectionné")
        self._pending_unmount_dir = None
        self._no_disk_label = None

        session_start()

        if os.geteuid() != 0:
            messagebox.showerror("Erreur", "Ce programme doit être exécuté en tant que root.")
            root.destroy()
            sys.exit(1)

        self.create_widgets()
        self.refresh_disks()
        self.root.after(self._REFRESH_INTERVAL_MS, self._auto_refresh_disks)

    def _setup_theme(self) -> None:
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('.', background=self._BG, foreground=self._TEXT, font=('Segoe UI', 10))
        style.configure(
            'TScrollbar',
            background=self._SURFACE2,
            troughcolor=self._BG_ELEVATED,
            arrowcolor=self._TEXT_DIM,
            bordercolor=self._BORDER_SOFT,
            darkcolor=self._SURFACE2,
            lightcolor=self._SURFACE2,
            relief='flat',
        )
        style.map('TScrollbar', background=[('active', self._SURFACE3)])
        style.configure(
            'TEntry',
            fieldbackground=self._BG_ELEVATED,
            foreground=self._TEXT,
            insertcolor=self._TEXT,
            bordercolor=self._BORDER,
            selectbackground=self._ACCENT,
            selectforeground='white',
            relief='flat',
            padding=6,
        )
        style.configure(
            'Admin.TButton',
            background='#1e3a5f',
            foreground='white',
            font=('Segoe UI', 10, 'bold'),
            borderwidth=0,
            padding=(12, 7),
            relief='flat',
        )
        style.map(
            'Admin.TButton',
            background=[('active', '#2a5080'), ('pressed', '#163050')],
            foreground=[('active', 'white'), ('pressed', 'white')],
        )
        style.configure(
            'Reboot.TButton',
            background='#3d4f66',
            foreground='white',
            font=('Segoe UI', 10, 'bold'),
            borderwidth=0,
            padding=(12, 7),
            relief='flat',
        )
        style.map(
            'Reboot.TButton',
            background=[('active', '#4d6280'), ('pressed', '#2e3d50')],
            foreground=[('active', 'white'), ('pressed', 'white')],
        )

    def _set_status(self, text: str, tone: str = 'idle') -> None:
        color = {
            'idle': self._SUCCESS,
            'busy': self._WARNING,
            'danger': self._DANGER,
            'info': self._ACCENT2,
        }.get(tone, self._SUCCESS)
        self.status_var.set(text)
        if hasattr(self, '_status_dot'):
            self._status_dot.configure(fg=color)

    def _make_card(self, parent: tk.Widget, pady=(0, 0), fill=tk.BOTH, expand=False) -> tk.Frame:
        outer = tk.Frame(parent, bg=self._BORDER_SOFT, bd=0, highlightthickness=0)
        outer.pack(fill=fill, expand=expand, pady=pady)
        inner = tk.Frame(outer, bg=self._SURFACE, padx=1, pady=1)
        inner.pack(fill=tk.BOTH, expand=True)
        content = tk.Frame(inner, bg=self._SURFACE)
        content.pack(fill=tk.BOTH, expand=True)
        return content

    def _section_label(self, parent: tk.Widget, text: str, subtitle: str = "") -> None:
        row = tk.Frame(parent, bg=parent.cget('bg'))
        row.pack(fill=tk.X, pady=(2, 8))
        tk.Frame(row, bg=self._ACCENT2, width=4, height=22).pack(side=tk.LEFT, fill=tk.Y, pady=(1, 1))
        txt = tk.Frame(row, bg=parent.cget('bg'))
        txt.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 0))
        tk.Label(txt, text=text, bg=parent.cget('bg'), fg=self._TEXT,
                 font=('Segoe UI', 10, 'bold')).pack(anchor='w')
        if subtitle:
            tk.Label(txt, text=subtitle, bg=parent.cget('bg'), fg=self._TEXT_FAINT,
                     font=('Segoe UI', 8)).pack(anchor='w', pady=(1, 0))

    def _divider(self, parent: tk.Widget, pady=12) -> None:
        tk.Frame(parent, bg=self._BORDER_SOFT, height=1).pack(fill=tk.X, pady=pady)

    def _action_button(self, parent: tk.Widget, text: str, command,
                       bg: str = None, hover_bg: str = None,
                       fg: str = '#ffffff', accent=False) -> tk.Button:
        bg = bg or self._SURFACE2
        hover_bg = hover_bg or self._SURFACE3
        btn = tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=hover_bg,
            activeforeground=fg,
            font=('Segoe UI', 10, 'bold' if accent else 'normal'),
            bd=0,
            padx=14,
            pady=10,
            cursor='hand2',
            relief=tk.FLAT,
            highlightthickness=0,
        )
        btn.bind('<Enter>', lambda e: btn.configure(bg=hover_bg))
        btn.bind('<Leave>', lambda e: btn.configure(bg=bg))
        return btn

    def _map_disk_color(self, legacy_color: str, is_erasing: bool = False) -> str:
        if is_erasing:
            return self._WARNING
        if legacy_color == 'red':
            return self._DANGER
        if legacy_color == 'blue':
            return self._SSD_COLOR
        return self._HDD_COLOR

    def create_widgets(self) -> None:
        self._setup_theme()

        shell = tk.Frame(self.root, bg=self._BG)
        shell.pack(fill=tk.BOTH, expand=True, padx=18, pady=18)

        header = tk.Frame(shell, bg=self._SURFACE)
        header.pack(fill=tk.X, pady=(0, 14))
        tk.Frame(header, bg=self._ACCENT2, height=3).pack(fill=tk.X, side=tk.TOP)

        header_body = tk.Frame(header, bg=self._SURFACE, padx=18, pady=16)
        header_body.pack(fill=tk.X)

        left_head = tk.Frame(header_body, bg=self._SURFACE)
        left_head.pack(side=tk.LEFT, fill=tk.X, expand=True)

        top_line = tk.Frame(left_head, bg=self._SURFACE)
        top_line.pack(anchor='w')
        tk.Label(top_line, text='e-Broyeur', bg=self._SURFACE, fg=self._TEXT,
                 font=('Segoe UI', 18, 'bold')).pack(side=tk.LEFT)
        tk.Label(top_line, text='  v7.1', bg=self._SURFACE, fg=self._ACCENT2,
                 font=('Segoe UI', 9, 'bold')).pack(side=tk.LEFT, pady=(5, 0))
        tk.Label(
            left_head,
            text='Effacement sécurisé pour supports mécaniques et électroniques, formatage et export de journaux.',
            bg=self._SURFACE,
            fg=self._TEXT_DIM,
            font=('Segoe UI', 9),
        ).pack(anchor='w', pady=(4, 0))

        right_head = tk.Frame(header_body, bg=self._SURFACE)
        right_head.pack(side=tk.RIGHT)
        status_area = tk.Frame(right_head, bg=self._BG_ELEVATED, padx=12, pady=9)
        status_area.pack(side=tk.RIGHT)
        self._status_dot = tk.Label(status_area, text='●', bg=self._BG_ELEVATED,
                                    fg=self._SUCCESS, font=('Segoe UI', 12, 'bold'))
        self._status_dot.pack(side=tk.LEFT)
        self.status_var = tk.StringVar(value='Prêt')
        tk.Label(status_area, textvariable=self.status_var, bg=self._BG_ELEVATED,
                 fg=self._TEXT, font=('Segoe UI', 10, 'bold'), padx=6).pack(side=tk.LEFT)

        body = tk.Frame(shell, bg=self._BG)
        body.pack(fill=tk.BOTH, expand=True)

        left_col = tk.Frame(body, bg=self._BG)
        left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        right_col = tk.Frame(body, bg=self._BG, width=360)
        right_col.pack(side=tk.RIGHT, fill=tk.BOTH)
        right_col.pack_propagate(False)

        disk_card = self._make_card(left_col, pady=(0, 10), expand=True)
        disk_inner = tk.Frame(disk_card, bg=self._SURFACE, padx=16, pady=14)
        disk_inner.pack(fill=tk.BOTH, expand=True)
        self._section_label(disk_inner, 'Disques détectés')

        disk_card_header = tk.Frame(disk_inner, bg=self._SURFACE)
        disk_card_header.pack(fill=tk.X, pady=(0, 10))
        self._disk_count_var = tk.StringVar(value='0 disque')
        tk.Label(disk_card_header, textvariable=self._disk_count_var,
                 bg=self._SURFACE, fg=self._TEXT_DIM, font=('Segoe UI', 9)).pack(side=tk.RIGHT)

        list_holder = tk.Frame(disk_inner, bg=self._BG_ELEVATED)
        list_holder.pack(fill=tk.BOTH, expand=True)

        self.disk_canvas = tk.Canvas(list_holder, bg=self._BG_ELEVATED, highlightthickness=0, bd=0)
        disk_sb = ttk.Scrollbar(list_holder, orient='vertical', command=self.disk_canvas.yview)
        self.scrollable_disk_frame = tk.Frame(self.disk_canvas, bg=self._BG_ELEVATED)
        self.scrollable_disk_frame.bind(
            '<Configure>',
            lambda e: self.disk_canvas.configure(scrollregion=self.disk_canvas.bbox('all')),
        )
        self.disk_canvas.create_window((0, 0), window=self.scrollable_disk_frame, anchor='nw')
        self.disk_canvas.configure(yscrollcommand=disk_sb.set)
        self.disk_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        disk_sb.pack(side=tk.RIGHT, fill=tk.Y)

        footer_legend = tk.Frame(disk_inner, bg=self._SURFACE)
        footer_legend.pack(fill=tk.X, pady=(10, 0))
        for symbol, color, text in [
            ('◉', self._HDD_COLOR, 'Mécanique'),
            ('◈', self._SSD_COLOR, 'Électronique'),
            ('⚠', self._DANGER, 'Système actif'),
            ('⚙', self._WARNING, 'En cours'),
        ]:
            grp = tk.Frame(footer_legend, bg=self._SURFACE)
            grp.pack(side=tk.LEFT, padx=(0, 14))
            tk.Label(grp, text=symbol, bg=self._SURFACE, fg=color,
                     font=('Segoe UI', 10, 'bold')).pack(side=tk.LEFT)
            tk.Label(grp, text=f' {text}', bg=self._SURFACE, fg=self._TEXT_FAINT,
                     font=('Segoe UI', 8)).pack(side=tk.LEFT)

        self.ssd_disclaimer_var = tk.StringVar(value='')
        self.ssd_disclaimer_label = tk.Label(
            left_col,
            textvariable=self.ssd_disclaimer_var,
            bg=self._BG,
            fg=self._SSD_COLOR,
            wraplength=720,
            font=('Segoe UI', 9),
            justify=tk.LEFT,
        )
        self.ssd_disclaimer_label.pack(anchor='w', pady=(2, 0))

        self.disclaimer_var = tk.StringVar(value='')
        self.disclaimer_label = tk.Label(
            left_col,
            textvariable=self.disclaimer_var,
            bg=self._BG,
            fg=self._DANGER,
            wraplength=720,
            font=('Segoe UI', 9),
            justify=tk.LEFT,
        )
        self.disclaimer_label.pack(anchor='w', pady=(2, 0))

        log_card = self._make_card(left_col, pady=(0, 10), expand=True)
        log_inner = tk.Frame(log_card, bg=self._SURFACE, padx=12, pady=12)
        log_inner.pack(fill=tk.BOTH, expand=True)

        log_meta = tk.Frame(log_inner, bg=self._SURFACE)
        log_meta.pack(fill=tk.X, pady=(0, 6))
        tk.Label(log_meta, textvariable=self._progress_phase_var, bg=self._SURFACE,
                 fg=self._TEXT_DIM, font=('Segoe UI', 9)).pack(side=tk.LEFT)
        tk.Label(log_meta, textvariable=self._progress_stats_var, bg=self._SURFACE,
                 fg=self._TEXT_FAINT, font=('Segoe UI', 8)).pack(side=tk.RIGHT)

        tk.Label(log_inner, textvariable=self._progress_detail_var, bg=self._SURFACE,
                 fg=self._TEXT, font=('Segoe UI', 9)).pack(anchor='w', pady=(0, 6))

        log_holder = tk.Frame(log_inner, bg=self._BG_ELEVATED)
        log_holder.pack(fill=tk.BOTH, expand=True)
        self.log_text = tk.Text(
            log_holder,
            height=16,
            wrap=tk.WORD,
            bg=self._BG_ELEVATED,
            fg=self._TEXT,
            insertbackground=self._TEXT,
            font=('Consolas', 9),
            bd=0,
            highlightthickness=0,
            selectbackground=self._ACCENT_SOFT,
            padx=12,
            pady=10,
        )
        log_sb = ttk.Scrollbar(log_holder, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_sb.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_sb.pack(side=tk.RIGHT, fill=tk.Y)

        opt_card = self._make_card(right_col, expand=True)
        inner = tk.Frame(opt_card, bg=self._SURFACE, padx=14, pady=8)
        inner.pack(fill=tk.BOTH, expand=True)

        # ── Méthode d'effacement ──────────────────────────────────────────────
        tk.Label(inner, text="Méthode d'effacement", bg=self._SURFACE,
                 fg=self._TEXT, font=('Segoe UI', 9, 'bold')).pack(anchor='w', pady=(0, 2))

        # Écrasement standard avec passes inline
        overwrite_row = tk.Frame(inner, bg=self._SURFACE)
        overwrite_row.pack(fill=tk.X)
        tk.Radiobutton(
            overwrite_row, text='Écrasement standard', value='overwrite',
            variable=self.erase_method_var,
            command=self.update_method_options,
            bg=self._SURFACE, fg=self._TEXT,
            selectcolor=self._BG_ELEVATED,
            activebackground=self._SURFACE,
            activeforeground=self._ACCENT2,
            font=('Segoe UI', 9), bd=0, highlightthickness=0,
        ).pack(side=tk.LEFT)
        ttk.Entry(overwrite_row, textvariable=self.passes_var, width=4,
                  state='readonly').pack(side=tk.LEFT, padx=(8, 2))
        tk.Label(overwrite_row, text='passes', bg=self._SURFACE,
                 fg=self._TEXT_DIM, font=('Segoe UI', 9)).pack(side=tk.LEFT)
        tk.Label(overwrite_row, text='(admin)', bg=self._SURFACE,
                 fg=self._TEXT_FAINT, font=('Segoe UI', 7, 'italic')).pack(side=tk.LEFT, padx=(4, 0))

        # Cryptographique
        tk.Radiobutton(
            inner, text='Effacement cryptographique', value='crypto',
            variable=self.erase_method_var,
            command=self.update_method_options,
            bg=self._SURFACE, fg=self._TEXT,
            selectcolor=self._BG_ELEVATED,
            activebackground=self._SURFACE,
            activeforeground=self._ACCENT2,
            font=('Segoe UI', 9), bd=0, highlightthickness=0,
        ).pack(anchor='w', pady=(2, 0))

        # passes_frame kept as alias for update_method_options compatibility
        self.passes_frame = overwrite_row

        self._divider(inner, pady=6)

        # ── Remplissage cryptographique ───────────────────────────────────────
        tk.Label(inner, text='Remplissage cryptographique', bg=self._SURFACE,
                 fg=self._TEXT, font=('Segoe UI', 9, 'bold')).pack(anchor='w', pady=(0, 2))
        self.crypto_fill_frame = tk.Frame(inner, bg=self._SURFACE)
        self.crypto_fill_frame.pack(fill=tk.X)
        for txt, val in [("Aléatoire", "random"), ("Zéros", "zero")]:
            tk.Radiobutton(
                self.crypto_fill_frame, text=txt, value=val,
                variable=self.crypto_fill_var,
                bg=self._SURFACE, fg=self._TEXT,
                selectcolor=self._BG_ELEVATED,
                activebackground=self._SURFACE,
                activeforeground=self._ACCENT2,
                font=('Segoe UI', 9), bd=0, highlightthickness=0,
            ).pack(side=tk.LEFT, padx=(0, 12))

        self._divider(inner, pady=6)

        # ── Système de fichiers + Table de partitions (côte à côte) ──────────
        fs_pt_row = tk.Frame(inner, bg=self._SURFACE)
        fs_pt_row.pack(fill=tk.X, pady=(0, 4))

        fs_block = tk.Frame(fs_pt_row, bg=self._SURFACE)
        fs_block.pack(side=tk.LEFT, fill=tk.Y, expand=True)
        tk.Label(fs_block, text='Système de fichiers', bg=self._SURFACE,
                 fg=self._TEXT, font=('Segoe UI', 9, 'bold')).pack(anchor='w', pady=(0, 2))
        fs_row = tk.Frame(fs_block, bg=self._SURFACE)
        fs_row.pack(anchor='w')
        for txt, val in [("ext4", "ext4"), ("NTFS", "ntfs"), ("FAT32", "vfat")]:
            tk.Radiobutton(
                fs_row, text=txt, value=val,
                variable=self.filesystem_var,
                bg=self._SURFACE, fg=self._TEXT,
                selectcolor=self._BG_ELEVATED,
                activebackground=self._SURFACE,
                activeforeground=self._ACCENT2,
                font=('Segoe UI', 9), bd=0, highlightthickness=0,
            ).pack(side=tk.LEFT, padx=(0, 6))

        tk.Frame(fs_pt_row, bg=self._BORDER_SOFT, width=1).pack(
            side=tk.LEFT, fill=tk.Y, padx=10, pady=2)

        pt_block = tk.Frame(fs_pt_row, bg=self._SURFACE)
        pt_block.pack(side=tk.LEFT, fill=tk.Y)
        tk.Label(pt_block, text='Table partition', bg=self._SURFACE,
                 fg=self._TEXT, font=('Segoe UI', 9, 'bold')).pack(anchor='w', pady=(0, 2))
        pt_row = tk.Frame(pt_block, bg=self._SURFACE)
        pt_row.pack(anchor='w')
        for rb_text, rb_val in [("MBR", "mbr"), ("GPT", "gpt")]:
            tk.Radiobutton(
                pt_row, text=rb_text, value=rb_val,
                variable=self.partition_table_var,
                bg=self._SURFACE, fg=self._TEXT,
                selectcolor=self._BG_ELEVATED,
                activebackground=self._SURFACE,
                activeforeground=self._ACCENT2,
                font=('Segoe UI', 9), bd=0, highlightthickness=0,
            ).pack(side=tk.LEFT, padx=(0, 10))
        tk.Label(pt_block, text='Par défaut:MBR', bg=self._SURFACE,
                 fg=self._TEXT, font=('Segoe UI', 8)).pack(anchor='w', pady=(0, 2))

        self._divider(inner, pady=6)

        # ── Libellé après formatage ───────────────────────────────────────────
        tk.Label(inner, text='Libellé après formatage', bg=self._SURFACE,
                 fg=self._TEXT, font=('Segoe UI', 9, 'bold')).pack(anchor='w', pady=(0, 2))

        label_mode_frame = tk.Frame(inner, bg=self._SURFACE)
        label_mode_frame.pack(fill=tk.X)

        for rb_text, rb_val in [("Aucun libellé", "none"), ("Conserver le libellé actuel", "preserve")]:
            tk.Radiobutton(
                label_mode_frame, text=rb_text, value=rb_val,
                variable=self.label_mode_var,
                command=self._update_label_options,
                bg=self._SURFACE, fg=self._TEXT,
                selectcolor=self._BG_ELEVATED,
                activebackground=self._SURFACE,
                activeforeground=self._ACCENT2,
                font=('Segoe UI', 9), bd=0, highlightthickness=0,
            ).pack(anchor='w', padx=4, pady=1)

        lbl_custom_row = tk.Frame(label_mode_frame, bg=self._SURFACE)
        lbl_custom_row.pack(fill=tk.X, pady=(1, 0))
        tk.Radiobutton(
            lbl_custom_row, text='Nouveau libellé :', value='custom',
            variable=self.label_mode_var,
            command=self._update_label_options,
            bg=self._SURFACE, fg=self._TEXT,
            selectcolor=self._BG_ELEVATED,
            activebackground=self._SURFACE,
            activeforeground=self._ACCENT2,
            font=('Segoe UI', 9), bd=0, highlightthickness=0,
        ).pack(side=tk.LEFT, padx=(4, 0))
        self._custom_label_entry = ttk.Entry(
            lbl_custom_row, textvariable=self.custom_label_var,
            width=14, state='disabled',
        )
        self._custom_label_entry.pack(side=tk.LEFT, padx=(4, 0))

        self._divider(inner, pady=6)

        # ── Boutons d'action ──────────────────────────────────────────────────
        self._action_button(
            inner,
            "▶  DÉMARRER L'EFFACEMENT",
            self.start_erasure,
            bg='#b3342b',
            hover_bg=self._DANGER,
            accent=True,
        ).pack(fill=tk.X, pady=(0, 5))
        self._action_button(
            inner,
            '▶  FORMATER SEULEMENT',
            self.format_only,
            bg=self._ACCENT,
            hover_bg=self._ACCENT2,
            accent=True,
        ).pack(fill=tk.X, pady=(0, 6))

        self._divider(inner, pady=6)

        counter_frame = tk.Frame(inner, bg=self._SURFACE)
        counter_frame.pack(fill=tk.X, pady=(0, 4))
        tk.Label(
            counter_frame, text='Supports blanchis :',
            bg=self._SURFACE, fg=self._TEXT_DIM, font=('Segoe UI', 9),
        ).pack(side=tk.LEFT)
        self._wipe_count_var = tk.StringVar(value='—')
        tk.Label(
            counter_frame, textvariable=self._wipe_count_var,
            bg=self._SURFACE, fg='#2ecc71', font=('Segoe UI', 9, 'bold'),
        ).pack(side=tk.LEFT, padx=(6, 0))

        ttk.Button(
            inner,
            text='◆  Administration',
            command=self._open_admin,
            style='Admin.TButton',
        ).pack(fill=tk.X, pady=3)

        ttk.Button(
            inner,
            text='⟲  Redémarrer',
            command=self._on_reboot_clicked,
            style='Reboot.TButton',
        ).pack(fill=tk.X, pady=3)

        self._update_wipe_counter()

        self.root.protocol('WM_DELETE_WINDOW', self._block_close)
        self.update_method_options()
        self._set_status('Prêt', 'idle')

    def _auto_refresh_disks(self) -> None:
        if not self._erasing_devs:
            self.refresh_disks()
        self.root.after(self._REFRESH_INTERVAL_MS, self._auto_refresh_disks)

    @staticmethod
    def _build_disk_label(disk: dict, active_physical_drives: set) -> tuple:
        device_name = disk['device'].replace('/dev/', '')

        try:
            disk_identifier = get_disk_serial(device_name)
        except Exception:
            disk_identifier = device_name

        try:
            ssd_indicator = ' (SSD)' if is_ssd(device_name) else ' (HDD)'
        except Exception:
            ssd_indicator = ' (Type inconnu)'

        try:
            is_active = get_base_disk(device_name) in active_physical_drives
        except Exception:
            is_active = False

        active_indicator = ' (DISQUE SYSTÈME ACTIF)' if is_active else ''
        disk_label_str = disk.get('label', 'Inconnu')
        label_indicator = (
            f" [Libellé : {disk_label_str}]"
            if disk_label_str and disk_label_str != 'No Label'
            else ' [Sans libellé]'
        )

        id_text = f"{disk_identifier}{ssd_indicator}{active_indicator}{label_indicator}"
        fs_str = disk.get('filesystem', '—')
        partition_table_str = disk.get('partition_table', 'Inconnue')
        details_text = (
            f"Taille : {disk['size']} • "
            f"FS : {fs_str} • "
            f"Table : {partition_table_str} • "
            f"Modèle : {disk['model']}"
        )
        text_color = 'red' if is_active else ('blue' if '(SSD)' in ssd_indicator else 'black')
        return id_text, details_text, text_color, is_active

    def _create_disk_row(self, disk: dict, active_physical_drives: set) -> None:
        dev = disk['device']
        id_text, details_text, text_color, _ = self._build_disk_label(disk, active_physical_drives)
        is_erasing = dev in self._erasing_devs
        display_color = self._map_disk_color(text_color, is_erasing)
        is_ssd_disk = '(SSD)' in id_text
        is_active_disk = text_color == 'red'

        var = tk.BooleanVar(value=is_erasing)
        self.disk_vars[dev] = var

        card_bg = self._SURFACE if not is_erasing else self._SURFACE2
        outer = tk.Frame(self.scrollable_disk_frame, bg=self._BORDER_SOFT, padx=1, pady=1)
        outer.pack(fill=tk.X, padx=8, pady=5)

        disk_entry_frame = tk.Frame(outer, bg=card_bg, padx=12, pady=10)
        disk_entry_frame.pack(fill=tk.BOTH, expand=True)

        top = tk.Frame(disk_entry_frame, bg=card_bg)
        top.pack(fill=tk.X)

        marker_color = self._DANGER if is_active_disk else self._WARNING if is_erasing else self._SSD_COLOR if is_ssd_disk else self._HDD_COLOR
        tk.Frame(top, bg=marker_color, width=4, height=34).pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))

        cb = tk.Checkbutton(
            top,
            variable=var,
            bg=card_bg,
            fg=display_color,
            activebackground=card_bg,
            selectcolor=self._BG_ELEVATED,
            bd=0,
            highlightthickness=0,
        )
        if is_erasing:
            cb.configure(state='disabled')
        cb.pack(side=tk.LEFT, pady=(1, 0))

        text_col = tk.Frame(top, bg=card_bg)
        text_col.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 8))

        id_label = tk.Label(text_col, text=id_text, fg=self._TEXT, bg=card_bg,
                            wraplength=520, justify=tk.LEFT,
                            font=('Segoe UI', 10, 'bold'))
        id_label.pack(anchor='w')

        details_label = tk.Label(text_col, text=details_text, fg=self._TEXT_DIM, bg=card_bg,
                                 wraplength=520, justify=tk.LEFT,
                                 font=('Segoe UI', 9))
        details_label.pack(anchor='w', pady=(4, 0))

        if is_erasing:
            badge_text, badge_bg, badge_fg = '⚙ EN COURS', self._WARNING, '#1a1a1a'
        elif is_active_disk:
            badge_text, badge_bg, badge_fg = '⚠ SYSTÈME', self._DANGER, '#ffffff'
        elif is_ssd_disk:
            badge_text, badge_bg, badge_fg = '◈ SSD', self._SSD_COLOR, '#08131d'
        else:
            badge_text, badge_bg, badge_fg = '◉ HDD', self._HDD_COLOR, '#08131d'

        badge = tk.Label(top, text=badge_text, bg=badge_bg, fg=badge_fg,
                         font=('Segoe UI', 8, 'bold'), padx=8, pady=4)
        badge.pack(side=tk.RIGHT, anchor='n')

        sep = tk.Frame(self.scrollable_disk_frame, bg=self._BG_ELEVATED, height=1)
        sep.pack(fill=tk.X, padx=8)

        self._disk_rows[dev] = {
            'frame': disk_entry_frame,
            'outer': outer,
            'sep': sep,
            'cb': cb,
            'var': var,
            'id_label': id_label,
            'details_label': details_label,
            'badge': badge,
            'top': top,
        }
        self._disk_row_cache[dev] = {
            'id_text': id_text,
            'details_text': details_text,
            'text_color': text_color,
        }

    def _update_disk_row(self, dev: str, disk: dict, active_physical_drives: set) -> None:
        id_text, details_text, text_color, _ = self._build_disk_label(disk, active_physical_drives)
        is_erasing = dev in self._erasing_devs
        display_color = self._map_disk_color(text_color, is_erasing)
        is_ssd_disk = '(SSD)' in id_text
        is_active_disk = text_color == 'red'
        row = self._disk_rows[dev]
        cache = self._disk_row_cache.get(dev, {})

        card_bg = self._SURFACE if not is_erasing else self._SURFACE2
        row['frame'].configure(bg=card_bg)
        row['top'].configure(bg=card_bg)
        row['id_label'].configure(bg=card_bg)
        row['details_label'].configure(bg=card_bg)
        row['cb'].configure(bg=card_bg, activebackground=card_bg, fg=display_color)

        if cache.get('id_text') != id_text or cache.get('text_color') != text_color:
            row['id_label'].configure(text=id_text)
        if cache.get('details_text') != details_text:
            row['details_label'].configure(text=details_text)

        var = row['var']
        cb = row['cb']
        if is_erasing:
            var.set(True)
            cb.configure(state='disabled')
        else:
            cb.configure(state='normal')

        if is_active_disk:
            row['badge'].configure(text='⚠ SYSTÈME', bg=self._DANGER, fg='#ffffff')
        elif is_erasing:
            row['badge'].configure(text='⚙ EN COURS', bg=self._WARNING, fg='#1a1a1a')
        elif is_ssd_disk:
            row['badge'].configure(text='◈ SSD', bg=self._SSD_COLOR, fg='#08131d')
        else:
            row['badge'].configure(text='◉ HDD', bg=self._HDD_COLOR, fg='#08131d')

        self._disk_row_cache[dev] = {
            'id_text': id_text,
            'details_text': details_text,
            'text_color': text_color,
        }
        self.disk_vars[dev] = var

    def _remove_disk_row(self, dev: str) -> None:
        row = self._disk_rows.pop(dev, None)
        if row:
            row['sep'].destroy()
            try:
                row['outer'].destroy()
            except Exception:
                pass
        self._disk_row_cache.pop(dev, None)
        self.disk_vars.pop(dev, None)

    def _show_no_disk_message(self) -> None:
        if self._no_disk_label is None or not self._no_disk_label.winfo_exists():
            self._no_disk_label = tk.Label(
                self.scrollable_disk_frame,
                text="Aucun disque détecté",
                bg=self._BG_ELEVATED,
                fg=self._TEXT_DIM,
                font=("Segoe UI", 10),
            )
            self._no_disk_label.pack(pady=20)

    def _hide_no_disk_message(self) -> None:
        if self._no_disk_label is not None:
            try:
                self._no_disk_label.destroy()
            except Exception:
                pass
            self._no_disk_label = None

    def refresh_disks(self) -> None:
        try:
            new_disks = get_disk_list()
        except (CalledProcessError, SubprocessError, FileNotFoundError, IOError, OSError) as e:
            error_msg = f"Erreur lors de la récupération des disques : {str(e)}"
            self.update_gui_log(error_msg)
            log_error(error_msg)
            new_disks = []

        try:
            active_base_disks = get_active_disk()
        except Exception as e:
            self.update_gui_log(f"Erreur lors de la détection du disque actif : {str(e)}")
            active_base_disks = None

        active_physical_drives = set(active_base_disks) if active_base_disks else set()

        if active_physical_drives and not self.active_drive_logged:
            log_info(f"Active physical devices: {active_physical_drives}")
            self.active_drive_logged = True

        filtered_disks = []
        for disk in new_disks:
            device_name = disk["device"].replace("/dev/", "")
            try:
                base_disk = get_base_disk(device_name)
                if base_disk in active_physical_drives:
                    continue
            except Exception:
                pass
            filtered_disks.append(disk)

        new_disks = filtered_disks
        self.disclaimer_var.set("")

        if new_disks:
            self._hide_no_disk_message()

        new_dev_set = {disk["device"] for disk in new_disks}
        old_dev_set = set(self._disk_rows.keys())

        added = new_dev_set - old_dev_set
        removed = old_dev_set - new_dev_set
        kept = new_dev_set & old_dev_set

        for dev in removed:
            self._remove_disk_row(dev)

        new_disk_map = {disk["device"]: disk for disk in new_disks}

        for dev in kept:
            self._update_disk_row(dev, new_disk_map[dev], active_physical_drives)

        for dev in added:
            self._create_disk_row(new_disk_map[dev], active_physical_drives)

        if not new_disks:
            self._show_no_disk_message()
            self.disclaimer_var.set("")
            self.ssd_disclaimer_var.set("")
            self._disk_count_var.set("0 disque")
            return

        has_ssd = False
        for disk in new_disks:
            try:
                if is_ssd(disk["device"].replace("/dev/", "")):
                    has_ssd = True
                    break
            except Exception:
                pass

        self.ssd_disclaimer_var.set(
            "SSD détecté. L’effacement multi-passes peut user le support et n’est pas le meilleur choix. "
            "Privilégiez l’effacement cryptographique."
            if has_ssd else ""
        )

        self.disks = new_disks
        self._disk_count_var.set(f"{len(new_disks)} disque{'s' if len(new_disks) > 1 else ''}")
        selected_count = sum(1 for var in self.disk_vars.values() if var.get())
        self._progress_stats_var.set(f"{selected_count} sélectionné{'s' if selected_count > 1 else ''}")

    def update_method_options(self) -> None:
        method = self.erase_method_var.get()
        for child in self.crypto_fill_frame.winfo_children():
            try:
                child.configure(state='normal' if method == 'crypto' else 'disabled')
            except tk.TclError:
                pass

    def _update_label_options(self) -> None:
        state = 'normal' if self.label_mode_var.get() == 'custom' else 'disabled'
        try:
            self._custom_label_entry.configure(state=state)
        except tk.TclError:
            pass

    def format_only(self) -> None:
        with self._busy_lock:
            requested_disks = [disk for disk, var in self.disk_vars.items() if var.get()]
            selected_disks = [d for d in requested_disks if d not in self._erasing_devs]
            skipped_disks = [d for d in requested_disks if d in self._erasing_devs]

        if not requested_disks:
            messagebox.showwarning('Avertissement', 'Aucun disque sélectionné.')
            return

        if skipped_disks:
            skipped_str = '\n'.join(d.replace('/dev/', '') for d in skipped_disks)
            
        if not selected_disks:
            return

        disk_identifiers = []
        for disk in selected_disks:
            disk_name = disk.replace('/dev/', '')
            try:
                disk_identifier = get_disk_serial(disk_name)
            except (CalledProcessError, SubprocessError) as e:
                disk_identifier = f"{disk_name} (numéro de série indisponible)"
                self.update_gui_log(f"Erreur lors de la récupération du numéro de série de {disk_name} : {str(e)}")
                log_error(f"Erreur lors de la récupération du numéro de série de {disk_name} : {str(e)}")
            except FileNotFoundError as e:
                disk_identifier = f"{disk_name} (commande introuvable)"
                self.update_gui_log(f"Commande introuvable pour obtenir le numéro de série de {disk_name} : {str(e)}")
                log_error(f"Commande introuvable pour obtenir le numéro de série de {disk_name} : {str(e)}")
            except PermissionError as e:
                disk_identifier = f"{disk_name} (permission refusée)"
                self.update_gui_log(f"Permission refusée pour obtenir le numéro de série de {disk_name} : {str(e)}")
                log_error(f"Permission refusée pour obtenir le numéro de série de {disk_name} : {str(e)}")
            except (IOError, OSError) as e:
                disk_identifier = f"{disk_name} (erreur d’E/S)"
                self.update_gui_log(f"Erreur d’E/S lors de la récupération du numéro de série de {disk_name} : {str(e)}")
                log_error(f"Erreur d’E/S lors de la récupération du numéro de série de {disk_name} : {str(e)}")
            disk_identifiers.append(disk_identifier)

        disk_list = '\n'.join(disk_identifiers)
        fs_choice = self.filesystem_var.get()
        if not messagebox.askyesno(
            'Confirmer le formatage',
            f"Attention : vous êtes sur le point de formater les disques suivants en {fs_choice} :\n\n{disk_list}\n\n"
            "Toutes les données existantes seront perdues.\n\n"
            "Voulez-vous continuer ?",
        ):
            return

        with self._busy_lock:
            self._erasing_devs.update(selected_disks)
        self.refresh_disks()

        self._set_status('Préparation du formatage…', 'busy')
        self._progress_phase_var.set('Formatage')
        self._progress_detail_var.set('Préparation des tâches de formatage')
        self._progress_stats_var.set(f"{len(selected_disks)} disque{'s' if len(selected_disks) > 1 else ''}")
        self.update_progress(0)
        disk_labels = self._resolve_labels(selected_disks)
        try:
            threading.Thread(
                target=self.format_disks_thread,
                args=(selected_disks, fs_choice, disk_labels, self.partition_table_var.get()),
                daemon=True,
            ).start()
        except (RuntimeError, OSError) as e:
            error_msg = f"Erreur lors du démarrage du thread de formatage : {str(e)}"
            messagebox.showerror('Erreur de thread', error_msg)
            self.update_gui_log(error_msg)
            log_error(error_msg)
            with self._busy_lock:
                self._erasing_devs.difference_update(selected_disks)
            self.refresh_disks()
            self._set_status('Prêt', 'idle')

    def format_disks_thread(self, disks, fs_choice, disk_labels=None, partition_table="mbr"):
        start_msg = f"Démarrage du formatage de {len(disks)} disque(s) en {fs_choice}"
        self.update_gui_log(start_msg)
        log_info(start_msg)
        total_disks = len(disks)
        completed_disks = 0
        try:
            with ThreadPoolExecutor() as executor:
                futures = {
                    executor.submit(
                        self.format_single_disk, disk, fs_choice,
                        (disk_labels or {}).get(disk), partition_table,
                    ): disk
                    for disk in disks
                }
                for future in as_completed(futures):
                    disk = futures[future]
                    try:
                        future.result()
                        completed_disks += 1
                        pct = (completed_disks / total_disks) * 100
                        self.update_progress(pct)
                        self._progress_detail_var.set(f"Formatage terminé pour {disk.replace('/dev/', '')}")
                        self._progress_stats_var.set(f"{completed_disks}/{total_disks} terminé{'s' if completed_disks > 1 else ''}")
                        self._set_status(f"Formatage : {completed_disks}/{total_disks} terminé", 'busy')
                    except Exception as e:
                        error_msg = f"Erreur lors du formatage du disque {disk} : {str(e)}"
                        self.update_gui_log(error_msg)
                        log_error(error_msg)
                    finally:
                        with self._busy_lock:
                            self._erasing_devs.discard(disk)
                        self.refresh_disks()
        except Exception as e:
            error_msg = f"Erreur du pool de threads pendant le formatage : {str(e)}"
            self.update_gui_log(error_msg)
            log_error(error_msg)
        finally:
            # Filet de sécurité : ne libère que les disques de CE lot, jamais
            # ceux d'un autre lot d'effacement/formatage lancé en parallèle.
            with self._busy_lock:
                self._erasing_devs.difference_update(disks)
            self.refresh_disks()
        log_info('Format process completed')
        # ── FIX : délégation au thread principal pour garantir l'ordre log → popup ──
        self.root.after(0, self._on_format_complete)

    def _on_format_complete(self) -> None:
        """Appelé sur le thread principal à la fin du formatage."""
        self._set_status('Formatage terminé', 'idle')
        self._progress_phase_var.set('Terminé')
        self._progress_detail_var.set('Opération de formatage terminée')
        self.update_gui_log("Operation completed")
        try:
            messagebox.showinfo('Terminé', "L'opération de formatage est terminée.")
        except tk.TclError as e:
            self.update_gui_log(f"Erreur lors de l’affichage de la boîte de dialogue de fin : {str(e)}")

    def format_single_disk(self, disk, fs_choice, label=None, partition_table="mbr"):
        disk_name = disk.replace('/dev/', '')
        try:
            disk_id = get_disk_serial(disk_name)
            self._set_status(f"Formatage de {disk_id}…", 'busy')
            self._progress_detail_var.set(f"Formatage de {disk_id}")
            log_info(f"Formatting {disk_id} as {fs_choice}")
        except Exception as e:
            self.update_gui_log(f"Erreur lors de la récupération du numéro de série : {str(e)}")
            self._set_status(f"Formatage de {disk_name}…", 'busy')
            self._progress_detail_var.set(f"Formatage de {disk_name}")
            log_info(f"Formatting {disk_name} as {fs_choice}")
        try:
            partition_disk(disk_name, partition_table=partition_table)
            self.update_gui_log(f"Partitionnement de {disk_name} effectué ({partition_table.upper()})")
            format_disk(disk_name, fs_choice, label=label)
            self.update_gui_log(f"{disk_name} formaté avec succès en {fs_choice}")
            log_info(f"Successfully formatted {disk_name} as {fs_choice}")
        except (CalledProcessError, FileNotFoundError, PermissionError, IOError, OSError,
                MemoryError, ValueError, TypeError, RuntimeError) as e:
            error_msg = f"Erreur lors du formatage de {disk_name} : {str(e)}"
            self.update_gui_log(error_msg)
            log_error(error_msg)
            raise

    def _get_external_disks(self) -> list:
        import json as _json
        import subprocess as _sp

        active_disks = set(self.active_disk or [])
        result = []
        try:
            raw = _sp.run(['lsblk', '-J', '-o', 'NAME,SIZE,TYPE,MODEL,MOUNTPOINT'],
                          stdout=_sp.PIPE, stderr=_sp.PIPE).stdout.decode()
            data = _json.loads(raw)
        except Exception as e:
            log_error(f"lsblk JSON failed: {e}")
            return result

        for dev in data.get('blockdevices', []):
            dev_name = dev.get('name', '')
            if dev.get('type') != 'disk':
                continue
            if dev_name in active_disks or dev_name.startswith('loop'):
                continue
            partitions, mount_map = [], {}
            children = dev.get('children') or []
            if children:
                for child in children:
                    if child.get('type') == 'part':
                        p = child['name']
                        partitions.append(p)
                        mount_map[p] = child.get('mountpoint') or None
            else:
                partitions.append(dev_name)
                mount_map[dev_name] = dev.get('mountpoint') or None
            result.append({
                'device': dev_name,
                'path': f"/dev/{dev_name}",
                'size': dev.get('size', '?'),
                'model': (dev.get('model') or '').strip(),
                'partitions': partitions,
                'mount_points': mount_map,
            })
        return result

    def _mount_partition(self, partition: str) -> 'str | None':
        import subprocess as _sp
        import tempfile as _tf

        mount_dir = _tf.mkdtemp(prefix='disk_eraser_export_')
        try:
            r = _sp.run(['mount', f"/dev/{partition}", mount_dir], stdout=_sp.PIPE, stderr=_sp.PIPE)
            if r.returncode != 0:
                log_error(f"mount /dev/{partition} -> {mount_dir} failed: {r.stderr.decode().strip()}")
                try:
                    os.rmdir(mount_dir)
                except OSError:
                    pass
                return None
            log_info(f"Mounted /dev/{partition} at {mount_dir}")
            return mount_dir
        except FileNotFoundError:
            log_error('mount command not found')
            return None
        except Exception as e:
            log_error(f"Unexpected error mounting /dev/{partition}: {e}")
            return None

    def _unmount_partition(self, mount_dir: str) -> None:
        import subprocess as _sp

        try:
            r = _sp.run(['umount', mount_dir], stdout=_sp.PIPE, stderr=_sp.PIPE)
            if r.returncode != 0:
                log_error(f"umount {mount_dir} failed: {r.stderr.decode().strip()}")
            else:
                log_info(f"Unmounted {mount_dir}")
        except Exception as e:
            log_error(f"Error during umount {mount_dir}: {e}")
        finally:
            try:
                os.rmdir(mount_dir)
            except OSError:
                pass

    def _show_disk_picker(self, external_disks: list):
        import tkinter as _tk

        result = {'partition': None, 'already_mounted': False, 'mount_point': None}
        dlg = _tk.Toplevel(self.root)
        dlg.title('Sélectionner le support externe')
        dlg.configure(bg=self._BG)
        dlg.grab_set()
        dlg.resizable(False, False)

        hdr = _tk.Frame(dlg, bg=self._SURFACE, pady=12, padx=16)
        hdr.pack(fill=_tk.X)
        _tk.Frame(hdr, bg=self._ACCENT2, width=4).pack(side=_tk.LEFT, fill=_tk.Y)
        title_dlg = _tk.Frame(hdr, bg=self._SURFACE, padx=8)
        title_dlg.pack(side=_tk.LEFT, fill=_tk.Y)
        _tk.Label(title_dlg, text='Support externe', bg=self._SURFACE, fg=self._TEXT,
                  font=('Segoe UI', 12, 'bold')).pack(anchor='w')
        _tk.Label(hdr, text="Choisissez la partition cible pour l’export PDF.",
                  bg=self._SURFACE, fg=self._TEXT_DIM, font=('Segoe UI', 9)).pack(anchor='w', pady=(3, 0))
        _tk.Frame(dlg, bg=self._BORDER, height=1).pack(fill=_tk.X)

        list_frame = _tk.Frame(dlg, bg=self._BG, padx=14, pady=12)
        list_frame.pack(fill=_tk.BOTH, expand=True)

        lb = _tk.Listbox(
            list_frame,
            width=68,
            height=12,
            font=('Courier New', 9),
            selectmode=_tk.SINGLE,
            activestyle='dotbox',
            bg=self._SURFACE,
            fg=self._TEXT,
            selectbackground=self._ACCENT,
            selectforeground='white',
            highlightthickness=1,
            highlightcolor=self._BORDER,
            highlightbackground=self._BORDER,
            bd=0,
            relief=_tk.FLAT,
        )
        sb = _tk.Scrollbar(list_frame, orient=_tk.VERTICAL, command=lb.yview,
                           bg=self._SURFACE2, troughcolor=self._BG,
                           activebackground=self._BORDER)
        lb.configure(yscrollcommand=sb.set)
        lb.pack(side=_tk.LEFT, fill=_tk.BOTH, expand=True)
        sb.pack(side=_tk.RIGHT, fill=_tk.Y)

        entries = []
        for disk in external_disks:
            model_str = f" [{disk['model']}]" if disk['model'] else ''
            lb.insert(_tk.END, f"── {disk['path']} {disk['size']}{model_str}")
            lb.itemconfig(_tk.END, foreground=self._SSD_COLOR, background=self._SURFACE2)
            entries.append(None)
            for part in disk['partitions']:
                mp = disk['mount_points'].get(part)
                lb.insert(_tk.END, f"   /dev/{part:<14} {'monté sur ' + mp if mp else 'non monté'}")
                entries.append((part, mp is not None, mp))

        _tk.Frame(dlg, bg=self._BORDER, height=1).pack(fill=_tk.X)
        btn_frame = _tk.Frame(dlg, bg=self._SURFACE, padx=14, pady=10)
        btn_frame.pack(fill=_tk.X)

        def on_select():
            sel = lb.curselection()
            if not sel:
                messagebox.showwarning('Aucune sélection', 'Veuillez sélectionner une partition.', parent=dlg)
                return
            entry = entries[sel[0]]
            if entry is None:
                messagebox.showwarning(
                    'Sélection invalide',
                    'Veuillez sélectionner une partition,\npas un en-tête de disque.',
                    parent=dlg,
                )
                return
            result['partition'], result['already_mounted'], result['mount_point'] = entry
            dlg.destroy()

        def on_cancel():
            dlg.destroy()

        select_btn = _tk.Button(btn_frame, text='  Sélectionner  ', command=on_select,
                                bg=self._ACCENT, fg='white', activebackground=self._ACCENT2,
                                activeforeground='white', font=('Segoe UI', 10, 'bold'),
                                bd=0, padx=10, pady=7, cursor='hand2', relief=_tk.FLAT)
        select_btn.pack(side=_tk.LEFT, padx=(0, 8))

        cancel_btn = _tk.Button(btn_frame, text='  Annuler  ', command=on_cancel,
                                bg=self._SURFACE2, fg=self._TEXT_DIM,
                                activebackground=self._SURFACE3, activeforeground=self._TEXT,
                                font=('Segoe UI', 10), bd=0, padx=10, pady=6,
                                cursor='hand2', relief=_tk.FLAT)
        cancel_btn.pack(side=_tk.LEFT)

        dlg.update_idletasks()
        w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        dlg.geometry(
            f"+{self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2}"
            f"+{self.root.winfo_rooty() + (self.root.winfo_height() - h) // 2}"
        )
        self.root.wait_window(dlg)
        return result['partition'], result['already_mounted'], result['mount_point']

    def _request_external_export_path(self, default_filename: str):
        external_disks = self._get_external_disks()
        if not external_disks:
            messagebox.showerror(
                'Aucun support externe détecté',
                "Aucun disque externe n’a été détecté.\n\nBranchez une clé USB, un disque dur externe ou tout autre support amovible, puis réessayez.",
            )
            return None

        partition, already_mounted, existing_mp = self._show_disk_picker(external_disks)
        if not partition:
            return None

        self._pending_unmount_dir = None
        if already_mounted and existing_mp:
            mount_point = existing_mp
        else:
            self._set_status(f"Montage de /dev/{partition}…", 'busy')
            self.root.update_idletasks()
            mount_point = self._mount_partition(partition)
            if not mount_point:
                messagebox.showerror(
                    'Erreur de montage',
                    f"Impossible de monter /dev/{partition}.\n\nVérifiez que le support est correctement branché et que le système de fichiers est pris en charge (ext4, NTFS, FAT32…).",
                )
                self._set_status('Prêt', 'idle')
                return None
            self._pending_unmount_dir = mount_point

        chosen_path = filedialog.asksaveasfilename(
            title='Exporter le PDF — support externe',
            initialdir=mount_point,
            initialfile=default_filename,
            defaultextension='.pdf',
            filetypes=[('Fichiers PDF', '*.pdf'), ('Tous les fichiers', '*.*')],
        )
        if not chosen_path:
            if self._pending_unmount_dir:
                self._set_status(f"Démontage de /dev/{partition}…", 'busy')
                self.root.update_idletasks()
                self._unmount_partition(self._pending_unmount_dir)
                self._pending_unmount_dir = None
            self._set_status('Prêt', 'idle')
            return None

        mp_norm = mount_point.rstrip('/') + '/'
        path_norm = os.path.abspath(chosen_path).rstrip('/') + '/'
        if not path_norm.startswith(mp_norm):
            messagebox.showwarning(
                'Destination invalide',
                f"Le chemin choisi n’est pas sur le support externe monté.\nVeuillez choisir un emplacement sous : {mount_point}",
            )
            if self._pending_unmount_dir:
                self._unmount_partition(self._pending_unmount_dir)
                self._pending_unmount_dir = None
            return None
        return chosen_path

    def _finalize_export(self) -> None:
        if getattr(self, '_pending_unmount_dir', None):
            self._set_status('Démontage du support externe…', 'busy')
            self.root.update_idletasks()
            self._unmount_partition(self._pending_unmount_dir)
            self._pending_unmount_dir = None
            self._set_status('Support externe démonté', 'info')
            self.update_gui_log('Support externe démonté avec succès.')

    def _update_wipe_counter(self) -> None:
        try:
            self._wipe_count_var.set(str(get_wipe_count()))
        except Exception:
            self._wipe_count_var.set('—')

    def _open_admin(self) -> None:
        open_admin_panel(self.root)
        self._update_wipe_counter()
        try:
            self.passes_var.set(str(get_passes()))
        except Exception:
            pass

    def _on_reboot_clicked(self) -> None:
        if self._erasing_devs:
            messagebox.showwarning(
                'Effacement en cours',
                'Impossible de redémarrer pendant un effacement de disque. '
                'Attendez la fin de l’opération, puis réessayez.',
                parent=self.root,
            )
            return
        if not messagebox.askyesno('Redémarrer', 'Redémarrer la machine maintenant ?', parent=self.root):
            return
        import subprocess as _sp
        log_info('Redémarrage système demandé via le bouton Redémarrer.')
        _sp.run(['systemctl', 'reboot'], check=False)

    def _block_close(self) -> None:
        messagebox.showinfo(
            'Accès restreint',
            'Pour quitter l’application, utilisez le bouton Administration.',
            parent=self.root,
        )

    def toggle_fullscreen(self) -> None:
        try:
            self.root.attributes('-fullscreen', not self.root.attributes('-fullscreen'))
        except tk.TclError as e:
            self.update_gui_log(f"Erreur lors du basculement en plein écran : {str(e)}")
            log_error(f"Erreur lors du basculement en plein écran : {str(e)}")

    def _resolve_labels(self, selected_disks: List[str]) -> Dict[str, str]:
        from utils import get_disk_label as _get_label
        mode = self.label_mode_var.get()
        disk_labels: Dict[str, str] = {}
        if mode == "preserve":
            for disk in selected_disks:
                disk_name = disk.replace('/dev/', '')
                try:
                    lbl = _get_label(disk_name)
                    lbl = lbl if lbl not in ("No Label", "Unknown", "") else None
                    disk_labels[disk] = lbl
                    if lbl:
                        self.update_gui_log(f"Libellé conservé pour {disk_name} : '{lbl}'")
                except Exception:
                    disk_labels[disk] = None
        elif mode == "custom":
            custom = self.custom_label_var.get().strip()
            for disk in selected_disks:
                disk_labels[disk] = custom if custom else None
        else:
            for disk in selected_disks:
                disk_labels[disk] = None
        return disk_labels

    def start_erasure(self) -> None:
        with self._busy_lock:
            requested_disks = [disk for disk, var in self.disk_vars.items() if var.get()]
            selected_disks = [d for d in requested_disks if d not in self._erasing_devs]
            skipped_disks = [d for d in requested_disks if d in self._erasing_devs]

        if not requested_disks:
            messagebox.showwarning('Avertissement', 'Aucun disque sélectionné.')
            return

        if skipped_disks:
            skipped_str = '\n'.join(d.replace('/dev/', '') for d in skipped_disks)
            messagebox.showwarning(
                'Disque(s) déjà occupé(s)',
                f"Les disques suivants sont déjà en cours de traitement et ont été ignorés :\n\n{skipped_str}",
            )

        if not selected_disks:
            return

        active_disk_selected = any(
            self.active_disk and any(ad in d.replace('/dev/', '') for ad in self.active_disk)
            for d in selected_disks
        )
        if active_disk_selected:
            if not messagebox.askyesno(
                'Danger — disque système sélectionné',
                'Attention : vous avez sélectionné le disque système actif.\n\n'
                'L’effacement de ce disque peut faire tomber le système et provoquer une perte définitive des données.\n\n'
                'Voulez-vous vraiment continuer ?',
                icon='warning',
            ):
                return

        erase_method = self.erase_method_var.get()

        ssd_selected = False
        if erase_method == 'overwrite':
            for disk in selected_disks:
                try:
                    if is_ssd(disk.replace('/dev/', '')):
                        ssd_selected = True
                        break
                except Exception:
                    pass
        if ssd_selected:
            if not messagebox.askyesno(
                'Avertissement — SSD sélectionné',
                'Attention : vous avez sélectionné un ou plusieurs SSD.\n\n'
                'Un effacement multi-passes sur SSD peut :\n'
                '• user prématurément le support\n'
                '• ne pas garantir un effacement sûr à cause du wear leveling\n'
                '• ne pas couvrir tous les blocs à cause de l’over-provisioning\n\n'
                'Pour un SSD, il est préférable d’utiliser l’effacement cryptographique.\n\n'
                'Voulez-vous continuer malgré tout ?',
                icon='warning',
            ):
                return

        disk_identifiers = []
        for disk in selected_disks:
            disk_name = disk.replace('/dev/', '')
            try:
                disk_identifier = get_disk_serial(disk_name)
            except Exception:
                disk_identifier = disk_name
            disk_identifiers.append(disk_identifier)
            fs_choice = self.filesystem_var.get()
            if erase_method == 'crypto':
                method_description = f"effacement cryptographique avec remplissage {self.crypto_fill_var.get()}"
            else:
                method_description = f"écrasement standard en {self.passes_var.get()} passes"
            try:
                log_erase_operation(disk_identifier, fs_choice, method_description)
            except Exception as e:
                self.update_gui_log(f"Erreur lors de la journalisation de l’opération pour {disk_identifier} : {str(e)}")
                log_error(f"Erreur lors de la journalisation de l’opération pour {disk_identifier} : {str(e)}")

        disk_list = '\n'.join(disk_identifiers)
        method_info = (
            f"avec effacement cryptographique et remplissage {self.crypto_fill_var.get()}"
            if erase_method == 'crypto'
            else f"avec écrasement en {self.passes_var.get()} passe(s)"
        )
        if not messagebox.askyesno(
            'Confirmer l’effacement',
            f"Attention : vous êtes sur le point d’effacer de manière sécurisée les disques suivants {method_info} :\n\n{disk_list}\n\n"
            'Cette opération est irréversible et toutes les données seront perdues.\n\n'
            'Voulez-vous continuer ?',
        ):
            return

        passes = 1
        if erase_method == 'overwrite':
            try:
                passes = int(self.passes_var.get())
                if passes < 1:
                    messagebox.showerror('Erreur', 'Le nombre de passes doit être supérieur ou égal à 1.')
                    return
            except (ValueError, OverflowError):
                messagebox.showerror('Erreur', 'Le nombre de passes doit être un entier valide.')
                return

        disk_labels = self._resolve_labels(selected_disks)
        with self._busy_lock:
            self._erasing_devs.update(selected_disks)
        self.disk_progress = {disk: 0.0 for disk in selected_disks}
        self._progress_phase_var.set('Effacement en cours')
        self._progress_detail_var.set('Initialisation des tâches')
        self._progress_stats_var.set(f"0/{len(selected_disks)} terminé")
        self.update_progress(0)
        self._set_status('Effacement en cours…', 'busy')
        self.refresh_disks()
        try:
            threading.Thread(
                target=self.progress_state,
                args=(selected_disks, self.filesystem_var.get(), passes, erase_method,
                      disk_labels, self.partition_table_var.get()),
                daemon=True,
            ).start()
        except (RuntimeError, OSError) as e:
            error_msg = f"Erreur lors du démarrage du thread d’effacement : {str(e)}"
            messagebox.showerror('Erreur de thread', error_msg)
            self.update_gui_log(error_msg)
            log_error(error_msg)
            with self._busy_lock:
                self._erasing_devs.difference_update(selected_disks)
            for disk in selected_disks:
                self.disk_progress.pop(disk, None)
            self.refresh_disks()
            self._set_status('Prêt', 'idle')

    def progress_state(self, disks: List[str], fs_choice: str, passes: int,
                       erase_method: str, disk_labels: Dict[str, str] = None,
                       partition_table: str = "mbr") -> None:
        if erase_method == 'crypto':
            fill_method = self.crypto_fill_var.get()
            method_str = f"effacement cryptographique avec remplissage {fill_method}"
        else:
            method_str = f"écrasement standard en {passes} passe(s)"

        start_msg = f"Démarrage de l’effacement sécurisé de {len(disks)} disque(s) avec {method_str}"
        self.update_gui_log(start_msg)
        log_info(start_msg)
        self.update_gui_log(f"Système de fichiers sélectionné : {fs_choice}")
        log_info(f"Selected filesystem: {fs_choice}")

        total_disks = len(disks)
        completed_disks = 0

        try:
            with ThreadPoolExecutor() as executor:
                futures = {
                    executor.submit(
                        self.process_disk_wrapper, disk, fs_choice, passes, erase_method,
                        (disk_labels or {}).get(disk), partition_table,
                    ): disk
                    for disk in disks
                }
                for future in as_completed(futures):
                    disk = futures[future]
                    try:
                        future.result()
                        completed_disks += 1
                        self.disk_progress[disk] = 100.0
                        with self._busy_lock:
                            self._erasing_devs.discard(disk)
                        self._recompute_global_progress()
                        self._progress_stats_var.set(f"{completed_disks}/{total_disks} terminé{'s' if completed_disks > 1 else ''}")
                        self._progress_detail_var.set(f"Terminé : {disk.replace('/dev/', '')}")
                        self._set_status(f"Effacement : {completed_disks}/{total_disks} terminé", 'busy')
                        self.refresh_disks()
                    except Exception as e:
                        with self._busy_lock:
                            self._erasing_devs.discard(disk)
                        error_msg = f"Erreur lors du traitement du disque {disk} : {str(e)}"
                        self.update_gui_log(error_msg)
                        log_error(error_msg)
                        self.refresh_disks()
        except Exception as e:
            error_msg = f"Erreur du pool de threads pendant l’effacement : {str(e)}"
            self.update_gui_log(error_msg)
            log_error(error_msg)
        finally:
            # Filet de sécurité : ne retire que les disques de CE lot, jamais
            # ceux d'un autre lot d'effacement/formatage lancé en parallèle.
            with self._busy_lock:
                self._erasing_devs.difference_update(disks)
            for disk in disks:
                self.disk_progress.pop(disk, None)
            self.refresh_disks()

        log_info('Erasure process completed')
        # ── FIX : délégation au thread principal pour garantir l'ordre log → popup ──
        self.root.after(0, self._on_erase_complete)

    def _on_erase_complete(self) -> None:
        """Appelé sur le thread principal à la fin de l'effacement."""
        self.update_progress(100)
        self._progress_phase_var.set('Terminé')
        self._progress_detail_var.set("L'opération d'effacement est terminée")
        self._set_status('Effacement terminé', 'idle')
        self.update_gui_log("Operation completed")
        try:
            messagebox.showinfo('Terminé', "L'opération d'effacement est terminée.")
        except tk.TclError as e:
            self.update_gui_log(f"Erreur lors de l’affichage de la boîte de dialogue de fin : {str(e)}")

    def process_disk_wrapper(self, disk: str, fs_choice: str, passes: int,
                             erase_method: str, label: str = None,
                             partition_table: str = "mbr") -> None:
        disk_name = disk.replace('/dev/', '')
        try:
            disk_id = get_disk_serial(disk_name)
            self._set_status(f"Effacement de {disk_id}…", 'busy')
            self._progress_detail_var.set(f"Effacement de {disk_id}")
        except Exception as e:
            self.update_gui_log(f"Erreur lors de la récupération du numéro de série : {str(e)}")
            self._set_status(f"Effacement de {disk_name}…", 'busy')
            self._progress_detail_var.set(f"Effacement de {disk_name}")

        try:
            use_crypto = erase_method == 'crypto'
            crypto_fill = self.crypto_fill_var.get() if use_crypto else 'random'
            process_disk(
                disk_name, fs_choice, passes, use_crypto, crypto_fill,
                log_func=self.update_gui_log,
                label=label,
                progress_callback=lambda value, d=disk: self.update_individual_progress(d, value),
                partition_table=partition_table,
            )
        except Exception as e:
            self.update_gui_log(f"Erreur lors du traitement de {disk_name} : {str(e)}")
            raise

    def update_individual_progress(self, disk: str, value: float) -> None:
        try:
            numeric = max(0.0, min(100.0, float(value)))
        except (ValueError, TypeError):
            return
        self.disk_progress[disk] = numeric
        self._progress_detail_var.set(f"{disk.replace('/dev/', '')} — {int(numeric)} %")
        self._recompute_global_progress()

    def _recompute_global_progress(self) -> None:
        if not self.disk_progress:
            self.update_progress(0)
            return
        avg = sum(self.disk_progress.values()) / len(self.disk_progress)
        self.update_progress(avg)

    def update_progress(self, value: float) -> None:
        try:
            numeric = max(0.0, min(100.0, float(value)))

            if hasattr(self, '_progress_stats_var') and 'terminé' not in self._progress_stats_var.get().lower():
                self._progress_stats_var.set(f"Progression interne : {int(numeric)} %")
            self.root.update_idletasks()
        except (tk.TclError, ValueError, TypeError) as e:
            self.update_gui_log(f"Erreur lors de la mise à jour de l'état de progression : {str(e)}")
            log_error(f"Erreur lors de la mise à jour de l'état de progression : {str(e)}")

    def update_gui_log(self, message: str) -> None:
        """
        Insertion thread-safe dans le journal GUI.
        Peut être appelé depuis n'importe quel thread : la mise à jour est
        toujours exécutée par la boucle principale Tkinter via root.after(0, …).
        """
        def _insert() -> None:
            try:
                timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
                self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
                self.log_text.see(tk.END)
            except (tk.TclError, ValueError, TypeError, OSError) as e:
                try:
                    log_error(f"Erreur lors de la mise à jour du journal GUI : {str(e)}")
                except (IOError, OSError):
                    pass

        self.root.after(0, _insert)
def run_gui_mode() -> None:
    try:
        root = tk.Tk()
        DiskEraserGUI(root)
        root.mainloop()
    except tk.TclError as e:
        print(f"Erreur d'initialisation de l'interface graphique : {str(e)}")
        log_error(f"Erreur d'initialisation de l'interface graphique : {str(e)}")
        sys.exit(1)
    except (ImportError, ModuleNotFoundError) as e:
        print(f"Bibliothèque GUI requise indisponible : {str(e)}")
        log_error(f"Bibliothèque GUI requise indisponible : {str(e)}")
        sys.exit(1)
    except MemoryError:
        print('Mémoire insuffisante pour démarrer l’interface graphique')
        log_error('Mémoire insuffisante pour démarrer l’interface graphique')
        sys.exit(1)
    except OSError as e:
        print(f"Erreur système au démarrage de l'interface graphique : {str(e)}")
        log_error(f"Erreur système au démarrage de l'interface graphique : {str(e)}")
        sys.exit(1)