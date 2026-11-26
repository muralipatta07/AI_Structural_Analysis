"""Offline regression tests. These tests do not connect to or modify ETABS."""
import ctypes
import math
import unittest
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import core
import app
import app_repair as repair
import robust_core as rc


class Value:
    def __init__(self,value):self.value=value
    def get(self):return self.value
    def set(self,value):self.value=value


class Widget:
    def __init__(self,rows=()):self.data=list(rows);self.options={}
    def rows(self):return self.data
    def set(self,rows):self.data=list(rows)
    def configure(self,**options):self.options.update(options)
    config=configure


def page(**values):
    saved=[]
    result=NS(**values)
    result.saved=saved;result.log=lambda *args:None
    result.save=lambda h,r,n=():saved.append((h,r,n))
    return result


class CoreTests(unittest.TestCase):
    def test_zero_name_count_is_empty(self):
        self.assertEqual(rc.names((0,[None],0),'Read names'),[])
    def test_nonzero_api_write_stops(self):
        e=NS(generation=0)
        api=rc.CheckedAPI(NS(SetCase=lambda *a:1),e)
        with self.assertRaises(RuntimeError):api.SetCase('x')
        self.assertEqual(e.generation,0)
    def test_successful_write_invalidates_generation(self):
        e=NS(generation=0);api=rc.CheckedAPI(NS(SetCase=lambda *a:0,SetPresentUnits=lambda *a:0),e)
        api.SetCase('x');api.SetPresentUnits(6)
        self.assertEqual(e.generation,1)
    def test_empty_headers_do_not_match(self):
        table=core.TB([None],[], 'Empty')
        with self.assertRaises(rc.NeedsData):table.col('story')
    def test_numeric_missing_is_not_zero(self):
        with self.assertRaises(ValueError):core.TB(['Drift'],[[None]]).col('drift')
    def test_filter_missing_field_is_not_ignored(self):
        with self.assertRaises(rc.NeedsData):core.TB(['Other'],[['A']]).sel('story','Story1')
    def test_table_column_aliases(self):
        self.assertEqual(core.TB(['FX','U1'],[['4','0.002']]).col('globalfx'),[4])
        self.assertEqual(core.TB(['FX','U1'],[['4','0.002']]).col('ux'),[.002])
    def test_empty_table_never_claims_result(self):
        with self.assertRaises(rc.NeedsData):core.TB(['Story'],[]).require()
    def test_table_shape(self):
        e=core.Etabs();e.select_all=lambda:None
        e.m=NS(DatabaseTables=NS(GetTableForDisplayArray=lambda *a:([],1,['Story','Drift'],1,['S1','.003'],0)))
        self.assertEqual(e.table('Story Drifts').col('drift'),[.003])
        e.m.DatabaseTables.GetTableForDisplayArray=lambda *a:([],1,['Story','Drift'],2,['S1','.003'],0)
        with self.assertRaises(rc.NeedsData):e.table('Story Drifts')
    def test_units_restore_after_error(self):
        m=MagicMock();m.GetPresentUnits.return_value=3;m.SetPresentUnits.return_value=0
        with self.assertRaises(ValueError):
            with core.units(NS(m=m)):raise ValueError('test')
        self.assertEqual(m.SetPresentUnits.call_args.args,(3,))
    def test_invalid_spectrum_parameters(self):
        for zone,I,R in [('IV',1,0),('bad',1,5),('IV',float('nan'),5)]:
            with self.assertRaises(ValueError):core.spectrum(zone,'II',I,R)
    def test_spectrum_respects_end_period(self):
        rows=core.spectrum('IV','II',1,5,.2)
        self.assertEqual(rows[-1][0],.2);self.assertTrue(all(r[0]<=.2 for r in rows))
    def test_wall_period_requires_wall_data(self):
        system=next(k for k,v in core.SYSTEMS.items() if v[0]=='wall')
        with self.assertRaises(rc.NeedsData):core.ta(system,30,15,[])
    def test_empty_geometry(self):
        e=core.Etabs();e.m=NS(PointObj=NS(GetAllPoints=lambda:(0,None,None,None,None,0)),FrameObj=NS(GetAllFrames=lambda:(0,0)))
        self.assertEqual(e.points(),{});self.assertEqual(e.frames(),[]);self.assertEqual(e.frame_info(),[])
    def test_locked_state_alone_not_finished_case(self):
        e=NS(m=NS(GetModelIsLocked=lambda:True,Analyze=NS(GetCaseStatus=lambda:(1,['Modal'],[1],0))),names=lambda k:['Modal'])
        with self.assertRaises(rc.NeedsData):rc.require_results(e,['Modal'])
    def test_real_typelib_array_marshalling(self):
        import comtypes.gen.ETABSv1 as api
        pointer=ctypes.POINTER(api.cCaseModalEigen)()
        args=rc.marshal(pointer,'SetLoads',('M',1,['Acceleration'],['UX'],[99.0],[True]))
        self.assertNotIsInstance(args[2],list)
        self.assertNotIsInstance(args[5],list)


class OperationTests(unittest.TestCase):
    def test_modal_results_indices_and_selection_restore(self):
        e=core.Etabs();e.m=MagicMock()
        e.m.GetModelIsLocked.return_value=True
        e.m.LoadCases.GetNameList.return_value=(1,['Modal'],0)
        e.m.RespCombo.GetNameList.return_value=(1,['Combo'],0)
        e.m.Analyze.GetCaseStatus.return_value=(1,['Modal'],[4],0)
        e.m.LoadCases.GetTypeOAPI.return_value=(3,1,0)
        e.m.DatabaseTables.SetLoadCasesSelectedForDisplay.return_value=0
        e.m.DatabaseTables.SetLoadCombinationsSelectedForDisplay.return_value=0
        setup=e.m.Results.Setup
        setup.GetCaseSelectedForOutput.return_value=(True,0)
        setup.GetComboSelectedForOutput.return_value=(False,0)
        setup.DeselectAllCasesAndCombosForOutput.return_value=0
        setup.SetCaseSelectedForOutput.return_value=0
        e.m.Results.ModalParticipatingMassRatios.return_value=(1,['Modal'],['Mode'],[1],[.5],[.8],[.1],[0],[.8],[.1],[0],[0],[0],[.03],[0],[0],[.03],0)
        result=core.modal_summary(e,'Modal')
        self.assertEqual(result['T'],[.5]);self.assertEqual(result['rz'],[.03]);self.assertEqual(result['sz'],[.03])
        self.assertEqual(setup.SetCaseSelectedForOutput.call_args.args,('Modal',True))
    def test_zero_base_shear_is_not_a_pass(self):
        names=['EQX','EQY','SPECX','SPECY']
        model=NS(GetModelIsLocked=lambda:True,Analyze=NS(GetCaseStatus=lambda:(4,names,[4]*4,0)))
        e=NS(m=model,names=lambda kind:names,table=lambda key:core.TB(['OutputCase','FX','FY'],[[name,0,0] for name in names]))
        s=page(e=e,sx=Value('EQX'),sy=Value('EQY'),rx=Value('SPECX'),ry=Value('SPECY'),sc=Widget())
        repair.spectrum_scale(s)
        self.assertTrue(all(row[-1]=='NOT CHECKED' for row in s.sc.data))
    def test_mass_flags_preserve_other_sources(self):
        import mass_source_api as mass
        fields=['Name','IsDefault','SourceSelf','SourceAdded','SourceLoads','IncludeLateralMass','IncludeVerticalMass','LumpMass','MoveMassCentroid','MoveMassCentroidX','MoveMassCentroidY','LoadPat','Multiplier','Extra']
        row=dict.fromkeys(fields,'');row.update(Name='Old',IsDefault='Yes',Extra='keep')
        settings=dict(name='New',default=True,self=False,added=False,loads=True,lateral=True,vertical=False,lump=False,move=True,x=.05,y=.03,patterns=[('Dead',1)])
        rows=mass.prepare(mass.Snapshot(1,fields,[row]),settings)
        self.assertEqual(rows[0]['Extra'],'keep');self.assertEqual(rows[0]['IsDefault'],'No')
        self.assertEqual(rows[-1]['IncludeVerticalMass'],'No');self.assertEqual(rows[-1]['LumpMass'],'No')
        self.assertEqual(rows[-1]['MoveMassCentroidY'],'0.03')
    def test_mass_negative_factor_rejected(self):
        import mass_source_api as mass
        values=dict(name='New',default=True,self=False,added=False,loads=True,lateral=True,vertical=True,lump=True,move=False,x=0,y=0,patterns=[('Dead',-1)])
        with self.assertRaises(ValueError):mass.validate(values)
    def modal(self,subtype):
        eigen=MagicMock();ritz=MagicMock()
        for api in [eigen,ritz]:
            api.SetCase.return_value=api.SetNumberModes.return_value=api.SetLoads.return_value=api.SetParameters.return_value=0
            api.GetNumberModes.return_value=(24,1,0)
        model=NS(GetModelIsLocked=lambda:False,LoadCases=NS(ModalEigen=eigen,ModalRitz=ritz))
        s=page(e=NS(m=model),nm=Value('Modal'),st=Value(subtype),mx=Value('24'),mn=Value('1'),fs=Value('0'),cf=Value('0'),tol=Value('1e-9'),ld=Widget([['Acceleration','UX','99','Yes']]))
        return s,eigen,ritz
    def test_eigen_subtype_parameters(self):
        s,eigen,ritz=self.modal('Eigen');repair.modal_write(s)
        eigen.SetParameters.assert_called_once();ritz.SetCase.assert_not_called()
        self.assertEqual(eigen.SetLoads.call_args.args[-1],[True])
    def test_ritz_is_not_written_as_eigen(self):
        s,eigen,ritz=self.modal('Ritz');repair.modal_write(s)
        eigen.SetCase.assert_not_called();ritz.SetCase.assert_called_once()
        self.assertEqual(ritz.SetLoads.call_args.args[-2],[0])
    def test_modal_invalid_input_precedes_writes(self):
        s,eigen,ritz=self.modal('Eigen');s.cf.set('-2')
        with self.assertRaises(ValueError):repair.modal_write(s)
        eigen.SetCase.assert_not_called()
    def test_combination_missing_mapping_preserves_existing(self):
        combo=MagicMock();s=page(e=NS(m=NS(GetModelIsLocked=lambda:False,RespCombo=combo),names=lambda k:['Dead'] if k=='case' else ['A01']),roles=Widget([['DL','Missing']]),t=Widget([['A01','Collapse','1.5']]),ROLES=['DL'],rep=Value(True))
        with self.assertRaises(rc.NeedsData):repair.combinations_apply(s)
        combo.Delete.assert_not_called();combo.Add.assert_not_called()
    def test_no_result_output_is_rejected(self):
        with self.assertRaises(rc.NeedsData):repair.output(page(),['Header'],[])
    def test_reports_distinguish_preview(self):
        app_state=NS(results={'Response Spectrum':{'generation':0,'stage':'Preview / calculation only'}},module_status={})
        s=page(app=app_state,e=NS(generation=0),t=Widget(),n=Widget())
        repair.reports_refresh(s)
        row=next(r for r in s.t.data if r[1]=='Response Spectrum')
        self.assertIn('Preview',row[2]);self.assertNotEqual(row[2],'Yes')
    def test_reports_flag_stale_after_model_change(self):
        s=page(app=NS(results={'Time Period':{'generation':0}},module_status={}),e=NS(generation=1),t=Widget(),n=Widget())
        repair.reports_refresh(s)
        row=next(r for r in s.t.data if r[1]=='Time Period')
        self.assertEqual(row[0],'No');self.assertIn('STALE',row[2])
    def test_axis_proxy_cannot_auto_change_case(self):
        with self.assertRaises(rc.NeedsData):repair.principal_apply(page())


class UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=app.App(core.Etabs());cls.root.withdraw()
    @classmethod
    def tearDownClass(cls):cls.root.destroy()
    def test_all_18_modules_and_reports_construct(self):
        count=0
        for section,entries in app.PAGE_DEFS:
            for number,name,kind in entries:
                with self.subTest(module=name):
                    widget=kind(self.root);widget.destroy();count+=1
        self.assertEqual(count,19)
    def test_empty_dropdown_is_combobox(self):
        from tkinter import ttk
        frame=ttk.Frame(self.root)
        dropdown=ui_entry=__import__('ui').entry(frame,'Story',Value(''),values=[])
        self.assertIsInstance(dropdown,ttk.Combobox)
        dropdown.configure(values=['Base','Story1']);frame.destroy()
    def test_action_bar_in_grid_parent(self):
        from tkinter import ttk
        import ui
        frame=ttk.Frame(self.root);ttk.Label(frame,text='Existing').grid(row=0,column=0)
        bar=ui.bar(frame,('Action',lambda:None))
        self.assertEqual(bar.winfo_manager(),'grid');frame.destroy()


if __name__=='__main__':unittest.main(verbosity=2)
