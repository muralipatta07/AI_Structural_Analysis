"""Checked ETABS calls, strict tables and shared prerequisites for the audited app."""
import ctypes
import functools
import math
import re
from contextlib import contextmanager


class NeedsData(RuntimeError):
    """An operation needs model data, results or setup that is not available."""


def checked(result, label='ETABS call'):
    code = result[-1] if isinstance(result, (tuple, list)) else result
    if code != 0:
        raise RuntimeError(f'{label} returned {code!r}. The operation did not complete successfully.')
    return result


def finite(value, label, positive=False):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{label} must be numeric.')
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(f'{label} must be finite' + (' and positive.' if positive else '.'))
    return value


def method_spec(obj, name):
    cls = type(obj)
    for base in getattr(cls, '__mro__', (cls,)):
        for method in getattr(base, '_methods_', ()):
            if getattr(method, 'name', '').casefold() == name.casefold():
                return method
    return None


def marshal(obj, name, args):
    spec = method_spec(obj, name)
    if spec is None:
        return args
    result, index = list(args), 0
    for flags, argtype in zip(spec.paramflags, spec.argtypes):
        if not flags[0] & 1:
            continue
        if index >= len(result):
            break
        target = getattr(argtype, '_type_', None)
        if isinstance(result[index], (tuple, list)) and hasattr(target, 'create'):
            result[index] = target.create(result[index])
        index += 1
    return result


class CheckedAPI:
    def __init__(self, raw, engine, path='SapModel'):
        self._raw, self._engine, self._path = raw, engine, path

    def __getattr__(self, name):
        value = getattr(self._raw, name)
        if callable(value):
            @functools.wraps(value)
            def call(*args, **kwargs):
                output = value(*marshal(self._raw, name, args), **kwargs)
                if name.startswith(('Set', 'Add', 'Delete')) or name in ('RunAnalysis', 'Save', 'ImportFile', 'ExportFile', 'NewBlank', 'NewGridOnly'):
                    checked(output, self._path + '.' + name)
                    if name not in ('SetPresentUnits', 'SetCaseSelectedForOutput', 'SetComboSelectedForOutput', 'SetLoadCasesSelectedForDisplay', 'SetLoadCombinationsSelectedForDisplay', 'SetLoadPatternsSelectedForDisplay', 'SetSelected'):
                        self._engine.generation = getattr(self._engine, 'generation', 0) + 1
                        owner=getattr(self._engine,'owner_app',None)
                        if owner is not None:
                            owner.state.pop('modal',None)
                            owner.state.pop('beam_rebar_layouts',None)
                return output
            return call
        if hasattr(type(value), '_iid_') and hasattr(type(value), '_methods_'):
            return CheckedAPI(value, self._engine, self._path + '.' + name)
        return value


def normalize(text):
    return re.sub('[^a-z0-9]', '', str(text or '').casefold())


def names(result, label):
    checked(result, label)
    if isinstance(result, (tuple, list)) and isinstance(result[0], int) and result[0]==0:
        return []
    values=next((list(v) for v in result if isinstance(v, (tuple, list))), [])
    if any(not isinstance(v,str) or not v for v in values):
        raise NeedsData(label+': ETABS returned invalid names.')
    return values


def require_results(e, cases=()):
    if not e.m.GetModelIsLocked():
        raise NeedsData('Analysis results are not current: the model is unlocked. Set loads, mass, diaphragms/meshing and analysis cases, then run analysis in ETABS.')
    if cases:
        data = checked(e.m.Analyze.GetCaseStatus(), 'Read analysis status')
        arrays = [list(v) for v in data if isinstance(v, (tuple, list))]
        status = dict(zip(*arrays[:2])) if len(arrays) >= 2 else {}
        bad = [case for case in cases if case in e.names('case') and status.get(case) != 4]
        if bad:
            raise NeedsData('Analysis cases have not finished: ' + ', '.join(bad))


def writable(e):
    if e.m.GetModelIsLocked():
        raise NeedsData('Model is locked. Save it and unlock in ETABS before editing. Unlocking deletes analysis results.')


@contextmanager
def units(e):
    if not e.m:
        raise NeedsData('Connect to an ETABS model first.')
    old = e.m.GetPresentUnits()
    checked(e.m.SetPresentUnits(6), 'Set calculation units kN, m')
    try:
        yield
    finally:
        checked(e.m.SetPresentUnits(old), 'Restore display units')


def install(core):
    core.NeedsData = NeedsData
    core.checked = checked
    core.finite = finite
    core.fl = lambda value: finite(value, 'ETABS numerical value')
    core.require_results = require_results
    core.writable = writable
    core.units = units
    original_connect = core.Etabs.connect
    def connect(e, *args, **kwargs):
        result = original_connect(e, *args, **kwargs)
        e.generation = 0
        e.m = CheckedAPI(e.m, e)
        return result
    core.Etabs.connect = connect

    class Table:
        def __init__(self, fields, rows, key='ETABS table'):
            self.f, self.rows, self.key = list(fields), list(rows), key
        def idx(self, *tokens):
            terms = [normalize(t) for t in tokens]
            exact = ''.join(terms)
            for i, field in enumerate(self.f):
                if normalize(field) == exact and exact:
                    return i
            aliases={'ux':['u1'],'uy':['u2'],'uz':['u3'],'u1':['ux'],'u2':['uy'],'u3':['uz'],'globalfx':['fx'],'globalfy':['fy'],'globalfz':['fz'],'outputcase':['case'],'uniquename':['uniquename','uniquejointname']}
            if len(terms)==1:
                for alt in aliases.get(terms[0],[]):
                    for i,field in enumerate(self.f):
                        if normalize(field)==alt:return i
            for i, field in enumerate(self.f):
                if isinstance(field, str) and all(t in normalize(field) for t in terms):
                    return i
        def col(self, *tokens, num=True):
            index = self.idx(*tokens)
            if index is None:
                raise NeedsData(f'{self.key}: field {tokens} is unavailable. Available fields: {self.f}. Check model assignments, analysis/design results and table availability.')
            values = [row[index] for row in self.rows]
            if not num:
                return [str(v) if v is not None else '' for v in values]
            return [finite(v, f'{self.key} {tokens}') for v in values]
        def sel(self, token, value):
            index = self.idx(token)
            if index is None:
                raise NeedsData(f'{self.key}: cannot filter missing field {token}.')
            return Table(self.f, [r for r in self.rows if r[index] == value], self.key)
        def require(self):
            if not self.rows:
                raise NeedsData(f'{self.key}: no matching records. Run the required case/design and check selected cases, story and assignments.')
            return self
    core.TB = Table

    def table(e, key):
        e.select_all()
        try:
            raw = checked(e.m.DatabaseTables.GetTableForDisplayArray(key, [], 'All', 0, [], 0, []), f'Read table {key}')
        except RuntimeError as exc:
            raise NeedsData(f'{key} is unavailable. Check analysis/design and assignments. {exc}') from exc
        if not isinstance(raw, (tuple, list)) or len(raw) != 6:
            raise NeedsData(f'{key}: unexpected ETABS table response. Copy diagnostics.')
        _, version, fields, count, flat, code = raw
        fields, flat = list(fields or []), list(flat or [])
        if int(count) == 0:
            return Table([f for f in fields if isinstance(f, str) and f], [], key)
        if not fields or any(not isinstance(f, str) or not f for f in fields) or len(flat) != int(count)*len(fields):
            raise NeedsData(f'{key}: invalid table schema or record count. No calculation performed.')
        return Table(fields, [flat[i:i+len(fields)] for i in range(0, len(flat), len(fields))], key)
    core.Etabs.table = table

    def get_names(e, kind):
        method = e.m.LoadCases.GetNameList if kind == 'case' else e.m.RespCombo.GetNameList
        return names(method(), f'Read {kind} names')
    core.Etabs.names = get_names
    def select_all(e):
        # Refresh each time: case lists can change after modal/nonlinear writes.
        checked(e.m.DatabaseTables.SetLoadCasesSelectedForDisplay(e.names('case')), 'Select cases for tables')
        checked(e.m.DatabaseTables.SetLoadCombinationsSelectedForDisplay(e.names('combo')), 'Select combinations for tables')
    core.Etabs.select_all = select_all

    def run_analysis(e):
        if not e.m.GetModelFilename():
            raise NeedsData('Save the ETABS model before running analysis.')
        checked(e.m.Analyze.RunAnalysis(), 'Run ETABS analysis')
        statuses = checked(e.m.Analyze.GetCaseStatus(), 'Read case status')
        arrays = [list(x) for x in statuses if isinstance(x, (tuple, list))]
        if len(arrays) < 2 or not any(v == 4 for v in arrays[1]):
            raise NeedsData('No finished analysis cases were returned. Review the ETABS analysis log.')
        e._sel = False
        return 0
    core.Etabs.run_analysis = run_analysis

    def modal_summary(e, case=None):
        require_results(e, [case] if case else [])
        e.select_all()
        if not case:
            candidates=[n for n in e.names('case') if checked(e.m.LoadCases.GetTypeOAPI(n),'Read case type')[0]==3]
            if len(candidates)!=1:raise NeedsData('Select one explicit modal case; zero or multiple modal cases exist.')
            case=candidates[0]
        setup=e.m.Results.Setup
        saved_cases={n:bool(checked(setup.GetCaseSelectedForOutput(n),'Read output selection')[0]) for n in e.names('case')}
        saved_combos={n:bool(checked(setup.GetComboSelectedForOutput(n),'Read combination output selection')[0]) for n in e.names('combo')}
        try:
            checked(setup.DeselectAllCasesAndCombosForOutput(),'Select modal results only')
            checked(setup.SetCaseSelectedForOutput(case,True),'Select modal case')
            result = checked(e.m.Results.ModalParticipatingMassRatios(), 'Read modal mass ratios')
        finally:
            checked(setup.DeselectAllCasesAndCombosForOutput(),'Restore output selection')
            for name,selected in saved_cases.items():
                if selected:checked(setup.SetCaseSelectedForOutput(name,True),'Restore case output')
            for name,selected in saved_combos.items():
                if selected:checked(setup.SetComboSelectedForOutput(name,True),'Restore combination output')
        if not isinstance(result, (tuple, list)) or len(result) != 18:
            raise NeedsData('Unexpected modal mass-ratio response from ETABS.')
        ids = [i for i, name in enumerate(result[1] or []) if not case or name == case]
        if not ids:
            raise NeedsData('No modal results for the selected case. Define Eigen/Ritz, mass and run analysis first.')
        if not case and len({result[1][i] for i in ids}) > 1:
            raise NeedsData('Multiple modal cases exist. Select one explicit modal case.')
        ids.sort(key=lambda i: float(result[3][i]))
        output = {key: [finite(result[index][i], key) for i in ids] for key, index in
                  [('T',4),('ux',5),('uy',6),('rz',13),('sx',8),('sy',9),('sz',16)]}
        if any(t <= 0 for t in output['T']):
            raise NeedsData('Modal results contain a zero or negative period.')
        return output
    core.modal_summary = modal_summary

    def points(e):
        raw=checked(e.m.PointObj.GetAllPoints(),'Read model joints')
        count=int(raw[0])
        if count==0:return {}
        arrays=[list(x or []) for x in raw[1:5]]
        if any(len(a)!=count for a in arrays):raise NeedsData('Incomplete joint coordinate arrays from ETABS.')
        return {name:(finite(x,'X coordinate'),finite(y,'Y coordinate'),finite(z,'Z coordinate')) for name,x,y,z in zip(*arrays)}
    core.Etabs.points=points
    def frames(e):
        raw=checked(e.m.FrameObj.GetAllFrames(),'Read model frames')
        if int(raw[0])==0:return []
        arrays=[list(x) for x in raw if isinstance(x,(tuple,list))]
        if len(arrays)<5:raise NeedsData('Frame endpoint arrays unavailable.')
        return list(zip(arrays[3],arrays[4]))
    core.Etabs.frames=frames
    def frame_info(e):
        raw=checked(e.m.FrameObj.GetAllFrames(),'Read frame information')
        if int(raw[0])==0:return []
        arrays=[list(x) for x in raw if isinstance(x,(tuple,list))]
        if len(arrays)<2:raise NeedsData('Frame names and properties unavailable.')
        result=[];cache={}
        for name,prop in zip(arrays[0],arrays[1]):
            if prop not in cache:
                try:
                    shape=checked(e.m.PropFrame.GetRectangle(prop),'Read rectangular section')
                    cache[prop]=(finite(shape[2],'Section depth'),finite(shape[3],'Section width'))
                except Exception:
                    cache[prop]=(0,0)
            depth,width=cache[prop]
            orientation=checked(e.m.FrameObj.GetDesignOrientation(name),'Read member orientation')[0]
            mods=None
            try:
                obj=checked(e.m.FrameObj.GetModifiers(name),'Read object modifiers')
                sec=checked(e.m.PropFrame.GetModifiers(prop),'Read section modifiers')
                a=next((list(x) for x in obj if isinstance(x,(tuple,list))),None)
                b=next((list(x) for x in sec if isinstance(x,(tuple,list))),None)
                if a is not None and b is not None and len(a)==len(b)==8:mods=[float(x)*float(y) for x,y in zip(a,b)]
            except Exception:
                pass
            result.append(dict(name=name,prop=prop,t3=depth,t2=width,ori=orientation,mod=mods))
        return result
    core.Etabs.frame_info = frame_info

    original_plot = core.plot_model
    def plot_model(fig, e, *args, **kwargs):
        if not e.points():
            raise NeedsData('The model contains no joints to plot.')
        return original_plot(fig, e, *args, **kwargs)
    core.plot_model = plot_model

    original_sag=core.sa_g
    def sag(period, soil='II'):
        period=finite(period,'Period')
        if period<0 or soil not in core.SOIL:raise ValueError('Period must be nonnegative and soil must be I, II or III.')
        return original_sag(period,soil)
    core.sa_g=sag
    def ah(zone,importance,reduction,period,soil):
        if zone not in core.ZONE:raise ValueError('Choose seismic zone II, III, IV or V.')
        return core.ZONE[zone]/2*finite(importance,'I',True)/finite(reduction,'R',True)*sag(period,soil)
    core.ah=ah
    def spectrum(zone,soil,importance,reduction,upto=100):
        upto=finite(upto,'Spectrum end period',True)
        ah(zone,importance,reduction,0,soil)
        ts=[0,.02,.04,.06,.08,.10]+[round(.10+.05*k,2) for k in range(1,79)]+[4,5,6,8,10,20,50,100,upto]
        ts=sorted({t for t in ts if t<=upto})
        av=(2/3)*(core.ZONE[zone]/2)*float(importance)/float(reduction)*2.5
        return [(t,sag(t,soil),ah(zone,importance,reduction,t,soil),av) for t in ts]
    core.spectrum=spectrum
    original_ta=core.ta
    def ta(system,height,dimension,walls=()):
        height=finite(height,'Height',True);dimension=finite(dimension,'Plan dimension',True)
        if system not in core.SYSTEMS:raise ValueError('Select a known structural system.')
        if core.SYSTEMS[system][0]=='wall' and not walls:raise NeedsData('Wall period calculation needs verified wall geometry.')
        return original_ta(system,height,dimension,walls)
    core.ta=ta
