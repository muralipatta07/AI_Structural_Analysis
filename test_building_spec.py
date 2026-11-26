import copy
import unittest
from building_spec import normalize, build_model

PLAN = dict(geometry_confirmed=True, nodes={'A':[0,0],'B':[4,0],'C':[4,3],'D':[0,3]},columns=['A','B','C','D'],beams=[['A','B'],['B','C'],['C','D'],['D','A']],slab_panels=[['A','B','C','D']])
SPEC = dict(units={'section_dimensions':'mm'},fck=30,slab_t=.15,plan=PLAN,storeys=[dict(floor='Ground',height_m=4,column=[900,900],beam=[400,700]),dict(floor='Floor 1',height_m=3.2,column=[850,850],beam=[350,650])],stair_headroom=dict(height_m=3,column=[450,450],beam=[300,450],plan=copy.deepcopy(PLAN)))

class Mock:
    def __init__(self, log=None, path='', fail=None): self.log=[] if log is None else log;self.path=path;self.fail=fail
    def __getattr__(self,name): return Mock(self.log,self.path+'.'+name,self.fail)
    def __call__(self,*args):
        self.log.append((self.path,args))
        if self.path==self.fail:return 9
        if self.path.endswith('AddByCoord'):return ('Object1',0)
        if self.path.endswith('GetPoints'):return ('P1','P2',0)
        return 0

class Tests(unittest.TestCase):
    def test_units_and_local_headroom(self):
        r=normalize(SPEC);self.assertEqual(r['main_storeys'],2);self.assertEqual(r['floors'][1]['beam'],[.35000000000000003,.65])
        self.assertEqual(r['floors'][2]['column'],[.45,.45])
    def test_legacy(self):
        s=dict(stories=31,bays_x=4,bay_x=5,bays_y=3,bay_y=5,story_h=3.2,bottom_h=4,beam=[.3,.6],column=[.6,.6],slab_t=.15,fck=30)
        r=normalize(s);self.assertEqual(len(r['floors']),31);self.assertAlmostEqual(sum(f['height'] for f in r['floors']),100)
    def test_previous_review_report_requires_geometry(self):
        with self.assertRaisesRegex(ValueError,'unconfirmed'): normalize(dict(main_storeys=[{}],plan={'geometry_confirmed':False}))
    def test_invalid_inputs_do_not_modify_etabs(self):
        for change in ({'fck':None},{'slab_t':-1},{'plan':{'geometry_confirmed':False}},{'storeys':[]}):
            s=copy.deepcopy(SPEC);s.update(change);m=Mock()
            with self.assertRaises((ValueError,TypeError)):build_model(m,s)
            self.assertEqual(m.log,[])
    def test_return_codes_fail_fast(self):
        m=Mock(fail='.PropFrame.SetRectangle')
        with self.assertRaisesRegex(RuntimeError,'Column section'):build_model(m,SPEC)
        self.assertFalse(any(p=='.FrameObj.AddByCoord' for p,a in m.log))
    def test_floor_sections_story_data_and_supports(self):
        m=Mock();r=build_model(m,SPEC)
        self.assertEqual(r['counts'],dict(columns=12,beams=12,slabs=3))
        calls={p:a for p,a in m.log}
        self.assertEqual(calls['.Story.SetStories'][0],['Ground','Floor 1','Stair_Headroom'])
        self.assertEqual(calls['.Story.SetStories'][1],[0,4.,7.2,10.2])
        sections=[a for p,a in m.log if p=='.PropFrame.SetRectangle']
        self.assertEqual(sections[2],('COL_01','CONC',.85,.85))
        self.assertEqual(len([p for p,a in m.log if p=='.PointObj.SetRestraint']),4)
    def test_headroom_is_local_and_openings_not_filled(self):
        s=copy.deepcopy(SPEC);s['plan']['slab_panels']=[]
        hp=copy.deepcopy(PLAN);hp['columns']=['A','B'];hp['beams']=[['A','B']];hp['slab_panels']=[]
        s['stair_headroom']['plan']=hp
        m=Mock();r=build_model(m,s);self.assertEqual(r['counts'],dict(columns=10,beams=9,slabs=0))
    def test_bad_topology_and_unsupported_headroom(self):
        for field,value in [('columns',['Z']),('beams',[['A','Z']]),('slab_panels',[['A','C','B','D']])]:
            s=copy.deepcopy(SPEC);s['plan'][field]=value
            with self.assertRaises(ValueError):normalize(s)
        s=copy.deepcopy(SPEC);s['stair_headroom']['plan']['nodes']['A']=[1,1]
        with self.assertRaises(ValueError):normalize(s)

if __name__=='__main__': unittest.main()
