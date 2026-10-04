import unittest, uuid, copy
from founder_package import build_package, render_markdown, plan_ledger_write, verify_ledger, LEDGER_TABS
from question_engine_v2 import seed_source_coverage, record_answer

class FounderPackageRegression(unittest.TestCase):
    def setUp(self):
        sid=str(uuid.uuid4())
        self.s={'id':sid,'event_id':str(uuid.uuid5(uuid.NAMESPACE_URL,'trainer-completed:'+sid)), 'idempotency_key':'trainer-completed:'+sid,'project_id':'VC-995','project_name':'Синтетический сервис здоровья','founder_name':'Тестовый основатель','founder_email':'test@example.com','jurisdiction':'RF','product_class':'WELLNESS_INFORMATION','mode':'B','created_at':'2026-10-04T18:40:00Z','completed_at':'2026-10-04T18:41:00Z','description':'10 клиентов и 100000 рублей; документов нет','intended_use':'Хранение истории лабораторных анализов без интерпретации','qa':[{'answer':'10 клиентов, документов нет','analysis':{'status':'fact'},'module':'traction'}],'stop_reason':'required_coverage_complete','selected_modules':['traction']}
        self.r={'facts':['10 клиентов и 100000 рублей'],'claims':['Основатель заявляет о клиентах'],'coverage':{'required':18,'required_assessed':18},'recommendation':'ready_for_investor_conversation'}
        self.p=build_package(self.s,self.r,'https://startup-trainer-a9-staging.onrender.com',self.s['completed_at'])
    def test_claim_never_becomes_verified(self):
        self.assertEqual(self.p['supported_facts'],[])
        self.assertEqual(self.p['readiness_score']['value'],0)
        self.assertTrue(all(e['evidence_level']!='E3' and e['status']!='fact' for e in self.p['evidence_delta']))
        self.assertEqual(self.p['recommendation'],'iterate')
        self.assertIn('10 клиентов и 100000 рублей',[x['statement'] for x in self.p['labelled_claims']])
    def test_complete_package(self):
        self.assertEqual(len(self.p['critical_gaps']),7)
        self.assertEqual([p['day'] for p in self.p['plan_30_60_90']],[30,60,90])
        self.assertTrue(all(p['actions'] for p in self.p['plan_30_60_90']))
        self.assertEqual(self.p['founder_delivery']['status'],'WAITING_APPROVAL')
        self.assertEqual(self.p['founder_delivery']['sent_status'],'NOT_SENT')
        self.assertIn('## План на 30 / 60 / 90 дней',render_markdown(self.p))
    def test_ledger_readback_idempotent_and_fail_closed(self):
        body={'valueRanges':[{'range':"'%s'!A1:Z1000"%tab,'values':[['header']]} for tab in LEDGER_TABS]}
        first=plan_ledger_write(self.s,self.p,body)
        for write in first['data']:
            tab=write['range'].split('!')[0].strip("'"); row=int(write['range'].split('!A')[1]); part=next(v for v in body['valueRanges'] if v['range'].startswith("'%s'!"%tab))
            while len(part['values'])<row: part['values'].append([])
            part['values'][row-1]=write['values'][0]
        counts=verify_ledger(self.s,self.p,body)
        self.assertEqual(counts['Founder Delivery'],1)
        self.assertEqual(first,plan_ledger_write(self.s,self.p,body))
        broken=copy.deepcopy(body);broken['valueRanges'][1]['values'].pop()
        with self.assertRaises(ValueError):verify_ledger(self.s,self.p,broken)
    def test_coverage_boundary_blocks_model_fact(self):
        text='10 клиентов'
        seed_source_coverage(self.s,[{'module':'traction','slot':'traction_metrics','status':'fact','quote':text}],text)
        self.assertEqual(self.s['coverage']['traction.traction_metrics']['status'],'weak')
        record_answer(self.s,{'module':'traction','slot':'retention'},'Есть продления',{'status':'fact'})
        self.assertEqual(self.s['coverage']['traction.retention']['status'],'weak')

if __name__=='__main__':unittest.main()
