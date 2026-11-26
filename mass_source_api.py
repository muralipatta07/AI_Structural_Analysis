"""Named mass-source table adapter. No geometry, loads or analysis are changed."""
import math
import re
import ctypes
from dataclasses import dataclass

TABLE = 'Mass Source Definition'


def checked(result, label):
    code = result[-1] if isinstance(result, (tuple, list)) else result
    if code != 0:
        raise RuntimeError(f'{label}: ETABS returned {code}; {result!r}')
    return result


def normal(value):
    return re.sub('[^a-z0-9]', '', str(value).lower())


ALIASES = {
    'name': ('Name', 'MassSource', 'MassSourceName'),
    'default': ('IsDefault', 'Default'),
    'self': ('SourceSelf', 'IncludeElements', 'IncludeElementSelfMass', 'ElementSelfMass'),
    'added': ('SourceAdded', 'IncludeAddedMass', 'AdditionalMass'),
    'loads': ('SourceLoads', 'IncludeLoads', 'SpecifiedLoadPatterns'),
    'lateral': ('IncLateral', 'IncludeLateralMass', 'IncludeLateral', 'LateralMass'),
    'vertical': ('IncVertical', 'IncludeVerticalMass', 'IncludeVertical', 'VerticalMass'),
    'lump': ('LumpMass', 'LumpLateralMass', 'LumpLateralMassAtStoryLevels'),
    'move': ('MoveMass', 'MoveMassCentroid', 'AdjustMassCentroid', 'MoveCentroid'),
    'x': ('MoveMassCentroidX', 'MoveMassCentroidRatioX', 'MoveRatioX', 'RatioX', 'MoveX'),
    'y': ('MoveMassCentroidY', 'MoveMassCentroidRatioY', 'MoveRatioY', 'RatioY', 'MoveY'),
    'pattern': ('LoadPat', 'LoadPattern', 'LoadPatternName'),
    'factor': ('Multiplier', 'LoadMultiplier', 'ScaleFactor'),
}


def columns(fields):
    lookup = {normal(f): f for f in fields}
    return {key: next((lookup[normal(a)] for a in aliases if normal(a) in lookup), None)
            for key, aliases in ALIASES.items()}


def truth(value):
    return normal(value) in ('yes', 'true', '1')


def array_for(db, values):
    # ETABS type libraries declare these arrays as [in,out] SAFEARRAY(BSTR).
    # Supply the actual array type for COM instead of a pointer to a Python list.
    if isinstance(db, ctypes._Pointer):
        from comtypes import BSTR
        from comtypes.safearray import _midlSAFEARRAY
        return _midlSAFEARRAY(BSTR).create([str(x) for x in values])
    return list(values)


@dataclass
class Snapshot:
    version: int
    fields: list
    rows: list

    @property
    def mapping(self):
        return columns(self.fields)

    def groups(self):
        name_field = self.mapping['name']
        if not name_field:
            raise RuntimeError(f'Unrecognised mass-source name field: {self.fields}')
        result, current = {}, None
        for row in self.rows:
            name = str(row.get(name_field, '')).strip()
            if name:
                current = name
            if current:
                result.setdefault(current, []).append(row)
        return result


def snapshot(model):
    db = model.DatabaseTables
    result = checked(db.GetTableForEditingArray(
        TABLE, 'All', 0, array_for(db, []), 0, array_for(db, [])), 'Read mass-source table')
    if not isinstance(result, (tuple, list)) or len(result) != 5:
        raise RuntimeError(f'Unexpected mass-source table output: {result!r}')
    version, fields, count, flat, _ = result
    fields, flat = list(fields or []), list(flat or [])
    if not fields or len(flat) != int(count) * len(fields):
        raise RuntimeError('Mass-source table schema is unavailable or incomplete. Copy API diagnostics.')
    rows = [dict(zip(fields, flat[i:i+len(fields)])) for i in range(0, len(flat), len(fields))]
    return Snapshot(version, fields, rows)


def decode(snap, name):
    c = snap.mapping
    missing = [k for k in ('name','default','self','added','loads','lateral','vertical','lump','move') if not c.get(k)]
    if missing:
        raise RuntimeError('Mass-source fields could not be read: ' + ', '.join(missing) + '. No checkbox values have been assumed.')
    rows = snap.groups().get(name)
    if not rows:
        raise ValueError(f'Mass source not found: {name}')
    first = rows[0]
    settings = {k: truth(first.get(c[k], 'No')) for k in
                ('default', 'self', 'added', 'loads', 'lateral', 'vertical', 'lump', 'move')}
    settings['name'] = name
    for key in ('x', 'y'):
        settings[key] = float(first.get(c[key], 0) or 0) if c[key] else 0.0
    settings['patterns'] = []
    if c['pattern'] and c['factor']:
        for row in rows:
            pattern = str(row.get(c['pattern'], '')).strip()
            if pattern:
                settings['patterns'].append((pattern, float(row.get(c['factor'], 0) or 0)))
    return settings


def validate(settings):
    if not str(settings['name']).strip():
        raise ValueError('Enter a mass-source name.')
    if not any(settings[k] for k in ('self', 'added', 'loads')):
        raise ValueError('Select at least one mass definition.')
    if not (settings['lateral'] or settings['vertical']):
        raise ValueError('Select lateral mass or vertical mass.')
    pairs = settings['patterns'] if settings['loads'] else []
    names = [p[0] for p in pairs]
    if settings['loads'] and not pairs:
        raise ValueError('Select at least one contributing load pattern.')
    if len(names) != len(set(names)):
        raise ValueError('Duplicate contributing load pattern.')
    for name, factor in pairs:
        if not name or not math.isfinite(factor) or factor < 0:
            raise ValueError('Multipliers must be finite nonnegative numbers.')
    for key in ('x', 'y'):
        if not math.isfinite(settings[key]) or abs(settings[key]) > 1:
            raise ValueError('Centroid movement ratios must be between -1 and 1.')
    return pairs


def prepare(snap, settings):
    pairs = validate(settings)
    c = snap.mapping
    required = ['name', 'default', 'self', 'added', 'loads', 'lateral', 'vertical', 'lump']
    if pairs:
        required += ['pattern', 'factor']
    if settings['move']:
        required += ['move', 'x', 'y']
    missing = [k for k in required if not c[k]]
    if missing:
        raise RuntimeError('ETABS does not expose compatible fields for: ' + ', '.join(missing) +
                           '. No changes made. Copy API diagnostics. Available fields: ' + ', '.join(snap.fields))
    groups = snap.groups()
    name = settings['name'].strip()
    # Prevent an accidental second definition differing only by case.
    same = next((n for n in groups if n.casefold() == name.casefold()), None)
    if same and same != name:
        raise ValueError(f'Use the existing mass-source spelling: {same}')
    updated = []
    for existing_name, rows in groups.items():
        if existing_name == name:
            continue
        for row in rows:
            row = dict(row)
            if settings['default']:
                row[c['default']] = 'No'
            updated.append(row)
    original = groups.get(name, [])
    for i, pair in enumerate(pairs or [(None, None)]):
        row = dict(original[min(i, len(original)-1)]) if original else dict.fromkeys(snap.fields, '')
        row[c['name']] = name
        for key in ('default', 'self', 'added', 'loads', 'lateral', 'vertical', 'lump', 'move'):
            if c[key]:
                row[c[key]] = 'Yes' if settings[key] else 'No'
        for key in ('x', 'y'):
            if c[key]:
                row[c[key]] = str(settings[key])
        if c['pattern']:
            row[c['pattern']] = pair[0] or ''
        if c['factor']:
            row[c['factor']] = format(pair[1], '.15g') if pair[1] is not None else ''
        updated.append(row)
    if not any(truth(r.get(c['default'])) for r in updated):
        raise ValueError('At least one mass source must remain the default.')
    return updated


def apply(model, settings):
    if model.GetModelIsLocked():
        raise RuntimeError('Model is locked. Save it and unlock in ETABS before editing mass sources; unlocking removes results.')
    before = snapshot(model)
    rows = prepare(before, settings)
    flat = [str(row.get(f, '') or '') for row in rows for f in before.fields]
    staged = False
    try:
        db = model.DatabaseTables
        checked(db.SetTableForEditingArray(
            TABLE, before.version, array_for(db, before.fields), len(rows), array_for(db, flat)), 'Stage mass-source table')
        staged = True
        report = checked(model.DatabaseTables.ApplyEditedTables(True, 0, 0, 0, 0, ''), 'Apply mass-source table')
        staged = False
    except Exception:
        if staged:
            model.DatabaseTables.CancelTableEditing()
        raise
    if not isinstance(report, (tuple, list)) or len(report) != 6:
        raise RuntimeError(f'Changes may have applied, but import report is unrecognised: {report!r}. Inspect ETABS.')
    fatal, errors, warnings, information, import_log, _ = report
    if fatal or errors:
        raise RuntimeError(f'ETABS import reported {fatal} fatal and {errors} errors. Changes may be partial. {import_log}')
    actual = decode(snapshot(model), settings['name'].strip())
    for key in ('default', 'self', 'added', 'loads', 'lateral', 'vertical', 'lump'):
        if actual[key] != settings[key]:
            raise RuntimeError(f'Mass-source readback mismatch for {key}; inspect ETABS before continuing.')
    if before.mapping['move'] and actual['move'] != settings['move']:
        raise RuntimeError('Centroid adjustment readback mismatch.')
    if settings['move'] and any(abs(actual[k]-settings[k]) > 1e-8 for k in ('x', 'y')):
        raise RuntimeError('Centroid ratio readback mismatch.')
    expected = dict(settings['patterns']) if settings['loads'] else {}
    got = dict(actual['patterns']) if actual['loads'] else {}
    if set(got) != set(expected) or any(abs(got[n]-expected[n]) > 1e-8 for n in expected):
        raise RuntimeError('Mass-source contributing loads did not persist. Inspect ETABS; no success claimed.')
    return actual, warnings, str(import_log)
