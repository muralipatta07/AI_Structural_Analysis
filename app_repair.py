"""Runtime repairs and module diagnostics for AI Structural Analysis."""
import datetime
import functools
import json
import math
import os
import subprocess
import sys
import threading
import traceback
from pathlib import Path
from tkinter import ttk, messagebox, filedialog

import core
import ui
import pages2
import robust_core as rc


def positive(value, label):
    return rc.finite(value, label, positive=True)


def output(page, headers, rows, notes=()):
    if not rows:
        raise rc.NeedsData('No matching records were returned. No passing result has been generated.')
    page.save(headers, rows, notes)


def modal_write(s, run=False):
    rc.writable(s.e)
    name = s.nm.get().strip()
    if not name:
        raise ValueError('Enter a modal-case name.')
    maximum, minimum = int(s.mx.get()), int(s.mn.get())
    if not 1 <= minimum <= maximum <= 10000:
        raise ValueError('Modes must satisfy 1 <= minimum <= maximum <= 10000.')
    subtype = s.st.get()
    if subtype not in ('Eigen', 'Ritz'):
        raise ValueError('Select Eigen or Ritz.')
    rows = s.ld.rows()
    if not rows or any(r[0] != 'Acceleration' or r[1] not in ('UX','UY','UZ','RX','RY','RZ') for r in rows):
        raise ValueError('Use acceleration loads UX/UY/UZ/RX/RY/RZ.')
    targets = [positive(r[2], 'Target participation percent') for r in rows]
    if any(v > 100 for v in targets):
        raise ValueError('Target participation must be at most 100 percent.')
    if subtype == 'Eigen':
        shift = rc.finite(s.fs.get(), 'Frequency shift')
        cutoff = rc.finite(s.cf.get(), 'Cutoff frequency')
        tolerance = positive(s.tol.get(), 'Modal tolerance')
        if cutoff < 0:
            raise ValueError('Cutoff frequency cannot be negative.')
    interface = getattr(s.e.m.LoadCases, 'Modal' + subtype)
    rc.checked(interface.SetCase(name), 'Create modal case')
    rc.checked(interface.SetNumberModes(name, maximum, minimum), 'Set modal count')
    if subtype == 'Eigen':
        rc.checked(interface.SetLoads(name, len(rows), [r[0] for r in rows], [r[1] for r in rows], targets, [r[3]=='Yes' for r in rows]), 'Set Eigen loads')
        rc.checked(interface.SetParameters(name, shift, cutoff, tolerance, 1), 'Set Eigen parameters')
    else:
        rc.checked(interface.SetLoads(name, len(rows), [r[0] for r in rows], [r[1] for r in rows], [0]*len(rows), targets), 'Set Ritz loads')
        s.log('Ritz selected: Eigen shift/cutoff/tolerance fields do not apply.')
    result = rc.checked(interface.GetNumberModes(name), 'Read back modal mode counts')
    if tuple(result[:2]) != (maximum, minimum):
        raise RuntimeError('Modal mode-count readback does not match requested settings.')
    output(s, ('Case','Subtype','Maximum modes','Minimum modes'), [[name,subtype,maximum,minimum]],
           ['Case setup only; mass participation has not been verified until analysis completes.'])
    s.log('Modal case created and count settings verified:', name, subtype)
    if run:
        s.e.run_analysis(); s.check()


def modal_read(s):
    subtype = s.st.get()
    if subtype not in ('Eigen','Ritz'):
        raise ValueError('Select Eigen or Ritz.')
    result = rc.checked(getattr(s.e.m.LoadCases,'Modal'+subtype).GetNumberModes(s.nm.get()), 'Read modal counts')
    s.mx.set(result[0]); s.mn.set(result[1]); s.log('Read modal counts:', result[:2])


def load_patterns_apply(s):
    rc.writable(s.e)
    rows = [r for r in s.t.rows() if r[0]=='Yes']
    if not rows:
        raise ValueError('Select at least one load pattern.')
    names = [r[1].strip() for r in rows]
    if any(not n for n in names) or len({n.casefold() for n in names}) != len(names):
        raise ValueError('Load-pattern names must be nonempty and unique.')
    for r in rows:
        if r[2] not in core.LOAD_TYPES:
            raise ValueError('Unknown load-pattern type: '+r[2])
        if rc.finite(r[3], 'Self-weight multiplier') < 0:
            raise ValueError('Self-weight multiplier cannot be negative in this editor.')
    m=s.e.m
    have={n.casefold():n for n in rc.names(m.LoadPatterns.GetNameList(),'Read load patterns')}
    done=[]
    for _, name, kind, weight, requested_code in rows:
        name=name.strip();weight=float(weight); actual=have.get(name.casefold())
        if actual:
            rc.checked(m.LoadPatterns.SetLoadType(actual,core.LOAD_TYPES[kind]),'Set load type')
            rc.checked(m.LoadPatterns.SetSelfWTMultiplier(actual,weight),'Set self weight')
            name=actual
        else:
            rc.checked(m.LoadPatterns.Add(name,core.LOAD_TYPES[kind],weight,True),'Add load pattern')
            have[name.casefold()]=name
        read_type=rc.checked(m.LoadPatterns.GetLoadType(name),'Verify load type')[0]
        read_weight=rc.checked(m.LoadPatterns.GetSelfWTMultiplier(name),'Verify self weight')[0]
        if read_type != core.LOAD_TYPES[kind] or abs(read_weight-weight)>1e-9:
            raise RuntimeError('Load-pattern readback mismatch: '+name)
        done.append([name,kind,weight,requested_code,'Not configured' if requested_code else 'Not requested'])
    output(s,('Pattern','Type','Self weight','Requested lateral code','Automatic lateral parameters'),done,
           ['Pattern definitions verified. Floor/wall loads and automatic lateral parameters are not assigned here.'])
    s.log('Applied and read back',len(done),'patterns. Configure auto lateral parameters separately in ETABS.')


def combinations_apply(s):
    rc.writable(s.e)
    mapping={r[0]:r[1].strip() for r in s.roles.rows()}
    rows=s.t.rows(); cases=set(s.e.names('case')); used=set()
    if not rows:
        raise ValueError('Generate or enter combinations first.')
    combo_names=[r[0].strip() for r in rows]
    if any(not n for n in combo_names) or len(set(combo_names))!=len(combo_names):
        raise ValueError('Combination names must be nonempty and unique.')
    for row in rows:
        for role,factor in zip(s.ROLES,row[2:]):
            if rc.finite(factor,'Combination factor'):
                used.add(role)
    missing=[f'{role} -> {mapping.get(role, "(unmapped)")}' for role in used if mapping.get(role) not in cases]
    if missing:
        raise rc.NeedsData('Map these roles to existing analysis cases before Apply: '+', '.join(missing))
    existing=set(s.e.names('combo'))
    if not s.rep.get() and existing.intersection(combo_names):
        raise ValueError('Some target combinations exist. Select Replace or rename them.')
    combo=s.e.m.RespCombo
    for row in rows:
        name=row[0].strip()
        # Delete only combinations being written, after all mappings validate.
        if name in existing:
            rc.checked(combo.Delete(name),'Replace combination '+name)
        rc.checked(combo.Add(name,0),'Create combination '+name)
        factors={}
        for role,factor in zip(s.ROLES,row[2:]):
            if float(factor):
                case=mapping[role];factors[case]=factors.get(case,0)+float(factor)
        for case,factor in factors.items():
            if factor:
                rc.checked(combo.SetCaseList(name,0,case,factor),'Set combination factor')
    output(s,s.CH,rows,['User-specified schedule applied; code applicability and load-case definitions require separate review.'])
    s.log('Applied',len(rows),'combinations after validating all case mappings.')


def spectrum_write(s):
    rc.writable(s.e);s.draw()
    damping=positive(s.dm.get(),'Damping ratio')
    if damping>=1:
        raise ValueError('Damping ratio must be less than 1.')
    for name,index in ((s.fh.get().strip(),2),(s.fv.get().strip(),3)):
        if not name:
            raise ValueError('Spectrum function names cannot be empty.')
        rc.checked(s.e.m.Func.FuncRS.SetUser(name,len(s.rows),[r[0] for r in s.rows],[r[index] for r in s.rows],damping),'Write spectrum '+name)
    s.log('Horizontal/vertical functions written. Case scale factors are accelerations in m/s².')


def spectrum_cases(s):
    rc.writable(s.e)
    modal=s.mc.get().strip()
    if modal not in s.e.names('case'):
        raise rc.NeedsData('Create the selected modal case before writing response-spectrum cases.')
    case_type=rc.checked(s.e.m.LoadCases.GetTypeOAPI(modal),'Read modal case type')[0]
    if case_type!=3:
        raise ValueError('Selected case is not an Eigen/Ritz modal case.')
    functions=set(rc.names(s.e.m.Func.GetNameList(),'Read function names'))
    if s.fh.get() not in functions:
        raise rc.NeedsData('Write the horizontal spectrum function first.')
    gravity=positive(s.g.get(),'Acceleration scale in m/s²')
    interface=s.e.m.LoadCases.ResponseSpectrum
    for name,angle in ((s.rx.get().strip(),0.0),(s.ry.get().strip(),90.0)):
        if not name:
            raise ValueError('Response-spectrum case names cannot be empty.')
        rc.checked(interface.SetCase(name),'Create response-spectrum case')
        rc.checked(interface.SetModalCase(name,modal),'Assign modal case')
        rc.checked(interface.SetLoads(name,1,['U1'],[s.fh.get()],[gravity],['Global'],[angle]),'Assign response-spectrum direction')
        rc.checked(interface.SetEccentricity(name,0.05),'Assign accidental eccentricity ratio')
    s.log('Response-spectrum case settings written. Review modal/directional combination rules in ETABS before analysis.')
    output(s,('Case','Direction','Function','Acceleration scale'),[[s.rx.get(),'X',s.fh.get(),gravity],[s.ry.get(),'Y',s.fh.get(),gravity]],['Case settings only; no analysis result or compliance established.'])


def spectrum_scale(s):
    core.require_results(s.e,[s.sx.get(),s.sy.get(),s.rx.get(),s.ry.get()])
    table=s.e.table('Base Reactions').require()
    oc,fx,fy=table.col('outputcase',num=False),table.col('globalfx'),table.col('globalfy')
    rows=[]
    for direction,static,dynamic,forces in [('X',s.sx.get(),s.rx.get(),fx),('Y',s.sy.get(),s.ry.get(),fy)]:
        a=[abs(v) for c,v in zip(oc,forces) if c==static]
        b=[abs(v) for c,v in zip(oc,forces) if c==dynamic]
        if not a or not b or max(a)<=0 or max(b)<=0:
            rows.append([direction,'Unavailable','Unavailable','--','NOT CHECKED'])
        else:
            ratio=max(1.0,max(a)/max(b));rows.append([direction,f'{max(a):.3f}',f'{max(b):.3f}',f'{ratio:.4f}','OK' if ratio<=1.0001 else 'Scale up'])
    s.sc.set(rows)
    output(s,('Direction','Static shear kN','Dynamic shear kN','Factor','Status'),rows,
           ['Comparison only; no case scale factors changed. Verify static empirical-period basis separately.'])


def drift_go(s):
    selected=set(s.sel[0]+s.sel[1])
    if not selected:
        raise rc.NeedsData('Select lateral load cases in X/Y first.')
    core.require_results(s.e,list(selected))
    drift_limit=1/positive(s.dl.get(),'Drift denominator')
    disp_den=positive(s.dv.get(),'Displacement denominator')
    excluded={n.strip() for n in s.ex.get().split(',')}
    table=s.e.table('Story Drifts').require()
    best=s.worst(table.col('story',num=False),table.col('outputcase',num=False),table.col('drift'),table.col('direction',num=False),excluded,s.sel)
    if not best:
        raise rc.NeedsData('No drift results match the selected cases/directions. Check assignments and completed analysis.')
    rows=[[story,direction,case,f'{value:.6f}',f'{drift_limit:.6f}','OK' if value<=drift_limit else 'NOT OK'] for (story,direction),(value,case) in best.items()]

    s.td.set(rows)
    displacements = []
    levels = dict(core.levels(s.e))
    base = min(levels.values())

    if s.dchk.get():
        try:


            data = s.e.table('Diaphragm Center Of Mass Displacements').require()

            stories,cases=data.col('story',num=False),data.col('outputcase',num=False)
            for direction,field in [('X','ux'),('Y','uy')]:
                worst={}
                for story,case,value in zip(stories,cases,data.col(field)):
                    if story not in excluded and case in s.sel[0 if direction=='X' else 1]:
                        if abs(value)>=worst.get(story,(0,''))[0]:worst[story]=(abs(value),case)
                for story,(value,case) in worst.items():
                    if story not in levels:
                        raise rc.NeedsData('Unknown story in displacement table: '+story)
                    limit=(levels[story]-base)/disp_den
                    displacements.append([story,direction,case,f'{value*1000:.3f}',f'{limit*1000:.3f}','OK' if value<=limit else 'NOT OK'])
        except rc.NeedsData as exc:
            s.log('Displacements NOT CHECKED:',exc)
            displacements=[['All','--','--','--','--','NOT CHECKED']]
        if not displacements:displacements=[['All','--','--','--','--','NOT CHECKED']]
    s.tp.set(displacements)
    all_rows=rows+displacements
    bad=[r for r in all_rows if r[5]=='NOT OK'];unknown=any(r[5]=='NOT CHECKED' for r in all_rows)
    ui.put(s.wn,'\n'.join(str(r) for r in bad) or ('Displacement results are unavailable.' if unknown else 'No exceedances in extracted results.'))
    ui.put(s.rs,f'{len(rows)} drift results; {len(bad)} exceedances; incomplete checks: {unknown}.')
    output(s,('Story','Direction','Case','Value (drift ratio / displacement mm)','Limit','Status'),all_rows,
           ['User-set comparison limits. Confirm design-level drift actions, combinations and diaphragm assumptions.'])


def torsion_go(s):
    if not(s.sel[0] or s.sel[1]):
        raise rc.NeedsData('Select lateral load cases before reading torsion results.')
    core.require_results(s.e,s.sel[0]+s.sel[1])
    table=s.e.table('Diaphragm Max Over Avg Drifts').require()
    lim=positive(s.lim.get(),'Torsion ratio limit');excluded={v.strip() for v in s.ex.get().split(',')};best={}
    for story,case,item,ratio in zip(table.col('story',num=False),table.col('outputcase',num=False),table.col('item',num=False),table.col('ratio')):
        direction=item.strip()[-1:].upper()
        if story not in excluded and direction in ('X','Y') and case in s.sel[0 if direction=='X' else 1]:
            if ratio>=best.get((story,direction),(0,''))[0]:best[(story,direction)]=(ratio,case)
    if not best:
        raise rc.NeedsData('No diaphragm drift-ratio results match selected cases. Review diaphragm definitions and analysis.')
    rows=[[story,direction,case,f'{ratio:.4f}',lim,'OK' if ratio<=lim else 'NOT OK'] for (story,direction),(ratio,case) in best.items()]
    s.tr.set(rows);s.tx.set([r for r in rows if r[1]=='X']);s.ty.set([r for r in rows if r[1]=='Y'])
    output(s,s.H,rows,['User-set ratio comparison; complete irregularity classification and applicability are not established by this table alone.'])


class PDeltaPage(ui.Page):
    key='P-Delta';title='P-Delta gravity case'
    sub='Define a nonlinear static gravity case with P-Delta. This is not the preset initial-P-Delta E2K workflow.'
    def build(s):
        ui.bar(s.main,('Read load patterns',lambda:s.run(s.read)),('Create / update gravity case',lambda:s.run(s.apply)))
        s.name=ui.V('AS_PDELTA')
        top=ttk.Frame(s.main);top.pack(fill='x');ui.entry(top,'Case name',s.name,25)
        s.t=ui.Table(s.main,('Use','Pattern','Factor','Type'),[60,200,100,140],edit=('Factor',),toggle=('Use',));s.t.pack(fill='both',expand=True)
    def read(s):
        inv={v:k for k,v in core.LOAD_TYPES.items()};rows=[]
        for name in rc.names(s.e.m.LoadPatterns.GetNameList(),'Read patterns'):
            kind=rc.checked(s.e.m.LoadPatterns.GetLoadType(name),'Read pattern type')[0]
            rows.append(['Yes' if kind in (1,2) else 'No',name,1.0,inv.get(kind,'Other')])
        s.t.set(rows);s.log('Review factors for the project. Defaults are 1.0 dead/super-dead only; no live or lateral load assumed.')
    def apply(s):
        rc.writable(s.e);name=s.name.get().strip();rows=[r for r in s.t.rows() if r[0]=='Yes']
        if not name or not rows:raise ValueError('Enter a case name and select gravity patterns.')
        factors=[positive(r[2],'Gravity factor') for r in rows]
        nl=s.e.m.LoadCases.StaticNonlinear
        rc.checked(nl.SetCase(name),'Create P-Delta case')
        rc.checked(nl.SetInitialCase(name,''),'Set zero initial state')
        rc.checked(nl.SetLoads(name,len(rows),['Load']*len(rows),[r[1] for r in rows],factors),'Set gravity loads')
        rc.checked(nl.SetGeometricNonlinearity(name,1),'Enable P-Delta')
        output(s,('Case','Pattern','Factor'),[[name,r[1],v] for r,v in zip(rows,factors)],['Nonlinear gravity P-Delta case defined only. Run and inspect convergence; subsequent cases are not automatically linked to this stiffness.'])
        s.log('Created gravity P-Delta case; no automatic analysis or linking performed.')



def pier_sections(e, story):
    result = []
    m = e.m

    labels = rc.names(
        m.PierLabel.GetNameList(),
        'Read pier labels'
    )

    for name in labels:
        assigned = False

        # Check area-object pier assignments
        for obj in rc.names(m.AreaObj.GetNameList(), 'Read areas'):
            info = m.AreaObj.GetPier(obj)
            if not isinstance(info, (list, tuple)) or info[-1] != 0:
                raise RuntimeError(f'Cannot read area pier assignment: {obj}')
            if info[0] == name:
                assigned = True
                break

        # Check frame-object pier assignments
        if not assigned:
            for obj in rc.names(m.FrameObj.GetNameList(), 'Read frames'):
                info = m.FrameObj.GetPier(obj)
                if not isinstance(info, (list, tuple)) or info[-1] != 0:
                    raise RuntimeError(f'Cannot read frame pier assignment: {obj}')
                if info[0] == name:
                    assigned = True
                    break

        # Skip only unused pier labels
        if not assigned:
            continue

        data = rc.checked(
            m.PierLabel.GetSectionProperties(name),
            'Read pier sections ' + name
        )

        for i, level in enumerate(data[1] or []):
            if level == story:
                result.append((
                    name,
                    rc.finite(data[5][i], 'Pier width'),
                    rc.finite(data[6][i], 'Pier thickness'),
                    rc.finite(data[2][i], 'Pier angle')
                ))

    return result



def density_calc(s):
    levels=core.levels(s.e);elevations=dict(levels)
    if s.pl.get() not in elevations:
        raise ValueError('Read model and select a plinth level.')
    z0=elevations[s.pl.get()];above=[n for n,z in levels if z>z0+1e-6]
    if not above:
        raise ValueError('Choose a plinth below the top storey.')
    rows=[];ax=ay=0.0
    for name,width,thick,angle in pier_sections(s.e,above[0]):
        if width<=0 or thick<=0:
            raise rc.NeedsData('Pier dimensions unavailable; no thickness has been guessed: '+name)
        if s.w13.get() and (thick<.150 or width/thick<4):
            continue
        area=width*thick;cx=math.cos(math.radians(angle))**2
        rows.append([len(rows)+1,name,'Pier',f'{width*1000:.1f}',f'{thick*1000:.1f}',f'{angle:.2f}',f'{area:.5f}',f'{area*cx:.5f}',f'{area*(1-cx):.5f}','Section geometry only'])
        ax+=area*cx;ay+=area*(1-cx)
    points=s.e.points()
    if s.blade.get():
        for frame in s.e.frame_info():
            if frame['ori']!=1 or min(frame['t2'],frame['t3'])<=0 or max(frame['t2'],frame['t3'])/min(frame['t2'],frame['t3'])<4:
                continue
            ends=rc.checked(s.e.m.FrameObj.GetPoints(frame['name']),'Read blade endpoints')
            if min(points[ends[0]][2],points[ends[1]][2])>z0+1e-6 or max(points[ends[0]][2],points[ends[1]][2])<=z0+1e-6:
                continue
            pier=rc.checked(s.e.m.FrameObj.GetPier(frame['name']),'Read frame pier assignment')[0]
            if pier:continue
            transform=rc.checked(s.e.m.FrameObj.GetTransformationMatrix(frame['name']),'Read blade axes')[0]
            if len(transform)!=9:raise rc.NeedsData('Blade local-to-global axes unavailable.')
            axis=1 if frame['t2']>=frame['t3'] else 2
            px=float(transform[axis])**2;py=float(transform[3+axis])**2
            area=frame['t2']*frame['t3'];width=max(frame['t2'],frame['t3']);thick=min(frame['t2'],frame['t3'])
            rows.append([len(rows)+1,frame['name'],'Blade',width*1000,thick*1000,'From axes',area,area*px,area*py,'Unlabelled frame crossing plinth; classify applicability separately'])
            ax+=area*px;ay+=area*py
    area=rc.finite(s.area.get(),'Plinth area')
    if area<0:raise ValueError('Plinth area cannot be negative.')
    if area==0:
        pts=[(x,y) for x,y,z in points.values() if abs(z-z0)<1e-3]
        if not pts:raise rc.NeedsData('No joints at plinth. Enter a verified plinth area.')
        area=(max(p[0] for p in pts)-min(p[0] for p in pts))*(max(p[1] for p in pts)-min(p[1] for p in pts))
    if area<=0:raise ValueError('Plinth area must be positive.')
    limit=positive(s.mn.get(),'Density threshold');dx,dy=100*ax/area,100*ay/area
    compare=lambda v:'OK' if v>=limit else 'NOT OK'
    s.tab.set(rows);s.res.configure(text=f'Area {area:.3f} m²; X {dx:.3f}%; Y {dy:.3f}%. Geometry comparison only.')
    output(s,('Direction','Density percent','User threshold','Comparison'),[['X',dx,limit,compare(dx)],['Y',dy,limit,compare(dy)]],
           ['Pier-labelled sections plus explicitly selected unlabelled blade frames. Bounding-box area is approximate; review actual footprint. Not a complete wall-system/code check.'])


def shear_calc(s):
    case=s.cs.get().strip();story=s.sto.get().strip()
    if not case or not story:raise ValueError('Read cases/storeys and select both.')
    core.require_results(s.e,[case])
    if case not in s.e.names('case') or rc.checked(s.e.m.LoadCases.GetTypeOAPI(case),'Read case type')[0]!=1:
        raise rc.NeedsData('Directional shear shares currently require a linear static analysis CASE. Response-spectrum, nonlinear and combination component envelopes cannot be treated as simultaneous signed forces.')
   
    table=s.e.table('Element Forces - Columns').sel('story',story).sel('outputcase',case).require()

    un,station=table.col('uniquename',num=False),table.col('station')
    forces=list(zip(table.col('p'),table.col('v2'),table.col('v3')))
    points=s.e.points();grouped={}
    for name,st,force in zip(un,station,forces):grouped.setdefault(name,[]).append((st,force))
    direction=0 if s.dr.get()=='X' else 1;items=[]
    for name,values in grouped.items():
        ends=rc.checked(s.e.m.FrameObj.GetPoints(name),'Read column endpoints')
        pick=min if points[ends[0]][2]<=points[ends[1]][2] else max
        force=pick(values,key=lambda x:x[0])[1]
        matrix=rc.checked(s.e.m.FrameObj.GetTransformationMatrix(name),'Read column transformation')[0]
        if len(matrix)!=9:raise rc.NeedsData('Column transformation matrix unavailable: '+name)
        value=sum(float(matrix[direction*3+i])*force[i] for i in range(3))
        pier=rc.checked(s.e.m.FrameObj.GetPier(name),'Read column pier assignment')[0]

        if pier is None or str(pier).strip().lower() in ("", "none"):
            items.append([name, 'Column', value])

    sections={n:angle for n,w,t,angle in pier_sections(s.e,story)}
    if sections:
        piers=s.e.table('Pier Forces').sel('story',story).sel('outputcase',case).require()
        for name,loc,v2,v3 in zip(piers.col('pier',num=False),piers.col('location',num=False),piers.col('v2'),piers.col('v3')):
            if 'bot' not in loc.casefold():continue
            if name not in sections:raise rc.NeedsData('Pier orientation unavailable: '+name)
            angle=math.radians(sections[name]);value=v2*math.cos(angle)-v3*math.sin(angle) if direction==0 else v2*math.sin(angle)+v3*math.cos(angle)
            items.append([name,'Pier',value])
    total=sum(abs(r[2]) for r in items)
    if total<=1e-12:raise rc.NeedsData('Selected direction has zero shear; shares are undefined.')
    rows=[[n,kind,f'{v:.4f}',f'{100*abs(v)/total:.3f}'] for n,kind,v in items]
    s.tab.set(rows);s.fc=1.0
    s.res.configure(text='Directional force magnitudes extracted. Independent 25% frame capacity is NOT CHECKED.')
    output(s,('Member','Kind','Global shear kN','Share of sum of magnitudes percent'),rows,
           ['Linear-static force extraction only. Not an independent frame-capacity check or proof of dual-system classification.'])


def detailing_go(s):
    core.require_results(s.e)
    if not s.e.m.DesignConcrete.GetResultsAvailable():
        raise rc.NeedsData('Run concrete frame design in ETABS before detailing.')
    params={k:rc.finite(v.get(),k) if k!='story' else v.get() for k,v in s.p.items()}
    for key in ('cover','sd','clr','tmin','tmax','send','smid','legs'):
        positive(params[key],key)
    if params['tmin']>params['tmax']:raise ValueError('Minimum bar diameter exceeds maximum.')
    if params['legs']!=int(params['legs']):raise ValueError('Number of tie legs must be an integer.')

    rows = []
    layouts = []
    s.log("DETAILING DEBUG - Selected story:", repr(params["story"]))

    for frame in s.e.frame_info():
        if frame['ori'] != 2:
            continue
    
        name=frame['name'];label=rc.checked(s.e.m.FrameObj.GetLabelFromName(name),'Read beam story')
        beam,story=label[0],label[1]
        if params['story'] and story!=params['story']:continue
        data=rc.checked(s.e.m.DesignConcrete.GetSummaryResultsBeam(name),'Read beam design '+name)
        if not data[0]:continue
        errors=[str(x) for x in (data[13] or []) if str(x).strip()]
        if errors:raise rc.NeedsData(f'Beam {name} has ETABS design errors: '+ '; '.join(errors))
        top=max(rc.finite(x,'Top required steel') for x in data[4])*1e6
        bottom=max(rc.finite(x,'Bottom required steel') for x in data[6])*1e6
        shear=max(rc.finite(x,'Shear required steel') for x in data[8])*1e3
        b,d=frame['t2']*1000,frame['t3']*1000
        if min(b,d)<=0:raise rc.NeedsData('Rectangular beam dimensions unavailable: '+name)
        tp=core.pick_bars(top,b,params['cover'],params['sd'],params['tmin'],params['tmax'],params['clr'])
        bt=core.pick_bars(bottom,b,params['cover'],params['sd'],params['tmin'],params['tmax'],params['clr'])
        if tp[2]>2 or bt[2]>2:raise rc.NeedsData(f'Beam {name}: requested steel does not fit the permitted two-layer arrangement. Revise the section/bar settings.')
        se,sm=core.stirrup(shear,params['sd'],int(params['legs']),int(params['send']),int(params['smid']))
        fmt=lambda bars:f'{bars[0]}-T{bars[1]}' + (' (2L)' if bars[2]>1 else '')
        info=dict(story=story,beam=beam,sec=frame['prop'],top=top,bot=bottom,v=shear)
        rows.append([story,beam,f'{b:.0f}x{d:.0f}',f'{top:.1f}',fmt(tp),f'{bottom:.1f}',fmt(bt),f'{int(params["legs"])}L-{params["sd"]:g} @ {se}/{sm}'])
        layouts.append((name,info,b,d,tp,bt))
    if not rows:raise rc.NeedsData('No concrete beam design results match the selected story.')
    s.rows=layouts;s.t.set(rows)
    s.app.state['beam_rebar_layouts']=[(u,e,b/1000,d/1000,tp,bt) for u,e,b,d,tp,bt in layouts]
    output(s,s.H,rows,['Draft bar-fit output from ETABS design demands only. Code detailing, anchorage, joints, splices and shear limits require separate checks.'])


def modal_results(s):
    data=core.modal_summary(s.e,s.case.get().strip() or None)
    s.app.state['modal']=data
    count=len(data['T']);threshold=positive(s.tors.get(),'Rotational participation reference')
    positive(s.H.get(),'Building height')
    rows=[]
    for direction,key in [('X','sx'),('Y','sy')]:
        value=data[key][-1]*100
        rows.append([f'Cumulative translational mass: {direction}',f'{value:.3f}%', '90% reference','OK' if value>=90 else 'NOT OK','Mass participation only; review model, mode count and governing requirements.'])
    for i in range(min(2,count)):
        value=data['rz'][i]*100
        rows.append([f'Mode {i+1} rotational participation RZ',f'{value:.3f}%',f'{threshold:g}% user reference','REVIEW' if value>threshold else 'REFERENCE ONLY','Rotational inertia participation cannot be divided by translational mass participation to certify torsional irregularity. Inspect mode shapes.'])
    rows.append(['IS 16700/torsional system applicability','Not established','Project-specific','NOT CHECKED','Full tall-building, irregularity and code checks are outside this extract.'])
    s.mp.set([[i+1,data['T'][i],data['ux'][i]*100,data['uy'][i]*100,data['rz'][i]*100,data['sx'][i]*100,data['sy'][i]*100,data['sz'][i]*100] for i in range(count)])
    s.md.set([[i+1,t,1/t,2*math.pi/t,(2*math.pi/t)**2] for i,t in enumerate(data['T'])])
    s.ck.set(rows);s.vd.configure(text='Modal results extracted. This is not a complete seismic-code compliance check.')
    ui.put(s.wn,'Review mode shapes and all NOT CHECKED/REVIEW items.');ui.put(s.rs,'\n'.join(str(r) for r in rows))
    output(s,s.CH,rows,[f'Modal case {s.case.get()}, {count} modes.'])


def principal_go(s):
    core.require_results(s.e,[s.case.get()])
    table=s.e.table('Modal Participation Factors').sel('outputcase',s.case.get()).require()
    number=int(s.nm.get());step=positive(s.step.get(),'Angle step');damping=positive(s.dmp.get(),'Damping')
    if number<1 or not .25<=step<=90 or damping>=1:
        raise ValueError('Use at least one mode, angle step 0.25–90 degrees and damping below 1.')
    n=min(number,len(table.rows));periods=table.col('period')[:n];gx=table.col('ux')[:n];gy=table.col('uy')[:n]
    if any(t<=0 for t in periods):raise rc.NeedsData('Zero/negative periods cannot be used.')
    result=core.principal_axes(periods,gx,gy,s.zone.get(),s.soil.get(),positive(s.I.get(),'I'),positive(s.R.get(),'R'),damping,step)
    s.best,maximum=max(result,key=lambda r:r[1]);s.proxy_only=True
    ui.put(s.pa,f'Participation proxy maximum at {s.best:.2f} degrees.\nThis omits generalized modal masses and is NOT a base-shear demand or verified critical direction.\nUse an independently verified directional response-spectrum study before applying any angle.')
    s.md.set([[i+1,periods[i],gx[i],gy[i],'Participation factors only'] for i in range(n)])
    s.rt.set([[angle,value] for angle,value in result])
    output(s,('Angle degrees','Participation proxy (not kN)'),result,['Exploratory proxy only. Not a verified principal direction or design action.'])


def principal_apply(s):
    raise rc.NeedsData('Automatic application of the participation proxy is disabled because it is not a verified critical seismic direction. Perform a force-based directional spectrum study in ETABS.')


def pushover_write(s):
    rc.writable(s.e)
    point=s.point.get().strip();target=positive(s.disp.get(),'Target displacement (m)');scale=positive(s.sf.get(),'Acceleration multiplier')
    if point not in s.e.points():raise ValueError('Select an existing control joint.')
    modal=s.modal.get().strip();initial=s.grav.get().strip()
    if modal not in s.e.names('case'):raise rc.NeedsData('The selected modal case does not exist.')
    if rc.checked(s.e.m.LoadCases.GetTypeOAPI(modal),'Read modal case type')[0]!=3:raise ValueError('Selected modal case is not modal.')
    if initial and initial not in s.e.names('case'):raise ValueError('The initial gravity case does not exist.')
    masses=s.e.table('Mass Source Definition').require()
    if s.mass.get().strip() not in masses.col('name',num=False):raise ValueError('Select an existing mass source.')
    nl=s.e.m.LoadCases.StaticNonlinear
    for name,axis,dof in [('Push X','UX',1),('Push Y','UY',2)]:
        rc.checked(nl.SetCase(name),'Create pushover case')
        rc.checked(nl.SetInitialCase(name,initial),'Assign initial case')
        rc.checked(nl.SetMassSource(name,s.mass.get().strip()),'Assign mass source')
        rc.checked(nl.SetModalCase(name,modal),'Assign modal case')
        rc.checked(nl.SetLoads(name,1,['Accel'],[axis],[scale]),'Assign acceleration loads')
        rc.checked(nl.SetGeometricNonlinearity(name,0),'Set selected no-geometric-nonlinearity assumption')
        rc.checked(nl.SetLoadApplication(name,2,1,target,1,dof,point,''),'Assign displacement control')
        rc.checked(nl.SetResultsSaved(name,True,10,200,True),'Save pushover states')
    output(s,('Case','Control joint','Target m','Acceleration multiplier','Initial case'),[[name,point,target,scale,initial or 'Zero initial state'] for name in ['Push X','Push Y']],['Case setup only. Nonlinear hinges/material behaviour, gravity preloading, geometric effects and acceptance criteria require project-specific review.'])
    s.log('Pushover cases defined. No hinges assigned and no capacity/compliance result generated.')


def python_run(s, command):
    if s.proc and s.proc.poll() is None:
        s.q.put('A process is already running. Stop it before starting another.\n');return
    if not command or not str(command[0]).strip():
        s.q.put('Select a Python executable.\n');return
    if len(command)==2 and (not command[1] or not Path(command[1]).is_file()):
        s.q.put('Select an existing Python script/server file.\n');return
    def worker():
        try:
            environment=os.environ.copy();environment['PYTHONIOENCODING']='utf-8';environment['PYTHONUNBUFFERED']='1'
            creation=getattr(subprocess,'CREATE_NO_WINDOW',0)
            is_server=len(command)==2 and str(command[1])==s.srv.get()
            s.proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,stdin=subprocess.PIPE if is_server else subprocess.DEVNULL,text=True,encoding='utf-8',errors='replace',env=environment,creationflags=creation)
            for line in s.proc.stdout:s.q.put(line)
            code=s.proc.wait();s.q.put(f'Process exited (code {code}).\n')
            s.app.module_status[s.key]={'state':'Completed' if code==0 else 'Error','action':'Python subprocess','detail':f'Exit {code}'}
        except Exception as exc:
            s.q.put(f'Could not launch Python: {exc}\n')
            s.app.module_status[s.key]={'state':'Error','action':'Python subprocess','detail':str(exc)}
    threading.Thread(target=worker,daemon=True).start()


def reports_refresh(s):
    results=s.app.results;states=s.app.module_status;rows=[]
    order=list(pages2.ORDER)+['Python and MCP']
    for key in order:
        record=results.get(key);state=states.get(key,{})
        if record and record.get('generation')!=getattr(s.e,'generation',0):status='STALE: model changed'
        elif state.get('state') in ('Error','Needs setup'):status=state['state']+': '+state.get('detail','')
        elif record:status=record.get('stage','Output available')
        elif state:status=state.get('state','Not run')+' — '+state.get('action','')
        else:status='Not run'
        rows.append(['Yes' if record and not status.startswith(('STALE','Error','Needs')) else 'No',key,status])
    s.t.set(rows);s.n.configure(text=f'{len(results)} modules have session report outputs. Preview/setup output is not an analysis pass. Use Module Diagnostics for prerequisites.')


class Diagnostics(ui.Page):
    key='Module Diagnostics';title='Module readiness and session status'
    sub='Read-only prerequisite checks. READY means you can try the action, not that the engineering result has passed.'
    def build(s):
        ui.bar(s.main,('Check prerequisites',lambda:s.run(s.check)),('Copy diagnostics',s.copy))
        s.t=ui.Table(s.main,('Module','Readiness','Required next step'),[240,160,650],height=22);s.t.pack(fill='both',expand=True)
        s.rows=[]
    def check(s):
        e=s.e;m=e.m
        points=e.points();frames=e.frames();levels=core.levels(e)
        patterns=rc.names(m.LoadPatterns.GetNameList(),'Read patterns');cases=e.names('case')
        locked=bool(m.GetModelIsLocked());design=bool(m.DesignConcrete.GetResultsAvailable())
        piers=rc.names(m.PierLabel.GetNameList(),'Read piers')
        statuses=rc.checked(m.Analyze.GetCaseStatus(),'Read case status');arrays=[list(v) for v in statuses if isinstance(v,(tuple,list))]
        done=[n for n,v in zip(*arrays[:2]) if v==4] if len(arrays)>=2 else []
        result_ready=locked and bool(done)
        rows=[]
        specs=[
            ('Automatic Code Checks',bool(frames),'Frame geometry; confirm applicability; results checks need analysis/design.'),
            ('P-Delta',bool(patterns),'Review gravity load factors; unlock to define nonlinear P-Delta case.'),
            ('Mass Source and Loads',bool(patterns),'Read source table; review self-mass and load multipliers; assigned loads still required.'),
            ('Modal Case',bool(points),'Set mass source, Eigen/Ritz and mode count; unlock to edit.'),
            ('Modal Analysis Check',result_ready,'Run a modal case; select its exact name.'),
            ('Time Period',len(levels)>1 and bool(points),'Read geometry; choose system and base/plinth; wall systems need verified piers.'),
            ('Principal Axes',result_ready,'Exploratory participation proxy only; not an automatic design direction.'),
            ('Response Spectrum',bool(cases),'Define valid modal case/functions; scaling requires finished static and spectrum cases.'),
            ('Pushover Analysis',bool(points) and bool(cases),'Define hinges, mass, initial gravity case and control displacement; results require analysis.'),
            ('Irregularity Check',result_ready,'Select lateral cases; define diaphragms; run analysis.'),
            ('Drift and Displacement',result_ready,'Select lateral cases and verify action levels; diaphragm displacement results may be unavailable.'),
            ('Base Shear Share',result_ready,'Select a linear static case; global signed components and member axes are needed.'),
            ('Wall Density',bool(points),'Review labelled piers/optional blade frames and actual plinth area.'),
            ('Load Patterns',bool(points),'Edit definitions; automatic lateral parameters and floor loads are separate.'),
            ('Load Combinations',bool(cases),'Map every used role to an existing analysis case before applying.'),
            ('Detailing',result_ready and design,'Run concrete frame design; review draft bar-fit and code detailing separately.'),
            ('AI Building Generator',True,'Review JSON; geometry creation replaces the connected model after confirmation.'),
            ('Python and MCP',True,'Select the correct interpreter/script; Start MCP is distinct from running a model generator.')]
        for key,ready,detail in specs:rows.append([key,'Ready to configure' if ready else 'Needs setup/results',detail])
        s.rows=rows;s.t.set(rows)
        s.log(f'{len(points)} joints; {len(frames)} frames; {len(levels)-1} storeys; {len(piers)} pier labels; finished cases: {done}; design results: {design}.')
    def copy(s):
        s.clipboard_clear();s.clipboard_append(json.dumps({'modules':s.rows,'session_status':s.app.module_status},indent=2));s.log('Diagnostics copied.')


def install_repairs(g):
    rc.install(core)
    from mass_source_page import Mass
    # PAGE_DEFS was constructed before this hook; replace its stored class references.
    g['Mass']=Mass;g['PDelta']=PDeltaPage
    for section,entries in g['PAGE_DEFS']:
        for i,(number,name,cls) in enumerate(entries):
            if cls.__name__=='Mass':entries[i]=(number,name,Mass)
            if cls.__name__=='PDelta':entries[i]=(number,name,PDeltaPage)
    g['ModalSetup'].write=modal_write;g['ModalSetup'].read=modal_read
    g['ModalResults'].go=modal_results
    g['RSpec'].write=spectrum_write;g['RSpec'].cases=spectrum_cases;g['RSpec'].scale=spectrum_scale
    g['PrincipalAxes'].go=principal_go;g['PrincipalAxes'].apply=principal_apply
    pages2.LoadPatterns.apply=load_patterns_apply;pages2.Combos.apply=combinations_apply
    pages2.Drift.go=drift_go;pages2.Torsion.go=torsion_go
    pages2.BaseShear.calc=shear_calc
    pages2.BaseShear.scaling=lambda s:messagebox.showinfo('Frame capacity','Force-share extraction does not establish independent frame capacity. No combination scaling factor is generated.')
    pages2.WallDensity.calc=density_calc
    pages2.Detailing.go=detailing_go;pages2.Pushover.write_both=pushover_write
    pages2.PyMCP.sh=python_run;pages2.Reports.refresh=reports_refresh
    original_geo=g['AutoChecks'].geo
    g['AutoChecks'].geo=lambda s,f,col:'NOT CHECKED' if min(f['t2'],f['t3'])<=0 else original_geo(s,f,col)
    original_checks=g['AutoChecks'].go
    def checks(s):
        original_checks(s)
        for row in s.data:
            if row[1]=='Analysis/design result freshness':
                row[4]='NOT CHECKED';row[6]='Lock state alone does not prove completed analysis or concrete design. Review case statuses and design availability.'
        s.data.append(['User confirmation','Drift action basis','Confirmed' if s.c2.get() else 'Not confirmed','Project-specific','NOT CHECKED','Separate drift module','Records the user statement only; no automatic drift check is performed on this page.'])
        s.show();s.save(s.H,s.data,['Geometry/settings checks only. Analysis, load validity and design coverage remain separate.'])
    g['AutoChecks'].go=checks
    g['AutoChecks'].sub='Read geometry, configured design code and effective section modifiers. Drift and result-based checks are in their separate modules.'
    original_period=g['TimePeriod'].calc
    def period(s):
        for value,label in [(s.h.get(),'Height'),(s.dx.get(),'X plan dimension'),(s.dy.get(),'Y plan dimension')]:positive(value,label)
        original_period(s)
        if s.key in s.app.results and float(s.h.get())>50:
            note='Height exceeds 50 m: this empirical estimate does not implement or verify all IS 16700 tall-building requirements.'
            s.app.results[s.key]['notes'].append(note);s.log(note)
    g['TimePeriod'].calc=period
    original_period_build=g['TimePeriod'].build
    def period_build(s):
        original_period_build(s)
        for widget in s.cb:
            widget.bind('<<ComboboxSelected>>',lambda event:s.run(s.load))
    g['TimePeriod'].build=period_build
    original_push_results=pages2.Pushover.results
    def push_results(s):
        core.require_results(s.e,['Push '+s.axis.get()]);return original_push_results(s)
    pages2.Pushover.results=push_results
    pages2.TitleBlockProxy=lambda page:g['TitleBlock'](page.app)
    original_report_html=pages2.Reports.build_html
    def report_html(s):
        selected=[r[1] for r in s.t.rows() if r[0]=='Yes']
        if not selected:raise rc.NeedsData('Select at least one available output before exporting a report.')
        for key in selected:
            record=s.app.results.get(key)
            state=s.app.module_status.get(key,{})
            if not record or record.get('generation')!=getattr(s.e,'generation',0) or state.get('state') in ('Error','Needs setup'):
                raise rc.NeedsData('Refresh report selections; output is missing, failed or stale: '+key)
        return original_report_html(s)
    pages2.Reports.build_html=report_html
    original_detail_build=pages2.Detailing.build
    def detail_build(s):
        original_detail_build(s)
        inactive={str(s.p[k]) for k in ('emin','emax','s0','s1')}
        def walk(widget):
            for child in widget.winfo_children():
                try:
                    if str(child.cget('textvariable')) in inactive:
                        child.configure(state='disabled')
                except Exception:pass
                walk(child)
        walk(s.main)
        s.log('Draft mode uses the through-bar range and chosen stirrup diameter only. Extra-bar and alternative stirrup-size controls are inactive.')
    pages2.Detailing.build=detail_build

    original_app_init=g['App'].__init__
    def app_init(s,*args,**kwargs):
        s.module_status={}
        original_app_init(s,*args,**kwargs)
        s.etabs.owner_app=s
        ttk.Button(s,text='Module Diagnostics',command=lambda:s.show('Module Diagnostics',Diagnostics)).pack(side='top',anchor='e',before=s.top,padx=8,pady=4)
        def callback_error(kind,value,tb):
            detail=''.join(traceback.format_exception(kind,value,tb))
            page=next((p for p in s.pages.values() if p.winfo_manager()),None)
            if page:
                page.log('ERROR:',value)
                s.module_status[page.key]={'state':'Error','action':'UI callback','detail':str(value)}
            print(detail,file=sys.stderr,flush=True)
        s.report_callback_exception=callback_error
    g['App'].__init__=app_init

    def run(s,fn):
        if not s.e.m:
            s.log('Needs setup: connect to ETABS.');return
        if not hasattr(s.app,'module_status'):s.app.module_status={}
        action=getattr(fn,'__name__','Action');before=s.app.results.get(s.key)
        s._in_action=True
        s.app.module_status[s.key]={'state':'Running','action':action,'detail':''}
        try:
            with core.units(s.e):result=fn()
            record=s.app.results.get(s.key)
            status='Output available' if record is not None and record is not before else 'Action completed (no new report)'
            s.app.module_status[s.key]={'state':status,'action':action,'detail':''}
            return result
        except (rc.NeedsData,ValueError,KeyError) as exc:
            s.app.results.pop(s.key,None)
            s.app.module_status[s.key]={'state':'Needs setup','action':action,'detail':str(exc)}
            s.log('NEEDS SETUP:',exc)
        except Exception as exc:
            s.app.results.pop(s.key,None)
            s.app.module_status[s.key]={'state':'Error','action':action,'detail':str(exc)}
            s.log('ERROR:',exc)
            print(traceback.format_exc(),file=sys.stderr,flush=True)
        finally:
            s._in_action=False
    ui.Page.run=run
    def save(s,headers,rows,notes=()):
        if not rows:raise rc.NeedsData('No output records; no report result saved.')
        s.app.results[s.key]={'headers':headers,'rows':rows,'notes':list(notes)+['Session output only; execution does not imply a passing engineering check.'],
            'generation':getattr(s.e,'generation',0),'stage':'Output available' if getattr(s,'_in_action',False) else 'Preview / calculation only'}
    ui.Page.save=save

    rs8_install(g)  # Module 8 correction


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
