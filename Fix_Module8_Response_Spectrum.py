"""Install the Module 8 correction in the Python version of AI Structural Analysis.

Run from Module 18 using Run Python file, or use py -3.12 <this file> --project
"path to AI Structural Analysis". Installation backs up and atomically updates
app_repair.py. The installer never starts, unlocks or edits an ETABS model.
"""
import argparse
import ast
import datetime
import math
import os
import shutil
import tempfile
from pathlib import Path


# BEGIN MODULE 8 CORRECTION
def rs8_spectrum(z, soil, I, R, upto=100):
    importance = positive(I, 'Importance factor')
    reduction = positive(R, 'Response reduction factor')
    end = positive(upto, 'Maximum spectrum period')
    if z not in core.ZONE or soil not in core.SOIL:
        raise ValueError('Select a listed zone and soil type.')
    grid = [0.0, .02, .04, .06, .08, .10, core.SOIL[soil][0]]
    grid.extend(round(.10 + .05 * k, 2) for k in range(1, 79))
    grid.extend((5, 6, 8, 10, 20, 50, 100))
    periods = sorted({t for t in grid if t < end} | {end})
    vertical = core.av(z, importance, reduction)
    return [(t, core.sa_g(t, soil), core.ah(z, importance, reduction, t, soil), vertical)
            for t in periods]


def rs8_draw(page):
    rows = core.spectrum(page.z.get(), page.so.get(), page.I.get(), page.R.get(), page.up.get())
    page.rows = rows
    page.tb.set([[f'{t:g}', f'{sa:.4f}', f'{h:.4f}', f'{v:.4f}'] for t, sa, h, v in rows])
    notes = [f'Zone {page.z.get()}, soil {page.so.get()}, I={page.I.get()}, R={page.R.get()}.']
    page.save(('T (s)', 'Sa/g', 'Ah', 'Av'), [list(row) for row in rows], notes)
    if page.fg:
        page.fg.clear()
        ax = page.fg.add_subplot(111)
        ax.plot([row[0] for row in rows], [row[2] for row in rows], label='Ah')
        ax.axhline(rows[0][3], ls='--', c='g', label='Av')
        ax.set_xlim(0, rows[-1][0])
        ax.set_xlabel('T (s)')
        ax.legend()
        page.cv.draw()


def rs8_case_types(page):
    api = page.e.m.LoadCases
    return {name: rc.checked(api.GetTypeOAPI(name), 'Read load case type for ' + name)[0]
            for name in page.e.names('case')}


def rs8_match(value, names):
    key = str(value).strip().casefold()
    return next((n for n in names if n.casefold() == key), None)


def rs8_read(page):
    types = rs8_case_types(page)
    modal_names = [n for n in types if types[n] == 3]
    chosen = rs8_match(page.mc.get(), modal_names) or rs8_match('Modal', modal_names)
    if chosen is None and len(modal_names) == 1:
        chosen = modal_names[0]
    page.mc.set(chosen or '')
    # Resolve only the documented X/Y case names (or an existing selection).
    # Never guess a lateral direction from the order of the ETABS case list.
    static_names = [n for n in types if types[n] == 1]
    for field, default, project_name in ((page.sx, 'EQX', 'earth quake in x d'),
                                          (page.sy, 'EQY', 'earth quake in y d')):
        current = field.get().strip()
        found = rs8_match(current, static_names)
        if not found and current.casefold() in ('', default.casefold()):
            found = rs8_match(project_name, static_names)
        if found:
            field.set(found)
    page.draw()
    if chosen:
        page.log('Read Model: modal case =', chosen)
    elif modal_names:
        raise rc.NeedsData('Enter one of the modal cases: ' + ', '.join(modal_names))
    else:
        raise rc.NeedsData('No Eigen or Ritz modal case exists in this model. Review Module 4.')
    try:
        raw = rc.checked(page.e.m.Analyze.GetCaseStatus(), 'Read analysis status')
        statuses = dict(zip(raw[1] or (), raw[2] or ()))
        labels = {1: 'Not run', 2: 'Could not start', 3: 'Not finished', 4: 'Finished'}
        for field in (page.mc, page.sx, page.sy, page.rx, page.ry):
            actual = rs8_match(field.get(), types)
            if actual:
                page.log(actual, '|', labels.get(statuses.get(actual), 'Status unavailable'))
    except Exception as exc:
        page.log('Case status unavailable:', str(exc))
    for field in (page.rx, page.ry):
        name = rs8_match(field.get(), types)
        if name and types[name] == 4:
            try:
                modal, loads, _ = rs8_read_case(page.e.m.LoadCases.ResponseSpectrum, name)
                unit = 'm/s²' if page.e.m.GetPresentUnits() == 6 else 'current ETABS acceleration units'
                for direction, function, sf, system, angle in zip(*loads):
                    page.log(f'{name} | {modal} | {system or "Global"} {direction} at {angle:g} deg'
                             f' | {function} | scale {sf:g} {unit}')
            except Exception as exc:
                page.log(name, 'load readback unavailable:', str(exc))


def rs8_function_names(page):
    h, v = page.fh.get().strip(), page.fv.get().strip()
    if not h or not v or h.casefold() == v.casefold():
        raise ValueError('Enter two distinct, nonempty horizontal and vertical function names.')
    return h, v


def rs8_export(page, names, damping, parent):
    import tempfile
    folder = Path(tempfile.mkdtemp(prefix='ETABS_spectra_', dir=parent))
    try:
        for column, filename in ((2, 'horizontal.txt'), (3, 'vertical.txt')):
            with (folder / filename).open('x', encoding='ascii', newline='\n') as stream:
                for row in page.rows:
                    stream.write(f'{row[0]:.15g} {row[column]:.15g}\n')
        instructions = (f'horizontal.txt = {names[0]}\nvertical.txt = {names[1]}\n'
                        f'Function damping ratio = {damping:.12g}\n'
                        'In ETABS use Define > Functions > Response Spectrum > From File.\n'
                        'Choose Period vs Value and zero header lines. Each file has two columns.\n'
                        'Convert to User-Defined to store the points in the model.\n')
        (folder / 'import.txt').write_text(instructions, encoding='utf-8')
    except Exception:
        import shutil
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return folder


def rs8_write(page):
    names = rs8_function_names(page)
    damping = positive(page.dm.get(), 'Function damping ratio')
    if damping >= 1:
        raise ValueError('Function damping ratio must be below 1.')
    page.draw()
    setter = getattr(page.e.m.Func.FuncRS, 'SetUser', None)
    if not callable(setter):
        directory = filedialog.askdirectory(parent=page, title='Choose a folder for the ETABS spectrum text files')
        if not directory:
            page.log('Spectrum export cancelled.')
            return
        folder = rs8_export(page, names, damping, directory)
        page.log('Function files exported to', str(folder))
        raise rc.NeedsData('Import horizontal.txt and vertical.txt in ETABS using Define > Functions > '
                           'Response Spectrum > From File, Period vs Value, zero header lines. '
                           'Import settings: ' + str(folder / 'import.txt'))
    rc.writable(page.e)
    written = []
    for i, name in enumerate(names, start=2):
        try:
            rc.checked(setter(name, len(page.rows), [r[0] for r in page.rows],
                              [r[i] for r in page.rows], damping), 'Write function ' + name)
        except Exception as exc:
            raise RuntimeError('Function write failed for ' + name + '. Earlier confirmed writes: '
                               + (', '.join(written) or 'none') + '. ' + str(exc)) from exc
        written.append(name)
    page.log('ETABS accepted both function definitions:', ', '.join(written))


def rs8_read_case(api, name):
    modal = rc.checked(api.GetModalCase(name), 'Read modal assignment ' + name)[0]
    loads = rc.checked(api.GetLoads(name), 'Read spectrum loads ' + name)
    if len(loads) not in (6, 7) or any(len(a or ()) != loads[0] for a in loads[1:6]):
        raise RuntimeError('Unexpected ETABS spectrum load fields for ' + name)
    eccentricity = rc.checked(api.GetEccentricity(name), 'Read eccentricity ' + name)[0]
    return (modal, tuple(tuple(a or ()) for a in loads[1:6]), float(eccentricity))


def rs8_same_case(state, modal, function, gravity, angle):
    assigned, (directions, functions, scales, systems, angles), eccentricity = state
    return (assigned.casefold() == modal.casefold() and len(directions) == 1
            and directions[0].upper() == 'U1' and functions[0].casefold() == function.casefold()
            and systems[0].casefold() in ('global', '') and math.isclose(scales[0], gravity, rel_tol=1e-8)
            and math.isclose(angles[0], angle, abs_tol=1e-6)
            and math.isclose(eccentricity, .05, abs_tol=1e-8))


def rs8_cases(page):
    types = rs8_case_types(page)
    modal = rs8_match(page.mc.get(), (n for n in types if types[n] == 3))
    if not modal:
        raise rc.NeedsData('Select an existing Eigen/Ritz case. Click Read Model.')
    function = rs8_match(page.fh.get(), rc.names(page.e.m.Func.GetNameList(), 'Read ETABS function names'))
    if not function:
        raise rc.NeedsData('Define the selected horizontal spectrum function in ETABS first.')
    gravity = positive(page.g.get(), 'Acceleration scale in m/s²')
    if page.e.m.GetPresentUnits() != 6:
        raise RuntimeError('Module 8 needs its kN-m unit context to read or write acceleration in m/s².')
    targets = [page.rx.get().strip(), page.ry.get().strip()]
    if any(not n for n in targets) or targets[0].casefold() == targets[1].casefold():
        raise ValueError('Enter distinct, nonempty X and Y response-spectrum case names.')
    api = page.e.m.LoadCases.ResponseSpectrum
    changes, rows = [], []
    for target, angle in zip(targets, (0.0, 90.0)):
        existing = rs8_match(target, types)
        if existing and types[existing] != 4:
            raise ValueError('Case name already has a different load type: ' + existing)
        name = existing or target
        before = rs8_read_case(api, name) if existing else None
        if before is None or not rs8_same_case(before, modal, function, gravity, angle):
            changes.append((name, angle, before))
        rows.append([name, modal, function, 'Global U1', angle, gravity])
    if changes:
        rc.writable(page.e)
    replaced = [name for name, _, before in changes if before is not None]
    if replaced and not messagebox.askyesno('Update existing response-spectrum cases',
            'Replace applied loads and set the selected modal case, acceleration scale and 5% '
            'diaphragm eccentricity for ' + ', '.join(replaced) + '?', parent=page):
        page.log('Response-spectrum case update cancelled.')
        return
    touched = []
    try:
        for name, angle, before in changes:
            if before is None:
                rc.checked(api.SetCase(name), 'Create spectrum case ' + name)
            touched.append((name, before))
            rc.checked(api.SetModalCase(name, modal), 'Assign modal case ' + name)
            rc.checked(api.SetLoads(name, 1, ['U1'], [function], [gravity], ['Global'], [angle]),
                       'Assign spectrum load ' + name)
            if before is None or not math.isclose(before[2], .05, abs_tol=1e-8):
                rc.checked(api.SetEccentricity(name, .05), 'Assign eccentricity ' + name)
            if not rs8_same_case(rs8_read_case(api, name), modal, function, gravity, angle):
                raise RuntimeError('Case readback differs for ' + name)
    except Exception as exc:
        errors = []
        for name, before in reversed(touched):
            try:
                if before is None:
                    rc.checked(page.e.m.LoadCases.Delete(name), 'Undo new case ' + name)
                else:
                    old_modal, old_loads, old_ecc = before
                    rc.checked(api.SetModalCase(name, old_modal), 'Restore modal case ' + name)
                    rc.checked(api.SetLoads(name, len(old_loads[0]), *[list(a) for a in old_loads]),
                               'Restore loads ' + name)
                    rc.checked(api.SetEccentricity(name, old_ecc), 'Restore eccentricity ' + name)
            except Exception as undo:
                errors.append(f'{name}: {undo}')
        detail = ' Rollback issues: ' + ', '.join(errors) if errors else ''
        raise RuntimeError(str(exc) + detail) from exc
    output(page, ('Case', 'Modal', 'Function', 'Direction', 'Angle (deg)', 'Scale (m/s²)'), rows,
           ['ETABS case definitions read back. No analysis run.'])
    page.log('Case definitions verified in ETABS:', ', '.join(r[0] for r in rows))


def rs8_install(app):
    cls = app['RSpec']
    original_scale = cls.scale
    original_build = cls.build

    def build(page):
        original_build(page)
        pending = [page.main]
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if widget.winfo_class() in ('TLabel', 'Label') and widget.cget('text') == 'g (model units)':
                widget.configure(text='g (m/s²)')

    def scale(page):
        types = rs8_case_types(page)
        for fields, case_type, kind in (((page.sx, page.sy), 1, 'linear static'),
                                        ((page.rx, page.ry), 4, 'response spectrum')):
            chosen = []
            for field in fields:
                name = rs8_match(field.get(), (n for n in types if types[n] == case_type))
                if name is None:
                    raise rc.NeedsData('Select an existing ' + kind + ' case for ' + field.get())
                field.set(name)
                chosen.append(name)
            if chosen[0] == chosen[1]:
                raise ValueError('Select distinct X and Y cases for ' + kind + '.')
        return original_scale(page)

    core.spectrum = rs8_spectrum
    cls.build, cls.draw, cls.read = build, rs8_draw, rs8_read
    cls.write, cls.cases, cls.scale = rs8_write, rs8_cases, scale
# END MODULE 8 CORRECTION


def apply_fix(folder):
    target = folder / 'app_repair.py'
    before = target.read_bytes()
    newline = '\r\n' if b'\r\n' in before else '\n'
    bom = before.startswith(b'\xef\xbb\xbf')
    source = before.decode('utf-8-sig').replace('\r\n', '\n')
    payload = Path(__file__).read_text(encoding='utf-8-sig')
    start, end = '# BEGIN MODULE 8 CORRECTION', '# END MODULE 8 CORRECTION'
    payload = payload[payload.index(start):payload.index(end) + len(end)]
    marker = '    rs8_install(g)  # Module 8 correction'
    if start in source or marker in source:
        if payload in source and source.count(marker) == 1:
            print('Module 8 correction already installed. Restart the Python app to load it.')
            return
        raise ValueError('Found a different Module 8 edit. Existing app_repair.py was preserved.')
    tree = ast.parse(source)
    hook = next((n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name == 'install_repairs'), None)
    if hook is None or "g['RSpec'].write=spectrum_write" not in ast.get_source_segment(source, hook):
        raise ValueError('This app_repair.py differs from the reviewed version. Share the current file.')
    lines = source.splitlines(keepends=True)
    lines.insert(hook.end_lineno, '\n' + marker + '\n')
    updated = ''.join(lines).rstrip('\n') + '\n\n\n' + payload + '\n'
    compile(updated, str(target), 'exec')
    encoded = updated.replace('\n', newline).encode('utf-8-sig' if bom else 'utf-8')
    backup = folder / ('app_repair.before_module8_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.bak')
    with backup.open('xb') as out:
        out.write(before)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=folder, delete=False) as out:
            temporary = Path(out.name)
            out.write(encoded)
            out.flush()
            os.fsync(out.fileno())
        shutil.copymode(target, temporary)
        if target.read_bytes() != before:
            raise ValueError('app_repair.py changed during installation. Run again with the current source.')
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print('Module 8 correction installed.', flush=True)
    print('Updated:', target, flush=True)
    print('Backup:', backup, flush=True)
    print('Restart the Python app, open 8 Response Spectrum, then click Read Model.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path)
    args = parser.parse_args()
    candidates = [args.project] if args.project else [
        Path.cwd(), Path(__file__).resolve().parent, Path.home() / 'OneDrive/Desktop/Ai Structural Analysis',
        Path.home() / 'Desktop/Ai Structural Analysis']
    valid = [p.resolve() for p in candidates if p and (p/'app.py').is_file() and (p/'app_repair.py').is_file()]
    if not valid and not args.project:
        from tkinter import Tk, filedialog
        root = Tk()
        root.withdraw()
        folder = filedialog.askdirectory(parent=root, title='Choose the app folder containing app.py and app_repair.py')
        root.destroy()
        if not folder:
            print('Cancelled.')
            return
        valid = [Path(folder)]
    if not valid:
        raise ValueError('Project folder not found. Use --project with the app folder.')
    apply_fix(valid[0])


if __name__ == '__main__':
    try:
        main()
    except (ValueError, SyntaxError, OSError) as exc:
        raise SystemExit(str(exc))
