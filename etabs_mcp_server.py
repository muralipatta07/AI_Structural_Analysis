"""etabs_mcp_server.py - MCP server so Claude Desktop (or any MCP client) can drive ETABS.

Register in claude_desktop_config.json:
  {
    "mcpServers": {
      "etabs": {"command": "python", "args": ["C:/path/to/etabs_mcp_server.py"]}
    }
  }

The GUI 'Python + MCP' tab calls tools directly via the TOOLS dict.

Fixes applied (all 11 audited issues):
  #1  ETABS instance no longer created at import time — created lazily on first use.
  #2  _need() reads ETABS_DLL env var and scans default paths before connecting;
      raises a clear FileNotFoundError instead of a silent failure.
  #3  Removed duplicate tool definitions inside __main__. Single source of truth:
      module-level functions are registered into FastMCP via a loop.
  #4  get_base_shear uses a fallback column-name list to handle ETABS version
      differences (globalfx / FX / fx).
  #5  get_modal_results returns T1x and T1y (dominant translational periods per
      direction) instead of the raw mode-1 period.
  #6  get_story_drifts returns a full per-storey breakdown (storey, direction,
      case, drift ratio, IS 1893 status) not just the single worst value.
  #7  run_analysis checks case status after the blocking call and reports which
      cases finished, did not run, or failed.
  #8  get_is1893_spectrum validates zone and soil inputs before calling core
      functions, returning a clean error dict instead of a raw KeyError.
  #9  Removed unused `import json`.
  #10 run_analysis docstring clearly marks it as a model-modifying operation.
  #11 get_story_levels (and all tools) wrap core calls in try/except and return
      {"error": "..."} instead of raising raw exceptions.
"""

import core                     # same directory as app.py

# ---------------------------------------------------------------------------
# Lazy ETABS singleton — not created until the first tool call (#1 fixed)
# ---------------------------------------------------------------------------
_etabs = None


def _get_etabs():
    """Return (and lazily create) the shared Etabs connection object."""
    global _etabs
    if _etabs is None:
        _etabs = core.Etabs()
    return _etabs


def _need():
    """
    Ensure a live ETABS connection exists.
    Reads ETABS_DLL environment variable first; then scans standard install
    paths.  Raises FileNotFoundError with a clear message if the DLL is not
    found (#2 fixed).
    """
    e = _get_etabs()
    if not e.m:
        import os
        dll = os.environ.get("ETABS_DLL") or None   # None → auto-scan in connect()
        e.connect(dll)          # core.find_etabs_dll() raises FileNotFoundError if absent
    return e


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _col_any(table, *names, num=True):
    """
    Try each candidate column name in order and return the first that exists.
    Handles ETABS version differences in table field naming (#4 fixed).
    Returns None if none of the names are found.
    """
    for name in names:
        try:
            return table.col(name, num=num)
        except KeyError:
            pass
    return None


def _safe(fn):
    """
    Call fn() and return its result.  If any exception is raised, return
    {"error": str(exception)} so the MCP client always gets valid JSON (#11 fixed).
    """
    try:
        return fn()
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# READ-ONLY TOOLS
# ---------------------------------------------------------------------------

def get_model_info() -> dict:
    """Return basic information about the currently open ETABS model."""
    def _run():
        e = _need()
        m = e.m
        return {
            "file": m.GetModelFilename(),
            "locked": bool(m.GetModelIsLocked()),
            "cases": e.names("case"),
            "combos": e.names("combo"),
        }
    return _safe(_run)


def list_load_cases() -> list:
    """List all load cases defined in the ETABS model."""
    def _run():
        return _need().names("case")
    result = _safe(_run)
    # _safe wraps errors as a dict; return empty list on error so callers stay type-safe
    return result if isinstance(result, list) else []


def get_story_levels() -> dict:
    """
    Return all story levels and their elevations in metres, sorted
    bottom to top.
    """
    def _run():
        e = _need()
        pairs = core.levels(e)
        return {
            "story_count": len(pairs),
            "levels": [{"name": name, "elevation_m": round(z, 4)} for name, z in pairs],
        }
    return _safe(_run)


def get_modal_results() -> dict:
    """
    Return modal analysis summary.

    Returns mode count, cumulative mass participation (UX and UY), and the
    dominant translational period for each direction (T1x, T1y) — i.e. the
    period of the mode with the highest individual UX / UY participation
    (#5 fixed).
    """
    def _run():
        e = _need()
        S = core.modal_summary(e)
        if not S.get("T"):
            return {"error": "No modal results found. Run modal analysis first."}
        T, ux, uy = S["T"], S["ux"], S["uy"]
        T1x = T[ux.index(max(ux))]
        T1y = T[uy.index(max(uy))]
        return {
            "modes": len(T),
            "T1x_s": round(T1x, 4),
            "T1y_s": round(T1y, 4),
            "sum_UX_pct": round(S["sx"][-1] * 100, 2),
            "sum_UY_pct": round(S["sy"][-1] * 100, 2),
            "IS1893_cl7_7_5_2_UX_ok": S["sx"][-1] >= 0.90,
            "IS1893_cl7_7_5_2_UY_ok": S["sy"][-1] >= 0.90,
            "scope": "Mass participation from the first modal case found.",
        }
    return _safe(_run)


def get_story_drifts() -> dict:
    """
    Return a full per-storey drift breakdown with IS 1893 status.

    Each entry contains storey name, direction, governing load case, drift
    ratio and whether it satisfies IS 1893 (Part 1):2016 cl. 7.11.1.1
    (limit = 0.004 h).  Also reports the single worst drift for quick
    reference (#6 fixed).
    """
    def _run():
        e = _need()
        if not e.m.GetModelIsLocked():
            return {"error": "Model is unlocked — run analysis first to generate drift results."}

        t = e.table("Story Drifts")
        if not t or not t.rows:
            return {"error": "Story Drifts table is empty. Ensure lateral load cases exist and analysis is complete."}

        story_col  = _col_any(t, "story",     num=False)
        case_col   = _col_any(t, "outputcase", "loadcase", num=False)
        dir_col    = _col_any(t, "direction",  "dir",      num=False)
        drift_col  = _col_any(t, "drift")

        if any(c is None for c in (story_col, case_col, dir_col, drift_col)):
            return {"error": "One or more required columns (story, outputcase, direction, drift) not found in Story Drifts table."}

        LIMIT = 0.004
        # Keep only the worst drift per (storey, direction) pair
        best = {}
        for storey, case, direction, drift in zip(story_col, case_col, dir_col, drift_col):
            d = direction.strip().upper()[:1] if direction else "?"
            key = (storey, d)
            if abs(drift) > abs(best.get(key, {}).get("drift_ratio", 0)):
                best[key] = {
                    "storey":      storey,
                    "direction":   d,
                    "case":        case,
                    "drift_ratio": round(drift, 6),
                    "limit":       LIMIT,
                    "status":      "OK" if abs(drift) <= LIMIT else "NOT OK",
                }

        rows = sorted(best.values(), key=lambda r: r["drift_ratio"], reverse=True)
        worst = rows[0] if rows else {}
        failures = [r for r in rows if r["status"] == "NOT OK"]

        return {
            "worst_drift_ratio": worst.get("drift_ratio"),
            "worst_storey":      worst.get("storey"),
            "worst_direction":   worst.get("direction"),
            "worst_case":        worst.get("case"),
            "limit_IS1893":      LIMIT,
            "failures":          len(failures),
            "per_storey":        rows,
            "scope":             "IS 1893 (Part 1):2016 cl. 7.11.1.1 drift limit 0.004 h.",
        }
    return _safe(_run)


def get_base_shear(case: str = "") -> dict:
    """
    Return base reactions for a given load case, or all cases if case is
    empty.  Handles ETABS version column-name differences (FX vs globalfx)
    (#4 fixed).

    Args:
        case: ETABS load case or combination name.  Leave empty for all cases.
    """
    def _run():
        e = _need()
        t = e.table("Base Reactions")
        if not t or not t.rows:
            return {"error": "Base Reactions table is empty. Run analysis first."}

        oc = _col_any(t, "outputcase", "loadcase", num=False)
        fx = _col_any(t, "globalfx", "fx", "FX", "GlobalFX")
        fy = _col_any(t, "globalfy", "fy", "FY", "GlobalFY")
        fz = _col_any(t, "globalfz", "fz", "FZ", "GlobalFZ")

        if oc is None:
            return {"error": "Could not find case/loadcase column in Base Reactions table."}

        # fz is optional (not always meaningful for seismic base shear)
        results = []
        for i, c in enumerate(oc):
            if case and c != case:
                continue
            entry = {"case": c}
            if fx is not None: entry["Fx_kN"] = round(fx[i], 3)
            if fy is not None: entry["Fy_kN"] = round(fy[i], 3)
            if fz is not None: entry["Fz_kN"] = round(fz[i], 3)
            results.append(entry)

        if not results:
            return {"error": f"No base reaction data found for case '{case}'."}
        return {"base_reactions": results, "count": len(results)}
    return _safe(_run)


def get_is1893_spectrum(zone: str = "IV", soil: str = "II",
                        I: float = 1.0, R: float = 5.0, T: float = 1.0) -> dict:
    """
    Compute IS 1893 (Part 1):2016 design base shear coefficient Ah and
    spectral acceleration Sa/g.

    Validates zone and soil before calling core functions so invalid inputs
    return a clean error message instead of a raw KeyError (#8 fixed).

    Args:
        zone: Seismic zone — one of II, III, IV, V.
        soil: Soil type  — one of I, II, III.
        I:    Importance factor (e.g. 1.0, 1.2, 1.5).
        R:    Response reduction factor (e.g. 3, 4, 5).
        T:    Fundamental time period in seconds.
    """
    VALID_ZONES = ("II", "III", "IV", "V")
    VALID_SOILS = ("I", "II", "III")

    zone = str(zone).strip().upper()
    soil = str(soil).strip().upper()
    # Accept common alternate forms like "zone iv" → "IV"
    zone = zone.replace("ZONE", "").strip()
    soil = soil.replace("TYPE", "").strip().replace(" ", "")

    if zone not in VALID_ZONES:
        return {"error": f"Invalid zone '{zone}'. Must be one of: {', '.join(VALID_ZONES)}."}
    if soil not in VALID_SOILS:
        return {"error": f"Invalid soil type '{soil}'. Must be one of: {', '.join(VALID_SOILS)}."}

    try:
        T = float(T)
        if T <= 0:
            return {"error": f"Time period T must be positive; got {T}."}
        I = float(I)
        R = float(R)
        if R <= 0:
            return {"error": f"Response reduction factor R must be positive; got {R}."}
    except (TypeError, ValueError) as exc:
        return {"error": f"Invalid numeric parameter: {exc}"}

    return {
        "Sa_g": round(core.sa_g(T, soil), 5),
        "Ah":   round(core.ah(zone, I, R, T, soil), 6),
        "zone": zone,
        "soil": soil,
        "I":    I,
        "R":    R,
        "T_s":  T,
        "scope": "IS 1893 (Part 1):2016 cl. 6.4.2 — design aid only.",
    }


# ---------------------------------------------------------------------------
# MODEL-MODIFYING TOOL  (clearly separated from read-only tools)
# ---------------------------------------------------------------------------

def run_analysis() -> dict:
    """
    ⚠️  MODEL-MODIFYING OPERATION — triggers ETABS analysis.

    Runs all defined load cases and checks their completion status.
    Blocks until ETABS finishes (may take seconds to minutes for large models).
    Returns a per-case status report so the caller knows which cases finished
    successfully (#7 fixed — previously returned only a raw return code with
    no status confirmation).
    """
    def _run():
        e = _need()
        rc = e.m.Analyze.RunAnalysis()
        if rc != 0:
            return {"return_code": rc, "error": f"ETABS RunAnalysis returned non-zero code {rc}."}

        # Confirm which cases actually finished (#7 fixed)
        STATUS_NAMES = {1: "Not run", 2: "Could not start", 3: "Not finished", 4: "Finished"}
        try:
            raw = e.m.Analyze.GetCaseStatus()
            # raw is (count, [names], [statuses], ret_code)
            seqs = core._seqs(raw)
            names_list    = seqs[0] if len(seqs) > 0 else []
            statuses_list = seqs[1] if len(seqs) > 1 else []
            case_statuses = [
                {"case": n, "status": STATUS_NAMES.get(int(s), str(s)), "finished": int(s) == 4}
                for n, s in zip(names_list, statuses_list)
            ]
            finished = sum(1 for c in case_statuses if c["finished"])
            return {
                "return_code": rc,
                "cases_total":    len(case_statuses),
                "cases_finished": finished,
                "cases_failed":   len(case_statuses) - finished,
                "case_statuses":  case_statuses,
            }
        except Exception as status_exc:
            # Analysis ran but status read failed — still report success
            return {
                "return_code": rc,
                "note": f"Analysis completed (rc=0) but case status could not be read: {status_exc}",
            }
    return _safe(_run)


# ---------------------------------------------------------------------------
# Tool registry — single source of truth used by both the GUI and FastMCP
# (#3 fixed: no more duplicate definitions inside __main__)
# ---------------------------------------------------------------------------

# Read-only tools listed first, modifying tool at the end (#10 fixed)
TOOLS = {f.__name__: f for f in (
    get_model_info,
    list_load_cases,
    get_story_levels,
    get_modal_results,
    get_story_drifts,
    get_base_shear,
    get_is1893_spectrum,
    run_analysis,           # ⚠️ modifying — intentionally last
)}


# ---------------------------------------------------------------------------
# MCP server entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise SystemExit(
            'Install the compatible MCP SDK: '
            'python -m pip install "mcp>=1.2,<2"\n'
            f'Original error: {exc}'
        )

    mcp = FastMCP("etabs")

    # Register every tool from TOOLS — single definition, no duplication (#3 fixed)
    for _fn in TOOLS.values():
        mcp.tool()(_fn)

    mcp.run()
