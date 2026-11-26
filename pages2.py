"""pages2.py - modules 9-18 and Reports for AI Structural Analysis."""
import os, json, csv, math, subprocess, tempfile, threading, queue, webbrowser, sys
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import core
from building_spec import normalize
from ui import *

def keep(d, ex): return {k: v for k, v in d.items() if k[0] not in ex}

# ---------------- 9 Nonlinear static pushover ----------------
class Pushover(Page):
    key = "Pushover Analysis"; title = "Pushover Analysis"; sub = (
        "Create reusable Push X and Push Y nonlinear static cases using the settings shown in your ETABS screenshots. "
        "Target displacement and roof control joint remain model-specific inputs. This workflow does not assign hinges or certify performance.")
    def build(s):
        b = bar(s.main, ("Read ETABS Model", lambda: s.run(s.read)), ("Create / Update Push X + Push Y", lambda: s.run(s.write_both)),
                ("Run ETABS Analysis", lambda: s.run(s.analyse)), ("Read Pushover Results", lambda: s.run(s.results)), ("Export CSV", s.export))
        g = ttk.Frame(s.main); g.pack(anchor="w", fill="x")
        s.grav, s.pattern, s.axis, s.point, s.disp, s.sf = V(""), V(""), V("X"), V(""), V(""), V("1.0")
        s.mass, s.modal = V("MsSrc1"), V("Modal")
        s.pattern_widget = entry(g, "Acceleration load name (reference)", s.pattern, 23, ["UX", "UY"])
        entry(g, "Direction/results", s.axis, 6, ["X", "Y"], c=2)
        s.mass_widget = entry(g, "Mass source", s.mass, 22, ["MsSrc1"], r=1); entry(g, "Modal case", s.modal, 14, c=2, r=1)
        s.point_widget = entry(g, "Roof control joint", s.point, 14, [""], r=2); entry(g, "Target displacement (m)", s.disp, 12, r=2, c=2)
        ttk.Label(s.main, text="Preset: nonlinear static; zero initial conditions (unstressed); Acceleration UX/UY, factor 1; Modal; geometric nonlinearity None; displacement control; multiple saved states; default nonlinear parameters. Assign/review hinges and choose a justified target displacement for this model before analysis.", wraplength=1100).pack(anchor="w", pady=3)
        s.curve = Table(s.main, ("Step", "Control displacement", "Base shear", "Unit / status"), [70, 180, 140, 170], height=11); s.curve.pack(fill="both", expand=True)
        s.fig, s.canvas = make_plot(s.main, 2.5); s._rows = []
    def read(s):
        cases = s.e.names("case"); patterns = core._seqs(s.e.m.LoadPatterns.GetNameList())[0]
        s.pattern_widget.configure(values=["UX", "UY"])
        mt = s.e.table("Mass Source Definition"); ni, di = mt.idx("name"), mt.idx("isdefault")
        mass_names = list(dict.fromkeys(r[ni] for r in mt.rows if ni is not None and r[ni]))
        if mass_names:
            s.mass_widget.configure(values=mass_names)
            default = next((r[ni] for r in mt.rows if ni is not None and di is not None and r[di] == "Yes"), None)
            if default: s.mass.set(default)
        # Locate the topmost model joints and offer them as the control-point choices.
        P = s.e.points()
        if P:
            ztop = max(p[2] for p in P.values()); top = sorted(n for n, p in P.items() if abs(p[2] - ztop) < 1e-6)
            s.point_widget.configure(values=top)
            if top and s.point.get() not in top: s.point.set(top[0])
        if not s.pattern.get(): s.pattern.set("UX")
        if not s.modal.get() and "Modal" in cases: s.modal.set("Modal")
        s.log(f"Found {len(cases)} existing cases and {len(P)} joints. Using the screenshot setup; target displacement must be selected for the model.")
    def write_both(s):
        point = s.point.get().strip()
        if not all((point, s.disp.get().strip(), s.mass.get().strip(), s.modal.get().strip())): raise ValueError("Enter mass source, modal case, roof control joint and target displacement.")
        target = float(s.disp.get())
        if target <= 0: raise ValueError("Target displacement must be positive.")
        if point not in s.e.points(): raise ValueError("Control joint was not found in ETABS.")
        if s.modal.get() not in s.e.names("case"): raise ValueError("The selected modal case was not found in ETABS.")
        nl = s.e.m.LoadCases.StaticNonlinear
        # ETABS StaticNonlinear API uses Accel as its load-type enum for Ux/Uy acceleration input.
        for name, axis, dof in (("Push X", "UX", 1), ("Push Y", "UY", 2)):
            calls = (lambda: nl.SetCase(name), lambda: nl.SetInitialCase(name, ""),
                     lambda: nl.SetMassSource(name, s.mass.get().strip()), lambda: nl.SetModalCase(name, s.modal.get().strip()),
                     lambda: nl.SetLoads(name, 1, ["Accel"], [axis], [1.0]),
                     lambda: nl.SetGeometricNonlinearity(name, 0),
                     lambda: nl.SetLoadApplication(name, 2, 1, target, 1, dof, point, ""),
                     lambda: nl.SetResultsSaved(name, True, 10, 200, True))
            for call in calls:
                ret = call()
                if ret not in (None, 0): raise RuntimeError(f"ETABS rejected a setting for {name} (API return {ret}).")
        s.pattern.set("UX" if s.axis.get() == "X" else "UY")
        s.log(f"Created/updated Push X and Push Y: Acceleration UX/UY x 1, unstressed initial state, mass source {s.mass.get()}, modal case {s.modal.get()}, None geometric nonlinearity, displacement control at {point} to {target:g}, multiple states. Confirm nonlinear hinges and case status in ETABS.")
    def analyse(s):
        s.e.run_analysis(); s.log("ETABS analysis command completed. Read results and confirm the case status is Finished in ETABS before interpreting the curve.")
    @staticmethod
    def _field(t, *names):
        for name in names:
            i = t.idx(name)
            if i is not None: return i
        return None
    def results(s):
        axis = s.axis.get(); name, point = "Push " + axis, s.point.get().strip()
        br, jd = s.e.table("Base Reactions"), s.e.table("Joint Displacements")
        oc_b = s._field(br, "outputcase"); step_b = s._field(br, "stepnum", "step"); force_i = s._field(br, "globalfx" if axis == "X" else "globalfy")
        oc_j = s._field(jd, "outputcase"); pt_j = s._field(jd, "uniquename", "joint"); step_j = s._field(jd, "stepnum", "step"); disp_i = s._field(jd, "u1" if axis == "X" else "u2")
        if None in (oc_b, step_b, force_i, oc_j, pt_j, step_j, disp_i): raise ValueError("ETABS result tables are missing case, step number, joint, force or displacement columns. No curve has been inferred.")
        force = {}
        for r in br.rows:
            if r[oc_b] == name:
                key = core.fl(r[step_b]) if step_b is not None else 0.0
                force[key] = abs(core.fl(r[force_i]))
        disp = {}
        for r in jd.rows:
            if r[oc_j] == name and r[pt_j] == point:
                key = core.fl(r[step_j]) if step_j is not None else 0.0
                disp[key] = core.fl(r[disp_i])
        keys = sorted(set(force) & set(disp))
        if not keys: raise ValueError("No matching pushover steps were found. Check that ETABS completed the selected case and that the control joint is correct.")
        s._rows = [[k, disp[k], force[k]] for k in keys]
        s.curve.set([[f"{k:g}", f"{disp[k]:.5g}", f"{force[k]:.5g}", "kN, m during extraction"] for k in keys])
        if s.fig:
            s.fig.clear(); ax = s.fig.add_subplot(111); ax.plot([r[1] for r in s._rows], [r[2] for r in s._rows], marker="."); ax.set_xlabel(f"Global {axis} control displacement (model units)"); ax.set_ylabel("Base shear (model force units)"); ax.grid(True, alpha=.25); s.canvas.draw()
        s.save(("Step", f"Global {axis} displacement", "Base shear"), s._rows, ["ETABS nonlinear static results; extracted using kN and m. Pushover acceptance criteria are project-specific and are not assessed here."])
        s.log(f"Read {len(keys)} matching states for {name} at joint {point}. The curve is a result extract; review hinge states, convergence and acceptance criteria in ETABS / project basis.")
    def export(s):
        if not s._rows: return s.log("Read pushover results before exporting.")
        p = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if p:
            with open(p, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f); w.writerow(("Step", f"Global {s.axis.get()} displacement (model units)", "Base shear (model units)")); w.writerows(s._rows)
            s.log("Saved", p)

# ---------------- 9 Torsion ----------------
class Torsion(Page):
    key = "Irregularity Check"; title = "Torsional Irregularity"
    sub = "Torsional irregularity to IS 1893 (Part 1):2016 Table 5 (i) and Amendment 2, with the torsional-mode condition. Needs the lateral cases (earthquake with accidental eccentricity) and an analysed model."
    H = ("Storey", "Dir", "Governing case", "Max/Avg drift", "Limit", "Status")
    def build(s):
        s.sel = ([], []); s.lim, s.ex = V(1.5), V("Base, PLINTH")
        bar(s.main, ("Load Results", lambda: s.run(s.go)), ("Run Analysis", lambda: s.run(s.e.run_analysis)), ("Select Lateral Load Cases...", lambda: s.run(s.pick)))
        g = ttk.Frame(s.main); g.pack(anchor="w"); entry(g, "Limit", s.lim, 5); entry(g, "Exclude storeys (comma list)", s.ex, 22, c=2)
        s.info = ttk.Label(s.main, text="No lateral load cases chosen yet."); s.info.pack(anchor="w"); nb = ttk.Notebook(s.main); nb.pack(fill="both", expand=True); T = {}
        for t in ("Ratio by Storey", "Plan - Displaced Shape", "Storey Data - X", "Storey Data - Y"): T[t] = ttk.Frame(nb); nb.add(T[t], text=t)
        s.tr = Table(T["Ratio by Storey"], s.H, [100, 50, 220, 120, 80, 90]); s.tr.pack(fill="both", expand=True); s.fp, s.cp = make_plot(T["Plan - Displaced Shape"])
        s.tx = Table(T["Storey Data - X"], s.H, [100, 50, 220, 120, 80, 90]); s.tx.pack(fill="both", expand=True); s.ty = Table(T["Storey Data - Y"], s.H, [100, 50, 220, 120, 80, 90]); s.ty.pack(fill="both", expand=True)
    def pick(s):
        r = CaseDialog(s.app, s.e.names("case") + s.e.names("combo"), *s.sel).result
        if r: s.sel = r; s.info.config(text=f"X: {', '.join(r[0])} | Y: {', '.join(r[1])}")
    def go(s):
        x, y = s.sel
        if not (x or y): return messagebox.showinfo("Torsion", "Select the lateral load cases first.")
        t = s.e.table("Diaphragm Max Over Avg Drifts"); ex = [a.strip() for a in s.ex.get().split(",")]; lim = float(s.lim.get()); best = {}
        for a, b, c, r in zip(t.col("story", num=False), t.col("outputcase", num=False), t.col("item", num=False), t.col("ratio")):
            d = c.strip()[-1:].upper()
            if a in ex or d not in ("X", "Y"): continue
            if b in (x if d == "X" else y) and r > best.get((a, d), (0, ""))[0]: best[(a, d)] = (r, b)
        rows = [[a, d, v[1], f"{v[0]:.3f}", f"<= {lim:g}", "OK" if v[0] <= lim else "NOT OK"] for (a, d), v in best.items()]
        s.tr.set(rows); s.tx.set([r for r in rows if r[1] == "X"]); s.ty.set([r for r in rows if r[1] == "Y"])
        s.save(s.H, rows, ["Torsional irregularity if max/avg storey drift exceeds 1.5 (IS 1893:2016 Table 5 (i))."]); bad = [r for r in rows if r[5] == "NOT OK"]; s.log(f"{len(rows)} storey/direction results, {len(bad)} NOT OK.")
        if rows and s.fp: core.plot_model(s.fp, s.e, max(rows, key=lambda r: float(r[3]))[2], None, "plan"); s.cp.draw()

# ---------------- 11 Drift & displacement ----------------
class Drift(Page):
    key = "Drift and Displacement"; title = "Storey Drift & Displacement"
    sub = "Storey drift to IS 1893 (Part 1):2016 cl. 7.11.1.1, with an optional check on total displacement. Needs the lateral cases and an analysed model."
    def build(s):
        s.sel = ([], []); s.dl, s.dv, s.dchk, s.ex = V(250), V(250), tk.BooleanVar(value=True), V("Base")
        bar(s.main, ("Load Results", lambda: s.run(s.go)), ("Run Analysis", lambda: s.run(s.e.run_analysis)), ("Select Lateral Load Cases...", lambda: s.run(s.pick)))
        g = ttk.Frame(s.main); g.pack(anchor="w"); entry(g, "Drift limit 1 /", s.dl, 5); ttk.Checkbutton(g, text="Displacement", variable=s.dchk).grid(row=0, column=2); entry(g, "1 /", s.dv, 5, c=3); entry(g, "Exclude storeys", s.ex, 14, c=5)
        s.info = ttk.Label(s.main, text=""); s.info.pack(anchor="w"); nb = ttk.Notebook(s.main); nb.pack(fill="both", expand=True); T = {}
        for t in ("Storey Drift", "Displacement", "Deformed Shape", "Warnings", "Results"): T[t] = ttk.Frame(nb); nb.add(T[t], text=t)
        s.td = Table(T["Storey Drift"], ("Storey", "Dir", "Case", "Drift ratio", "Limit", "Status"), [100, 50, 240, 110, 100, 90]); s.td.pack(fill="both", expand=True)
        s.tp = Table(T["Displacement"], ("Storey", "Dir", "Case", "Disp (mm)", "Limit (mm)", "Status"), [100, 50, 240, 110, 100, 90]); s.tp.pack(fill="both", expand=True)
        a, b = ttk.Frame(T["Deformed Shape"]), ttk.Frame(T["Deformed Shape"]); a.pack(side="left", fill="both", expand=True); b.pack(side="left", fill="both", expand=True)
        s.f1, s.c1 = make_plot(a); s.f2, s.c2 = make_plot(b); s.wn = textbox(T["Warnings"]); s.rs = textbox(T["Results"])
    def pick(s):
        r = CaseDialog(s.app, s.e.names("case") + s.e.names("combo"), *s.sel).result
        if r: s.sel = r; s.info.config(text=f"X: {', '.join(r[0])} | Y: {', '.join(r[1])}")
    def worst(s, names, cases, dirs, vals, ex, sel):
        # dirs = string column (e.g. "X", "Y"), vals = numeric column (drift ratios)
        best = {}
        for a, b, d_, v in zip(names, cases, dirs, vals):
            d = d_.strip().upper()[:1]
            if a in ex or d not in ("X", "Y"): continue
            if b in sel[0 if d == "X" else 1] and abs(v) > best.get((a, d), (0, ""))[0]: best[(a, d)] = (abs(v), b)
        return best
    def go(s):
        if not (s.sel[0] or s.sel[1]): return messagebox.showinfo("Drift", "Select the lateral load cases first.")
        ex = [a.strip() for a in s.ex.get().split(",")]; lv = dict(core.levels(s.e)); lim = 1 / float(s.dl.get()); t = s.e.table("Story Drifts")
        # Fixed: direction column must be passed before drift (numeric) column to match worst() signature
        B = s.worst(t.col("story", num=False), t.col("outputcase", num=False), t.col("direction", num=False), t.col("drift"), ex, s.sel)
        rows = [[a, d, v[1], f"{v[0]:.5f}  (1/{1/max(v[0],1e-12):.0f})", f"{lim:.5f}", "OK" if v[0] <= lim else "NOT OK"] for (a, d), v in B.items()]; s.td.set(rows); allr = list(rows)
        P = []
        if s.dchk.get():
            
            td = s.e.table("Diaphragm Center Of Mass Displacements"); sto, oc = td.col("story", num=False), td.col("outputcase", num=False)
            for d, vals in (("X", td.col("ux")), ("Y", td.col("uy"))):
                best = {}
                for a, b, v in zip(sto, oc, vals):
                    if a not in ex and b in s.sel[0 if d == "X" else 1] and abs(v) > best.get(a, (0, ""))[0]: best[a] = (abs(v), b)
                for a, (v, b) in best.items():
                    L = lv.get(a, 0) / float(s.dv.get()); P.append([a, d, b, f"{v*1000:.2f}", f"{L*1000:.2f}", "OK" if v <= L else "NOT OK"])
            s.tp.set(P); allr += P
        bad = [r for r in allr if r[5] == "NOT OK"]; put(s.wn, "\n".join(f"{r[0]} {r[1]} {r[2]}: {r[3]} > {r[4]}" for r in bad) or "No warnings.")
        w = max(B.items(), key=lambda kv: kv[1][0]) if B else None; put(s.rs, ("OK - drift and displacement within the limits" if not bad else f"NOT OK - {len(bad)} exceedance(s)") + (f"\nWorst drift {w[1][0]:.5f} at {w[0][0]} ({w[0][1]}) under {w[1][1]}" if w else ""))
        s.save(("Storey", "Dir", "Case", "Value", "Limit", "Status"), allr, [f"Drift limit 1/{s.dl.get()} h (cl. 7.11.1.1); displacement limit H/{s.dv.get()}."])
        for dr, fg, cv in (("X", s.f1, s.c1), ("Y", s.f2, s.c2)):
            c = [v[1] for (a, d), v in B.items() if d == dr]
            if c and fg: core.plot_model(fg, s.e, max(set(c), key=c.count), None, "iso"); cv.draw()

# ---------------- 12 Base shear ----------------
class BaseShear(Page):
    key = "Base Shear Share"; title = "Base Shear: Walls & Columns"
    sub = "Share of the base (storey) shear taken by the walls and by the columns, and by each one - with the 25 % frame check for dual systems (cl. 4.2). Walls need pier labels (Assign > Shell > Pier Label)."
    def build(s):
        s.sto, s.cs, s.dr, s.md, s.lt = V(""), V(""), V("X"), V("as modelled"), V(4.0)
        bar(s.main, ("Read cases and storeys", lambda: s.run(s.read)), ("Calculate shares", lambda: s.run(s.calc)), ("Send scaling to Load Combinations", s.scaling))
        g = ttk.Frame(s.main); g.pack(anchor="w"); s.c1 = entry(g, "Storey", s.sto, 10, []); s.c2 = entry(g, "Load case / combination", s.cs, 22, [], c=2); entry(g, "Direction", s.dr, 3, ["X", "Y"], c=4)
        entry(g, "Walls are", s.md, 28, ["as modelled"], r=1); ttk.Label(g, text="Wall geometry classification is separate.").grid(row=1,column=2,columnspan=3)
        s.res = ttk.Label(s.main, text="Choose a story, linear static case and global direction. Independent frame capacity is not checked."); s.res.pack(anchor="w")
        s.tab = Table(s.main, ("Element", "Kind", "Shear (kN)", "Share %"), [220, 90, 110, 90]); s.tab.pack(fill="both", expand=True); s.fc = 1.0
    def read(s):
        L = core.levels(s.e); s.c1.configure(values=[n for n, _ in L[1:]]); s.sto.set(L[1][0]); s.c2.configure(values=s.e.names("case") + s.e.names("combo"))
    def calc(s):
        X = s.dr.get() == "X"; story, case = s.sto.get(), s.cs.get(); dims = {f["name"]: (f["t2"], f["t3"]) for f in s.e.frame_info()} if s.md.get().startswith("classify") else {}
        tc = s.e.table("Column Forces").sel("story", story).sel("outputcase", case); un, stn, v = tc.col("uniquename", num=False), tc.col("station"), tc.col("v2" if X else "v3"); cols = {}
        for u, a, b in zip(un, stn, v):
            if u not in cols or a < cols[u][0]: cols[u] = (a, b)
        items = []
        for u, (a, b) in cols.items():
            w = dims.get(u); kind = "wall" if w and max(w) / max(min(w), 1e-9) >= float(s.lt.get()) else "column"; items.append([f"Column {u}", kind, b])
        tp = s.e.table("Pier Forces").sel("story", story).sel("outputcase", case)
        for n, loc, b in zip(tp.col("pier", num=False), tp.col("location", num=False), tp.col("v2" if X else "v3")):
            if "bot" in loc.lower(): items.append([f"Pier {n}", "wall", b])
        tot = sum(abs(i[2]) for i in items) or 1; Vw = sum(abs(i[2]) for i in items if i[1] == "wall"); Vc = tot - Vw
        s.tab.set([[i[0], i[1], f"{i[2]:.1f}", f"{abs(i[2])/tot*100:.1f}"] for i in sorted(items, key=lambda i: -abs(i[2]))]); s.fc = 0.25 * tot / Vc if Vc and Vc / tot < .25 and Vw else 1.0
        dual = Vw > 0; ok = (not dual) or Vc / tot >= .25
        s.res.config(text=f"Walls {Vw/tot*100:.1f} % | Columns {Vc/tot*100:.1f} % of storey shear {tot:.1f} kN" + (f" | 25 % frame check: {'OK' if ok else 'NOT OK'}" if dual else ""))
        s.save(("Item", "Value", "Status"), [["Storey shear (kN)", f"{tot:.1f}", ""], ["Walls %", f"{Vw/tot*100:.1f}", ""], ["Columns %", f"{Vc/tot*100:.1f}", "OK" if ok else "NOT OK"]], [f"Storey {story}, case {case}, direction {s.dr.get()}. Dual-system rule: frames resist at least 25 % of storey shear (IS 1893 cl. 4.2)."])
    def scaling(s): messagebox.showinfo("Scaling", f"Frame-share factor on the lateral combinations for the columns/frames: {s.fc:.3f}\n(1.000 = no scaling needed). Apply it in ETABS or by editing the EQ factors in step 14.")

# ---------------- 13 Wall density ----------------
class WallDensity(Page):
    key = "Wall Density"; title = "Wall Plan Density"
    sub = "Plan density of the RC structural walls in X and Y at the plinth level, checked against IS 1893 (Part 1):2016 cl. 7.10 - with the IS 13920 wall definition if you want it. Needs only the geometry - walls and floors; no analysis."
    def build(s):
        s.pl, s.area, s.zone, s.mn, s.w13, s.blade, s.dt = V("Base"), V(0.0), V("IV"), V(2.0), tk.BooleanVar(value=True), tk.BooleanVar(value=True), V(200)
        bar(s.main, ("Read model", lambda: s.run(s.read)), ("Calculate density", lambda: s.run(s.calc))); g = ttk.Frame(s.main); g.pack(anchor="w")
        s.cp = entry(g, "Plinth level", s.pl, 10, []); entry(g, "Plinth area (m2, 0 = bounding box)", s.area, 8, c=2); entry(g, "Seismic zone", s.zone, 4, list(core.ZONE), r=1); entry(g, "User density reference %", s.mn, 5, r=1, c=2); ttk.Label(g, text="Verified pier thickness required.").grid(row=1,column=4,columnspan=2)
        ttk.Checkbutton(g, text="IS 13920 wall: t >= 150 mm, L/t >= 4", variable=s.w13).grid(row=2, column=0, columnspan=2, sticky="w"); ttk.Checkbutton(g, text="Count blade columns (L/t >= ratio) as walls", variable=s.blade).grid(row=2, column=2, columnspan=2, sticky="w")
        s.res = ttk.Label(s.main, text="Read the model, choose the plinth level, then Calculate."); s.res.pack(anchor="w")
        s.tab = Table(s.main, ("Count", "Member", "Kind", "L (mm)", "t (mm)", "Angle °", "A (m2)", "→X (m2)", "→Y (m2)", "Note"), [50, 140, 80, 70, 70, 60, 70, 70, 70, 240]); s.tab.pack(fill="both", expand=True)
    def read(s): s.cp.configure(values=[n for n, _ in core.levels(s.e)])
    def calc(s):
        L = core.levels(s.e); names = [n for n, _ in L]; z0 = dict(L)[s.pl.get()]; first = names[min(names.index(s.pl.get()) + 1, len(names) - 1)]; t = s.e.table("Pier Section Properties").sel("story", first)
        ai = t.idx("axis"); rows = []; ax = ay = 0.0
        for n, a, b, ang in zip(t.col("pier", num=False), t.col("width", "bot"), t.col("thick", "bot"), t.col("axis") if ai is not None else [0] * len(t.rows)):
            Lm, tm = a * 1000, (b * 1000 or float(s.dt.get()))
            if s.w13.get() and not (tm >= 150 and Lm / tm >= 4): continue
            A = a * tm / 1000; c2 = math.cos(math.radians(ang)) ** 2; rows.append([len(rows) + 1, n, "wall", f"{Lm:.0f}", f"{tm:.0f}", f"{ang:.0f}", f"{A:.3f}", f"{A*c2:.3f}", f"{A*(1-c2):.3f}", ""]); ax += A * c2; ay += A * (1 - c2)
        if s.blade.get():
            for f in s.e.frame_info():
                if f["ori"] == 1 and max(f["t2"], f["t3"]) / max(min(f["t2"], f["t3"]), 1e-9) >= 4:
                    a, b = max(f["t2"], f["t3"]), min(f["t2"], f["t3"]); A = a * b; rows.append([len(rows) + 1, f["name"], "blade col", f"{a*1000:.0f}", f"{b*1000:.0f}", "0", f"{A:.3f}", f"{A:.3f}", "0.000", "angle assumed 0° - check local axes"]); ax += A
        P = s.e.points(); pts = [(x, y) for x, y, z in P.values() if abs(z - z0) < 1e-3]; area = float(s.area.get()) or (max(p[0] for p in pts) - min(p[0] for p in pts)) * (max(p[1] for p in pts) - min(p[1] for p in pts))
        dx, dy, mn = ax / area * 100, ay / area * 100, float(s.mn.get()); st = lambda v: "OK" if v >= mn else "NOT OK"; s.tab.set(rows)
        s.res.config(text=f"Plinth area {area:.1f} m2 | density X = {dx:.2f} % ({st(dx)}) | Y = {dy:.2f} % ({st(dy)}) | minimum {mn:g} % each way in zones III, IV and V (cl. 7.10)")
        s.save(("Direction", "Density %", "Minimum %", "Status"), [["X", f"{dx:.2f}", f"{mn:g}", st(dx)], ["Y", f"{dy:.2f}", f"{mn:g}", st(dy)]], [f"Plinth area {area:.1f} m2; {len(rows)} wall members counted."])

# ---------------- 14 Load patterns ----------------
class LoadPatterns(Page):
    key = "Load Patterns"; title = "Load Patterns"; sub = "Editable standard load patterns, load types and self-weight multipliers. Apply creates or updates patterns. Configure automatic lateral-load parameters separately in ETABS."
    H = ("Apply", "Load pattern", "Type", "Self-weight multiplier", "Requested auto lateral code")
    def build(s):
        bar(s.main, ("Default loads", s.defaults), ("Read Model", lambda: s.run(s.read)), ("Apply to Model (defaults)", lambda: s.run(s.apply)))
        s.t = Table(s.main, s.H, [60, 200, 140, 150, 200], edit=("Load pattern", "Type", "Self-weight multiplier", "Requested auto lateral code"), toggle=("Apply",), height=18); s.t.pack(fill="both", expand=True); s.defaults()
    def defaults(s): s.t.set([["Yes", n, t, w, c] for n, t, w, c in core.DEFAULT_PATTERNS])
    def read(s):
        m = s.e.m; inv = {v: k for k, v in core.LOAD_TYPES.items()}
        s.t.set([["Yes", n, inv.get(s.e.try_(lambda: m.LoadPatterns.GetLoadType(n)[0], 8), "Other"), s.e.try_(lambda: m.LoadPatterns.GetSelfWTMultiplier(n)[0], 0), ""] for n in core._seqs(m.LoadPatterns.GetNameList())[0]])
    def apply(s):
        m = s.e.m; have = core._seqs(m.LoadPatterns.GetNameList())[0]; done = []
        for a, n, t, w, c in s.t.rows():
            if a != "Yes": continue
            if n in have: m.LoadPatterns.SetLoadType(n, core.LOAD_TYPES.get(t, 8)); m.LoadPatterns.SetSelfWTMultiplier(n, float(w))
            else: m.LoadPatterns.Add(n, core.LOAD_TYPES.get(t, 8), float(w), True)
            done.append([n, t, w, c])
        s.log(f"{len(done)} patterns applied. Set auto lateral parameters (IS 1893 / IS 875) in Define > Load Patterns > Modify Lateral Load."); s.save(("Load pattern", "Type", "Self-weight", "Auto lateral code"), done)

# ---------------- 15 Load combinations ----------------
class Combos(Page):
    key = "Load Combinations"; title = "Load Combinations"; sub = "A01-A50 strength and B01-B33 service combinations from the user-supplied schedule. Map each role to an ETABS load case before applying. This supplied schedule is not verified as Table 18 of IS 456; verify the governing code, factors, signs, orthogonal effects, eccentricity cases and applicability with the project engineer."
    ROLES = core.USER_ROLES
    CH = ("Combination name", "Group") + tuple(ROLES)
    def build(s):
        s.rep = tk.BooleanVar(value=True)
        b = ttk.Frame(s.main); b.pack(anchor="w")
        ttk.Checkbutton(b, text="Replace existing A## / B## combinations", variable=s.rep).pack(side="left", padx=4)
        ttk.Button(b, text="Generate / Preview", command=s.gen).pack(side="left")
        ttk.Button(b, text="Apply to Model", command=lambda: s.run(s.apply)).pack(side="left", padx=3)
        s.roles = Table(s.main, ("Role", "Mapped model load case (editable)"), [100, 480], edit=("Mapped model load case (editable)",), height=11); s.roles.pack(fill="x")
        s.roles.set([[r, r] for r in s.ROLES])
        s.t = Table(s.main, s.CH, [105, 90] + [85] * len(s.ROLES), edit=s.CH[2:], height=14); s.t.pack(fill="both", expand=True)
        s.gen()
    def gen(s):
        R = core.gen_user_combos()
        s.t.set([[r["name"], r["group"]] + [f"{r[k]:g}" for k in s.ROLES] for r in R])
        s.log(f"Generated {len(R)} requested combinations. Confirm the ETABS case mapping before applying.")
        s.save(s.CH, s.t.rows(), ["User-specified combinations"])
    def apply(s):
        mp = {r[0]: r[1].strip() for r in s.roles.rows()}
        missing = [k for k, v in mp.items() if not v]
        if missing: return s.log("Map all load case roles before applying: " + ", ".join(missing))
        rc_api = s.e.m.RespCombo
        rows = s.t.rows()
        if s.rep.get():
            for n in s.e.names("combo"):
                if (len(n) == 3 and n[0] in "AB" and n[1:].isdigit()): rc_api.Delete(n)
        for r in rows:
            name = r[0]
            rc_api.Add(name, 0)
            for role, f in zip(s.ROLES, r[2:]):
                factor = float(f)
                if factor: rc_api.SetCaseList(name, 0, mp[role], factor)
        s.log("Applied", len(rows), "load combinations to the ETABS model.")
# ---------------- 16 Detailing ----------------
class Detailing(Page):
    key = "Detailing"; title = "RC Beam Detailing"
    sub = "Bar selection from ETABS concrete design. The user shall independently re-evaluate all detailing against the applicable code-based design criteria (IS 456 / IS 13920) before use. Needs concrete frame design run in ETABS (Design > Concrete Frame Design > Start Design/Check)."
    H = ("Story", "Beam", "Section", "Top As req (mm2)", "Top bars", "Bot As req (mm2)", "Bottom bars", "Stirrups")
    def build(s):
        v = lambda x: V(x); s.p = {k: v(x) for k, x in dict(story="", cover=25, sd=8, clr=75, tmin=16, tmax=25, emin=12, emax=20, send=100, smid=150, legs=2, s0=8, s1=12).items()}
        bar(s.main, ("Load Stories", lambda: s.run(s.stories)), ("Perform Detailing", lambda: s.run(s.go)), ("Highlight Beam", lambda: s.run(s.hl)), ("Save Diagram as Image", s.img), ("Bar Notation", lambda: messagebox.showinfo("Bar notation", "n-Td : n bars of diameter d (T = high-strength deformed bar). (2L) = two layers.")),
            ("Export to Excel", s.xl), ("Export Plan DXF...", lambda: s.run(s.dxf)))
        g = ttk.Frame(s.main); g.pack(anchor="w"); s.cs = entry(g, "Story", s.p["story"], 12, [])
        for i, (k, l) in enumerate((("cover", "Cover (mm)"), ("sd", "Stirrup dia for spacing check"), ("clr", "Min clear spacing (mm)"), ("tmin", "Through bar dia min"), ("tmax", "max"), ("emin", "Extra bar dia min"), ("emax", "max"), ("send", "Stirrup spacing end"), ("smid", "mid-span"), ("legs", "Legs"), ("s0", "Stirrup dia min"), ("s1", "max"))):
            entry(g, l, s.p[k], 5, r=1 + i // 4, c=(i % 4) * 2)
        s.big = tk.BooleanVar(value=True); ttk.Label(g, text="Draft through-bar fit only; extra-bar curtailment is not calculated.").grid(row=5,column=0,columnspan=4,sticky="w")
        m = ttk.Frame(s.main); m.pack(fill="both", expand=True); s.t = Table(m, s.H, [80, 90, 90, 110, 120, 110, 120, 150], height=14); s.t.pack(side="left", fill="both", expand=True)
        s.t.tv.bind("<<TreeviewSelect>>", s.show); s.dt = textbox(m, 14); s.dt.pack_forget(); s.dt.pack(side="left", fill="both", expand=True); put(s.dt, "Select a beam to see the required and provided reinforcement."); s.rows = []
    def stories(s): s.cs.configure(values=[n for n, _ in core.levels(s.e)][1:])
    def find(s, t, *opts):
        for o in opts:
            i = t.idx(*o)
            if i is not None: return i
        raise KeyError(f"Missing design field {opts}; available: {t.f}")
    def go(s):
        t = None
        for key in ("Concrete Beam Summary - IS 456-2000", "Concrete Beam Flexure Envelope - IS 456-2000"):
            t = s.e.try_(lambda: s.e.table(key))
            if t and t.rows: break
        if not t or not t.rows: return s.log("No beam design table found. Run concrete frame design first.")
        iS, iB, iU = s.find(t, ("story",)), s.find(t, ("beam",)), s.find(t, ("uniquename",)); iSec = s.find(t, ("designsect",), ("section",))
        iT, iBo = s.find(t, ("astop",), ("top", "area"), ("as", "top")), s.find(t, ("asbot",), ("bot", "area"), ("as", "bot")); iV = t.idx("av") if t.idx("av") is not None else t.idx("shear", "area")
        P = {k: float(v.get()) if k != "story" else v.get() for k, v in s.p.items()}; env = {}
        for r in t.rows:
            if P["story"] and r[iS] != P["story"]: continue
            e = env.setdefault(r[iU], dict(story=r[iS], beam=r[iB], sec=r[iSec], top=0, bot=0, v=0))
            e["top"] = max(e["top"], core.fl(r[iT]) * 1e6); e["bot"] = max(e["bot"], core.fl(r[iBo]) * 1e6); e["v"] = max(e["v"], core.fl(r[iV]) * 1e3 if iV is not None else 0)
        rows, s.rows = [], []
        for u, e in env.items():
            g = s.e.try_(lambda: s.e.m.PropFrame.GetRectangle(e["sec"]), None); b = g[3] * 1000 if g else 300; D = g[2] * 1000 if g else 500
            tp = core.pick_bars(e["top"], b, P["cover"], P["sd"], P["tmin"], P["tmax"], P["clr"]); bt = core.pick_bars(e["bot"], b, P["cover"], P["sd"], P["tmin"], P["tmax"], P["clr"])
            se, sm = core.stirrup(e["v"], P["sd"], int(P["legs"]), int(P["send"]), int(P["smid"])); fmt = lambda x: f"{x[0]}-T{x[1]}" + (" (2L)" if x[2] > 1 else "")
            rows.append([e["story"], e["beam"], f"{b:.0f}x{D:.0f}", f"{e['top']:.0f}", fmt(tp), f"{e['bot']:.0f}", fmt(bt), f"{int(P['legs'])}L-{int(P['sd'])}φ @ {se}/{sm}"]); s.rows.append((u, e, b, D, tp, bt))
        s.app.state["beam_rebar_layouts"] = [(u, e, b/1000, D/1000, tp, bt) for u, e, b, D, tp, bt in s.rows]
        s.t.set(rows); s.save(s.H, rows, ["Drafting aid only. Independently re-evaluate against IS 456 / IS 13920 before use."]); s.log(len(rows), "beams detailed.")
    def show(s, *_):
        r = s.t.selected()
        if r: put(s.dt, f"Beam {r[1]} ({r[0]}), section {r[2]} mm\n\nTOP    required {r[3]} mm2 -> provided {r[4]}\nBOTTOM required {r[5]} mm2 -> provided {r[6]}\nSTIRRUPS {r[7]} (end / mid-span)\n\nThe user shall verify development length, curtailment and IS 13920 end-zone detailing.")
    def hl(s):
        r = s.t.selected(); u = next((x[0] for x in s.rows if x[1]["beam"] == (r or [0, 0])[1]), None)
        if u: s.e.m.SelectObj.ClearSelection(); s.e.m.FrameObj.SetSelected(u, True); s.e.m.View.RefreshView(0, False)
    def img(s):
        r = s.t.selected()
        if not r: return
        from matplotlib.figure import Figure; from matplotlib.patches import Rectangle
        b, D = [float(x) for x in r[2].split("x")]; f = Figure(figsize=(3, 4)); ax = f.add_subplot(111); ax.add_patch(Rectangle((0, 0), b, D, fill=False)); ax.set_xlim(-50, b + 50); ax.set_ylim(-50, D + 50); ax.set_aspect("equal"); ax.set_title(f"{r[1]}  top {r[4]} / bot {r[6]}", fontsize=8)
        p = filedialog.asksaveasfilename(defaultextension=".png")
        if p: f.savefig(p, dpi=150)
    def xl(s):
        p = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx"), ("CSV", "*.csv")])
        if not p: return
        try:
            import openpyxl; wb = openpyxl.Workbook(); ws = wb.active; ws.append(list(s.H)); [ws.append(r) for r in s.t.rows()]; wb.save(p)
        except Exception:
            with open(p.replace(".xlsx", ".csv"), "w", newline="") as f:
                csv.writer(f).writerows([s.H] + s.t.rows())
    def dxf(s):
        import ezdxf; P = s.e.points(); d = ezdxf.new(); ms = d.modelspace(); z = max(p[2] for p in P.values())
        for a, b in s.e.frames():
            if P[a][2] == z == P[b][2]: ms.add_line(P[a][:2], P[b][:2])
        p = filedialog.asksaveasfilename(defaultextension=".dxf")
        if p: d.saveas(p)

# ---------------- 17 AI building generator ----------------
def _extract_json(text):
    t = (text or "").replace("```json", "").replace("```JSON", "").replace("```", "").strip()

    if not t:
        raise ValueError("The JSON box is empty.")

    a, b = t.find("{"), t.rfind("}")

    if a == -1 or b <= a:
        raise ValueError("No JSON object found.")

    return json.loads(t[a:b + 1])

SCHEMA = '{"stories":5,"bays_x":3,"bay_x":5,"bays_y":2,"bay_y":4,"story_h":3.2,"bottom_h":3.5,"beam":[0.3,0.5],"column":[0.4,0.6],"slab_t":0.15,"fck":30}'
class AIGen(Page):
    key = "AI Building Generator"; title = "AI Building Generator"
    sub = "GPT / Claude web workflow: copy the prompt, use your browser account, paste the JSON reply, review it and create a separate model. No API key or automatic browser control is needed for the web workflow. Save the current ETABS model before building."
    def build(s):
        ttk.Label(s.main, text="Legacy rectangular JSON and explicit-plan floor-wise JSON are supported. Never infer unconfirmed plan dimensions.").pack(anchor="w")
        s.d = textbox(s.main, 4); s.d.pack_configure(expand=False); s.d.insert("end", "Create a 5-storey RCC building, 3 bays in X at 5 m, 2 bays in Y at 4 m, typical storey 3.2 m and bottom storey 3.5 m. Use 300x500 mm beams, 400x600 mm columns, 150 mm slabs, M30 concrete and fixed bases. Treat sections as preliminary.")
        bar(s.main, ("1. Copy Prompt", s.copy), ("2. Open ChatGPT", lambda: webbrowser.open("https://chatgpt.com/")), ("Open Claude", lambda: webbrowser.open("https://claude.ai/new")), ("3. Paste JSON", s.paste), ("4. Review Specification", s.review), ("5. Create ETABS Model", lambda: s.run(s.make)), ("Ask Claude (API, optional)", s.api))
        ttk.Label(s.main, text="Paste/edit JSON below - dimensions in metres, concrete strength in MPa").pack(anchor="w"); s.j = textbox(s.main, 12); s.sp = None
    def prompt(s):
        return ("Return ONLY JSON. Legacy rectangular format (metres, MPa):\n" + SCHEMA +
                "\nFor nonuniform framing use the documented explicit-plan format: plan.geometry_confirmed, "
                "plan.nodes mapping names to [x_m,y_m], columns (node names), beams ([start,end] names), "
                "slab_panels (convex perimeter node lists; omit panels over openings). "
                "Set units.section_dimensions to mm or m and storeys to rows with floor, height_m, beam and column. "
                "Always supply fck in MPa and slab_t in metres. Optional stair_headroom needs its own plan, "
                "height_m, beam and column. Never invent missing dimensions: set geometry_confirmed false. "
                "Do not mix the rectangular and explicit-plan formats. Building: " + s.d.get("1.0", "end").strip())
    def copy(s): s.clipboard_clear(); s.clipboard_append(s.prompt()); s.log("Prompt copied. Paste it into Claude, then copy its JSON reply.")
    def paste(s):
        try:
            txt = s.clipboard_get().strip()
            if not txt:
                raise ValueError("Clipboard is empty. Copy Claude's JSON reply first.")
            put(s.j, txt)
            s.log("Clipboard text pasted. Click Review Specification.")
        except Exception as ex:
            s.log("Paste error:", ex)

    def api(s):
        if not os.getenv("ANTHROPIC_API_KEY"):
            s.log("No ANTHROPIC_API_KEY found. Use the browser workflow instead.")
            return

        try:
            import anthropic

            client = anthropic.Anthropic()
            r = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=1000,
                messages=[{"role": "user", "content": s.prompt()}]
            )

            put(s.j, r.content[0].text.strip())
            s.log("Claude API response received. Review the specification.")

        except Exception as ex:
            s.log("API error:", ex)
    def review(s):
        try:
            sp = _extract_json(s.j.get("1.0", "end"))
            model = normalize(sp)
            s.sp = sp
            s.reviewed_text = s.j.get("1.0", "end")
            rows = []
            z = 0.0
            for f in model["floors"]:
                z += f["height"]
                rows.append([f["name"], round(z, 3), f["column"], f["beam"]])
            s.log(f"OK: {model['main_storeys']} main storeys; {len(model['floors']) - model['main_storeys']} headroom; top {z:.2f} m. {model['layout_kind']}.")
            s.log("Sections are trial inputs. Loads, reinforcement, diaphragms, meshing and design are not established.")
            s.save(("Storey", "Top elevation (m)", "Column (m)", "Beam width/depth (m)"), rows,
                   ["Geometry and trial section review only; not a structural design."])
        except Exception as ex:
            s.sp = None
            s.log("Specification problem:", ex)

    def make(s):
        # Always review the live editor, never a stale previously accepted object.
        s.review()
        if not s.sp:
            return
        if not messagebox.askyesno("Create new model", "This replaces the model in the connected ETABS session. Save any work first. Continue with the reviewed geometry and trial sections?"):
            return
        try:
            result = s.e.build(s.sp)
            s.log("Geometry created:", result)
            s.log("Save under a new filename. Set loads, diaphragms, meshing, reinforcement and analysis/design settings before analysis.")
        except Exception as ex:
            s.log("BUILD STOPPED:", ex)

# ---------------- 18 Python + MCP ----------------
class PyMCP(Page):
    key = "Python and MCP"; title = "Python + MCP"; sub = "Run local Python and an MCP server so Claude Desktop (or any MCP client) can read and drive the connected ETABS model. Claude web copy/paste remains in AI Building Generator."
    def build(s):
        here = os.path.dirname(os.path.abspath(__file__))
        if getattr(sys, "frozen", False):
            # PyInstaller places __file__ under dist\\...\\_internal. Prefer the
            # source project beside dist so the existing root-level server is found.
            exe_dir = os.path.dirname(sys.executable)
            project_dir = os.path.dirname(os.path.dirname(exe_dir))
            candidates = (os.path.join(project_dir, "etabs_mcp_server.py"),
                          os.path.join(exe_dir, "etabs_mcp_server.py"))
            here = next((os.path.dirname(p) for p in candidates if os.path.isfile(p)), project_dir)
        server = os.path.join(here, "etabs_mcp_server.py")
        # Prefer sys.executable so the MCP server always uses the same Python
        # as the running app. The old hardcoded path only existed on one developer's machine.
        python = sys.executable if not getattr(sys, "frozen", False) else "python"
        s.py, s.srv, s.scr = V(python), V(server), V("")
        for i, (l, v) in enumerate((("Python executable", s.py), ("MCP server (.py)", s.srv), ("Python script (.py)", s.scr))):
            f = ttk.Frame(s.main); f.pack(fill="x"); ttk.Label(f, text=l, width=20).pack(side="left"); ttk.Entry(f, textvariable=v, width=80).pack(side="left"); ttk.Button(f, text="Browse...", command=lambda v=v, l=l: s.browse(v, l)).pack(side="left")
        bar(s.main, ("Install dependencies", lambda: s.sh([s.py.get(), "-m", "pip", "install", "-r", os.path.join(here, "requirements.txt")])), ("Check setup", s.check), ("Start MCP", s.start), ("Stop", s.stop), ("Run Python file", lambda: s.sh([s.py.get(), s.scr.get()])), ("Clear log", lambda: s.lg.delete("1.0", "end")))
        ttk.Label(s.main, text="MCP tools appear below. Enter a tool name and its JSON arguments, then Call tool.").pack(anchor="w"); f = ttk.Frame(s.main); f.pack(fill="x")
        s.tn, s.ta = V("get_model_info"), V("{}"); ttk.Entry(f, textvariable=s.tn, width=24).pack(side="left"); ttk.Entry(f, textvariable=s.ta, width=50).pack(side="left"); ttk.Button(f, text="Call tool", command=s.call).pack(side="left", padx=4)
        s.lg = textbox(s.main, 16); s.q = queue.Queue(); s.proc = None; s.after(300, s.poll)
    def browse(s, variable, label):
        if label == "Python executable":
            path = filedialog.askopenfilename(filetypes=[("Python executable", "python.exe"), ("All files", "*.*")])
        else:
            path = filedialog.askopenfilename(filetypes=[("Python files", "*.py"), ("All files", "*.*")])
        if path: variable.set(path)
    def poll(s):
        try:
            while True: s.lg.insert("end", s.q.get_nowait()); s.lg.see("end")
        except queue.Empty: pass
        s.after(300, s.poll)
    def sh(s, cmd):
        def w():
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True); s.proc = p
            for ln in p.stdout: s.q.put(ln)
            s.q.put(f"Process exited (code {p.wait()}).\n")
        threading.Thread(target=w, daemon=True).start()
    def check(s):
        code = "import sys, importlib.util; print('Python ' + sys.version.split()[0]); [print('  %s: %s' % (m, 'installed' if importlib.util.find_spec(m) else 'MISSING')) for m in ('comtypes','mcp','matplotlib','anthropic','openpyxl','ezdxf','numpy','openseespy')]"
        s.sh([s.py.get(), "-c", code])
    def start(s):
        if not os.path.isfile(s.srv.get()):
            s.q.put(f"MCP server file not found: {s.srv.get()}\n")
            return
        s.sh([s.py.get(), s.srv.get()]); s.q.put("Add to claude_desktop_config.json:\n" + json.dumps({"mcpServers": {"etabs": {"command": s.py.get(), "args": [s.srv.get()]}}}, indent=2) + "\n")
    def stop(s):
        if s.proc and s.proc.poll() is None: s.proc.terminate(); s.q.put("Stopped.\n")
    def call(s):
        try:
            import etabs_mcp_server as srv; srv.ETABS = s.e; r = srv.TOOLS[s.tn.get()](**json.loads(s.ta.get() or "{}")); s.q.put(json.dumps(r, indent=1, default=str) + "\n")
        except Exception as ex: s.q.put(f"ERROR: {ex!r}\n")

# ---------------- Reports ----------------
ORDER = ["Automatic Code Checks", "P-Delta", "Mass Source and Loads", "Modal Case", "Modal Analysis Check", "Time Period", "Principal Axes", "Response Spectrum", "Pushover Analysis", "Irregularity Check",
         "Drift and Displacement", "Base Shear Share", "Wall Density", "Load Patterns", "Load Combinations", "Detailing", "AI Building Generator"]
class Reports(Page):
    key = "Reports"; title = "Reports"; sub = "One document from the checks that have been run, under your title block. Gathers whichever of the steps above have been run."
    def build(s):
        nb=ttk.Notebook(s.main); nb.pack(fill="both",expand=True)
        rp, cp, bp=(ttk.Frame(nb),ttk.Frame(nb),ttk.Frame(nb))
        nb.add(rp,text="Report"); nb.add(cp,text="3D Concrete Frame"); nb.add(bp,text="3D Rebar")
        ttk.Label(rp, text="Tick the checks to include. Outputs are session records; a preview or setup operation is not a passing analysis/design result.").pack(anchor="w")
        s.t = Table(rp, ("Include", "Check", "Output / action status"), [70, 260, 560], toggle=("Include",), height=10); s.t.pack(fill="x"); s.n = ttk.Label(rp); s.n.pack(anchor="w")
        bar(rp, ("Refresh", s.refresh), ("Select All", lambda: s.setall("Yes")), ("Select None", lambda: s.setall("No")))
        g = ttk.Frame(rp); g.pack(anchor="w"); s.dt = {k: V("Summary") for k in ("Modal Analysis Check", "Principal Axes", "Irregularity Check", "Drift and Displacement")}
        for i, (k, v) in enumerate(s.dt.items()): entry(g, k + " detail", v, 9, ["Summary", "Detailed"], r=i)
        s.pp, s.po = V("A4"), V("Portrait"); entry(g, "Paper", s.pp, 5, ["A4", "A3", "Letter"], r=0, c=3); entry(g, "Orientation", s.po, 8, ["Portrait", "Landscape"], r=1, c=3)
        bar(rp, ("Title Block...", lambda: __import__("ui") and TitleBlockProxy(s)), ("View Report", s.view), ("Save Report...", s.savef))
        bar(cp,("Refresh 3D Concrete View",lambda:s.run(s.concrete)))
        ttk.Label(cp,text="Section-sized frame preview from the connected ETABS model. Area objects such as slabs and walls are not shown.",wraplength=1000).pack(anchor="w")
        s.cfig,s.ccanvas=make_plot(cp,5.2)
        bar(bp,("Refresh 3D Rebar View",lambda:s.run(s.rebar)))
        ttk.Label(bp,text="Draft beam longitudinal bars from the Detailing page. Run Detailing after concrete design; columns, stirrups, anchorage, curtailment and fabrication details are not represented.",wraplength=1000).pack(anchor="w")
        s.bfig,s.bcanvas=make_plot(bp,5.2); s.refresh()
    def concrete(s):
        core.plot_concrete_frame(s.cfig,s.e)
        if s.ccanvas: s.ccanvas.draw()
        s.log("Refreshed concrete frame preview. Slabs and walls are excluded from this view.")
    def rebar(s):
        layouts=s.app.state.get("beam_rebar_layouts",[])
        if not layouts: raise ValueError("Run the Detailing page after concrete frame design to generate beam reinforcement layouts.")
        core.plot_rebar_frame(s.bfig,s.e,layouts)
        if s.bcanvas: s.bcanvas.draw()
        s.log(f"Refreshed draft reinforcement view for {len(layouts)} detailed beams.")
    def refresh(s):
        R = s.app.results; s.t.set([["Yes" if k in R else "No", k, "Yes" if k in R else "not run"] for k in ORDER]); s.n.config(text=f"{sum(k in R for k in ORDER)} of {len(ORDER)} checks have been run and can be written.")
    def setall(s, v): s.t.set([[v] + r[1:] for r in s.t.rows()])
    def build_html(s):
        inc = [r[1] for r in s.t.rows() if r[0] == "Yes"]; return core.report_html(s.app.cfg.get("title", {}), s.app.results, inc, {k: v.get() for k, v in s.dt.items()}, s.pp.get(), s.po.get())
    def view(s):
        p = os.path.join(tempfile.gettempdir(), "ai_structural_report.html"); open(p, "w", encoding="utf-8").write(s.build_html()); webbrowser.open(p)
    def savef(s):
        p = filedialog.asksaveasfilename(defaultextension=".html")
        if p: open(p, "w", encoding="utf-8").write(s.build_html()); s.log("Saved", p)

def TitleBlockProxy(page): messagebox.showinfo("Title block", "Use 'Report settings' at the bottom of the sidebar.")

PAGES2 = [[("10", "Torsion Check", Torsion), ("11", "Drift & Displacement", Drift), ("12", "Base Shear", BaseShear), ("13", "Wall Density", WallDensity)],
          [("14", "Load Patterns", LoadPatterns), ("15", "Load Combinations", Combos)], [("16", "Detailing", Detailing)],
          [("17", "AI Building Generator", AIGen), ("18", "Python + MCP", PyMCP)], [("R", "Reports", Reports)]]
