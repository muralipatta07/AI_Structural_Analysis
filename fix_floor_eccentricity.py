"""Restrict Module 3 geometric eccentricity to defined ETABS story elevations."""
import ast
import datetime
from pathlib import Path
import shutil
import tkinter as tk
from tkinter import filedialog, messagebox

METHOD = '''    def eccent(s):
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
'''


def updated_text(text):
    tree=ast.parse(text)
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Mass')
    fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='eccent')
    lines=text.splitlines(keepends=True)
    updated=''.join(lines[:fn.lineno-1])+METHOD.rstrip()+'\n'+''.join(lines[fn.end_lineno:])
    compile(updated,'mass_source_page.py','exec')
    return updated


def repair(selected):
    selected=Path(selected).resolve()
    if selected.name.casefold() not in ('start_app.cmd','app.py'):
        raise ValueError('Select your active Audited app start_app.cmd or app.py.')
    target=selected.parent/'mass_source_page.py'
    text=target.read_text(encoding='utf-8-sig')
    updated=updated_text(text)
    backup=target.with_name('mass_source_page_before_floor_fix_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.bak')
    shutil.copy2(target,backup)
    temporary=target.with_suffix('.floor_fix.tmp')
    temporary.write_text(updated,encoding='utf-8');temporary.replace(target)
    return selected.parent


if __name__=='__main__':
    root=tk.Tk();root.withdraw()
    try:
        selected=filedialog.askopenfilename(title='Select your active start_app.cmd',filetypes=[('App launcher','*.cmd'),('Python source','*.py')])
        if selected:
            folder=repair(selected)
            messagebox.showinfo('Floor-level calculation repaired',f'Updated: {folder}\n\nRestart this folder’s start_app.cmd. Open Module 3 > Design Eccentricity > Compute per floor. Only defined story elevations are now listed.')
    except Exception as exc:
        print('FIX STOPPED:',exc,flush=True);messagebox.showerror('Fix stopped',str(exc))
    finally:root.destroy()
