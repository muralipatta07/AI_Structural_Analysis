"""AI Structural Analysis - ETABS seismic / design toolkit (IS 1893:2016, IS 16700, IS 456, IS 13920).
Run:  python app.py        (Windows + ETABS 21/22, pip install -r requirements.txt)
A design aid only - every result must be checked by a qualified structural engineer."""
import os, sys, json, csv, math, webbrowser
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import core
from ui import *
from pages2 import PAGES2, Pushover

# ---------------- 1 Automatic code checks ----------------
NOT_ESTABLISHED = ["Seismic weight and imposed-load fractions", "Zone, I, R, soil and seismic-system applicability",
    "Static/dynamic base-shear matching and scaling", "Plan/vertical irregularities and diaphragm checks",
    "Column 20db joint dimension and beam bar anchorage", "Beam supplied steel, end links and splices",
    "Column axial-stress cap for governing combinations", "Strong-column weak-beam joint capacity",
    "Joint shear and confinement classification", "Wall/boundary-element/coupling-beam provisions",
    "Exposure/fire cover and crack widths", "Long-term beam/slab deflection", "Foundations, soil and construction quality"]

class AutoChecks(Page):
    key = "Automatic Code Checks"; title = "Automatic Code Checks"
    sub = ("Storey drift to IS 1893 (Part 1):2016 cl. 7.11.1.1, with an optional check on total displacement. Read the connected ETABS model and existing "
           "results in one click. Each OK applies only to its named check. Missing data or unverified applicability is NOT CHECKED. This panel does not run "
           "analysis, change sections or certify building safety.")
    H = ("Member / case", "Check", "Value", "Limit", "Status", "Clause", "Evidence / limitation")
    def build(s):
        s.c1, s.c2 = tk.BooleanVar(), tk.BooleanVar(); s.data = []
        ttk.Checkbutton(s.main, text="Confirm all RC frame beams/columns reviewed here are subject to IS13920 ductile-frame geometry provisions", variable=s.c1).pack(anchor="w")
        ttk.Checkbutton(s.main, text="Record confirmation: drift action levels reviewed separately (not checked on this page)", variable=s.c2).pack(anchor="w")
        b = bar(s.main, ("Read ETABS + Check All", lambda: s.run(s.go)), ("Export CSV", s.export))
        s.flt = V("All results"); ttk.Combobox(b, textvariable=s.flt, values=["All results", "OK", "NOT OK", "NOT CHECKED"], width=14).pack(side="left")
        s.flt.trace_add("write", lambda *a: s.show())
        s.sm = ttk.Label(s.main, text="Run analysis and concrete design in ETABS first for result-based checks. Geometry can be read without results."); s.sm.pack(anchor="w")
        s.tab = Table(s.main, s.H, [120, 190, 80, 80, 100, 100, 480]); s.tab.pack(fill="both", expand=True)
    def geo(s, f, col):
        if not s.c1.get(): return "NOT CHECKED"
        a, b = sorted((f["t2"] * 1000, f["t3"] * 1000))
        if col: return "OK" if a >= 300 and a / b >= .4 else "NOT OK"
        return "OK" if f["t2"] * 1000 >= 200 and f["t2"] / f["t3"] >= .3 else "NOT OK"
    def go(s):
        m, e, D = s.e.m, s.e, []
        code = e.try_(lambda: m.DesignConcrete.GetCode()[0], "unknown")
        D.append(["Model", "Concrete design-code setting", code, "IS456", "OK" if "456" in str(code) else "NOT OK", "Design basis", "Only identifies the configured code. IS13920 detailing and model-specific applicability are separate checks."])
        lk = bool(e.try_(m.GetModelIsLocked, False))
        D.append(["Model", "Analysis/design result freshness", "Locked" if lk else "Unlocked", "Locked", "OK" if lk else "NOT CHECKED", "Analysis prerequisite", "Model is unlocked; result-based checks are skipped. Run analysis and design first."])
        try:
            sq = core._seqs(m.Analyze.GetCaseStatus()); nm = {1: "Not run", 2: "Could not start", 3: "Not finished", 4: "Finished"}
            for n, st in zip(sq[0], sq[1]):
                D.append([n, "Analysis case status", nm.get(int(st), st), "Finished", "NOT CHECKED" if int(st) == 4 else "NOT OK", "Analysis prerequisite", "A finished case does not establish correct loads or combinations."])
        except Exception as ex: s.log("case status:", ex)
        for f in e.frame_info():
            col, beam = f["ori"] == 1, f["ori"] == 2
            if not (col or beam): continue
            n, p = f["name"], f["prop"]; dims = f"{f['t2']*1000:.0f}x{f['t3']*1000:.0f}"
            D.append([n, "Concrete design results", "—", "—", "NOT CHECKED", "IS456", "Requires locked analysed model and IS456 design results. Read them in the Detailing page."])
            D.append([n, "Ductile geometry applicability", "—", "—", s.geo(f, col), "IS13920", "Confirm that the concrete beams/columns checked are governed by IS13920 ductile-frame geometry provisions."])
            if f["mod"]:
                lim = .70 if col else .35
                for i, nmod in ((4, "I22"), (5, "I33")):
                    v = f["mod"][i]; D.append([n, f"Effective {nmod} modifier", f"{v:.2f}", f"{lim:.2f}", "OK" if v <= lim + 1e-6 else "NOT OK", "IS1893 6.4.3", f"ETABS frame {n}, section {p}; dimensions in mm. Object {dims}."])
        for x in NOT_ESTABLISHED: D.append(["Coverage", x, "—", "—", "NOT CHECKED", "IS456 / IS13920 / IS1893", "Not established by this automatic reader. Use the specialist module or a manual check."])
        s.data = D; s.show()
        c = lambda k: sum(r[4] == k for r in D)
        s.sm.config(text=f"OK: {c('OK')} | NOT OK: {c('NOT OK')} | NOT CHECKED: {c('NOT CHECKED')} | Results are a snapshot; rerun after model changes.")
        s.save(s.H, D)
    def show(s):
        f = s.flt.get(); s.tab.set([r for r in s.data if f == "All results" or r[4] == f])
    def export(s):
        p = filedialog.asksaveasfilename(defaultextension=".csv")
        if p:
            with open(p, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows([s.H] + s.data)

# ---------------- 2 P-Delta ----------------
class PDelta(Page):
    key = "P-Delta"; title = "P-Delta"; sub = "PRESET P-DELTA OPTIONS - ITERATIVE BASED ON LOADS. Prepares an ETABS .e2k text copy; import it in ETABS (File > Import > ETABS .e2k). Save your model first; analysis results must be regenerated."
    def build(s):
        b = bar(s.main, ("Open complete E2K", s.open_e2k), ("Export / read connected model", lambda: s.run(s.read)), ("Gravity defaults", s.defaults))
        s.tol = V("0.000100"); ttk.Label(b, text="Relative tolerance").pack(side="left"); ttk.Entry(b, textvariable=s.tol, width=10).pack(side="left")
        s.t = Table(s.main, ("Include", "Load pattern", "Scale factor", "Pattern type"), [70, 220, 100, 220], edit=("Scale factor",), toggle=("Include",), height=14); s.t.pack(fill="both", expand=True)
        s.rest = tk.BooleanVar(); ttk.Checkbutton(s.main, text="Return cases linked to JP_PDELTA to preset stiffness in the exported copy", variable=s.rest).pack(anchor="w")
        bar(s.main, ("Prepare Preset Update (.e2k)", s.prepare)); s.e2k = None
    def sf(s, typ): return {"Dead": 1.2, "Super Dead": 1.2, "Live": .5, "Reducible Live": .5, "Seismic": 1.5}.get(typ, 1)
    def read(s):
        m = s.e.m; names = core._seqs(m.LoadPatterns.GetNameList())[0]; inv = {v: k for k, v in core.LOAD_TYPES.items()}; rows = []
        for n in names:
            typ = inv.get(s.e.try_(lambda: m.LoadPatterns.GetLoadType(n)[0], 8), "Other")
            rows.append(["Yes" if typ in ("Dead", "Super Dead", "Live", "Reducible Live", "Seismic") else "No", n, s.sf(typ), typ])
        s.t.set(rows); s.log(f"Preset method: ITERATIVE; {sum(r[0]=='Yes' for r in rows)} included patterns. No changes made to the live ETABS model.")
    def defaults(s): s.t.set([[r[0], r[1], s.sf(r[3]), r[3]] for r in s.t.rows()])
    def prepare(s):
        p = filedialog.asksaveasfilename(defaultextension=".e2k", initialfile="PDelta_preset.e2k")
        if not p: return
        L = ["$ P-DELTA PRESET (iterative based on loads) - generated by AI Structural Analysis",
             "$ TEMPLATE: compare the keywords with an .e2k exported from your ETABS version before importing.",
             f'  PDELTA  METHOD "ITERATIVE"  TOLERANCE {s.tol.get()}']
        L += [f'  PDELTA  LOADPATTERN "{r[1]}"  SCALEFACTOR {r[2]}' for r in s.t.rows() if r[0] == "Yes"]
        open(p, "w").write("\n".join(L)); s.e2k = p; s.log("Wrote", p)
    def open_e2k(s):
        p = s.e2k or filedialog.askopenfilename(filetypes=[("E2K", "*.e2k")])
        if p: os.startfile(p) if hasattr(os, "startfile") else webbrowser.open(p)

# ---------------- 3 Mass & loads ----------------
class Mass(Page):
    key = "Mass Source and Loads"; title = "Mass Source & Eccentricity"; sub = "Mass source (cl. 7.3), design eccentricity per floor and diaphragm (cl. 7.8.2), and vertical seismic. Start here - everything dynamic rests on the mass being right."
    def build(s):
        bar(s.main, ("Read Model", lambda: s.run(s.read)), ("Copy API diagnostics", s.diag))
        nb = ttk.Notebook(s.main); nb.pack(fill="both", expand=True); a, b, c = (ttk.Frame(nb) for _ in range(3))
        for f, t in ((a, "Mass Source"), (b, "Design Eccentricity"), (c, "Vertical Seismic")): nb.add(f, text=t)
        g = ttk.Frame(a); g.pack(anchor="w"); s.name = V("MASS SOURCE"); s.dflt = tk.BooleanVar(value=True)
        entry(g, "Mass source name", s.name, 22); ttk.Checkbutton(g, text="Use as the default mass source", variable=s.dflt).grid(row=0, column=3)
        s.opt = {k: tk.BooleanVar(value=v) for k, v in (("Element self mass", 0), ("Additional mass", 0), ("Specified load patterns", 1), ("Include lateral mass", 1), ("Include vertical mass", 1), ("Lump lateral mass at storey levels", 1))}
        for i, (k, v) in enumerate(s.opt.items()): ttk.Checkbutton(g, text=k, variable=v).grid(row=1 + i // 3, column=i % 3, sticky="w", padx=6)
        s.lp = Table(a, ("Use", "Load pattern", "Type", "Multiplier"), [50, 180, 110, 90], edit=("Multiplier",), toggle=("Use",), height=9); s.lp.pack(fill="both", expand=True)
        bar(a, ("Apply to ETABS", lambda: s.run(s.apply)), ("Default loads", s.defaults), ("Show guidance", lambda: messagebox.showinfo("Guidance", "IS 1893 cl. 7.3.1: full dead + super-dead; imposed load 25% when <= 3 kN/m2, 50% when > 3 kN/m2. No roof live load.")))
        s.ecc = V("0.05"); entry(b, "Design eccentricity ratio (x plan dimension)", s.ecc, 6); ttk.Button(b, text="Compute per floor", command=lambda: s.run(s.eccent)).grid(row=0, column=2)
        s.et = Table(b, ("Level", "b_x (m)", "b_y (m)", "e_x (m)", "e_y (m)"), [120, 90, 90, 90, 90]); s.et.grid(row=1, column=0, columnspan=4, sticky="nsew")
        s.vz, s.vi, s.vr = V("IV"), V("1.0"), V("5.0"); entry(c, "Zone", s.vz, 5, list(core.ZONE)); entry(c, "I", s.vi, 5, r=1); entry(c, "R", s.vr, 5, r=2)
        ttk.Button(c, text="Compute Av", command=s.vert).grid(row=3, column=1); s.vt = ttk.Label(c, text=""); s.vt.grid(row=4, column=0, columnspan=3)
    def defaults(s):
        out = []
        for r in s.lp.rows():
            n, t = r[1], r[2]; k = 1 if t in ("Dead", "Super Dead") else (.5 if ">" in n else .25) if t.startswith("Live") or "Live" in n else 0
            out.append(["Yes" if k else "No", n, t, k])
        s.lp.set(out)
    def read(s):
        m = s.e.m; inv = {v: k for k, v in core.LOAD_TYPES.items()}
        rows = [["No", n, inv.get(s.e.try_(lambda: m.LoadPatterns.GetLoadType(n)[0], 8), "Other"), 0] for n in core._seqs(m.LoadPatterns.GetNameList())[0]]
        s.lp.set(rows); s.defaults(); s.eccent()
    def apply(s):
        U = [r for r in s.lp.rows() if r[0] == "Yes"]; o = s.opt
        r = s.e.m.SourceMass.SetMassSource(s.name.get(), o["Element self mass"].get(), o["Additional mass"].get(), True, s.dflt.get(), len(U), [x[1] for x in U], [float(x[3]) for x in U])
        s.log("SetMassSource ->", r); s.save(("Load pattern", "Type", "Multiplier"), [[x[1], x[2], x[3]] for x in U], ["Mass source: " + s.name.get()])
    def eccent(s):
        P = s.e.points(); lv = {}
        for x, y, z in P.values(): lv.setdefault(round(z, 3), []).append((x, y))
        k = float(s.ecc.get()); rows = []
        for z, pts in sorted(lv.items()):
            bx = max(p[0] for p in pts) - min(p[0] for p in pts); by = max(p[1] for p in pts) - min(p[1] for p in pts)
            rows.append([f"Z = {z:g}", f"{bx:.2f}", f"{by:.2f}", f"{k*bx:.3f}", f"{k*by:.3f}"])
        s.et.set(rows)
    def vert(s): s.vt.config(text=f"Av = (2/3)(Z/2)(I/R)(Sa/g) = {core.av(s.vz.get(), float(s.vi.get()), float(s.vr.get())):.4f}  (Sa/g = 2.5, cl. 6.4.6)")
    def diag(s):
        s.clipboard_clear(); s.clipboard_append(f"connected={bool(s.e.m)} file={s.e.try_(lambda: s.e.m.GetModelFilename())}"); s.log("Diagnostics copied")

# ---------------- 4 Modal setup ----------------
class ModalSetup(Page):
    key = "Modal Case"; title = "Modal Case Definition"; sub = "Define or modify the modal load case with the advanced options of ETABS (Eigen / Ritz, loads, targets, shifts), then run and check it. Needs the mass source from step 3."
    def build(s):
        bar(s.main, ("Read from ETABS", lambda: s.run(s.read)), ("Suggest IS 1893 settings", lambda: s.run(s.suggest)), ("Write to ETABS", lambda: s.run(s.write)),
            ("Write, run & check", lambda: s.run(lambda: s.write(True))), ("Check results only", lambda: s.run(s.check)))
        g = ttk.Frame(s.main); g.pack(anchor="w"); s.nm, s.st, s.mx, s.mn, s.fs, s.cf, s.tol = V("Modal"), V("Eigen"), V(24), V(1), V(0), V(0), V("1e-9")
        entry(g, "Load case name", s.nm, 14); entry(g, "Subtype", s.st, 8, ["Eigen", "Ritz"], c=2); entry(g, "Max modes", s.mx, r=1); entry(g, "Min modes", s.mn, r=1, c=2)
        entry(g, "Frequency shift (cyc/s)", s.fs, r=2); entry(g, "Cutoff frequency", s.cf, r=2, c=2); entry(g, "Convergence tol.", s.tol, r=3)
        s.ld = Table(s.main, ("Load type", "Load name", "Target mass par.", "Static corr."), [110, 110, 120, 90], edit=("Load name", "Target mass par."), toggle=("Static corr.",), height=4); s.ld.pack(fill="x")
        s.ld.set([["Acceleration", n, 99, "Yes"] for n in ("UX", "UY", "RZ")])
        s.pt = Table(s.main, ("Mode", "Period (s)", "UX", "UY", "RZ", "ΣUX", "ΣUY", "ΣRZ", "Dominant"), [50, 80, 70, 70, 70, 70, 70, 70, 80], height=10); s.pt.pack(fill="both", expand=True)
    def read(s):
        r = s.e.m.LoadCases.ModalEigen.GetNumberModes(s.nm.get()); s.mx.set(r[0]); s.mn.set(r[1]); s.log("Read", s.nm.get(), r)
    def suggest(s):
        n = len(core.levels(s.e)) - 1; s.mx.set(max(12, 3 * n)); s.ld.set([["Acceleration", k, 99, "Yes"] for k in ("UX", "UY", "RZ")])
        s.log(f"Suggested: {s.mx.get()} modes (3 per storey, at least 12), Eigen with UX, UY, RZ targets of 99 % and static (missing-mass) correction. Check after the run that ΣUX and ΣUY reach 90 % (cl. 7.7.5.2); if not, raise the mode count or use Ritz vectors.")
    def write(s, run=False):
        ME = s.e.m.LoadCases.ModalEigen; nm = s.nm.get(); L = s.ld.rows()
        ME.SetCase(nm); ME.SetNumberModes(nm, int(s.mx.get()), int(s.mn.get()))
        ME.SetLoads(nm, len(L), [x[0] for x in L], [x[1] for x in L], [float(x[2]) for x in L], [1 if x[3] == "Yes" else 0 for x in L])
        ME.SetParameters(nm, float(s.fs.get()), float(s.cf.get()), float(s.tol.get()), True); s.log("Modal case written.")
        if run: s.e.run_analysis(); s.check()
    def check(s):
        S = core.modal_summary(s.e, s.nm.get()); s.app.state["modal"] = S; rows = []
        for i, t in enumerate(S["T"]):
            d = max(zip((S["ux"][i], S["uy"][i], S["rz"][i]), ("UX", "UY", "RZ")))[1]
            rows.append([i + 1, f"{t:.3f}", f"{S['ux'][i]*100:.1f}", f"{S['uy'][i]*100:.1f}", f"{S['rz'][i]*100:.1f}", f"{S['sx'][i]*100:.1f}", f"{S['sy'][i]*100:.1f}", f"{S['sz'][i]*100:.1f}", d])
        s.pt.set(rows); ok = lambda v: "OK" if v >= .9 else "NOT OK"
        s.log(f"ΣUX = {S['sx'][-1]*100:.1f}% {ok(S['sx'][-1])}, ΣUY = {S['sy'][-1]*100:.1f}% {ok(S['sy'][-1])} (>= 90 %, cl. 7.7.5.2)")
        s.save(("Mode", "Period (s)", "UX %", "UY %", "RZ %", "ΣUX %", "ΣUY %", "ΣRZ %", "Dominant"), rows, [f"Modal case {s.nm.get()}: {s.mx.get()} modes"])

# ---------------- 5 Modal results ----------------
class ModalResults(Page):
    key = "Modal Analysis Check"; title = "Modal Analysis Check"; sub = "Mode shapes, mass participation and the modal criteria of IS 1893 (Part 1):2016 (and IS 16700 above 50 m). Needs the modal case from step 4, analysed."
    CH = ("Check", "Value", "Limit", "Status", "Reference")
    def build(s):
        bar(s.main, ("Load Modal Results", lambda: s.run(s.go)), ("Show mode", lambda: s.run(s.shape)))
        g = ttk.Frame(s.main); g.pack(anchor="w"); s.case, s.zone, s.tors, s.H, s.mode = V("Modal"), V("IV"), V("5.0"), V("29.25"), V("1")
        entry(g, "Modal case", s.case, 10); entry(g, "Seismic zone", s.zone, 4, list(core.ZONE), c=2); entry(g, "Allowable torsion %", s.tors, 5, c=4); entry(g, "Height H (m)", s.H, 6, c=6); entry(g, "Mode", s.mode, 4, c=8)
        s.vd = ttk.Label(s.main, text="", foreground="#b00020"); s.vd.pack(anchor="w"); nb = ttk.Notebook(s.main); nb.pack(fill="both", expand=True); T = {}
        for t in ("Mode Shape", "Mass Participation", "IS 1893 Checks", "Modal Data", "Warnings", "Results"):
            T[t] = ttk.Frame(nb); nb.add(T[t], text=t)
        a, b = ttk.Frame(T["Mode Shape"]), ttk.Frame(T["Mode Shape"]); a.pack(side="left", fill="both", expand=True); b.pack(side="left", fill="both", expand=True)
        s.fi, s.ci = make_plot(a); s.fp, s.cp = make_plot(b)
        s.mp = Table(T["Mass Participation"], ("Mode", "Period (s)", "UX %", "UY %", "RZ %", "ΣUX %", "ΣUY %", "ΣRZ %"), [60] + [90] * 7); s.mp.pack(fill="both", expand=True)
        s.ck = Table(T["IS 1893 Checks"], s.CH, [330, 330, 170, 70, 420]); s.ck.pack(fill="both", expand=True)
        s.md = Table(T["Modal Data"], ("Mode", "T (s)", "f (Hz)", "ω (rad/s)", "λ=ω² (1/s²)"), [60, 90, 90, 100, 110]); s.md.pack(fill="both", expand=True)
        s.wn = textbox(T["Warnings"]); s.rs = textbox(T["Results"])
    def shape(s):
        k = int(s.mode.get())
        if s.fi: core.plot_model(s.fi, s.e, s.case.get(), k, "iso"); s.ci.draw(); core.plot_model(s.fp, s.e, s.case.get(), k, "plan"); s.cp.draw()
    def go(s):
        S = core.modal_summary(s.e, s.case.get() or None); s.app.state["modal"] = S; n = len(S["T"]); T, ux, uy, rz = S["T"], S["ux"], S["uy"], S["rz"]
        tors, H, zone, R = float(s.tors.get()), float(s.H.get()), s.zone.get(), []
        for k in (0, 1):
            if k < n:
                sh = rz[k] / max(ux[k] + uy[k] + rz[k], 1e-9) * 100
                R.append([f"Torsion in mode {k+1} (T = {T[k]:.3f} s)", f"{sh:.1f} % rotational", f"<= {tors:g} %", "OK" if sh <= tors else "NOT OK", "User-set limit. IS 1893 (Part 1):2016 sets no explicit limit; a torsional first or second mode indicates torsional irregularity (Table 5) and is to be avoided."])
        for d, v in (("X", ux), ("Y", uy)):
            ids = sorted(range(n), key=lambda i: -v[i])[:3]; tot = sum(v[i] for i in ids) * 100
            R.append([f"First three lateral translational modes along {d}", f"{tot:.1f} % (modes {', '.join(str(i+1) for i in ids)})", ">= 65.0 %", "OK" if tot >= 65 else "NOT OK", "IS 1893 (Part 1):2016, Table 6 (vii), as amended by Amendment No. 2 (Nov 2020)."])
        Tx, Ty = T[ux.index(max(ux))], T[uy.index(max(uy))]; sep = abs(Tx - Ty) / max(Tx, Ty) * 100
        R.append(["Separation of the two fundamental translational periods", f"Tx = {Tx:.3f} s, Ty = {Ty:.3f} s -> {sep:.1f} % apart", ">= 10.0 % of the larger", "OK" if (sep >= 10 or zone not in ("IV", "V")) else "NOT OK", "IS 1893 (Part 1):2016, Table 6 (vii)(b); mandatory in Seismic Zones IV and V."])
        for d, k in (("X", "sx"), ("Y", "sy")):
            v = S[k][-1] * 100; R.append([f"Total translational mass participation along {d}", f"{v:.1f} % over {n} modes", ">= 90.0 %", "OK" if v >= 90 else "NOT OK", "IS 1893 (Part 1):2016, cl. 7.7.5.2."])
        i3 = rz.index(max(rz)); ratio = T[i3] / min(Tx, Ty)
        R.append(["Fundamental torsional period against translational", f"Tθ = {T[i3]:.3f} s (mode {i3+1}), Tθ / min(Tx, Ty) = {ratio:.2f}", f">= {core.IS16700_TORS_RATIO:.2f} (IS 16700, tall buildings)", "OK" if (H <= 50 or ratio >= core.IS16700_TORS_RATIO) else "NOT OK", f"IS 16700 applies to buildings taller than 50 m; this one is {H:g} m, so the ratio is shown for information only." if H <= 50 else "IS 16700 applies - verify the clause limit."])
        s.ck.set(R); bad = [r for r in R if r[3] == "NOT OK"]
        s.vd.config(text=f"✗ One or more IS 1893 checks are NOT satisfied. ({len(bad)} warnings -- see the Warnings tab)" if bad else "✓ All modal checks satisfied.")
        put(s.wn, "\n".join(f"{r[0]}: {r[1]} (limit {r[2]})" for r in bad) or "No warnings."); put(s.rs, "\n".join(f"{r[3]:7} {r[0]}: {r[1]}" for r in R))
        s.mp.set([[i + 1, f"{T[i]:.3f}", f"{ux[i]*100:.1f}", f"{uy[i]*100:.1f}", f"{rz[i]*100:.1f}", f"{S['sx'][i]*100:.1f}", f"{S['sy'][i]*100:.1f}", f"{S['sz'][i]*100:.1f}"] for i in range(n)])
        s.md.set([[i + 1, f"{T[i]:.3f}", f"{1/T[i]:.3f}", f"{2*math.pi/T[i]:.3f}", f"{(2*math.pi/T[i])**2:.2f}"] for i in range(n)])
        s.save(s.CH, R, [f"Modal case {s.case.get()}, {n} modes, T = {T[0]:.3f} to {T[-1]:.3f} s, Seismic zone {zone}."]); s.shape()

# ---------------- 6 Time period ----------------
class TimePeriod(Page):
    key = "Time Period"; title = "Fundamental Time Period"; sub = "Approximate fundamental period to IS 1893 (Part 1):2016 cl. 7.6, and IS 16700 cl. 6.3.4 above 50 m. Needs only the geometry; compare it with the modal periods from step 5."
    def build(s):
        bar(s.main, ("Load Model Data", lambda: s.run(s.load)), ("Calculate", s.calc), ("Analyse & Lock Model", lambda: s.run(lambda: (s.e.run_analysis(), s.log("Analysed and locked.")))),
            ("Unlock Model", lambda: s.run(lambda: (s.e.m.SetModelIsLocked(False), s.log("Unlocked.")))))
        g = ttk.Frame(s.main); g.pack(anchor="w"); s.base, s.top, s.pl, s.h, s.dx, s.dy = V("Base"), V(""), V("Base"), V("0"), V("0"), V("0")
        s.cb = [entry(g, "Base level", s.base, 10, []), entry(g, "Top level (roof)", s.top, 10, [], c=2), entry(g, "Plinth level (for d)", s.pl, 10, [], c=4)]
        entry(g, "h = H (m)", s.h, 8, r=1); entry(g, "dx (m)", s.dx, 8, r=1, c=2); entry(g, "dy (m)", s.dy, 8, r=1, c=4)
        s.wl = tk.BooleanVar(value=True); ttk.Checkbutton(g, text="Walls per IS 13920 (t >= 150 mm, Lw/t >= 4) - recommended", variable=s.wl).grid(row=2, column=0, columnspan=4, sticky="w")
        sy = list(core.SYSTEMS); s.sx, s.sy_, s.soil, s.t = V(sy[0]), V(sy[0]), V("II"), V("200"); entry(g, "IS 1893 system, X", s.sx, 40, sy, r=3); entry(g, "Y", s.sy_, 40, sy, r=3, c=2)
        entry(g, "Soil", s.soil, 5, ["I", "II", "III"], r=4); entry(g, "Wall t if unreadable (mm)", s.t, 6, r=4, c=2)
        nb = ttk.Notebook(s.main); nb.pack(fill="both", expand=True); T = {}
        for t in ("Report", "3D View", "Plan & Elevation", "Sa/g Spectrum", "Structural Walls"): T[t] = ttk.Frame(nb); nb.add(T[t], text=t)
        s.rp = textbox(T["Report"]); s.f3, s.c3 = make_plot(T["3D View"]); s.fe, s.ce = make_plot(T["Plan & Elevation"]); s.fs, s.cs = make_plot(T["Sa/g Spectrum"])
        s.wt = Table(T["Structural Walls"], ("Wall", "L (m)", "t (mm)", "Counted"), [150, 90, 90, 90]); s.wt.pack(fill="both", expand=True); s.walls = []; s.Tx = s.Ty = None
    def load(s):
        m = s.e.m
        old_units = m.GetPresentUnits()
        s.walls = []
        s.wall_data_verified = False
        s.wt.set([])
        try:
            ret = m.SetPresentUnits(6)
            if ret != 0:
                raise RuntimeError(f"Could not read geometry in metres: {ret}")
            L = core.levels(s.e)
            if len(L) < 2:
                raise ValueError("The model needs at least one storey above Base.")
            names = [name for name, z in L]
            s.L = dict(L)
            for widget in s.cb:
                widget.configure(values=names)
            if s.base.get() not in s.L:
                s.base.set(names[0])
            if s.pl.get() not in s.L:
                s.pl.set(s.base.get())
            if s.top.get() not in s.L:
                s.top.set(names[-1])
            height = s.L[s.top.get()] - s.L[s.base.get()]
            if height <= 0:
                raise ValueError("Choose a top level above the base level.")
            s.h.set(f"{height:.3f}")
            points = s.e.points()
            z0 = s.L[s.pl.get()]
            floor_points = [(x, y) for x, y, z in points.values() if abs(z-z0) < 0.001]
            if not floor_points:
                raise ValueError("No model joints found at the selected plinth elevation.")
            dx = max(p[0] for p in floor_points) - min(p[0] for p in floor_points)
            dy = max(p[1] for p in floor_points) - min(p[1] for p in floor_points)
            s.dx.set(f"{dx:.3f}")
            s.dy.set(f"{dy:.3f}")
            rows = []
            try:
                raw = m.PierLabel.GetNameList()
                if raw[-1] != 0:
                    raise RuntimeError(f"Pier-label read failed: {raw!r}")
                piers = next((list(v) for v in raw if isinstance(v, (tuple, list))), [])
                if not piers:
                    s.wall_data_verified = True
                    s.log("No pier labels found. No wall contribution included; unlabeled walls are not checked.")
                else:
                    table = s.e.table("Pier Section Properties")
                    required = [("story",), ("width", "bot"), ("thick", "bot"), ("pier",)]
                    if not table.rows or any(table.idx(*tokens) is None for tokens in required):
                        raise RuntimeError("Pier Section Properties has no usable wall fields. Check pier assignments/table availability.")
                    levels_above = [(n,z) for n,z in L if z > z0 + 0.001]
                    if not levels_above:
                        raise RuntimeError("No storey above the selected plinth for wall data.")
                    first = levels_above[0][0]
                    for story, width, thick, name in zip(
                        table.col('story', num=False), table.col('width', 'bot'),
                        table.col('thick', 'bot'), table.col('pier', num=False)):
                        if story == first:
                            use = width > 0 and thick > 0 and ((not s.wl.get()) or (thick*1000 >= 150 and width/thick >= 4))
                            rows.append([name, f"{width:.3f}", f"{thick*1000:.0f}", "Yes" if use else "No"])
                    if not rows:
                        raise RuntimeError("No pier section rows matched the storey above the plinth.")
                    s.wall_data_verified = True
            except Exception as exc:
                rows = []
                s.wall_data_verified = False
                s.log("Wall contribution NOT ESTABLISHED:", exc)
            s.wt.set(rows)
            s.walls = [(float(r[1]), float(r[2])/1000) for r in rows if r[3] == 'Yes']
            s.log(f"Loaded {len(L)-1} storeys: H={height:.3f} m; dx={dx:.3f} m; dy={dy:.3f} m.")
            if s.f3:
                try:
                    core.plot_model(s.f3, s.e, view='iso')
                    s.c3.draw()
                    core.plot_model(s.fe, s.e, view='elev')
                    s.ce.draw()
                except Exception as exc:
                    s.log("Geometry loaded; plot unavailable:", exc)
        finally:
            ret = m.SetPresentUnits(old_units)
            if ret != 0:
                s.log("WARNING: Could not restore original display units:", ret)
    def calc(s):
        if any('wall' in value.casefold() for value in (s.sx.get(), s.sy_.get())) and (not getattr(s, 'wall_data_verified', False) or not s.walls):
            messagebox.showwarning('Time period', 'The selected wall system needs verified wall data. Read usable pier sections or review the structural-system selection.')
            return
        h, dx, dy = float(s.h.get()), float(s.dx.get()), float(s.dy.get())
        if h <= 0 or dx <= 0 or dy <= 0: return messagebox.showwarning("Time period", "Enter h, dx and dy (or use Load Model Data).")
        s.Tx, s.Ty = core.ta(s.sx.get(), h, dx, s.walls), core.ta(s.sy_.get(), h, dy, s.walls); aw = core.wall_aw(s.walls, h)
        L = ["=" * 78, f"APPROXIMATE FUNDAMENTAL NATURAL PERIOD  IS 1893 (Part 1):2016 cl. 7.6.2 (H <= 50 m) | IS 16700 {'applies' if h > 50 else 'not applied'}", "=" * 78,
             f"h = H = {h:.3f} m   d along X (dx) = {dx:.3f} m   d along Y (dy) = {dy:.3f} m   Aw = {aw:.3f} m2", "",
             f"X  {s.sx.get()}\n   Ta,x = {s.Tx:.3f} s", f"Y  {s.sy_.get()}\n   Ta,y = {s.Ty:.3f} s", "",
             f"Design Sa/g (soil {s.soil.get()}):  X {core.sa_g(s.Tx, s.soil.get()):.3f}   Y {core.sa_g(s.Ty, s.soil.get()):.3f}"]
        S = s.app.state.get("modal")
        if S:
            mx, my = S["T"][S["ux"].index(max(S["ux"]))], S["T"][S["uy"].index(max(S["uy"]))]; L += ["", f"Modal periods: Tx = {mx:.3f} s, Ty = {my:.3f} s  (analytical / empirical X = {mx/s.Tx:.2f}, Y = {my/s.Ty:.2f})",
                "IS 1893 cl. 7.7.1.2: design with the analytical period, but not less than the empirical (cl. 7.6.2) in the base-shear scaling check."]
        put(s.rp, "\n".join(L)); s.save(("Direction", "System", "Ta (s)", "Sa/g"), [["X", s.sx.get(), f"{s.Tx:.3f}", f"{core.sa_g(s.Tx, s.soil.get()):.3f}"], ["Y", s.sy_.get(), f"{s.Ty:.3f}", f"{core.sa_g(s.Ty, s.soil.get()):.3f}"]], [f"h = {h:.3f} m, dx = {dx:.3f} m, dy = {dy:.3f} m"])
        if s.fs:
            f = s.fs; f.clear(); ax = f.add_subplot(111)
            for k, c in zip(("I", "II", "III"), ("#888", "#1565c0", "#c62828")): ax.plot([t / 100 for t in range(1, 401)], [core.sa_g(t / 100, k) for t in range(1, 401)], c, label=f"soil {k}")
            ax.axvline(s.Tx, ls="--", c="g"); ax.axvline(s.Ty, ls=":", c="m"); ax.set_xlabel("T (s)"); ax.set_ylabel("Sa/g"); ax.legend(); s.cs.draw()

# ---------------- 7 Principal axes ----------------
class PrincipalAxes(Page):
    key = "Principal Axes"; title = "Principal Axes"; sub = "The angle at which the seismic demand is greatest, by sweeping the direction of incidence (CQC of modal base shears). Needs the modal results from step 5."
    def build(s):
        bar(s.main, ("Load Modal Results", lambda: s.run(s.go)), ("Apply to Case", lambda: s.run(s.apply)), ("New Modal Case...", lambda: messagebox.showinfo("Modal case", "Use step 4 (Modal Setup) to define a modal case.")), ("Run Analysis", lambda: s.run(s.e.run_analysis)))
        g = ttk.Frame(s.main); g.pack(anchor="w"); s.case, s.nm, s.comb, s.dmp, s.step, s.zone, s.soil, s.I, s.R, s.rs = V("Modal"), V(999), V("CQC"), V(.05), V(1.0), V("IV"), V("II"), V(1.0), V(5.0), V("SPECX")
        entry(g, "Modal case", s.case, 10); entry(g, "Modes used", s.nm, 5, c=2); entry(g, "Combination", s.comb, 6, ["CQC"], c=4); entry(g, "Damping", s.dmp, 6, c=6); entry(g, "Sweep step (°)", s.step, 5, c=8)
        entry(g, "Zone", s.zone, 4, list(core.ZONE), r=1); entry(g, "Soil", s.soil, 4, ["I", "II", "III"], r=1, c=2); entry(g, "I", s.I, 5, r=1, c=4); entry(g, "R", s.R, 5, r=1, c=6); entry(g, "RS case to rotate", s.rs, 10, r=1, c=8)
        nb = ttk.Notebook(s.main); nb.pack(fill="both", expand=True); T = {}
        for t in ("Principal Axes", "Reaction vs Angle", "Modal Data", "Results"): T[t] = ttk.Frame(nb); nb.add(T[t], text=t)
        s.pa = textbox(T["Principal Axes"]); s.fg, s.cv = make_plot(T["Reaction vs Angle"]); s.md = Table(T["Modal Data"], ("Mode", "T (s)", "Γx", "Γy", "Eff. mass dir."), [60, 90, 100, 100, 110]); s.md.pack(fill="both", expand=True)
        s.rt = Table(T["Results"], ("Angle (°)", "Base shear (kN)"), [100, 130]); s.rt.pack(fill="both", expand=True); s.best = 0
    def go(s):
        t = s.e.table("Modal Participation Factors").sel("outputcase", s.case.get()); n = min(int(s.nm.get()), len(t.rows))
        T, gx, gy = t.col("period")[:n], t.col("ux")[:n], t.col("uy")[:n]
        out = core.principal_axes(T, gx, gy, s.zone.get(), s.soil.get(), float(s.I.get()), float(s.R.get()), float(s.dmp.get()), float(s.step.get()))
        s.best, vm = max(out, key=lambda r: r[1]); v0, v90 = out[0][1], min(out, key=lambda r: abs(r[0] - 90))[1]
        put(s.pa, f"Principal axis angle = {s.best:.1f}° from global X\nDirectional participation proxy = {vm:.1f} kN\nV(0°) = {v0:.1f} kN   V(90°) = {v90:.1f} kN\nIncrease over X-direction = {(vm/v0-1)*100:.1f} %")
        s.md.set([[i + 1, f"{T[i]:.3f}", f"{gx[i]:.3f}", f"{gy[i]:.3f}", f"{gx[i]**2:.2f}/{gy[i]**2:.2f}"] for i in range(n)]); s.rt.set([[f"{a:.1f}", f"{v:.1f}"] for a, v in out])
        s.save(("Item", "Value"), [["Principal axis (°)", f"{s.best:.1f}"], ["Max base shear (kN)", f"{vm:.1f}"], ["V at 0° (kN)", f"{v0:.1f}"], ["V at 90° (kN)", f"{v90:.1f}"]], ["Sweep of incidence angle, CQC, damping " + s.dmp.get()])
        if s.fg: s.fg.clear(); ax = s.fg.add_subplot(111); ax.plot([a for a, _ in out], [v for _, v in out]); ax.axvline(s.best, ls="--", c="r"); ax.set_xlabel("Angle (°)"); ax.set_ylabel("Base shear (kN)"); s.cv.draw()
    def apply(s):
        nm = s.rs.get(); r = core._seqs(s.e.m.LoadCases.ResponseSpectrum.GetLoads(nm)); k = len(r[0]); s.e.m.LoadCases.ResponseSpectrum.SetLoads(nm, k, r[0], r[1], r[2], r[3], [s.best] * k); s.log(f"Case {nm} rotated to {s.best:.1f}°")

# ---------------- 8 Response spectrum ----------------
class RSpec(Page):
    key = "Response Spectrum"; title = "Response Spectrum & Base-Shear Scaling"; sub = "Response spectrum functions and cases, with base shear scaling to cl. 7.7.3. Needs the modal case (steps 2-3), the periods (step 4) and the angle (step 5) if cases act at the principal axes."
    def build(s):
        bar(s.main, ("Read Model", lambda: s.run(s.read)), ("Write the Function(s)", lambda: s.run(s.write)), ("Write RS cases", lambda: s.run(s.cases)), ("Compute scaling", lambda: s.run(s.scale)))
        g = ttk.Frame(s.main); g.pack(anchor="w"); s.z, s.so, s.I, s.R, s.dm, s.g, s.mc, s.fh, s.fv, s.up, s.sv = V("IV"), V("II"), V(1.0), V(5.0), V(.05), V(core.G), V("Modal"), V("IS1893-H"), V("IS1893-V"), V("100"), V(2.5)
        entry(g, "Zone", s.z, 5, list(core.ZONE)); entry(g, "Soil", s.so, 5, ["I", "II", "III"], c=2); entry(g, "I", s.I, 5, c=4); entry(g, "R", s.R, 5, c=6); entry(g, "Damping", s.dm, 5, r=1); entry(g, "g (model units)", s.g, 9, r=1, c=2); entry(g, "Modal case", s.mc, 9, r=1, c=4)
        entry(g, "Horizontal fn", s.fh, 10, r=2); entry(g, "Vertical fn", s.fv, 10, r=2, c=2); entry(g, "Up to (s)", s.up, 5, r=2, c=4)
        nb = ttk.Notebook(s.main); nb.pack(fill="both", expand=True); a, b = ttk.Frame(nb), ttk.Frame(nb); nb.add(a, text="Functions"); nb.add(b, text="Cases / Scaling")
        s.tb = Table(a, ("T (s)", "Sa/g", "Ah (function, H)", "Av (function, V)"), [80, 80, 130, 130], height=14); s.tb.pack(side="left", fill="y"); pf = ttk.Frame(a); pf.pack(side="left", fill="both", expand=True); s.fg, s.cv = make_plot(pf)
        s.sc = Table(b, ("Direction", "Static Vb (kN)", "Dynamic VB (kN)", "Scale factor", "Status"), [90, 130, 130, 110, 100]); s.sc.pack(fill="x")
        g2 = ttk.Frame(b); g2.pack(anchor="w"); s.sx, s.sy, s.rx, s.ry = V("EQX"), V("EQY"), V("SPECX"), V("SPECY"); entry(g2, "Static X case", s.sx, 8); entry(g2, "Static Y case", s.sy, 8, c=2); entry(g2, "RS X case", s.rx, 8, r=1); entry(g2, "RS Y case", s.ry, 8, r=1, c=2)
        s.draw()
    def draw(s):
        R = core.spectrum(s.z.get(), s.so.get(), float(s.I.get()), float(s.R.get()), float(s.up.get())); s.rows = R; s.tb.set([[f"{t:g}", f"{a:.4f}", f"{b:.4f}", f"{c:.4f}"] for t, a, b, c in R])
        s.save(("T (s)", "Sa/g", "Ah", "Av"), [[f"{t:g}", f"{a:.3f}", f"{b:.4f}", f"{c:.4f}"] for t, a, b, c in R if t in (0, .1, .55, 1, 2, 4)], [f"Zone {s.z.get()}, soil {s.so.get()}, I = {s.I.get()}, R = {s.R.get()}; Ah = (Z/2)(I/R)(Sa/g)"])
        if s.fg: s.fg.clear(); ax = s.fg.add_subplot(111); r = [x for x in R if x[0] <= 4]; ax.plot([x[0] for x in r], [x[2] for x in r], label="Ah"); ax.axhline(R[0][3], ls="--", c="g", label="Av"); ax.legend(); ax.set_xlabel("T (s)"); s.cv.draw()
    def read(s): s.mc.set(s.e.names("case")[0] if s.e.names("case") else "Modal"); s.draw(); s.log("Read model; spectrum recomputed.")
    def write(s):
        s.draw(); fn = s.e.m.Func.FuncRS; pts = [r for r in s.rows]; g = float(s.g.get())
        fn.SetUser(s.fh.get(), len(pts), [r[0] for r in pts], [r[2] for r in pts], float(s.dm.get()))
        fn.SetUser(s.fv.get(), len(pts), [r[0] for r in pts], [r[3] for r in pts], float(s.dm.get())); s.log("Functions written (ordinates are Ah / Av; case scale factor = g).")
    def cases(s):
        RS = s.e.m.LoadCases.ResponseSpectrum
        for nm, fn, ang in ((s.rx.get(), s.fh.get(), 0), (s.ry.get(), s.fh.get(), 90)):
            RS.SetCase(nm); RS.SetModalCase(nm, s.mc.get()); RS.SetLoads(nm, 1, ["U1"], [fn], [float(s.g.get())], ["Global"], [ang]); RS.SetEccentricity(nm, .05)
        s.log("RS cases written.")
    def scale(s):
        t = s.e.table("Base Reactions"); oc, fx, fy = t.col("outputcase", num=False), t.col("globalfx"), t.col("globalfy"); rows = []
        get = lambda c, v: max((abs(x[1]) for x in zip(oc, v) if x[0] == c), default=0)
        for d, st, rs, v in (("X", s.sx.get(), s.rx.get(), fx), ("Y", s.sy.get(), s.ry.get(), fy)):
            a, b = get(st, v), get(rs, v); k = max(1, a / b) if b else 0; rows.append([d, f"{a:.1f}", f"{b:.1f}", f"{k:.3f}", "OK" if k <= 1.0001 else "Scale up"])
        s.sc.set(rows); s.log("IS 1893 cl. 7.7.3: if VB (dynamic) < Vb (static, with the empirical period), scale response by Vb/VB.")

PAGE_DEFS = [("SEISMIC CHECKS", [("1", "Automatic Code Checks", AutoChecks), ("2", "P-Delta", PDelta), ("3", "Mass & Loads", Mass), ("4", "Modal Setup", ModalSetup), ("5", "Modal Results", ModalResults),
             ("6", "Time Period", TimePeriod), ("7", "Principal Axes", PrincipalAxes), ("8", "Response Spectrum", RSpec), ("9", "Pushover Analysis", Pushover)] + PAGES2[0]),
             ("LOAD SETUP", PAGES2[1]), ("REINFORCEMENT", PAGES2[2]), ("AI MODELLING", PAGES2[3]), ("OUTPUT", PAGES2[4])]

# ---------------- title block ----------------
class TitleBlock(tk.Toplevel):
    def __init__(s, app):
        super().__init__(app); s.app = app; s.title("Report title block - optional"); c = app.cfg.get("title", {})
        ttk.Label(s, text="These appear on the report's front page. Every field may be left empty.").grid(row=0, column=0, columnspan=4, padx=8, pady=6)
        s.v = {k: V(c.get(k, "")) for k in ("project", "ref", "client", "designed", "firm", "checked", "approved", "logo")}
        for i, (k, l) in enumerate((("project", "Project name"), ("ref", "Project ref"), ("client", "Client"), ("designed", "Designed by"), ("firm", "of (firm)"), ("checked", "Checked by"), ("approved", "Approved by"), ("logo", "Company logo (PNG/JPEG)"))):
            entry(s, l, s.v[k], 30, r=1 + i)
        ttk.Button(s, text="Choose...", command=lambda: s.v["logo"].set(filedialog.askopenfilename(filetypes=[("Images", "*.png *.jpg *.jpeg")]))).grid(row=8, column=2)
        s.det = tk.Text(s, width=40, height=4); s.det.grid(row=9, column=1, columnspan=2, pady=4); s.det.insert("end", c.get("details", "")); ttk.Label(s, text="Company details").grid(row=9, column=0)
        buttons = ttk.Frame(s)
        buttons.grid(row=10, column=0, columnspan=4, pady=8)
        bar(buttons, ("Save and Continue", s.ok), ("Skip", s.destroy))
        s.grab_set()
    def ok(s):
        d = {k: v.get() for k, v in s.v.items()}; d["details"] = s.det.get("1.0", "end").strip(); s.app.cfg["title"] = d; save_cfg(s.app.cfg); s.destroy()

# ---------------- main window ----------------
class App(tk.Tk):
    def __init__(s, etabs, theme="Light Blue"):
        super().__init__(); s.title(f"AI Structural Analysis v{VERSION}"); s.geometry("1320x800"); s.etabs, s.results, s.state, s.cfg = etabs, {}, {}, load_cfg(); s.pages, s.btns = {}, {}
        apply_style(s, theme); t = THEMES[theme]
        s.side = tk.Frame(s, bg=t["side"], width=210); s.side.pack(side="left", fill="y"); s.side.pack_propagate(False)
        tk.Label(s.side, text="AI Structural\nAnalysis", bg=t["side"], fg=t["sfg"], font=("Segoe UI", 15, "bold"), justify="left").pack(anchor="w", padx=12, pady=10)
        s.top = ttk.Label(s, text=("Connected to ETABS | " + os.path.basename(etabs.try_(lambda: etabs.m.GetModelFilename(), "") or "(Untitled)")) if etabs.m else "Offline mode (calculators only)"); s.top.pack(anchor="e", padx=8)
        ttk.Label(s, text=FOOT, style="Sub.TLabel").pack(side="bottom", anchor="w", padx=8); s.body = ttk.Frame(s); s.body.pack(fill="both", expand=True)
        for sec, items in PAGE_DEFS:
            tk.Label(s.side, text=sec, bg=t["side"], fg="#8fa8d6", font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=12, pady=(8, 0))
            for num, name, cls in items:
                b = tk.Button(s.side, text=f" {num}   {name}", anchor="w", bg=t["side"], fg=t["sfg"], relief="flat", activebackground=t["acc"], activeforeground="#fff", command=lambda n=name, c=cls: s.show(n, c)); b.pack(fill="x", padx=6); s.btns[name] = b
        tk.Button(s.side, text="Report settings", bg=t["side"], fg=t["sfg"], relief="flat", command=lambda: TitleBlock(s)).pack(anchor="w", padx=10, pady=(10, 0))
        tk.Button(s.side, text="About", bg=t["side"], fg=t["sfg"], relief="flat", command=lambda: messagebox.showinfo("About", f"AI Structural Analysis v{VERSION}\nETABS automation for IS 1893 / IS 456 / IS 13920.\nA design aid - results must be checked by a qualified structural engineer.")).pack(anchor="w", padx=10)
        s.tv = V(theme); cb = ttk.Combobox(s.side, textvariable=s.tv, values=list(THEMES), width=14, state="readonly"); cb.pack(anchor="w", padx=12, pady=8)
        cb.bind("<<ComboboxSelected>>", lambda e: (s.cfg.update(theme=s.tv.get()), save_cfg(s.cfg), messagebox.showinfo("Theme", "Theme saved - restart the app to apply it fully.")))
        s.show("Automatic Code Checks", AutoChecks)
        if "title" not in s.cfg: s.after(400, lambda: TitleBlock(s))
    def show(s, name, cls):
        for p in s.pages.values(): p.pack_forget()
        if name not in s.pages: s.pages[name] = cls(s)
        s.pages[name].pack(fill="both", expand=True)

class ConnectWin(tk.Tk):
    def __init__(s):
        super().__init__(); s.title("AI Structural Analysis - Connect"); cfg = load_cfg(); s.theme = V(cfg.get("theme", "Light Blue")); apply_style(s, s.theme.get()); s.e = core.Etabs()
        L = tk.Frame(s, bg="#0b1d3a", width=230); L.pack(side="left", fill="y"); L.pack_propagate(False)
        tk.Label(L, text="AI Structural\nAnalysis", bg="#0b1d3a", fg="white", font=("Segoe UI", 18, "bold"), justify="left").pack(anchor="w", padx=14, pady=16)
        tk.Label(L, text="Seismic checks (IS 1893)\nLoad patterns & combinations\nRC beam detailing\nAI building generator\nPython + MCP automation", bg="#0b1d3a", fg="#cfe0ff", justify="left").pack(anchor="w", padx=14)
        R = ttk.Frame(s, padding=14); R.pack(side="left", fill="both", expand=True); ttk.Label(R, text="Connect to ETABS", style="H1.TLabel").pack(anchor="w")
        ttk.Label(R, text="Open your model in ETABS, then choose the running instance below. The toolkit works on the live model.", wraplength=420).pack(anchor="w", pady=4)
        s.lb = tk.Listbox(R, height=5, width=60); s.lb.pack(fill="x"); ttk.Button(R, text="Refresh", command=s.refresh).pack(anchor="e", pady=3)
        ttk.Label(R, text="ETABS API library (ETABSv1.dll)").pack(anchor="w"); f = ttk.Frame(R); f.pack(fill="x")
        s.dll = V(cfg.get("dll", r"C:\Program Files\Computers and Structures\ETABS 22\ETABSv1.dll")); ttk.Entry(f, textvariable=s.dll, width=52).pack(side="left"); ttk.Button(f, text="Browse...", command=lambda: s.dll.set(filedialog.askopenfilename() or s.dll.get())).pack(side="left")
        bar(R, ("Connect →", s.go), ("Continue offline", lambda: s.launch(False)), ("About", lambda: messagebox.showinfo("About", "AI Structural Analysis - design aid."))); ttk.Combobox(R, textvariable=s.theme, values=list(THEMES), width=12, state="readonly").pack(anchor="w")
        s.msg = ttk.Label(R, text=""); s.msg.pack(anchor="w"); s.refresh()
    def refresh(s):
        s.lb.delete(0, "end"); s.instances = core.Etabs.instances(); I = s.instances
        for pid, title in I: s.lb.insert("end", f"PID {pid}  -  {title}")
        s.msg.config(text=f"{len(I)} ETABS instance(s) found." if I else "No visible ETABS model window found. Refresh, or run this app at the same Windows permission level as ETABS.")
    def go(s):
        try:
            sel=s.lb.curselection(); pid=s.instances[sel[0]][0] if sel else None
            s.e.connect(s.dll.get(), pid); s.launch(True)
        except Exception as ex: messagebox.showerror("Connect failed", f"Open ETABS with a model, select its window, and check that both apps use the same Windows permission level.\n{ex}")
    def launch(s, ok):
        c = load_cfg(); c.update(theme=s.theme.get(), dll=s.dll.get()); save_cfg(c); th = s.theme.get(); s.destroy(); App(s.e, th).mainloop()

from app_repair import install_repairs
install_repairs(globals())

if __name__ == "__main__":
    ConnectWin().mainloop()
