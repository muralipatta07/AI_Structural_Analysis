"""core.py - IS 1893 / IS 456 / IS 13920 engine + ETABS API layer for AI Structural Analysis.
A design aid only - every result must be checked by a qualified structural engineer.
Fixed: missing 'import os', hardened Windows window scan, flexible ETABSv1.dll lookup (Python 3.12)."""
import os, sys, math, csv, io, itertools, subprocess, html, datetime

G = 9.80665
ZONE = {"II": .10, "III": .16, "IV": .24, "V": .36}
SOIL = {"I": (.40, 1.0, .25), "II": (.55, 1.36, .34), "III": (.67, 1.67, .42)}
SYSTEMS = {
    "RC moment frame, bare (no infill) - 0.075 h^0.75": ("mrf", .075),
    "RC-steel composite MRF, bare - 0.080 h^0.75": ("mrf", .080),
    "Steel MRF, bare - 0.085 h^0.75": ("mrf", .085),
    "RC structural walls - 0.075 h^0.75 / sqrt(Aw) >= 0.09 h / sqrt(d)": ("wall", .075),
    "All other buildings (incl. MRF with infill) - 0.09 h / sqrt(d)": ("other", .09)}
IS16700_TORS_RATIO = 0.90      # shown for information only (verify against IS 16700 cl. 5.5)
LOAD_TYPES = {"Dead": 1, "Super Dead": 2, "Live": 3, "Reducible Live": 4, "Seismic": 5, "Wind": 6,
              "Snow": 7, "Other": 8, "Temperature": 10, "Roof Live": 11}
DEFAULT_PATTERNS = [("Dead", "Dead", 1, ""), ("Live <= 3", "Live", 0, ""), ("Live > 3", "Live", 0, ""),
                    ("Finish", "Super Dead", 0, ""), ("SIDL", "Super Dead", 0, ""),
                    ("EQX", "Seismic", 0, "IS 1893:2016"), ("EQY", "Seismic", 0, "IS 1893:2016"),
                    ("WLX", "Wind", 0, "Indian IS 875:2015"), ("WLY", "Wind", 0, "Indian IS 875:2015"),
                    ("TEMPERATURE", "Temperature", 0, ""), ("EQZ", "Seismic", 0, "IS 1893:2016"),
                    ("Sunken", "Super Dead", 0, ""), ("Services", "Super Dead", 0, "")]

# ---------------- code calculations ----------------
def sa_g(T, soil="II"):
    if T <= .10: return 1 + 15 * T
    top, k, tail = SOIL[soil]
    return 2.5 if T <= top else (k / T if T <= 4 else tail)

def ah(z, I, R, T, soil): return ZONE[z] / 2 * I / R * sa_g(T, soil)
def av(z, I, R): return 2 / 3 * ZONE[z] / 2 * I / R * 2.5

def spectrum(z, soil, I, R, upto=100):
    ts = [0, .02, .04, .06, .08, .10] + [round(.10 + .05 * k, 2) for k in range(1, 79)]
    ts += [t for t in (5, 6, 8, 10, 20, 50, 100) if t <= upto]
    return [(t, sa_g(t, soil), ah(z, I, R, t, soil), av(z, I, R)) for t in ts]

def wall_aw(walls, h): return sum(L * t * (.2 + min(L / h, .9) ** 2) for L, t in walls)

def ta(system, h, d, walls=()):
    kind, c = SYSTEMS[system]
    if kind == "mrf": return c * h ** .75
    if kind == "other": return .09 * h / math.sqrt(d)
    aw = wall_aw(walls, h)
    tw = c * h ** .75 / math.sqrt(aw) if aw > 0 else float("inf")
    return max(tw, .09 * h / math.sqrt(d))

def principal_axes(T, gx, gy, zone, soil, I, R, xi=.05, step=1.0):
    n = len(T); om = [2 * math.pi / t for t in T]; A = [ah(zone, I, R, t, soil) * G for t in T]
    def rho(i, j):
        if i == j: return 1.0
        r = om[j] / om[i]
        return 8 * xi * xi * (1 + r) * r ** 1.5 / ((1 - r * r) ** 2 + 4 * xi * xi * r * (1 + r) ** 2)
    P = [[rho(i, j) for j in range(n)] for i in range(n)]
    out, th = [], 0.0
    while th <= 180 + 1e-9:
        c, s = math.cos(math.radians(th)), math.sin(math.radians(th))
        V = [(gx[i] * c + gy[i] * s) ** 2 * A[i] for i in range(n)]
        out.append((th, math.sqrt(max(sum(P[i][j] * V[i] * V[j] for i in range(n) for j in range(n)), 0))))
        th += step
    return out

# ---------------- load combinations (IS 456 / IS 1893) ----------------
ROLES = ["DL", "LL", "EQA", "EQB", "EQV", "WLA", "WLB", "TL"]

def lateral_sets(a, b, v, mode):
    keys = [k for k in ((a, b, v) if mode.startswith("Three") else (a, b)) if k]
    if mode.startswith("Ortho"): return [{k: s} for k in keys for s in (1, -1)]
    S = []
    for p in keys:
        oth = [k for k in keys if k != p]
        for sg in itertools.product((1, -1), repeat=1 + len(oth)):
            S.append({p: sg[0], **{o: .3 * x for o, x in zip(oth, sg[1:])}})
    return S

USER_ROLES = ["DL", "SIDL", "LL", "WLX", "WLY", "ELX", "ELX+e", "ELX-e", "ELY", "ELY+e", "ELY-e"]

def gen_user_combos():
    """Return the requested A01-A50 strength and B01-B33 service combinations."""
    rows = []
    def add(name, group, factors):
        rows.append({"name": name, "group": group, **{k: factors.get(k, 0) for k in USER_ROLES}})
    def base(scale, live=0): return {"DL": scale, "SIDL": scale, "LL": live}
    # A01-A18: 1.5 gravity and lateral cases.
    add("A01", "Collapse", base(1.5))
    add("A02", "Collapse", base(1.5, 1.5))
    n = 3
    for case in ("WLX", "WLY", "ELX", "ELX+e", "ELX-e", "ELY", "ELY+e", "ELY-e"):
        add(f"A{n:02d}", "Collapse", {**base(1.5), case: 1.5}); n += 1
        add(f"A{n:02d}", "Collapse", {**base(1.5), case: -1.5}); n += 1
    # A19-A34: 1.2 gravity + live load + signed lateral.
    add("A19", "Collapse", {**base(1.2, 1.2), "WLX": 1.2})
    add("A20", "Collapse", {**base(1.2, 1.2), "WLX": -1.2})
    add("A21", "Collapse", {**base(1.2, 1.2), "WLY": 1.2})
    add("A22", "Collapse", {**base(1.2, 1.2), "WLY": -1.2})
    n = 23
    for case in ("ELX", "ELX+e", "ELX-e", "ELY", "ELY+e", "ELY-e"):
        add(f"A{n:02d}", "Collapse", {**base(1.2, 1.2), case: 1.2}); n += 1
        add(f"A{n:02d}", "Collapse", {**base(1.2, 1.2), case: -1.2}); n += 1
    # A35-A50: 0.9 permanent + signed 1.5 lateral.
    n = 35
    for case in ("WLX", "WLY", "ELX", "ELX+e", "ELX-e", "ELY", "ELY+e", "ELY-e"):
        add(f"A{n:02d}", "Collapse", {**base(.9), case: 1.5}); n += 1
        add(f"A{n:02d}", "Collapse", {**base(.9), case: -1.5}); n += 1
    # B01-B17: service gravity and signed lateral.
    add("B01", "Service", base(1, 1))
    n = 2
    for case in ("WLX", "WLY", "ELX", "ELX+e", "ELX-e", "ELY", "ELY+e", "ELY-e"):
        add(f"B{n:02d}", "Service", {**base(1), case: 1}); n += 1
        add(f"B{n:02d}", "Service", {**base(1), case: -1}); n += 1
    # B18-B33: 1.0 permanent + 0.8 (live + signed lateral).
    n = 18
    for case in ("WLX", "WLY", "ELX", "ELX+e", "ELX-e", "ELY", "ELY+e", "ELY-e"):
        add(f"B{n:02d}", "Service", {**base(1, .8), case: .8}); n += 1
        add(f"B{n:02d}", "Service", {**base(1, .8), case: -.8}); n += 1
    return rows

def gen_combos(mode, wind=True, temp=True):
    R = []
    def add(g, f): R.append({"group": g, **{k: f.get(k, 0) for k in ROLES}})
    add("Collapse", {"DL": 1.5, "LL": 1.5}); add("Service", {"DL": 1, "LL": 1})
    for a, b, v in [("EQA", "EQB", "EQV")] + ([("WLA", "WLB", None)] if wind else []):
        for L in lateral_sets(a, b, v, mode):
            sc = lambda k: {x: y * k for x, y in L.items()}
            add("Collapse", {"DL": 1.5, **sc(1.5)}); add("Collapse", {"DL": 1.2, "LL": 1.2, **sc(1.2)})
            add("Collapse", {"DL": .9, **sc(1.5)})
            add("Service", {"DL": 1, **sc(1)}); add("Service", {"DL": 1, "LL": 1, **sc(1)})
    if temp:
        add("Temperature", {"DL": 1.5, "LL": 1.5, "TL": 1.5}); add("Temperature", {"DL": 1, "LL": 1, "TL": 1})
    for i, r in enumerate(R, 1): r["name"] = f"AS_{r['group']}_{i:03d}"
    return R

# ---------------- RC beam detailing helpers (IS 456 / IS 13920 drafting aid) ----------------
BAR_SIZES = (8, 10, 12, 16, 20, 25, 32)

def pick_bars(As, b, cover, stir, dmin, dmax, min_clear):
    """Return (n, dia, layers) giving the least excess steel that fits width b (mm)."""
    avail, best = b - 2 * (cover + stir), None
    for d in [x for x in BAR_SIZES if dmin <= x <= dmax]:
        a = math.pi * d * d / 4; n = max(2, math.ceil(As / a - 1e-9))
        for layers in (1, 2):
            per = math.ceil(n / layers)
            if per * d + (per - 1) * max(min_clear, d) <= avail:
                cand = (layers, n * a - As, n, d)
                if best is None or cand < best: best = cand
                break
    if best is None:
        d = max(x for x in BAR_SIZES if dmin <= x <= dmax); a = math.pi * d * d / 4
        return (max(2, math.ceil(As / a)), d, 3)
    return (best[2], best[3], best[0])

def stirrup(asv_s, dia, legs, pref_end, pref_mid):
    if asv_s <= 0: return pref_end, pref_mid
    sv = legs * math.pi * dia * dia / 4 / asv_s
    return min(pref_end, int(sv // 25 * 25) or 25), min(pref_mid, int(sv // 25 * 25) or 25)

# ---------------- table helper ----------------
def fl(v):
    try: return float(v)
    except Exception: return 0.0

class TB:
    def __init__(s, f, rows): s.f, s.rows = list(f), rows
    def idx(s, *tok):
        for i, x in enumerate(s.f):
            if isinstance(x, str) and all(str(t).lower() in x.lower() for t in tok): return i
    def col(s, *tok, num=True):
        i = s.idx(*tok)
        if i is None: raise KeyError(f"field {tok} not found; available: {s.f}")
        return [fl(r[i]) if num else r[i] for r in s.rows]
    def sel(s, tok, val):
        i = s.idx(tok)
        return TB(s.f, [r for r in s.rows if r[i] == val]) if i is not None else s

def _seqs(ret): return [x for x in ret if isinstance(x, (list, tuple))]

# ---------------- ETABS connection ----------------
def _dll_candidates():
    """Possible ETABSv1.dll locations: ETABS_DLL env var first, then ETABS 22/21/20 default installs."""
    out = []
    env = os.environ.get("ETABS_DLL")
    if env: out.append(env)
    for pf in {os.environ.get("ProgramFiles", r"C:\Program Files"), r"C:\Program Files"}:
        for ver in ("22", "21", "20"):
            out.append(os.path.join(pf, "Computers and Structures", f"ETABS {ver}", "ETABSv1.dll"))
    return out

def find_etabs_dll(dll=None):
    """Return the first existing ETABSv1.dll path (or the given one). Raises a clear error if none."""
    if dll and os.path.isfile(dll): return dll
    for c in _dll_candidates():
        if os.path.isfile(c): return c
    raise FileNotFoundError("ETABSv1.dll not found. Set the ETABS_DLL environment variable to its full path, "
                            "or pass the path in the app. Looked in: " + "; ".join(_dll_candidates()))

class Etabs:
    def __init__(s): s.m = None; s.obj = None; s._sel = False
    @staticmethod
    def instances():
        # Enumerate visible ETABS windows directly. `tasklist` can be filtered by
        # Windows integrity levels when ETABS and this app are run differently.
        try:
            import ctypes
            from ctypes import wintypes
            u = ctypes.windll.user32; k = ctypes.windll.kernel32; found = {}
            # Explicit prototypes: without them, 64-bit window handles can be truncated and the scan fails silently.
            callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            u.EnumWindows.argtypes = (callback_type, wintypes.LPARAM); u.EnumWindows.restype = wintypes.BOOL
            u.IsWindowVisible.argtypes = (wintypes.HWND,); u.IsWindowVisible.restype = wintypes.BOOL
            u.GetWindowTextLengthW.argtypes = (wintypes.HWND,); u.GetWindowTextLengthW.restype = ctypes.c_int
            u.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int); u.GetWindowTextW.restype = ctypes.c_int
            u.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
            u.GetWindowThreadProcessId.restype = wintypes.DWORD
            k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD); k.OpenProcess.restype = wintypes.HANDLE
            k.CloseHandle.argtypes = (wintypes.HANDLE,); k.CloseHandle.restype = wintypes.BOOL
            k.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
            k.QueryFullProcessImageNameW.restype = wintypes.BOOL
            def visit(hwnd, _):
                try:
                    if not u.IsWindowVisible(hwnd): return True
                    n = u.GetWindowTextLengthW(hwnd)
                    if not n: return True
                    title = ctypes.create_unicode_buffer(n + 1); u.GetWindowTextW(hwnd, title, n + 1)
                    text = title.value
                    if text.startswith("ETABS ") and " - " in text:
                        pid = wintypes.DWORD(); u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                        # Window titles from browsers and Explorer can mention ETABS;
                        # accept only windows owned by the actual ETABS executable.
                        h = k.OpenProcess(0x1000, False, pid.value)
                        if h:
                            image = ctypes.create_unicode_buffer(1024); size = wintypes.DWORD(len(image))
                            ok = k.QueryFullProcessImageNameW(h, 0, image, ctypes.byref(size)); k.CloseHandle(h)
                            if ok and os.path.basename(image.value).lower() != "etabs.exe": return True
                        found[int(pid.value)] = text
                except Exception:
                    pass          # never let one window stop the whole scan
                return True
            u.EnumWindows(callback_type(visit), 0)
            res = sorted([(pid, title) for pid, title in found.items()], key=lambda x: x[0])
            if res: return res
            raise RuntimeError("no window found")     # fall through to tasklist fallback
        except Exception:
            try:
                o = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ETABS.exe", "/V", "/FO", "CSV", "/NH"],
                                   capture_output=True, text=True).stdout
                return [(r[1], r[-1]) for r in csv.reader(io.StringIO(o)) if len(r) > 5 and r[0].lower() == "etabs.exe"]
            except Exception: return []
    def connect(s, dll=None, pid=None):
        import comtypes.client
        # ETABS 20+ exposes its active-instance helper in the ETABSv1 type library.
        # Create it through the generated interface so late-bound COM does not fail
        # with "No such interface supported" on newer ETABS releases.
        api_dll = find_etabs_dll(dll)
        api_dir = os.path.dirname(api_dll)
        tlb = os.path.join(api_dir, "NativeAPI", "x64", "ETABSv1.tlb")
        mod = comtypes.client.GetModule(tlb if os.path.isfile(tlb) else api_dll)
        helper = comtypes.client.CreateObject(mod.Helper)
        if pid:
            s.obj = helper.GetObjectProcess("CSI.ETABS.API.ETABSObject", int(pid))
        else:
            s.obj = helper.GetObject("CSI.ETABS.API.ETABSObject")
        if s.obj is None: raise RuntimeError("ETABS did not return an API connection. If ETABS is running as administrator, restart this app as administrator and select its ETABS window.")
        s.m = s.obj.SapModel
        # Preserve ETABS' active display units; connecting must not change them.
        return s.m.GetModelFilename()
    def try_(s, f, d=None):
        try: return f()
        except Exception: return d
    def names(s, kind): return list(_seqs(s.m.LoadCases.GetNameList() if kind == "case" else s.m.RespCombo.GetNameList())[0] or [])
    def select_all(s):
        if s._sel: return
        try:
            s.m.DatabaseTables.SetLoadCasesSelectedForDisplay(s.names("case"))
            s.m.DatabaseTables.SetLoadCombinationsSelectedForDisplay(s.names("combo")); s._sel = True
        except Exception: pass
    def run_analysis(s): s._sel = False; return s.m.Analyze.RunAnalysis()
    def table(s, key):
        s.select_all()
        raw = s.m.DatabaseTables.GetTableForDisplayArray(key, [], "All", 0, [], 0, [])
        # Current ETABS returns the field keys at tuple[2] and flattened records
        # at tuple[4]. Choosing arrays only by length accidentally treated the
        # empty status array as the table header and corrupted every result table.
        try:
            fields, data = list(raw[2]), list(raw[4])
        except Exception:
            sq = [list(x) for x in _seqs(raw) if len(x)]
            if len(sq) < 2: return TB([], [])
            data, fields = max(sq, key=len), sorted(sq, key=len)[-2]
        n = len(fields)
        if not n: return TB([], [])
        return TB(fields, [list(data[i:i + n]) for i in range(0, len(data), n)])
    def points(s):
        sq = _seqs(s.m.PointObj.GetAllPoints()); return {n: (x, y, z) for n, x, y, z in zip(*sq[:4])}
    def frames(s):
        sq = _seqs(s.m.FrameObj.GetAllFrames()); return list(zip(sq[3], sq[4]))
    def frame_info(s):
        m = s.m; sq = _seqs(m.FrameObj.GetAllFrames()); out, cache = [], {}
        for n, p in zip(sq[0], sq[1]):
            if p not in cache:
                try: g = m.PropFrame.GetRectangle(p); cache[p] = (float(g[2]), float(g[3]))
                except Exception: cache[p] = (0, 0)
            ori = s.try_(lambda: m.FrameObj.GetDesignOrientation(n)[0], 4)
            mod = s.try_(lambda: _seqs(m.FrameObj.GetModifiers(n))[0], None)
            out.append(dict(name=n, prop=p, ori=ori, t3=cache[p][0], t2=cache[p][1], mod=mod))
        return out
    def build(s, sp):
        from building_spec import build_model
        return build_model(s.m, sp)


def levels(e):
    """Read actual story elevations directly; do not infer them from a table."""
    result = e.m.Story.GetStories()
    if not isinstance(result, (tuple, list)) or len(result) < 4 or result[-1] != 0:
        raise RuntimeError(f"ETABS could not read stories: {result!r}")
    names, elevations = list(result[1] or []), list(result[2] or [])
    if not names:
        raise RuntimeError("ETABS returned no story names. Open the intended model.")
    if len(elevations) == len(names) + 1 and not any(str(n).casefold() == 'base' for n in names):
        names = ['Base'] + names
    if len(names) != len(elevations):
        raise RuntimeError(f"Story names/elevations have different lengths: {result!r}")
    pairs = []
    for name, z in zip(names, elevations):
        if not isinstance(name, str) or not name.strip() or not math.isfinite(float(z)):
            raise RuntimeError("ETABS returned an invalid story name or elevation.")
        pairs.append(('Base' if name.casefold() == 'base' else name, float(z)))
    if not any(n == 'Base' for n, z in pairs):
        base = e.m.Story.GetElevation('Base')
        if not isinstance(base, (tuple, list)) or base[-1] != 0 or not math.isfinite(float(base[0])):
            raise RuntimeError(f"ETABS did not return the base elevation: {base!r}")
        pairs.append(('Base', float(base[0])))
    if len({n for n, z in pairs}) != len(pairs):
        raise RuntimeError("Duplicate story names returned by ETABS.")
    return sorted(pairs, key=lambda item: item[1])

def modal_summary(e, case=None):
    t = e.table("Modal Participating Mass Ratios")
    if case: t = t.sel("outputcase", case)
    S = {"T": t.col("period"), "ux": t.col("ux"), "uy": t.col("uy"), "rz": t.col("rz"),
         "sx": t.col("sumux"), "sy": t.col("sumuy"), "sz": t.col("sumrz")}
    return S

# ---------------- plotting ----------------
def plot_model(fig, e, case=None, step=None, view="iso"):
    from matplotlib.collections import LineCollection
    fig.clear(); P = e.points(); F = e.frames(); U = {}
    if case:
        t = e.table("Joint Displacements"); oc, un = t.idx("outputcase"), t.idx("uniquename")
        ix, iy, iz = t.idx("ux"), t.idx("uy"), t.idx("uz"); st = t.idx("stepnum")
        for r in t.rows:
            if r[oc] == case and (step is None or st is None or int(fl(r[st])) == step):
                U[r[un]] = (fl(r[ix]), fl(r[iy]), fl(r[iz]))
    ext = max(max(abs(c) for c in p) for p in P.values()) or 1; mx = max([max(map(abs, u)) for u in U.values()] or [0])
    k = .08 * ext / mx if mx else 0; ztop = max(p[2] for p in P.values())
    def xyz(n, d): return tuple(P[n][i] + (U.get(n, (0, 0, 0))[i] * k if d else 0) for i in range(3))
    if view == "iso":
        from mpl_toolkits.mplot3d.art3d import Line3DCollection
        ax = fig.add_subplot(111, projection="3d")
        ax.add_collection3d(Line3DCollection([[xyz(a, 0), xyz(b, 0)] for a, b in F], colors="#bbbbbb", linewidths=.5))
        if U: ax.add_collection3d(Line3DCollection([[xyz(a, 1), xyz(b, 1)] for a, b in F], colors="#1b7f3b", linewidths=.8))
        xs, ys, zs = zip(*P.values()); ax.set_xlim(min(xs), max(xs)); ax.set_ylim(min(ys), max(ys)); ax.set_zlim(min(zs), max(zs))
    else:
        ax = fig.add_subplot(111); a, b = (0, 1) if view == "plan" else (0, 2)
        seg = lambda d: [[tuple(xyz(n, d)[i] for i in (a, b)) for n in f] for f in F
                         if view != "plan" or (P[f[0]][2] == ztop and P[f[1]][2] == ztop)]
        ax.add_collection(LineCollection(seg(0), colors="#bbbbbb", linewidths=.6))
        if U: ax.add_collection(LineCollection(seg(1), colors="#1b7f3b", linewidths=.9))
        ax.autoscale(); ax.set_aspect("equal")
    ax.set_title(f"{view}  {case or 'undeformed'}" + (f" (x{k:.0f})" if U else ""), fontsize=9)

def plot_concrete_frame(fig, e):
    """Render ETABS frame members as section-sized concrete prisms (frame-only preview)."""
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    import numpy as np
    fig.clear(); ax = fig.add_subplot(111, projection="3d"); P = e.points(); sq = _seqs(e.m.FrameObj.GetAllFrames())
    dims = {f["prop"]: (f["t2"], f["t3"]) for f in e.frame_info()}
    faces = []
    for name, prop, a, b in zip(sq[0], sq[1], sq[3], sq[4]):
        if a not in P or b not in P: continue
        p0, p1 = np.array(P[a], float), np.array(P[b], float); d = p1-p0; L=np.linalg.norm(d)
        if L < 1e-9: continue
        u=d/L; ref=np.array([0.,0.,1.]) if abs(u[2]) < .95 else np.array([1.,0.,0.])
        v=np.cross(u,ref); v/=np.linalg.norm(v); w=np.cross(u,v); width,depth=dims.get(prop,(.3,.5))
        corners=[p0+sv*v*width/2+sw*w*depth/2 for sv,sw in ((-1,-1),(1,-1),(1,1),(-1,1))]
        end=[x+d for x in corners]; faces.extend([[corners[i],corners[(i+1)%4],end[(i+1)%4],end[i]] for i in range(4)]+[corners,end])
    if faces: ax.add_collection3d(Poly3DCollection(faces, facecolors="#9ab7d5", edgecolors="#365878", linewidths=.25, alpha=.85))
    if P:
        xs,ys,zs=zip(*P.values()); ax.set_xlim(min(xs),max(xs) or 1); ax.set_ylim(min(ys),max(ys) or 1); ax.set_zlim(min(zs),max(zs) or 1)
    ax.set_box_aspect((1,1,1.4)); ax.set_xlabel("X"); ax.set_ylabel("Y"); ax.set_zlabel("Z"); ax.set_title("Concrete frame section preview (beams and columns; slabs/walls omitted)", fontsize=9)

def plot_rebar_frame(fig, e, layouts):
    """Show draft beam longitudinal bars using detailing output, not as a fabrication model."""
    from mpl_toolkits.mplot3d.art3d import Line3DCollection
    import numpy as np
    fig.clear(); ax=fig.add_subplot(111,projection="3d"); P=e.points(); F=dict(zip(_seqs(e.m.FrameObj.GetAllFrames())[0], zip(_seqs(e.m.FrameObj.GetAllFrames())[3],_seqs(e.m.FrameObj.GetAllFrames())[4]))); seg=[]
    for name, info, width, depth, top, bottom in layouts:
        ends=F.get(name)
        if not ends or any(n not in P for n in ends): continue
        p0,p1=(np.array(P[n],float) for n in ends); d=p1-p0; L=np.linalg.norm(d)
        if L < 1e-9: continue
        u=d/L; ref=np.array([0.,0.,1.]) if abs(u[2])<.95 else np.array([1.,0.,0.]); v=np.cross(ref,u); v/=np.linalg.norm(v); w=np.cross(u,v)
        # Sections are displayed schematically: selected bars along beam axis with cover offsets.
        for bars, sign in ((top,1),(bottom,-1)):
            count, dia, layers=bars
            n=max(1,int(count)); cover=.04; zoff=sign*(depth/2-cover)
            for i in range(n):
                off=(i-(n-1)/2)*max(.035,(width-2*cover)/max(n-1,1))
                q0=p0+v*off+w*zoff; q1=p1+v*off+w*zoff; seg.append([q0,q1])
    if seg: ax.add_collection3d(Line3DCollection(seg,colors="#b3261e",linewidths=1.1))
    if P:
        xs,ys,zs=zip(*P.values()); ax.set_xlim(min(xs),max(xs) or 1); ax.set_ylim(min(ys),max(ys) or 1); ax.set_zlim(min(zs),max(zs) or 1)
    ax.set_box_aspect((1,1,1.4)); ax.set_xlabel("X"); ax.set_ylabel("Y"); ax.set_zlabel("Z"); ax.set_title("Draft longitudinal beam reinforcement from Detailing output",fontsize=9)

# ---------------- report ----------------
CSS = ("body{font-family:Calibri,Arial;margin:30px;color:#222}h1{color:#0b2a5b}h2{color:#0b2a5b;border-bottom:2px solid #0b2a5b}"
       "table{border-collapse:collapse;margin:8px 0;width:100%}th{background:#0b2a5b;color:#fff;padding:4px 8px;text-align:left}"
       "td{border:1px solid #ccc;padding:3px 8px;font-size:12px}.bad{color:#b00020;font-weight:bold}.ok{color:#1b7f3b}"
       ".banner{border-left:5px solid #b00020;background:#fdeaea;padding:8px;margin:10px 0}.front{text-align:center;margin-bottom:20px}")

def report_html(tb, results, order, detail, paper="A4", orient="Portrait"):
    e = html.escape; secs, bad = [], []
    for i, k in enumerate([o for o in order if o in results], 1):
        R = results[k]; rows = R["rows"]
        fails = [r for r in rows if "NOT OK" in [str(c) for c in r]]
        if fails: bad.append(k)
        show = rows if detail.get(k, "Detailed") == "Detailed" else (fails or rows[:15])
        cell = lambda c: f'<td class="{"bad" if str(c)=="NOT OK" else "ok" if str(c)=="OK" else ""}">{e(str(c))}</td>'
        t = "<table><tr>" + "".join(f"<th>{e(h)}</th>" for h in R["headers"]) + "</tr>" + \
            "".join("<tr>" + "".join(cell(c) for c in r) + "</tr>" for r in show) + "</table>"
        n = "".join(f"<p>{e(x)}</p>" for x in R.get("notes", []))
        secs.append(f"<h2>{i}. {e(k)}</h2>{n}{t}")
    head = (f'<div class="front"><h1>{e(tb.get("project","Structural seismic checks"))}</h1><p>{e(tb.get("details",""))}</p>'
            f'<p>Ref: {e(tb.get("ref",""))} | Client: {e(tb.get("client",""))}<br>Designed: {e(tb.get("designed",""))} '
            f'Checked: {e(tb.get("checked",""))} Approved: {e(tb.get("approved",""))}<br>{datetime.date.today()}</p></div>')
    ban = f'<div class="banner">IS 1893 checks NOT satisfied - see: {e("; ".join(bad))}</div>' if bad else ""
    foot = ("<p><i>Produced with AI Structural Analysis. Analysis, design and checking remain the responsibility of the "
            "engineers named above. Design aid only.</i></p>")
    return f"<html><head><meta charset='utf-8'><style>{CSS}@page{{size:{paper} {orient.lower()}}}</style></head><body>{head}{ban}{''.join(secs)}{foot}</body></html>"
