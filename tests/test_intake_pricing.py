import unittest
from decimal import Decimal
from crm import CRM, Conflict
from reliability import open_database, migrate
from sales_analysis import pricing, analyze_reply
from web_intake import submit, RateLimited


class IntakePricingTests(unittest.TestCase):
    def setUp(self):
        self.db = open_database(':memory:')
        migrate(self.db)
        self.crm = CRM(self.db, 9)

    def tearDown(self):
        self.db.close()

    def request(self, key='intake-request-0001'):
        return {'name':'Иван','contact':'@ivan','service':'Сайт','description':'Нужен сайт с каталогом для магазина','consent':True,'request_id':key}

    def test_intake_creates_atomic_crm_client_outbox_and_receipt(self):
        data = self.request()
        first = submit(self.db,9,data,'ip')
        self.assertEqual(first,submit(self.db,9,data,'ip'))
        self.assertEqual(self.db.execute('SELECT count(*) FROM notification_queue').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM clients').fetchone()[0],1)
        self.assertEqual(self.crm.list_leads()['total'],1)
        self.assertEqual(self.crm.list_leads()['items'][0]['platform'],'website')

    def test_consent_and_honeypot_do_not_create_leads(self):
        for change in ({'consent':False},{'website':'spam'},{'description':'x'}):
            with self.assertRaises(ValueError):
                submit(self.db,9,{**self.request(),**change},'ip')
        self.assertEqual(self.crm.list_leads()['total'],0)

    def test_rate_limit_is_durable_and_does_not_consume_duplicate_retry(self):
        for i in range(5):
            submit(self.db,9,self.request('intake-request-'+str(i).zfill(4)),'ip')
        with self.assertRaises(RateLimited):
            submit(self.db,9,self.request('intake-request-9999'),'ip')
        self.assertTrue(submit(self.db,9,self.request('intake-request-0000'),'ip')['accepted'])
        self.assertEqual(self.crm.list_leads()['total'],5)

    def test_changed_payload_same_id_rejected(self):
        submit(self.db,9,self.request(),'ip')
        with self.assertRaises(Conflict):
            submit(self.db,9,{**self.request(),'name':'Другой'},'ip')

    def test_simple_form_contact_task_optional_budget(self):
        data={'contact':'@customer','description':'Нужен сайт с каталогом мебели','budget':'50 000 рублей','consent':True,'request_id':'simple-form-request-01'}
        submit(self.db,9,data,'ip')
        lead=self.crm.card(self.crm.list_leads()['items'][0]['id'])
        self.assertEqual(lead['client']['contact'],'@customer')
        self.assertIsNone(lead['client']['name'])
        self.assertIn('50 000 рублей',lead['original_text'])
        self.assertIsNone(lead['confirmed_budget'])
        self.assertEqual(lead['categories'],[])

    def test_simple_form_without_budget(self):
        submit(self.db,9,{'contact':'+79991234567','description':'Нужно приложение для записи клиентов','consent':True,'request_id':'simple-form-request-02'},'ip')
        self.assertEqual(self.crm.list_leads()['total'],1)

    def test_price_preview_and_explicit_save(self):
        lead = self.crm.create({'title':'Сайт'})['id']
        data={'hours':'10','hourly_rate':'1000','expenses':'2000','risk_percent':'10','margin_percent':'20','discount_percent':'10','currency':'RUB'}
        result=pricing(self.crm,lead,data)
        self.assertEqual(result['minimum_price'],'13200.00')
        self.assertEqual(result['recommended_price'],'16500.00')
        self.assertEqual(result['discounted_price'],'14850.00')
        self.assertIsNone(self.crm.lead(lead)['recommended_price'])
        pricing(self.crm,lead,{**data,'save':True})
        self.assertEqual(self.crm.lead(lead)['recommended_price'],'16500.00')
        self.assertIsNone(self.crm.lead(lead)['confirmed_budget'])
        self.assertIsNone(self.crm.lead(lead)['desired_price'])

    def test_price_requires_inputs_and_valid_margin(self):
        lead=self.crm.create({'title':'Сайт'})['id']
        for data in ({'currency':'RUB'}, {'hours':'1','hourly_rate':'1','currency':'RUB','margin_percent':'100'}):
            with self.assertRaises(ValueError):
                pricing(self.crm,lead,data)

    def test_reply_analysis_is_repeatable_and_does_not_change_stage(self):
        lead=self.crm.create({'title':'Сайт'})['id']
        message=self.crm.add_conversation(lead,'Слишком дорого, нужен вариант дешевле','incoming')
        result=analyze_reply(self.crm,lead,{'conversation_id':message})
        self.assertEqual(result['intent'],'price_objection')
        self.assertIsNone(result['probability'])
        self.assertEqual(self.crm.lead(lead)['status'],'new')
        self.assertEqual(result,analyze_reply(self.crm,lead,{'conversation_id':message}))
        self.assertEqual(len(self.crm.related('drafts',lead)),1)

    def test_english_refusal_is_not_treated_as_interest(self):
        lead=self.crm.create({'title':'Website'})['id']
        message=self.crm.add_conversation(lead,'Not interested, thanks','incoming')
        result=analyze_reply(self.crm,lead,{'conversation_id':message})
        self.assertEqual(result['intent'],'declined')
        self.assertIn('will not follow up',self.crm.draft(result['draft_id'])['text'])

    def test_not_ready_is_not_agreement(self):
        lead=self.crm.create({'title':'Сайт'})['id']
        message=self.crm.add_conversation(lead,'Мы пока не готовы начинать','incoming')
        self.assertEqual(analyze_reply(self.crm,lead,{'conversation_id':message})['intent'],'postponed')


if __name__=='__main__':
    unittest.main()
