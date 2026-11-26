"""Validated floor-wise geometry specification. Metres and MPa internally."""
import copy
import math


def positive(v, label):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
        raise ValueError(f'{label} must be a finite positive number')
    return float(v)


def pair(v, label, scale=1):
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise ValueError(f'{label} needs [width, depth]')
    return [positive(x, label) * scale for x in v]


def layout(p):
    if not isinstance(p, dict) or p.get('geometry_confirmed') is not True:
        raise ValueError('Plan geometry is unconfirmed. Supply plan.geometry_confirmed=true, nodes, columns, beams and slab_panels; do not guess dimensions from the image.')
    nodes = p.get('nodes')
    if not isinstance(nodes, dict) or not nodes:
        raise ValueError('plan.nodes must map point names to [x_m, y_m]')
    pts = {}
    for name, xy in nodes.items():
        if not isinstance(name, str) or not name or len(xy) != 2 or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in xy):
            raise ValueError('Each plan node requires a name and two finite coordinates in metres')
        pts[name] = list(xy)
    if len({tuple(x) for x in pts.values()}) != len(pts):
        raise ValueError('Duplicate node coordinates')
    cols, beams, panels = p.get('columns'), p.get('beams'), p.get('slab_panels')
    if not isinstance(cols, list) or not cols or len(set(cols)) != len(cols) or any(n not in pts for n in cols):
        raise ValueError('plan.columns must be unique node names')
    if not isinstance(beams, list) or not beams or not isinstance(panels, list):
        raise ValueError('plan.beams and plan.slab_panels must be explicit lists; use [] for no slabs')
    seen = set()
    for edge in beams:
        if not isinstance(edge, list) or len(edge) != 2 or any(n not in pts for n in edge) or edge[0] == edge[1]:
            raise ValueError('Each beam requires two different existing node names')
        key = tuple(sorted(edge))
        if key in seen: raise ValueError('Duplicate beam')
        seen.add(key)
    # Only convex panels supported. Openings are left empty by omitting panels.
    seen_panels = set()
    for panel in panels:
        if not isinstance(panel, list) or len(panel) < 3 or len(set(panel)) != len(panel) or any(n not in pts for n in panel):
            raise ValueError('Each slab panel needs at least three unique existing nodes in perimeter order')
        key = tuple(sorted(panel))
        if key in seen_panels: raise ValueError('Duplicate slab panel')
        seen_panels.add(key)
        vertices = [pts[n] for n in panel]
        cross = []
        for i in range(len(vertices)):
            a,b,c = vertices[i-2],vertices[i-1],vertices[i]
            cross.append((b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0]))
        if not (all(x > 1e-10 for x in cross) or all(x < -1e-10 for x in cross)):
            raise ValueError('Slab panels must be convex, nonzero polygons in perimeter order; split complex floors into panels around openings')
    return dict(nodes=pts, columns=cols, beams=beams, slab_panels=panels, geometry_confirmed=True)


def normalize(raw):
    if not isinstance(raw, dict): raise ValueError('Specification must be a JSON object')
    sp = copy.deepcopy(raw)
    if 'plan' in sp:
        p = layout(sp['plan'])
    else:
        # Existing rectangular format remains supported, explicitly identified in review.
        for k in ('bays_x','bays_y'):
            if type(sp.get(k)) is not int or not 1 <= sp[k] <= 100: raise ValueError(f'{k} must be an integer from 1 to 100, or supply an explicit plan')
        dx,dy = positive(sp.get('bay_x'),'bay_x'), positive(sp.get('bay_y'),'bay_y')
        nx,ny = sp['bays_x'],sp['bays_y']
        nodes = {f'{i}_{j}':[i*dx,j*dy] for i in range(nx+1) for j in range(ny+1)}
        beams = [[f'{i}_{j}',f'{i+1}_{j}'] for i in range(nx) for j in range(ny+1)] + [[f'{i}_{j}',f'{i}_{j+1}'] for i in range(nx+1) for j in range(ny)]
        panels = [[f'{i}_{j}',f'{i+1}_{j}',f'{i+1}_{j+1}',f'{i}_{j+1}'] for i in range(nx) for j in range(ny)]
        p = layout(dict(geometry_confirmed=True,nodes=nodes,columns=list(nodes),beams=beams,slab_panels=panels))
    rows = sp.get('storeys',sp.get('main_storeys'))
    if rows is None:
        n = sp.get('stories')
        if type(n) is not int or not 1 <= n <= 200: raise ValueError('stories must be an integer from 1 to 200')
        rows = [dict(floor=f'Storey_{i+1:02}',height_m=sp.get('bottom_h') if i==0 else sp.get('story_h'),beam=sp.get('beam'),column=sp.get('column')) for i in range(n)]
        scale = 1
    else:
        if not isinstance(rows,list) or not 1 <= len(rows) <= 200: raise ValueError('storeys must contain 1 to 200 rows')
        if 'stories' in sp and sp['stories'] != len(rows): raise ValueError('stories does not match storeys count')
        unit = sp.get('units',{}).get('section_dimensions',sp.get('units',{}).get('sections'))
        if unit not in ('m','mm'): raise ValueError('Set units.section_dimensions to m or mm')
        scale = .001 if unit=='mm' else 1
    result=[]
    names=set()
    for i,r in enumerate(rows):
        name=r.get('floor',r.get('storey_name',f'Storey_{i+1:02}'))
        if not isinstance(name,str) or not name.strip() or name in names or name.lower()=='base': raise ValueError('Storey names must be unique, nonempty and different from Base')
        names.add(name)
        result.append(dict(name=name,height=positive(r.get('height_m'),'height_m'),beam=pair(r.get('beam',r.get('beam_width_depth_mm')),'beam',scale),column=pair(r.get('column',r.get('column_mm')),'column',scale),plan=p))
    hr=sp.get('stair_headroom',sp.get('headroom'))
    if hr:
        hp=layout(hr.get('plan'))
        for n in hp['columns']:
            if hp['nodes'][n] not in [p['nodes'][c] for c in p['columns']]: raise ValueError('Headroom columns must align with main-storey columns')
        name=hr.get('storey_name','Stair_Headroom')
        if name in names or name.lower()=='base': raise ValueError('Headroom name conflicts with a main storey')
        result.append(dict(name=name,height=positive(hr.get('height_m'),'headroom height'),beam=pair(hr.get('beam',hr.get('beam_width_depth_mm')),'headroom beam',scale),column=pair(hr.get('column',hr.get('column_mm')),'headroom column',scale),plan=hp))
    return dict(floors=result,main_storeys=len(rows),fck=positive(sp.get('fck'),'fck (MPa)'),slab_t=positive(sp.get('slab_t'),'slab_t (metres)'),layout_kind='Explicit plan' if 'plan' in sp else 'Uniform rectangular grid')


def checked(ret, label):
    code=ret[-1] if isinstance(ret,(list,tuple)) else ret
    if type(code) is not int or code != 0: raise RuntimeError(f'{label} failed: {ret!r}. Model may be incomplete; do not run analysis.')
    return ret


def build_model(m, raw):
    spec=normalize(raw)  # complete validation before changing ETABS
    checked(m.InitializeNewModel(6),'InitializeNewModel')
    checked(m.File.NewBlank(),'NewBlank')
    checked(m.SetPresentUnits(6),'SetPresentUnits')
    floors=spec['floors']; names=[f['name'] for f in floors]; zs=[0.0]
    for f in floors: zs.append(zs[-1]+f['height'])
    n=len(floors)
    checked(m.Story.SetStories(names,zs,[f['height'] for f in floors],[True]*n,['None']*n,[False]*n,[0.0]*n),'SetStories')
    checked(m.PropMaterial.SetMaterial('CONC',2),'SetMaterial')
    checked(m.PropMaterial.SetMPIsotropic('CONC',5000*math.sqrt(spec['fck'])*1000,.2,1e-5),'SetMPIsotropic')
    checked(m.PropMaterial.SetWeightAndMass('CONC',1,25.0),'SetWeightAndMass')
    checked(m.PropMaterial.SetOConcrete('CONC',spec['fck']*1000,False,0,1,2,.002,.0035),'SetOConcrete')
    checked(m.PropArea.SetSlab('SLAB',0,2,'CONC',spec['slab_t']),'SetSlab')
    counts=dict(columns=0,beams=0,slabs=0)
    for i,f in enumerate(floors):
        cn,bn=f'COL_{i:02}',f'BEAM_{i:02}'
        checked(m.PropFrame.SetRectangle(cn,'CONC',f['column'][1],f['column'][0]),'Column section')
        checked(m.PropFrame.SetRectangle(bn,'CONC',f['beam'][1],f['beam'][0]),'Beam section')
        p=f['plan']; pts=p['nodes']; z0,z1=zs[i:i+2]
        for node in p['columns']:
            x,y=pts[node]
            ret=checked(m.FrameObj.AddByCoord(x,y,z0,x,y,z1,'',cn),'Add column')
            counts['columns']+=1
            if i==0:
                if not isinstance(ret,(tuple,list)) or not isinstance(ret[0],str): raise RuntimeError('Unexpected ETABS frame name output')
                ends=checked(m.FrameObj.GetPoints(ret[0],'',''),'Column end points')
                checked(m.PointObj.SetRestraint(ends[0],[True]*6),'Fixed base')
        for a,b in p['beams']:
            checked(m.FrameObj.AddByCoord(*pts[a],z1,*pts[b],z1,'',bn),'Add beam');counts['beams']+=1
        for panel in p['slab_panels']:
            checked(m.AreaObj.AddByCoord(len(panel),[pts[v][0] for v in panel],[pts[v][1] for v in panel],[z1]*len(panel),'','SLAB'),'Add slab');counts['slabs']+=1
    checked(m.View.RefreshView(0,False),'RefreshView')
    return dict(counts=counts,roof_height=zs[spec['main_storeys']],top_height=zs[-1])
