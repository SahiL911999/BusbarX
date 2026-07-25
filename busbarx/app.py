#!/usr/bin/env python3
"""
BusbarX Nexus — Milestone 3 STEP-extraction GUI (CustomTkinter).

Add up to 10 STEP files, click Extract; each part is written to its own folder under a
single BusbarX_Output directory (see pipeline.py). Launch with:  python -m busbarx
"""
import os
import threading

import customtkinter as ctk
from tkinter import filedialog
from PIL import Image

from . import pipeline

MAX_FILES = 10
STEP_TYPES = [("STEP files", "*.stp *.step *.STP *.STEP"), ("All files", "*.*")]

# status colors (readable in both light & dark)
C_OK = "#2e9e5b"
C_WORK = "#3b82f6"
C_FAIL = "#e0533d"
C_WARN = "#d99a00"
C_MUTE = "#8a8a8a"


# ── file row widget ─────────────────────────────────────────────────────────────
ROW_BG = ("#e9e9ea", "#333437")
ROW_BORDER = ("#c4c4c6", "#4a4b4f")
ROW_SEL = "#3b82f6"


class FileRow(ctk.CTkFrame):
    def __init__(self, master, path, on_remove, on_select, **kw):
        super().__init__(master, corner_radius=10, fg_color=ROW_BG,
                         border_width=1, border_color=ROW_BORDER, **kw)
        self.path = path
        self.result = None
        self.on_select = on_select
        self.grid_columnconfigure(1, weight=1)

        self.dot = ctk.CTkLabel(self, text="●", width=18, text_color=C_MUTE)
        self.dot.grid(row=0, column=0, rowspan=2, padx=(10, 4), pady=8)

        self.name = ctk.CTkLabel(self, text=os.path.basename(path), anchor="w",
                                 font=ctk.CTkFont(size=13, weight="bold"))
        self.name.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(0, 6), pady=(8, 0))

        self.detail = ctk.CTkLabel(self, text="queued", anchor="w", text_color=C_MUTE,
                                   font=ctk.CTkFont(size=11))
        self.detail.grid(row=1, column=1, sticky="ew", padx=(0, 6), pady=(0, 8))

        self.bar = ctk.CTkProgressBar(self, width=90, height=8)
        self.bar.set(0)
        self.bar.grid(row=1, column=2, sticky="e", padx=6, pady=(0, 8))

        self.remove_btn = ctk.CTkButton(self, text="✕", width=28, fg_color="transparent",
                                        text_color=C_MUTE, hover_color="#5a5a5a",
                                        command=lambda: on_remove(self))
        self.remove_btn.grid(row=0, column=3, rowspan=2, padx=(0, 8))

        for w in (self, self.name, self.detail, self.dot):
            w.bind("<Button-1>", lambda e: self._clicked())

    def _clicked(self):
        if self.result and self.on_select:
            self.on_select(self)

    def set_selected(self, on):
        self.configure(border_color=ROW_SEL if on else ROW_BORDER,
                       border_width=2 if on else 1)

    def set_working(self):
        self.dot.configure(text_color=C_WORK)
        self.detail.configure(text="working…", text_color=C_WORK)
        self.bar.configure(mode="indeterminate"); self.bar.start()

    def set_result(self, res):
        self.result = res
        self.bar.stop(); self.bar.configure(mode="determinate"); self.bar.set(1.0)
        if not res["ok"]:
            self.dot.configure(text_color=C_FAIL)
            self.detail.configure(text=f"failed — {res['error'][:48]}", text_color=C_FAIL)
            return
        out = res["out"]; p = out["part"]
        st = p["flat_pattern_status"]
        col = C_OK if st == "computed" else C_WARN
        self.dot.configure(text_color=col)
        self.detail.configure(
            text=f"{st}  ·  {len(out['features'])} feat · {len(out['bends'])} bend",
            text_color=col)


# ── app ──────────────────────────────────────────────────────────────────────
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.title("BusbarX Nexus — STEP → Flat Pattern")
        self.geometry("1000x680")
        self.minsize(880, 560)

        self.paths = []
        self.rows = []
        self.out_root = ctk.StringVar(value="")
        self.running = False
        self._preview_img = None
        self._sel = None
        self._auto = True               # auto-follow preview until the user clicks a row

        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(2, weight=1)
        self._build_header()
        self._build_toolbar()
        self._build_queue()
        self._build_preview()
        self._build_footer()
        self._refresh_counts()

    # header
    def _build_header(self):
        h = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        h.grid(row=0, column=0, columnspan=2, sticky="ew", padx=18, pady=(14, 4))
        h.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(h, text="BusbarX Nexus", font=ctk.CTkFont(size=22, weight="bold")
                     ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(h, text="STEP → true flat-pattern JSON + visualization",
                     text_color=C_MUTE).grid(row=1, column=0, sticky="w")
        self.mode = ctk.CTkSegmentedButton(h, values=["Dark", "Light"],
                                           command=self._set_mode, width=140)
        self.mode.set("Dark")
        self.mode.grid(row=0, column=1, rowspan=2, sticky="e")

    def _set_mode(self, v):
        ctk.set_appearance_mode(v.lower())

    # toolbar: add zone + output folder
    def _build_toolbar(self):
        t = ctk.CTkFrame(self, corner_radius=12)
        t.grid(row=1, column=0, columnspan=2, sticky="ew", padx=18, pady=8)
        t.grid_columnconfigure(0, weight=1)

        self.drop = ctk.CTkButton(
            t, text="＋   Add STEP files     (click to browse · up to 10)",
            height=58, corner_radius=10, border_width=2, border_color=C_MUTE,
            fg_color="transparent", hover_color="#2a2d2e",
            font=ctk.CTkFont(size=14, weight="bold"), command=self.add_files)
        self.drop.grid(row=0, column=0, columnspan=3, sticky="ew", padx=10, pady=(10, 6))

        self.count_lbl = ctk.CTkLabel(t, text="0 / 10 files", text_color=C_MUTE)
        self.count_lbl.grid(row=1, column=0, sticky="w", padx=12, pady=(0, 10))
        ctk.CTkButton(t, text="Clear", width=70, command=self.clear_files,
                      fg_color="#444", hover_color="#555").grid(row=1, column=1, padx=4, pady=(0, 10))

        of = ctk.CTkFrame(t, fg_color="transparent")
        of.grid(row=2, column=0, columnspan=3, sticky="ew", padx=10, pady=(0, 10))
        of.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(of, text="Output:", text_color=C_MUTE).grid(row=0, column=0, padx=(2, 6))
        self.out_lbl = ctk.CTkLabel(of, textvariable=self.out_root, anchor="w",
                                    text_color=C_MUTE, font=ctk.CTkFont(size=11))
        self.out_lbl.grid(row=0, column=1, sticky="ew")
        ctk.CTkButton(of, text="Change…", width=80, command=self.choose_out,
                      fg_color="#444", hover_color="#555").grid(row=0, column=2, padx=4)

    # queue (left)
    def _build_queue(self):
        self.queue = ctk.CTkScrollableFrame(self, corner_radius=12, label_text="Files")
        self.queue.grid(row=2, column=0, sticky="nsew", padx=(18, 8), pady=4)
        self.queue.grid_columnconfigure(0, weight=1)
        self.empty_lbl = ctk.CTkLabel(self.queue, text="No files yet — add STEP files above.",
                                      text_color=C_MUTE)
        self.empty_lbl.grid(row=0, column=0, pady=30)

    # preview (right)
    def _build_preview(self):
        p = ctk.CTkFrame(self, corner_radius=12)
        p.grid(row=2, column=1, sticky="nsew", padx=(8, 18), pady=4)
        p.grid_columnconfigure(0, weight=1)
        p.grid_rowconfigure(1, weight=1)
        self.prev_title = ctk.CTkLabel(p, text="Preview", anchor="w",
                                       font=ctk.CTkFont(size=14, weight="bold"))
        self.prev_title.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        self.prev_img = ctk.CTkLabel(p, text="Select a finished file to preview its flat pattern.",
                                     text_color=C_MUTE)
        self.prev_img.grid(row=1, column=0, sticky="nsew", padx=14)
        self.prev_detail = ctk.CTkLabel(p, text="", anchor="w", justify="left",
                                        font=ctk.CTkFont(size=12))
        self.prev_detail.grid(row=2, column=0, sticky="ew", padx=14, pady=6)
        bb = ctk.CTkFrame(p, fg_color="transparent")
        bb.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 12))
        self.open_folder_btn = ctk.CTkButton(bb, text="Open folder", state="disabled",
                                             command=self._open_folder)
        self.open_folder_btn.pack(side="left", padx=(0, 6))
        self.open_json_btn = ctk.CTkButton(bb, text="Open JSON", state="disabled",
                                           fg_color="#444", hover_color="#555",
                                           command=self._open_json)
        self.open_json_btn.pack(side="left")

    # footer: extract
    def _build_footer(self):
        f = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        f.grid(row=3, column=0, columnspan=2, sticky="ew", padx=18, pady=(4, 14))
        f.grid_columnconfigure(1, weight=1)
        self.extract_btn = ctk.CTkButton(f, text="▶   Extract", height=44, width=180,
                                         font=ctk.CTkFont(size=15, weight="bold"),
                                         command=self.extract)
        self.extract_btn.grid(row=0, column=0, sticky="w")
        self.status = ctk.CTkLabel(f, text="Ready.", text_color=C_MUTE, anchor="w")
        self.status.grid(row=0, column=1, sticky="ew", padx=14)

    # ── file management ──
    def add_files(self):
        sel = filedialog.askopenfilenames(title="Select STEP files (up to 10)", filetypes=STEP_TYPES)
        if not sel:
            return
        for p in sel:
            if p in self.paths:
                continue
            if len(self.paths) >= MAX_FILES:
                self.status.configure(text=f"Limit is {MAX_FILES} files — extra files ignored.")
                break
            self.paths.append(p)
            if self.empty_lbl.winfo_ismapped():
                self.empty_lbl.grid_forget()
            row = FileRow(self.queue, p, self._remove_row, self._on_row_click)
            row.grid(sticky="ew", padx=6, pady=4)
            self.rows.append(row)
        if not self.out_root.get():
            self.out_root.set(pipeline.default_out_root(self.paths))
        self._refresh_counts()

    def _remove_row(self, row):
        if self.running:
            return
        if row.path in self.paths:
            self.paths.remove(row.path)
        row.destroy()
        self.rows.remove(row)
        if not self.rows:
            self.empty_lbl.grid(row=0, column=0, pady=30)
        self._refresh_counts()

    def clear_files(self):
        if self.running:
            return
        for r in self.rows:
            r.destroy()
        self.rows.clear(); self.paths.clear()
        self.empty_lbl.grid(row=0, column=0, pady=30)
        self._refresh_counts()

    def choose_out(self):
        d = filedialog.askdirectory(title="Choose output folder")
        if d:
            self.out_root.set(os.path.join(d, "BusbarX_Output"))

    def _refresh_counts(self):
        self.count_lbl.configure(text=f"{len(self.paths)} / {MAX_FILES} files")
        self.extract_btn.configure(state="normal" if self.paths else "disabled")

    # ── extraction ──
    def extract(self):
        if self.running or not self.paths:
            return
        self.running = True
        self._auto = True
        self.extract_btn.configure(state="disabled", text="Working…")
        self.drop.configure(state="disabled")
        out_root = self.out_root.get() or pipeline.default_out_root(self.paths)
        self.out_root.set(out_root)
        for r in self.rows:
            r.result = None; r.bar.set(0)
            r.dot.configure(text_color=C_MUTE); r.detail.configure(text="queued", text_color=C_MUTE)
        threading.Thread(target=self._worker, args=(out_root,), daemon=True).start()

    def _worker(self, out_root):
        n = len(self.rows)
        ok = 0
        for i, row in enumerate(list(self.rows), 1):
            self.after(0, lambda r=row, i=i: (r.set_working(),
                       self.status.configure(text=f"Processing {i}/{n} — {os.path.basename(r.path)}…")))
            res = pipeline.process_one(row.path, out_root, "default")
            if res["ok"]:
                ok += 1
            self.after(0, self._row_done, row, res)
        self.after(0, self._batch_done, ok, n, out_root)

    def _row_done(self, row, res):
        row.set_result(res)
        if res["ok"] and self._auto:        # preview follows completion until the user clicks
            self._select_row(row)

    def _batch_done(self, ok, n, out_root):
        self.running = False
        self.extract_btn.configure(state="normal", text="▶   Extract")
        self.drop.configure(state="normal")
        self.status.configure(text=f"Done — {ok}/{n} extracted → {out_root}")

    # ── preview ──
    def _on_row_click(self, row):
        self._auto = False                  # user took control of the preview
        self._select_row(row)

    def _select_row(self, row):
        res = row.result
        if not res or not res["ok"]:
            return
        self._sel = res
        for r in self.rows:                 # highlight the active card, dim the rest
            r.set_selected(r is row)
        out = res["out"]; p = out["part"]; fp = p["flat_pattern"]
        self.prev_title.configure(text=res["part"])
        bp = out["bend_parameters"]
        self.prev_detail.configure(text=(
            f"status: {p['flat_pattern_status']}        "
            f"flat-pattern: {fp['length_mm']} × {fp['width_mm']} × {fp['thickness_mm']} mm\n"
            f"features: {len(out['features'])}    bends: {len(out['bends'])}    "
            f"footprint: {p['formed_footprint']['length_mm']} × {p['formed_footprint']['width_mm']} mm\n"
            f"bend profile: {bp['profile']} ({bp['method']}, value={bp['value']})"))
        self.open_folder_btn.configure(state="normal")
        self.open_json_btn.configure(state="normal")
        if res["png"] and os.path.exists(res["png"]):
            try:
                im = Image.open(res["png"])
                maxw, maxh = max(self.prev_img.winfo_width() - 8, 360), 320
                r = min(maxw / im.width, maxh / im.height)
                size = (int(im.width * r), int(im.height * r))
                self._preview_img = ctk.CTkImage(light_image=im, dark_image=im, size=size)
                self.prev_img.configure(image=self._preview_img, text="")
            except Exception as e:
                self.prev_img.configure(image=None, text=f"(preview unavailable: {e})")
        else:
            self.prev_img.configure(image=None, text="(no visualization)")

    def _open_folder(self):
        if self._sel:
            try:
                os.startfile(self._sel["part_dir"])
            except Exception:
                pass

    def _open_json(self):
        if self._sel and self._sel["json"]:
            try:
                os.startfile(self._sel["json"])
            except Exception:
                pass


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
