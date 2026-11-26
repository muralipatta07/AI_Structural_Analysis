"""Repair Module 3 field mapping and source-list population. No ETABS edits."""
import ast
import datetime
from pathlib import Path
import shutil
import tkinter as tk
from tkinter import filedialog, messagebox


LOAD_SOURCE = '''    def load_source(s):
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
'''


def replace_function(text, class_name, name, replacement):
    tree = ast.parse(text)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    function = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
    lines = text.splitlines(keepends=True)
    return ''.join(lines[:function.lineno-1]) + replacement.rstrip() + '\n' + ''.join(lines[function.end_lineno:])


def backend_text(text):
    tree = ast.parse(text)
    node = next(n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'ALIASES' for t in n.targets))
    aliases = ast.literal_eval(node.value)
    for key, actual in {'lateral':'IncLateral', 'vertical':'IncVertical', 'move':'MoveMass'}.items():
        aliases[key] = tuple(dict.fromkeys((actual,) + tuple(aliases[key])))
    lines = text.splitlines(keepends=True)
    definition = 'ALIASES = {\n' + ''.join(f'    {k!r}: {tuple(v)!r},\n' for k,v in aliases.items()) + '}\n'
    text = ''.join(lines[:node.lineno-1]) + definition + ''.join(lines[node.end_lineno:])
    if 'Mass-source fields could not be read:' not in text:
        tree = ast.parse(text)
        fn = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='decode')
        assignment = next(n for n in fn.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='c' for t in n.targets))
        guard = "    missing = [k for k in ('name','default','self','added','loads','lateral','vertical','lump','move') if not c.get(k)]\n    if missing:\n        raise RuntimeError('Mass-source fields could not be read: ' + ', '.join(missing) + '. No checkbox values have been assumed.')\n"
        lines = text.splitlines(keepends=True); lines.insert(assignment.end_lineno, guard); text=''.join(lines)
    return text


def prepare(folder):
    folder = Path(folder).resolve()
    backend = folder/'mass_source_api.py'
    page = folder/'mass_source_page.py'
    api_text = backend_text(backend.read_text(encoding='utf-8-sig'))
    ui_text = replace_function(page.read_text(encoding='utf-8-sig'), 'Mass', 'load_source', LOAD_SOURCE)
    ui_text = ui_text.replace("details = {'connected': bool(s.e.m)}", "details = {'connected': bool(s.e.m), 'page_file': __file__, 'backend_file': api.__file__}")
    for name, text in [('mass_source_api.py',api_text),('mass_source_page.py',ui_text)]:
        compile(text,name,'exec')
    return {'mass_source_api.py':api_text,'mass_source_page.py':ui_text}


def repair(selected):
    selected=Path(selected).resolve()
    if selected.name.casefold() not in ('start_app.cmd','app.py'):
        raise ValueError('Select the start_app.cmd you use to launch the Audited app, or its app.py.')
    folder=selected.parent
    updates=prepare(folder)
    backup=folder/('mass_read_backup_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    backup.mkdir()
    for name in updates:shutil.copy2(folder/name,backup/name)
    try:
        for name,text in updates.items():
            temp=folder/(name+'.mass_read.tmp');temp.write_text(text,encoding='utf-8');temp.replace(folder/name)
    except Exception:
        for name in updates:shutil.copy2(backup/name,folder/name)
        raise
    return folder


if __name__=='__main__':
    root=tk.Tk();root.withdraw()
    try:
        selected=filedialog.askopenfilename(title='Select the start_app.cmd used to open your current Audited app',filetypes=[('App launcher','*.cmd'),('Python source','*.py')])
        if selected:
            folder=repair(selected)
            messagebox.showinfo('Module 3 reading repaired',f'Updated folder:\n{folder}\n\nLaunch THIS folder’s start_app.cmd, connect, then Module 3 > Read Model.\n\nExpected source flags must be checked against ETABS before Apply.')
            print('Updated:',folder,flush=True)
    except Exception as exc:
        print('REPAIR STOPPED:',exc,flush=True);messagebox.showerror('Repair stopped',str(exc))
    finally:root.destroy()
