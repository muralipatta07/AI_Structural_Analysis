"""Replacement Mass Source & Eccentricity page for AI Structural Analysis."""
import json
import math
import tkinter as tk
from tkinter import ttk, messagebox

import core
from ui import Page, Table, V, bar, entry
import mass_source_api as api


class Mass(Page):
    key = 'Mass Source and Loads'
    title = 'Mass Source & Eccentricity'
    sub = 'Read and define mass sources, review geometric eccentricity and compute a vertical seismic coefficient.'

    def build(s):
        bar(s.main, ('Read Model', lambda: s.run(s.read)), ('Copy API diagnostics', s.diag))
        notebook = ttk.Notebook(s.main)
        notebook.pack(fill='both', expand=True)
        a, b, c = (ttk.Frame(notebook, padding=8) for _ in range(3))
        for frame, title in ((a, 'Mass Source'), (b, 'Design Eccentricity'), (c, 'Vertical Seismic')):
            notebook.add(frame, text=title)
        s.snap = None
        s.name, s.dflt = V('MASS SOURCE'), tk.BooleanVar(value=True)
        top = ttk.Frame(a); top.pack(fill='x')
        ttk.Label(top, text='Mass source name').pack(side='left')
        s.names = ttk.Combobox(top, textvariable=s.name, width=27)
        s.names.pack(side='left', padx=8)
        s.names.bind('<<ComboboxSelected>>', lambda event: s.run(s.load_source))
        ttk.Button(top, text='Refresh list', command=lambda: s.run(s.refresh)).pack(side='left')
        ttk.Checkbutton(top, text='Use as the default mass source', variable=s.dflt).pack(side='left', padx=10)
        ttk.Label(a, text='Type a new name to create a source; choose an existing name to edit it.').pack(anchor='w', pady=6)
        body = ttk.Frame(a); body.pack(fill='x')
        left, right = ttk.Frame(body), ttk.Frame(body)
        left.pack(side='left', fill='x', expand=True); right.pack(side='right', padx=12)
        s.opt = {k: tk.BooleanVar(value=v) for k, v in (
            ('self', False), ('added', False), ('loads', True),
            ('lateral', True), ('vertical', True), ('lump', True), ('move', False))}
        definitions = ttk.LabelFrame(left, text='MASS DEFINITION', padding=6)
        definitions.pack(fill='x')
        for i, (key, text) in enumerate((('self', 'Element self mass'), ('added', 'Additional mass'), ('loads', 'Specified load patterns'))):
            ttk.Checkbutton(definitions, text=text, variable=s.opt[key]).grid(row=0, column=i, sticky='w', padx=8)
        options = ttk.LabelFrame(left, text='MASS OPTIONS', padding=6)
        options.pack(fill='x', pady=6)
        for i, (key, text) in enumerate((('lateral', 'Include lateral mass'), ('vertical', 'Include vertical mass'), ('lump', 'Lump lateral mass at storey levels'))):
            ttk.Checkbutton(options, text=text, variable=s.opt[key]).grid(row=0, column=i, sticky='w', padx=8)
        ttk.Checkbutton(options, text='Adjust diaphragm lateral mass to move mass centroid', variable=s.opt['move']).grid(row=1, column=0, columnspan=3, sticky='w', pady=6)
        ratios = ttk.Frame(options); ratios.grid(row=2, column=0, columnspan=3, sticky='w')
        s.mx, s.my = V('0.05'), V('0.05')
        entry(ratios, 'Ratio of diaphragm width in X', s.mx, 8)
        entry(ratios, 'in Y', s.my, 8, c=2)
        ttk.Label(options, text='X/Y movement is applied only when ETABS exposes compatible X/Y fields.', wraplength=650).grid(row=3, column=0, columnspan=3, sticky='w')
        for text, fn in (('Apply to ETABS', lambda: s.run(s.apply)), ('Default loads', s.defaults), ('Show guidance', s.guidance)):
            ttk.Button(right, text=text, command=fn, width=22).pack(fill='x', pady=5)
        ttk.Label(a, text='LOAD PATTERNS CONTRIBUTING MASS — double-click Use or Multiplier to edit.').pack(anchor='w', pady=(8, 3))
        s.lp = Table(a, ('Use', 'Load pattern', 'Type', 'Multiplier'), [55, 200, 140, 100], edit=('Multiplier',), toggle=('Use',), height=10)
        s.lp.pack(fill='both', expand=True)
        s.status = ttk.Label(a, text='Click Read Model before editing or applying.')
        s.status.pack(anchor='w', pady=3)

        s.ecc = V('0.05')
        entry(b, 'Geometric eccentricity ratio', s.ecc, 8)
        ttk.Button(b, text='Compute per floor', command=lambda: s.run(s.eccent)).grid(row=0, column=2)
        ttk.Label(b, text='Bounding-box dimensions only; no diaphragm assignment or seismic eccentricity is changed.', wraplength=800).grid(row=1, column=0, columnspan=4, sticky='w', pady=8)
        s.et = Table(b, ('Level', 'b_x (m)', 'b_y (m)', 'e_x (m)', 'e_y (m)'), [140, 100, 100, 100, 100])
        s.et.grid(row=2, column=0, columnspan=4, sticky='nsew'); b.rowconfigure(2, weight=1)
        s.vz, s.vi, s.vr, s.sag = V('IV'), V('1.0'), V('5.0'), V('2.5')
        entry(c, 'Zone', s.vz, 7, list(core.ZONE)); entry(c, 'Importance factor I', s.vi, 8, r=1)
        entry(c, 'Response reduction R', s.vr, 8, r=2); entry(c, 'Sa/g (project-specific)', s.sag, 8, r=3)
        ttk.Button(c, text='Compute Av', command=s.vert).grid(row=4, column=1, pady=8)
        s.vt = ttk.Label(c, text='Coefficient calculation only; no vertical load case is created.', wraplength=800)
        s.vt.grid(row=5, column=0, columnspan=4, sticky='w')

    def refresh(s):
        s.snap = api.snapshot(s.e.m)
        s.names.configure(values=list(s.snap.groups()))
        s.log('Mass sources:', ', '.join(s.snap.groups()) or '(none)')

    def read(s):
        m = s.e.m
        response = api.checked(m.LoadPatterns.GetNameList(), 'Read load patterns')
        names = next((list(x) for x in response if isinstance(x, (tuple, list))), [])
        inv = {v: k for k, v in core.LOAD_TYPES.items()}
        rows = []
        for name in names:
            result = api.checked(m.LoadPatterns.GetLoadType(name), 'Read load type')
            rows.append(['No', name, inv.get(result[0], 'Other'), '0'])
        s.lp.set(rows)
        s.refresh()
        groups = s.snap.groups()
        selected = s.name.get().strip()
        if selected not in groups:
            selected = next((name for name in groups if api.decode(s.snap, name)['default']), next(iter(groups), ''))
        if selected:
            s.name.set(selected); s.load_source()
        s.status.configure(text='Model read. Review settings and multipliers before Apply.')

    def load_source(s):
        # Read fresh source data even when only Refresh list was used.
        s.snap = api.snapshot(s.e.m)
        values = api.decode(s.snap, s.name.get().strip())
        s.dflt.set(values['default'])
        for key, variable in s.opt.items():
            variable.set(values[key])
        s.mx.set(values['x']); s.my.set(values['y'])
        factors = dict(values['patterns'])
        existing = {r[1]: r for r in s.lp.rows() if r[1]}
        # A refreshed source must populate its own contributions, even if the
        # general load-pattern list has not been read yet.
        for name in factors:
            if name not in existing:
                result = api.checked(s.e.m.LoadPatterns.GetLoadType(name), 'Read contributing pattern type')
                inv = {v: k for k, v in core.LOAD_TYPES.items()}
                existing[name] = ['No', name, inv.get(result[0], 'Other'), 0]
        s.lp.set([['Yes' if name in factors else 'No', name, row[2], factors.get(name, 0)] for name, row in existing.items()])
        s.status.configure(text='Source read from ETABS. Review before Apply. Module: ' + __file__)
        s.log('Read source:', values['name'], '| lateral:', values['lateral'], '| vertical:', values['vertical'], '| contributing patterns:', len(factors))

    def defaults(s):
        if not s.lp.rows():
            s.log('Click Read Model first.'); return
        out = []
        for row in s.lp.rows():
            name, kind = row[1], row[2]
            if kind in ('Dead', 'Super Dead'):
                factor = 1.0
            elif 'Live' in kind and 'roof' not in name.casefold():
                factor = 0.5 if '>' in name else 0.25
            else:
                factor = 0.0
            out.append(['Yes' if factor else 'No', name, kind, factor])
        s.lp.set(out)
        s.log('Editable trial multipliers loaded. Verify live-load intensity, occupancy and roof exclusions.')

    def apply(s):
        if not s.lp.rows():
            raise ValueError('Click Read Model first.')
        settings = {key: variable.get() for key, variable in s.opt.items()}
        settings.update(name=s.name.get().strip(), default=s.dflt.get(), x=float(s.mx.get()), y=float(s.my.get()),
                        patterns=[(r[1], float(r[3])) for r in s.lp.rows() if r[0] == 'Yes'])
        api.validate(settings)
        if settings['self'] and settings['loads']:
            for name, factor in settings['patterns']:
                sw = api.checked(s.e.m.LoadPatterns.GetSelfWTMultiplier(name), 'Read self-weight multiplier')[0]
                if factor > 0 and float(sw) > 0:
                    raise ValueError(f'Potential duplicate self mass: element self mass and {name} self-weight are both included. Review the mass definition.')
        actual, warnings, import_log = api.apply(s.e.m, settings)
        s.refresh()
        s.status.configure(text='Applied and read back successfully. Save the model in ETABS.')
        s.log('Applied and verified:', actual['name'], '| import warnings:', warnings)
        if import_log and warnings:
            s.log(import_log)
        s.save(('Load pattern', 'Multiplier'), actual['patterns'],
               ['Mass source: ' + actual['name'], 'Settings read back from ETABS; model masses still depend on assigned loads.'])

    def guidance(s):
        messagebox.showinfo('Mass-source guidance',
            'Choose element mass, added mass and/or mass from loads. Avoid counting structural self-weight twice.\n\n'
            'Default loads supplies editable trial factors matching the reference workflow. Verify live-load intensity, roof exclusions and the governing project requirements.\n\n'
            'Defining load patterns alone does not assign floor/wall loads. A mass-source definition does not establish the assembled building mass.')

    def eccent(s):
        ratio = float(s.ecc.get())
        if not math.isfinite(ratio) or not 0 <= ratio <= 1:
            raise ValueError('Enter a finite geometric ratio between 0 and 1.')
        model = s.e.m
        previous_units = model.GetPresentUnits()
        try:
            api.checked(model.SetPresentUnits(6), 'Read coordinates in metres')
            levels = core.levels(s.e)
            points = s.e.points()
            if not levels:
                raise ValueError('No defined ETABS story levels were returned.')
            if not points:
                raise ValueError('No model joints were returned.')
            rows = []
            used_elevations = set()
            tolerance = 0.001
            for name, elevation in levels:
                if not math.isfinite(float(elevation)):
                    raise ValueError('Invalid story elevation: ' + str(name))
                used_elevations.add(round(float(elevation), 3))
                selected = [(x, y) for x, y, z in points.values() if abs(z-elevation) <= tolerance]
                label = f'{name} (Z={elevation:.3f} m)'
                if len(selected) < 2:
                    rows.append([label, 'NOT CHECKED', 'NOT CHECKED', 'NOT CHECKED', 'NOT CHECKED'])
                    s.log('No sufficient joints at defined story level:', name)
                    continue
                bx = max(p[0] for p in selected)-min(p[0] for p in selected)
                by = max(p[1] for p in selected)-min(p[1] for p in selected)
                if bx <= 0 or by <= 0:
                    rows.append([label, f'{bx:.3f}', f'{by:.3f}', 'NOT CHECKED', 'NOT CHECKED'])
                    s.log('Incomplete plan extents at level:', name)
                    continue
                rows.append([label, f'{bx:.3f}', f'{by:.3f}', f'{ratio*bx:.3f}', f'{ratio*by:.3f}'])
            s.et.set(rows)
            s.log(f'Computed {len(rows)} defined levels only; intermediate joint elevations excluded. Values are geometric ratio x bounding-box dimensions, not applied seismic eccentricities.')
        finally:
            api.checked(model.SetPresentUnits(previous_units), 'Restore display units')

    def vert(s):
        try:
            zone = core.ZONE[s.vz.get()]
            importance, reduction, sag = float(s.vi.get()), float(s.vr.get()), float(s.sag.get())
            if any(not math.isfinite(v) or v <= 0 for v in (importance, reduction, sag)):
                raise ValueError('I, R and Sa/g must be finite positive numbers.')
            value = (2/3)*(zone/2)*(importance/reduction)*sag
            s.vt.configure(text=f'Av = (2/3)(Z/2)(I/R)(Sa/g) = {value:.5f}. Coefficient only; verify project applicability.')
        except Exception as ex:
            s.log('ERROR:', ex)

    def diag(s):
        details = {'connected': bool(s.e.m), 'page_file': __file__, 'backend_file': api.__file__}
        if s.e.m:
            try:
                details['model'] = s.e.m.GetModelFilename()
                snap = api.snapshot(s.e.m)
                details.update(fields=snap.fields, supported_options=snap.mapping, sources=list(snap.groups()))
            except Exception as ex:
                details['error'] = repr(ex)
        s.clipboard_clear(); s.clipboard_append(json.dumps(details, indent=2))
        s.log('API diagnostics copied to clipboard.')
