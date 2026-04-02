"""
CnC SHP Editor — open, edit, and save C&C / OpenRA SHP(TD) sprite files.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os

try:
    from PIL import Image, ImageTk, ImageDraw
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from palettes import PALETTES, PALETTE_NAMES
from shp_reader import read_shp, write_shp_openra, SHPFile

# ── constants ────────────────────────────────────────────────────────────────
REMAP_START = 80
REMAP_END   = 96
ZOOM_LEVELS = [1, 2, 4, 6, 8, 12, 16]
THUMB_SIZE  = 48
CANVAS_BG   = '#1a1a2e'


# ── SHP editor application ───────────────────────────────────────────────────
class SHPEditor:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("CnC SHP Editor")
        root.configure(bg='#0d0d1a')
        root.geometry("1300x820")

        self.shp_file: SHPFile | None = None
        self.filename:  str | None    = None
        self.frames:    list          = []

        self.current_frame_idx = 0
        self.palette_name      = PALETTE_NAMES[0]
        self.palette           = list(PALETTES[self.palette_name])
        self.selected_color    = 1
        self.zoom              = 8
        self.tool              = 'pencil'
        self.modified          = False

        self.sel_start  = None
        self.sel_end    = None
        self.sel_active = False
        self.clipboard  = None
        self._drag_last = None

        self._pal_swatches = []
        self._thumb_images = []
        self._canvas_image = None

        self._build_ui()
        self._bind_keys()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = self.root

        # Menu
        menubar = tk.Menu(root, bg='#1a1a2e', fg='white',
                          activebackground='#3a3a5e', activeforeground='white')
        root.config(menu=menubar)
        file_menu = tk.Menu(menubar, tearoff=0, bg='#1a1a2e', fg='white',
                            activebackground='#3a3a5e')
        file_menu.add_command(label='Open SHP…',  command=self._open_shp, accelerator='Ctrl+O')
        file_menu.add_command(label='Save',        command=self._save_shp, accelerator='Ctrl+S')
        file_menu.add_command(label='Save As…',    command=self._save_as,  accelerator='Ctrl+Shift+S')
        file_menu.add_separator()
        file_menu.add_command(label='Exit',        command=root.quit)
        menubar.add_cascade(label='File', menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=0, bg='#1a1a2e', fg='white',
                            activebackground='#3a3a5e')
        edit_menu.add_command(label='Copy',        command=self._copy_frame,  accelerator='Ctrl+C')
        edit_menu.add_command(label='Paste',       command=self._paste_frame, accelerator='Ctrl+V')
        edit_menu.add_command(label='Clear Frame', command=self._clear_frame)
        menubar.add_cascade(label='Edit', menu=edit_menu)

        # Toolbar
        toolbar = tk.Frame(root, bg='#1a1a2e', pady=4)
        toolbar.pack(side=tk.TOP, fill=tk.X)

        TOOL_BTNS = [
            ('pencil',  'P', 'Pencil (P)'),
            ('erase',   'E', 'Eraser (E)'),
            ('fill',    'F', 'Fill (F)'),
            ('eyedrop', 'I', 'Eyedropper (I)'),
            ('select',  'S', 'Select (S)'),
        ]
        self._tool_buttons = {}
        for tool, label, tip in TOOL_BTNS:
            btn = tk.Button(toolbar, text=label, width=4,
                            bg='#2a2a4e', fg='white', relief=tk.FLAT,
                            font=('Consolas', 10, 'bold'),
                            command=lambda t=tool: self._set_tool(t))
            btn.pack(side=tk.LEFT, padx=2)
            self._tool_buttons[tool] = btn
        self._highlight_tool('pencil')

        tk.Label(toolbar, text='Zoom:', bg='#1a1a2e', fg='#aaa',
                 font=('Consolas', 9)).pack(side=tk.LEFT, padx=(14, 2))
        self.zoom_var = tk.StringVar(value=str(self.zoom))
        zoom_cb = ttk.Combobox(toolbar, textvariable=self.zoom_var,
                               values=[str(z) for z in ZOOM_LEVELS],
                               width=4, state='readonly')
        zoom_cb.pack(side=tk.LEFT)
        zoom_cb.bind('<<ComboboxSelected>>', self._on_zoom_change)

        tk.Label(toolbar, text='Palette:', bg='#1a1a2e', fg='#aaa',
                 font=('Consolas', 9)).pack(side=tk.LEFT, padx=(14, 2))
        self.pal_var = tk.StringVar(value=self.palette_name)
        pal_cb = ttk.Combobox(toolbar, textvariable=self.pal_var,
                              values=PALETTE_NAMES, width=16, state='readonly')
        pal_cb.pack(side=tk.LEFT)
        pal_cb.bind('<<ComboboxSelected>>', self._on_palette_change)

        self.status_var = tk.StringVar(value='Open an SHP file to begin  (File > Open SHP)')
        tk.Label(toolbar, textvariable=self.status_var,
                 bg='#1a1a2e', fg='#88aaff',
                 font=('Consolas', 9)).pack(side=tk.RIGHT, padx=8)

        # Main area
        main = tk.Frame(root, bg='#0d0d1a')
        main.pack(fill=tk.BOTH, expand=True)

        self._build_palette_panel(main)

        center = tk.Frame(main, bg='#0d0d1a')
        center.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._build_frame_browser(center)  # pack BOTTOM first so canvas fills remainder
        self._build_canvas(center)

    def _build_palette_panel(self, parent):
        outer = tk.Frame(parent, bg='#111122', width=184)
        outer.pack(side=tk.LEFT, fill=tk.Y, padx=(4, 2), pady=4)
        outer.pack_propagate(False)

        tk.Label(outer, text='PALETTE', bg='#111122', fg='#88aaff',
                 font=('Consolas', 9, 'bold')).pack(pady=(6, 1))

        # Selected color box
        ind_frame = tk.Frame(outer, bg='#111122')
        ind_frame.pack(pady=2)
        self.sel_color_box = tk.Frame(ind_frame, bg='black', width=28, height=18,
                                      relief=tk.SUNKEN, bd=1)
        self.sel_color_box.pack(side=tk.LEFT, padx=2)
        self.sel_color_lbl = tk.Label(ind_frame, text='idx:1', bg='#111122', fg='#ccc',
                                      font=('Consolas', 8))
        self.sel_color_lbl.pack(side=tk.LEFT)

        # Scrollable swatch area
        scroll_frame = tk.Frame(outer, bg='#111122')
        scroll_frame.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        vsb = tk.Scrollbar(scroll_frame, orient=tk.VERTICAL)
        pc  = tk.Canvas(scroll_frame, bg='#111122', highlightthickness=0,
                        yscrollcommand=vsb.set)
        vsb.config(command=pc.yview)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        pc.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        pc.bind('<MouseWheel>',
                lambda e: pc.yview_scroll(-1 * (e.delta // 120), 'units'))

        inner = tk.Frame(pc, bg='#111122')
        pc.create_window((0, 0), window=inner, anchor=tk.NW)
        inner.bind('<Configure>',
                   lambda e: pc.configure(scrollregion=pc.bbox('all')))

        self._pal_swatches = []

        def section(title, indices):
            tk.Label(inner, text=title, bg='#111122', fg='#ffcc44',
                     font=('Consolas', 8, 'bold'), anchor='w').pack(
                         fill=tk.X, pady=(5, 1), padx=2)
            row_f = None
            for k, idx in enumerate(indices):
                if k % 8 == 0:
                    row_f = tk.Frame(inner, bg='#111122')
                    row_f.pack(fill=tk.X)
                r, g, b = self.palette[idx]
                sw = tk.Label(row_f, bg=f'#{r:02x}{g:02x}{b:02x}',
                              width=2, height=1, relief=tk.RAISED, bd=1,
                              cursor='hand2')
                sw.pack(side=tk.LEFT, padx=1, pady=1)
                sw.bind('<Button-1>', lambda e, i=idx: self._pick_color(i))
                self._pal_swatches.append((idx, sw))

        section('SPECIAL  (0=transparent, 4=shadow)', [0, 4])
        section('REMAP  ← color-picker affected', list(range(REMAP_START, REMAP_END)))
        remaining = [i for i in range(256)
                     if i not in (0, 4) and not (REMAP_START <= i < REMAP_END)]
        section('ALL COLORS', remaining)

    def _build_canvas(self, parent):
        wrap = tk.Frame(parent, bg='#0d0d1a')
        wrap.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        hscroll = tk.Scrollbar(wrap, orient=tk.HORIZONTAL)
        vscroll = tk.Scrollbar(wrap, orient=tk.VERTICAL)
        self.canvas = tk.Canvas(wrap, bg=CANVAS_BG,
                                xscrollcommand=hscroll.set,
                                yscrollcommand=vscroll.set,
                                cursor='crosshair', highlightthickness=0)
        hscroll.config(command=self.canvas.xview)
        vscroll.config(command=self.canvas.yview)
        vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        hscroll.pack(side=tk.BOTTOM, fill=tk.X)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.canvas.bind('<Button-1>',        self._canvas_click)
        self.canvas.bind('<B1-Motion>',       self._canvas_drag)
        self.canvas.bind('<ButtonRelease-1>', self._canvas_release)
        self.canvas.bind('<Button-3>',        self._canvas_right_click)
        self.canvas.bind('<Motion>',          self._canvas_motion)

    def _build_frame_browser(self, parent):
        bf = tk.Frame(parent, bg='#111122', height=THUMB_SIZE + 30)
        bf.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=(0, 4))
        bf.pack_propagate(False)

        tk.Label(bf, text='FRAMES  (click to select, ← → to navigate)',
                 bg='#111122', fg='#88aaff',
                 font=('Consolas', 8, 'bold')).pack(side=tk.LEFT, padx=6)

        scroll = tk.Scrollbar(bf, orient=tk.HORIZONTAL)
        self.thumb_canvas = tk.Canvas(bf, bg='#0a0a18',
                                      height=THUMB_SIZE + 22,
                                      xscrollcommand=scroll.set,
                                      highlightthickness=0)
        scroll.config(command=self.thumb_canvas.xview)
        scroll.pack(side=tk.BOTTOM, fill=tk.X)
        self.thumb_canvas.pack(fill=tk.BOTH, expand=True)

    # ── palette ───────────────────────────────────────────────────────────────

    def _refresh_palette_swatches(self):
        for idx, sw in self._pal_swatches:
            r, g, b = self.palette[idx]
            sw.configure(bg=f'#{r:02x}{g:02x}{b:02x}')
        self._update_sel_indicator()

    def _update_sel_indicator(self):
        idx = self.selected_color
        r, g, b = self.palette[idx]
        self.sel_color_box.configure(bg=f'#{r:02x}{g:02x}{b:02x}')
        lbl = f'idx:{idx}'
        if idx == 0:
            lbl += ' transp'
        elif idx == 4:
            lbl += ' shadow'
        elif REMAP_START <= idx < REMAP_END:
            lbl += ' remap'
        self.sel_color_lbl.configure(text=lbl)

    def _pick_color(self, idx: int):
        self.selected_color = idx
        self._update_sel_indicator()

    def _highlight_tool(self, tool: str):
        for t, btn in self._tool_buttons.items():
            btn.configure(bg='#4a4a8e' if t == tool else '#2a2a4e')

    def _set_tool(self, tool: str):
        self.tool = tool
        self._highlight_tool(tool)
        if tool != 'select':
            self._clear_selection()

    # ── file I/O ──────────────────────────────────────────────────────────────

    def _open_shp(self):
        path = filedialog.askopenfilename(
            title='Open SHP file',
            filetypes=[('SHP files', '*.shp'), ('All files', '*.*')])
        if not path:
            return
        try:
            shp = read_shp(path)
        except Exception as ex:
            messagebox.showerror('Open error', str(ex))
            return

        self.shp_file          = shp
        self.filename          = path
        self.frames            = [list(f.pixels) for f in shp.frames]
        self.current_frame_idx = 0
        self.modified          = False

        self.palette_name = PALETTE_NAMES[0]
        self.palette      = list(PALETTES[self.palette_name])
        self.pal_var.set(self.palette_name)
        self._refresh_palette_swatches()

        # Pick zoom so sprite fits comfortably
        target = 400
        self.zoom = 1
        for z in ZOOM_LEVELS:
            if shp.width * z <= target and shp.height * z <= target:
                self.zoom = z
        self.zoom_var.set(str(self.zoom))

        self._refresh_thumb_strip()
        self._draw_frame()
        name = os.path.basename(path)
        self.root.title(f'CnC SHP Editor — {name}')
        self.status_var.set(
            f'{name}  |  {shp.num_frames} frame(s)  |  {shp.width}×{shp.height} px')

    def _save_shp(self):
        if not self.filename:
            self._save_as(); return
        self._do_save(self.filename)

    def _save_as(self):
        init = os.path.basename(self.filename) if self.filename else 'untitled.shp'
        path = filedialog.asksaveasfilename(
            title='Save SHP As', defaultextension='.shp',
            filetypes=[('SHP files', '*.shp'), ('All files', '*.*')],
            initialfile=init)
        if path:
            self._do_save(path)
            self.filename = path
            self.root.title(f'CnC SHP Editor — {os.path.basename(path)}')

    def _do_save(self, path: str):
        if not self.shp_file:
            return
        for i, px in enumerate(self.frames):
            self.shp_file.frames[i].pixels = list(px)
        try:
            write_shp_openra(path, self.shp_file)
            self.modified = False
            self.status_var.set(f'Saved: {os.path.basename(path)}')
        except Exception as ex:
            messagebox.showerror('Save error', str(ex))

    # ── canvas ────────────────────────────────────────────────────────────────

    def _draw_frame(self):
        if not self.frames or not self.shp_file:
            self.status_var.set('No frames loaded — open an SHP file.')
            return

        try:
            self._do_draw_frame()
        except Exception as ex:
            messagebox.showerror('Draw error', str(ex))

    def _do_draw_frame(self):
        w  = self.shp_file.width
        h  = self.shp_file.height
        z  = self.zoom
        px = self.frames[self.current_frame_idx]

        if PIL_AVAILABLE:
            img = Image.new('RGB', (w * z, h * z))
            pix = img.load()

            for row in range(h):
                for col in range(w):
                    idx = px[row * w + col]
                    if idx == 0:
                        shade = 55 if (col + row) % 2 == 0 else 35
                        color = (shade, shade, shade)
                    else:
                        color = self.palette[idx]
                    if z == 1:
                        pix[col, row] = color
                    else:
                        for dy in range(z):
                            for dx in range(z):
                                pix[col * z + dx, row * z + dy] = color

            if z >= 4:
                draw = ImageDraw.Draw(img)
                gc = (30, 30, 52)
                for c in range(w + 1):
                    draw.line([(c * z, 0), (c * z, h * z - 1)], fill=gc)
                for r in range(h + 1):
                    draw.line([(0, r * z), (w * z - 1, r * z)], fill=gc)

            if self.sel_active and self.sel_start and self.sel_end:
                draw = ImageDraw.Draw(img)
                x1 = min(self.sel_start[0], self.sel_end[0]) * z
                y1 = min(self.sel_start[1], self.sel_end[1]) * z
                x2 = (max(self.sel_start[0], self.sel_end[0]) + 1) * z - 1
                y2 = (max(self.sel_start[1], self.sel_end[1]) + 1) * z - 1
                draw.rectangle([x1, y1, x2, y2], outline=(255, 220, 0), width=2)

            photo = ImageTk.PhotoImage(img)
            self.canvas._photo = photo   # store on widget to prevent GC
            self._canvas_image = photo   # also keep here
            self.canvas.delete('all')
            self.canvas.create_image(4, 4, anchor=tk.NW, image=photo)
            self.canvas.configure(scrollregion=(0, 0, w * z + 8, h * z + 8))
            self.canvas.xview_moveto(0)
            self.canvas.yview_moveto(0)
            self.canvas.update_idletasks()
        else:
            self.canvas.delete('all')
            for row in range(h):
                for col in range(w):
                    idx = px[row * w + col]
                    if idx == 0:
                        shade = '#373737' if (col + row) % 2 == 0 else '#232323'
                        color = shade
                    else:
                        r, g, b = self.palette[idx]
                        color = f'#{r:02x}{g:02x}{b:02x}'
                    x1 = 4 + col * z
                    y1 = 4 + row * z
                    self.canvas.create_rectangle(x1, y1, x1 + z - 1, y1 + z - 1,
                                                 fill=color, outline='')
            self.canvas.configure(scrollregion=(0, 0, w * z + 8, h * z + 8))
            self.canvas.xview_moveto(0)
            self.canvas.yview_moveto(0)
            self.canvas.update_idletasks()

    def _canvas_to_pixel(self, event):
        if not self.shp_file:
            return None
        cx = self.canvas.canvasx(event.x)
        cy = self.canvas.canvasy(event.y)
        col = int((cx - 4) / self.zoom)
        row = int((cy - 4) / self.zoom)
        if 0 <= col < self.shp_file.width and 0 <= row < self.shp_file.height:
            return col, row
        return None

    def _canvas_click(self, event):
        if not self.frames:
            return
        pos = self._canvas_to_pixel(event)
        if pos is None:
            return
        col, row = pos

        if self.tool == 'select':
            self.sel_start  = pos
            self.sel_end    = pos
            self.sel_active = True
            self._drag_last = pos
            self._draw_frame()
        elif self.tool == 'eyedrop':
            w  = self.shp_file.width
            px = self.frames[self.current_frame_idx]
            self._pick_color(px[row * w + col])
        elif self.tool == 'fill':
            self._flood_fill(pos)
            self._draw_frame()
            self._refresh_thumb(self.current_frame_idx)
        else:
            self._paint_pixel(pos)
            self._draw_frame()
            self._refresh_thumb(self.current_frame_idx)
            self._drag_last = pos

    def _canvas_drag(self, event):
        if not self.frames:
            return
        pos = self._canvas_to_pixel(event)
        if pos is None:
            return

        if self.tool == 'select' and self.sel_start:
            self.sel_end = pos
            self._draw_frame()
        elif self.tool in ('pencil', 'erase'):
            if pos != self._drag_last:
                self._paint_pixel(pos)
                self._draw_frame()
                self._drag_last = pos

    def _canvas_release(self, event):
        if self._drag_last is not None and self.tool in ('pencil', 'erase'):
            self._refresh_thumb(self.current_frame_idx)
        self._drag_last = None

    def _canvas_right_click(self, event):
        pos = self._canvas_to_pixel(event)
        if pos:
            col, row = pos
            px = self.frames[self.current_frame_idx]
            self._pick_color(px[row * self.shp_file.width + col])

    def _canvas_motion(self, event):
        if not self.frames or not self.shp_file:
            return
        pos = self._canvas_to_pixel(event)
        if pos:
            col, row = pos
            px  = self.frames[self.current_frame_idx]
            idx = px[row * self.shp_file.width + col]
            r, g, b = self.palette[idx]
            note = '  remap' if REMAP_START <= idx < REMAP_END else (
                   '  transparent' if idx == 0 else (
                   '  shadow' if idx == 4 else ''))
            self.status_var.set(f'({col},{row})  index={idx}  RGB({r},{g},{b}){note}')

    # ── drawing ───────────────────────────────────────────────────────────────

    def _paint_pixel(self, pos):
        col, row = pos
        color = self.selected_color if self.tool == 'pencil' else 0
        w = self.shp_file.width
        self.frames[self.current_frame_idx][row * w + col] = color
        self.modified = True

    def _flood_fill(self, pos):
        col, row = pos
        w, h = self.shp_file.width, self.shp_file.height
        px     = self.frames[self.current_frame_idx]
        target = px[row * w + col]
        fill   = self.selected_color
        if target == fill:
            return
        stack = [(col, row)]
        seen  = set()
        while stack:
            c, r = stack.pop()
            if (c, r) in seen or c < 0 or c >= w or r < 0 or r >= h:
                continue
            if px[r * w + c] != target:
                continue
            seen.add((c, r))
            px[r * w + c] = fill
            stack += [(c+1, r), (c-1, r), (c, r+1), (c, r-1)]
        self.modified = True

    def _clear_selection(self):
        self.sel_start  = None
        self.sel_end    = None
        self.sel_active = False
        self._draw_frame()

    # ── copy / paste ──────────────────────────────────────────────────────────

    def _copy_frame(self):
        if not self.frames:
            return
        if self.sel_active and self.sel_start and self.sel_end:
            w  = self.shp_file.width
            px = self.frames[self.current_frame_idx]
            c1 = min(self.sel_start[0], self.sel_end[0])
            r1 = min(self.sel_start[1], self.sel_end[1])
            c2 = max(self.sel_start[0], self.sel_end[0])
            r2 = max(self.sel_start[1], self.sel_end[1])
            sel_w = c2 - c1 + 1
            sel_h = r2 - r1 + 1
            region = [px[r * w + c] for r in range(r1, r2+1) for c in range(c1, c2+1)]
            self.clipboard = (region, sel_w, sel_h, c1, r1)
            self.status_var.set(f'Copied selection {sel_w}×{sel_h}')
        else:
            px = list(self.frames[self.current_frame_idx])
            w  = self.shp_file.width
            h  = self.shp_file.height
            self.clipboard = (px, w, h, 0, 0)
            self.status_var.set('Copied full frame')

    def _paste_frame(self):
        if not self.clipboard or not self.frames:
            return
        data, cw, ch, cx, cy = self.clipboard
        w, h = self.shp_file.width, self.shp_file.height
        px   = self.frames[self.current_frame_idx]
        for r in range(ch):
            for c in range(cw):
                dr, dc = cy + r, cx + c
                if 0 <= dr < h and 0 <= dc < w:
                    px[dr * w + dc] = data[r * cw + c]
        self.modified = True
        self._draw_frame()
        self._refresh_thumb(self.current_frame_idx)
        self.status_var.set('Pasted')

    def _clear_frame(self):
        if not self.frames:
            return
        w, h = self.shp_file.width, self.shp_file.height
        self.frames[self.current_frame_idx] = [0] * (w * h)
        self.modified = True
        self._draw_frame()
        self._refresh_thumb(self.current_frame_idx)

    # ── frame browser ─────────────────────────────────────────────────────────

    def _refresh_thumb_strip(self):
        self.thumb_canvas.delete('all')
        self._thumb_images.clear()
        if not self.frames:
            return
        x = 4
        for i, px in enumerate(self.frames):
            self._draw_thumb(i, px, x)
            x += THUMB_SIZE + 10
        self.thumb_canvas.configure(scrollregion=(0, 0, x + 4, THUMB_SIZE + 24))
        self._highlight_current_thumb()

    def _draw_thumb(self, idx, px, x):
        w = self.shp_file.width
        h = self.shp_file.height
        z = max(1, THUMB_SIZE // max(w, h))

        if PIL_AVAILABLE:
            tw = w * z
            th = h * z
            img = Image.new('RGB', (tw, th), (26, 26, 46))
            pix = img.load()
            for row in range(h):
                for col in range(w):
                    ci = px[row * w + col]
                    if ci == 0:
                        shade = 50 if (col + row) % 2 == 0 else 30
                        color = (shade, shade, shade)
                    else:
                        color = self.palette[ci]
                    for dy in range(z):
                        for dx in range(z):
                            pix[col * z + dx, row * z + dy] = color
            img   = img.resize((THUMB_SIZE, THUMB_SIZE), Image.NEAREST)
            photo = ImageTk.PhotoImage(img)
        else:
            photo = tk.PhotoImage(width=THUMB_SIZE, height=THUMB_SIZE)

        tag = f'thumb_{idx}'
        self.thumb_canvas.create_image(x, 4, anchor=tk.NW, image=photo, tags=tag)
        self.thumb_canvas.create_text(x + THUMB_SIZE // 2, THUMB_SIZE + 10,
                                      text=str(idx), fill='#aaa',
                                      font=('Consolas', 7), tags=tag)
        self.thumb_canvas.tag_bind(tag, '<Button-1>',
                                   lambda e, i=idx: self._select_frame(i))
        self._thumb_images.append(photo)

    def _refresh_thumb(self, idx):
        if not self.frames or not self.shp_file:
            return
        tag = f'thumb_{idx}'
        self.thumb_canvas.delete(tag)
        x = 4 + idx * (THUMB_SIZE + 10)
        # re-insert at same position; append new photo ref
        old_len = len(self._thumb_images)
        self._draw_thumb(idx, self.frames[idx], x)
        # fix: swap ref into correct position
        if len(self._thumb_images) > old_len and idx < old_len:
            self._thumb_images[idx] = self._thumb_images.pop()
        self._highlight_current_thumb()

    def _highlight_current_thumb(self):
        self.thumb_canvas.delete('thumb_sel')
        x = 4 + self.current_frame_idx * (THUMB_SIZE + 10)
        self.thumb_canvas.create_rectangle(
            x - 2, 2, x + THUMB_SIZE + 2, THUMB_SIZE + 8,
            outline='#ffcc00', width=2, tags='thumb_sel')

    def _select_frame(self, idx: int):
        self.current_frame_idx = idx
        self._highlight_current_thumb()
        self._draw_frame()
        self.status_var.set(f'Frame {idx}  ({len(self.frames)} total)')

    # ── settings changes ──────────────────────────────────────────────────────

    def _on_palette_change(self, event=None):
        self.palette_name = self.pal_var.get()
        self.palette      = list(PALETTES[self.palette_name])
        self._refresh_palette_swatches()
        if self.frames:
            self._draw_frame()
            self._refresh_thumb_strip()

    def _on_zoom_change(self, event=None):
        try:
            self.zoom = int(self.zoom_var.get())
        except ValueError:
            pass
        if self.frames:
            self._draw_frame()

    # ── keyboard bindings ─────────────────────────────────────────────────────

    def _bind_keys(self):
        self.root.bind('<Control-o>', lambda e: self._open_shp())
        self.root.bind('<Control-s>', lambda e: self._save_shp())
        self.root.bind('<Control-S>', lambda e: self._save_as())
        self.root.bind('<Control-c>', lambda e: self._copy_frame())
        self.root.bind('<Control-v>', lambda e: self._paste_frame())
        self.root.bind('p',           lambda e: self._set_tool('pencil'))
        self.root.bind('e',           lambda e: self._set_tool('erase'))
        self.root.bind('f',           lambda e: self._set_tool('fill'))
        self.root.bind('i',           lambda e: self._set_tool('eyedrop'))
        self.root.bind('s',           lambda e: self._set_tool('select'))
        self.root.bind('<Left>',      lambda e: self._prev_frame())
        self.root.bind('<Right>',     lambda e: self._next_frame())
        self.root.bind('<Escape>',    lambda e: self._clear_selection())

    def _prev_frame(self):
        if self.frames and self.current_frame_idx > 0:
            self._select_frame(self.current_frame_idx - 1)

    def _next_frame(self):
        if self.frames and self.current_frame_idx < len(self.frames) - 1:
            self._select_frame(self.current_frame_idx + 1)


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    root = tk.Tk()
    style = ttk.Style(root)
    try:
        style.theme_use('clam')
    except Exception:
        pass
    style.configure('TCombobox',
                    fieldbackground='#1a1a2e', background='#1a1a2e',
                    foreground='white', selectbackground='#3a3a5e',
                    selectforeground='white')
    SHPEditor(root)
    root.mainloop()


if __name__ == '__main__':
    main()
