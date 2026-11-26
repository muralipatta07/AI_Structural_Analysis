"""Move the audited app's Diagnostics button above the connection label."""
import datetime
from pathlib import Path
import shutil
import tkinter as tk
from tkinter import filedialog, messagebox

OLD = "ttk.Button(s.side,text='Module Diagnostics',command=lambda:s.show('Module Diagnostics',Diagnostics)).pack(fill='x',padx=8,pady=4)"
NEW = "ttk.Button(s,text='Module Diagnostics',command=lambda:s.show('Module Diagnostics',Diagnostics)).pack(side='top',anchor='e',before=s.top,padx=8,pady=4)"


def repair(app_path):
    app_path = Path(app_path).resolve()
    if app_path.name.casefold() != 'app.py':
        raise ValueError('Select app.py from your extracted Audited Update folder.')
    target = app_path.parent / 'app_repair.py'
    text = target.read_text(encoding='utf-8-sig')
    if NEW in text:
        return 'The button fix is already installed. Close and reopen the app.'
    if text.count(OLD) != 1:
        raise ValueError('The expected Diagnostics button was not found. Select the Audited Update app.py.')
    fixed = text.replace(OLD, NEW)
    compile(fixed, str(target), 'exec')
    backup = target.with_name('app_repair_before_button_fix_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.bak')
    shutil.copy2(target, backup)
    temp = target.with_suffix('.button_fix.tmp')
    temp.write_text(fixed, encoding='utf-8')
    temp.replace(target)
    return 'Fixed. Reopen start_app.cmd. Module Diagnostics is now at the TOP RIGHT, above the connection label.'


if __name__ == '__main__':
    root = tk.Tk()
    root.withdraw()
    try:
        selected = filedialog.askopenfilename(title='Select app.py from your Audited Update folder', filetypes=[('Python source', '*.py')])
        if selected:
            result = repair(selected)
            print(result, flush=True)
            messagebox.showinfo('Diagnostics button', result)
    except Exception as exc:
        print('FIX STOPPED:', exc, flush=True)
        messagebox.showerror('Fix stopped', str(exc))
    finally:
        root.destroy()
