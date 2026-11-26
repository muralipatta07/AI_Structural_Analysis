"""AI Structural Analysis - ETABS seismic / design toolkit (IS 1893:2016, IS 16700, IS 456, IS 13920).
Run:  python app.py        (Windows + ETABS 21/22, pip install -r requirements.txt)
A design aid only - every result must be checked by a qualified structural engineer."""
import os, sys, json, csv, math, subprocess, tempfile, threading, queue, webbrowser
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import core

CFG = os.path.join(os.path.expanduser("~"), ".ai_structural_analysis.json")
VERSION = "1.3.0-audit"
FOOT = f"AI Structural Analysis {VERSION}  ·  IS 1893 (Part 1):2016, IS 16700, IS 456, IS 13920  ·  A design aid - every result must be checked by a qualified structural engineer."
THEMES = {
    "Light Blue": dict(side="#0b1d3a", sfg="#ffffff", acc="#1565c0", bg="#ffffff", fg="#1a1a1a", hd="#0b3a8c", ent="#ffffff"),
    "Dark": dict(side="#0d0d0d", sfg="#e8e8e8", acc="#3a7bd5", bg="#1f1f1f", fg="#e8e8e8", hd="#7fb2ff", ent="#2b2b2b"),
    "Light Grey": dict(side="#37474f", sfg="#ffffff", acc="#546e7a", bg="#f2f2f2", fg="#1a1a1a", hd="#263238", ent="#ffffff")}
CUR = ["Light Blue"]

def load_cfg():
    try:
        with open(CFG, encoding='utf-8') as file:
            return json.load(file)
    except Exception: return {}
def save_cfg(c):
    try:
        with open(CFG, 'w', encoding='utf-8') as file:
            json.dump(c, file, indent=1)
    except Exception: pass

def V(x=""): return tk.StringVar(value=str(x))

def apply_style(root, name):
    CUR[0] = name; t = THEMES[name]; st = ttk.Style(root); st.theme_use("clam")
    st.configure(".", background=t["bg"], foreground=t["fg"], fieldbackground=t["ent"], font=("Segoe UI", 9))
    st.configure("TButton", background=t["acc"], foreground="#ffffff", padding=(10, 4), borderwidth=0)
    st.map("TButton", background=[("active", t["hd"])])
    st.configure("H1.TLabel", font=("Segoe UI", 15, "bold"), foreground=t["hd"])
    st.configure("Sub.TLabel", font=("Segoe UI", 8, "italic"))
    st.configure("Treeview", background=t["ent"], fieldbackground=t["ent"], foreground=t["fg"], rowheight=20)
    st.configure("Treeview.Heading", background=t["hd"], foreground="#ffffff")
    st.configure("TNotebook.Tab", padding=(10, 3)); root.configure(bg=t["bg"])

# ---------------- reusable widgets ----------------
class Table(ttk.Frame):
    def __init__(s, p, cols, widths=None, edit=(), toggle=(), height=12):
        super().__init__(p); s.cols, s.edit, s.toggle = cols, edit, toggle
        s.tv = ttk.Treeview(s, columns=cols, show="headings", height=height)
        for i, c in enumerate(cols):
            s.tv.heading(c, text=c); s.tv.column(c, width=widths[i] if widths else 110, anchor="w")
        vs = ttk.Scrollbar(s, command=s.tv.yview); hs = ttk.Scrollbar(s, orient="horizontal", command=s.tv.xview)
        s.tv.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        s.tv.grid(row=0, column=0, sticky="nsew"); vs.grid(row=0, column=1, sticky="ns"); hs.grid(row=1, column=0, sticky="ew")
        s.rowconfigure(0, weight=1); s.columnconfigure(0, weight=1)
        for t, c in (("bad", "#c62828"), ("ok", "#1b7f3b"), ("nc", "#7a7a7a")): s.tv.tag_configure(t, foreground=c)
        s.tv.bind("<Double-1>", s._dbl)
    def set(s, rows):
        s.tv.delete(*s.tv.get_children())
        for r in rows:
            v = [str(x) for x in r]
            tag = ("bad",) if "NOT OK" in v else ("ok",) if "OK" in v else ("nc",) if "NOT CHECKED" in v else ()
            s.tv.insert("", "end", values=v, tags=tag)
    def rows(s): return [list(s.tv.item(i, "values")) for i in s.tv.get_children()]
    def selected(s):
        x = s.tv.selection(); return list(s.tv.item(x[0], "values")) if x else None
    def _dbl(s, ev):
        iid, col = s.tv.identify_row(ev.y), s.tv.identify_column(ev.x)
        if not iid: return
        ci = int(col[1:]) - 1; name = s.cols[ci]; v = list(s.tv.item(iid, "values"))
        if name in s.toggle:
            v[ci] = "No" if v[ci] == "Yes" else "Yes"; s.tv.item(iid, values=v); return
        if name not in s.edit: return
        x, y, w, h = s.tv.bbox(iid, col); e = ttk.Entry(s.tv); e.place(x=x, y=y, width=w, height=h)
        e.insert(0, v[ci]); e.focus()
        def done(_=None):
            v[ci] = e.get(); s.tv.item(iid, values=v); e.destroy()
        e.bind("<Return>", done); e.bind("<FocusOut>", done)

def make_plot(parent, h=3.6):
    try:
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        fig = Figure(figsize=(5, h), dpi=90); cv = FigureCanvasTkAgg(fig, parent)
        cv.get_tk_widget().pack(fill="both", expand=True); return fig, cv
    except Exception:
        ttk.Label(parent, text="Install matplotlib (pip install matplotlib) to see plots").pack(); return None, None

def bar(p, *items):
    f = ttk.Frame(p)
    if any(child.winfo_manager() == 'grid' for child in p.winfo_children() if child != f):
        columns, rows = p.grid_size()
        f.grid(row=rows, column=0, columnspan=max(1, columns), sticky='ew', pady=3)
    else:
        f.pack(fill='x', pady=3)
    for title, command in items:
        ttk.Button(f, text=title, command=command).pack(side='left', padx=3)
    return f

def entry(p, label, var, w=9, values=None, r=0, c=0):
    ttk.Label(p, text=label).grid(row=r, column=c, sticky="e", padx=3, pady=2)
    w_ = ttk.Combobox(p, textvariable=var, values=values, width=w + 3) if values is not None else ttk.Entry(p, textvariable=var, width=w)
    w_.grid(row=r, column=c + 1, sticky="w", padx=2); return w_

def textbox(p, h=8):
    t = tk.Text(p, height=h, font=("Consolas", 9), bg=THEMES[CUR[0]]["ent"], fg=THEMES[CUR[0]]["fg"]); t.pack(fill="both", expand=True); return t
def put(t, s): t.delete("1.0", "end"); t.insert("end", s)

class Page(ttk.Frame):
    key = title = sub = ""
    def __init__(s, app):
        super().__init__(app.body, padding=6); s.app, s.e = app, app.etabs
        ttk.Label(s, text=s.title, style="H1.TLabel").pack(anchor="w")
        ttk.Label(s, text=s.sub, style="Sub.TLabel", wraplength=900).pack(anchor="w")
        ttk.Separator(s).pack(fill="x", pady=4)
        s.logbox = tk.Text(s, height=4, font=("Consolas", 9), bg=THEMES[CUR[0]]["ent"], fg=THEMES[CUR[0]]["fg"]); s.logbox.pack(fill="x", side="bottom")
        s.main = ttk.Frame(s); s.main.pack(fill="both", expand=True); s.build()
    def build(s): pass
    def log(s, *m): s.logbox.insert("end", " ".join(map(str, m)) + "\n"); s.logbox.see("end")
    def run(s, fn):
        if not s.e.m: return messagebox.showwarning("ETABS", "Not connected to ETABS. Restart and use Connect.")
        try: fn()
        except Exception as ex: s.log("ERROR:", repr(ex))
    def save(s, headers, rows, notes=()): s.app.results[s.key] = {"headers": headers, "rows": rows, "notes": list(notes)}

class CaseDialog(tk.Toplevel):
    def __init__(s, app, cases, sx, sy):
        super().__init__(app); s.title("Select the lateral load cases"); s.result = None; s.cases = cases
        ttk.Label(s, text="Tick the cases or combinations that apply the earthquake in each direction.\nA case selected in both lists applies in both directions.").grid(row=0, column=0, columnspan=2, padx=8, pady=6)
        s.lb = []
        for i, (t, sel) in enumerate((("Acting along X", sx), ("Acting along Y", sy))):
            ttk.Label(s, text=t).grid(row=1, column=i)
            l = tk.Listbox(s, selectmode="multiple", exportselection=False, width=32, height=16); l.grid(row=2, column=i, padx=6)
            for c in cases: l.insert("end", c)
            for k, c in enumerate(cases):
                if c in sel: l.selection_set(k)
            s.lb.append(l)
        bar(s, ("OK", s.ok), ("Cancel", s.destroy)); s.grab_set(); s.wait_window()
    def ok(s): s.result = tuple([s.cases[i] for i in l.curselection()] for l in s.lb); s.destroy()
