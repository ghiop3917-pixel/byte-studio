import json
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

from bot_menu import dispatch
from crm import CRM,NotFound,Conflict
from crm_api import CRMApp,Unauthorized
from insights import Insights
from maintenance import (seed_jobs,claim_job,finish_job,request_run,create_backup,verify_backup,
                         run_tick,health_report,operations_report)
from operations_migrations import migrate_operations
from quality import Quality
from reliability import open_database,migrate,ingest,process_one,heartbeat


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'monitor.sqlite3'
        self.db=open_database(self.path);migrate(self.db);self.crm=CRM(self.db,9);self.next_message=1

    def tearDown(self):
        self.db.close();self.temp.cleanup()

    def collect(self,chat=-1001,owner=9):
        message={'chat':{'id':chat,'type':'supergroup','title':'Источник '+str(chat)},'message_id':self.next_message,
                 'text':'Нужен сайт для магазина, проект '+str(self.next_message),'from':{'id':100+self.next_message}}
        self.next_message+=1;raw=ingest(self.db,message,owner);process_one(self.db,lambda _:('dev',))
        return self.db.execute('SELECT lead_id FROM lead_messages WHERE raw_id=? AND recipient=?',(raw,owner)).fetchone()[0]

    def test_no_ratings_do_not_claim_quality(self):
        self.collect();report=Insights(self.db,9).report()
        self.assertEqual(report['feedback']['judged'],0)
        self.assertIsNone(report['feedback']['observed_approval_rate'])
        self.assertTrue(report['sources'][0]['small_sample'])
        self.assertFalse(report['automatic_learning'])
        self.assertIsNone(report['recall'])

    def test_latest_feedback_only_and_owner_isolation(self):
        lead=self.collect();self.collect(chat=-1002,owner=10)
        quality=Quality(self.db,9);quality.feedback(lead,'approved');quality.feedback(lead,'rejected','Реклама / рассылка')
        report=Insights(self.db,9).report()
        self.assertEqual(len(report['sources']),1)
        self.assertEqual(report['sources'][0]['judged'],1)
        self.assertEqual(report['sources'][0]['rejected'],1)
        self.assertEqual(report['feedback']['approved'],0)
        self.assertEqual(report['rejection_reasons'],{'Реклама / рассылка':1})
        self.assertEqual(Insights(self.db,11).report()['sources'],[])

    def test_merged_projects_count_once_per_source_and_category(self):
        first=self.collect();second=self.collect(chat=-1002);self.crm.merge(first,second)
        Quality(self.db,9).feedback(first,'approved')
        report=Insights(self.db,9).report()
        self.assertEqual(report['feedback']['leads'],1)
        self.assertEqual([s['approved'] for s in report['sources']],[1,1])
        self.assertEqual(report['categories'][0]['approved'],1)

    def test_suggestions_are_idempotent_do_not_change_rules_and_keep_decisions(self):
        for _ in range(5):Quality(self.db,9).feedback(self.collect(),'rejected','Реклама / рассылка')
        insights=Insights(self.db,9);insights.generate_suggestions();items=insights.suggestions()
        self.assertTrue(any(item['kind']=='review_source' for item in items))
        insights.generate_suggestions();self.assertEqual(len(items),len(insights.suggestions()))
        suggestion=items[0]['id'];result=insights.decide(suggestion,'approved')
        self.assertFalse(result['rules_changed'])
        insights.decide(suggestion,'approved');self.assertEqual(self.db.execute('SELECT count(*) FROM suggestion_decisions').fetchone()[0],1)
        insights.decide(suggestion,'rejected');self.assertEqual(self.db.execute('SELECT count(*) FROM suggestion_decisions').fetchone()[0],2)
        with self.assertRaises(NotFound):Insights(self.db,10).decide(suggestion,'approved')

    def test_prices_are_real_and_separated_by_currency(self):
        lead=self.crm.create({'title':'Проект'})['id']
        for amount in ['8500','9500','10000']:self.crm.add_deal(lead,{'title':'Сделка','status':'won','actual_revenue':amount,'currency':'RUB'})
        self.crm.add_deal(lead,{'title':'Другая сделка','status':'won','actual_revenue':'200','currency':'USD'})
        report=Insights(self.db,9).report();prices={p['currency']:p for p in report['won_prices']}
        self.assertEqual(prices['RUB']['median'],'9500')
        self.assertIsNone(prices['USD']['median'])

    def test_backup_is_verified_and_restorable_without_changing_source(self):
        lead=self.crm.create({'title':'Сохранить меня'})['id'];self.crm.add_note(lead,'Важная история')
        backup=create_backup(self.db,9);self.assertTrue(verify_backup(self.db,9,backup['id'])['sha256_match'])
        with closing(sqlite3.connect(self.path.parent/'backups'/backup['path'])) as copy:
            self.assertEqual(copy.execute('SELECT text FROM notes').fetchone()[0],'Важная история')
            self.assertEqual(copy.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            with closing(sqlite3.connect(Path(self.temp.name)/'restored.sqlite3')) as restored:
                copy.backup(restored)
                self.assertEqual(restored.execute('SELECT title FROM leads').fetchone()[0],'Сохранить меня')
        self.assertEqual(self.crm.card(lead)['notes'][0]['text'],'Важная история')
        with self.assertRaises(NotFound):verify_backup(self.db,10,backup['id'])
        with (self.path.parent/'backups'/backup['path']).open('ab') as stream:stream.write(b'tampered')
        with self.assertRaises(ValueError):verify_backup(self.db,9,backup['id'])

    def test_backup_refuses_write_transaction_and_low_disk_space(self):
        self.db.execute('BEGIN IMMEDIATE')
        with self.assertRaises(RuntimeError):create_backup(self.db,9)
        self.db.rollback()
        with patch('maintenance.shutil.disk_usage',return_value=SimpleNamespace(free=0)):
            with self.assertRaises(OSError):create_backup(self.db,9)
        self.assertEqual(self.db.execute('SELECT count(*) FROM backup_records').fetchone()[0],0)

    def test_expired_job_is_recovered_and_old_worker_cannot_finish_it(self):
        seed_jobs(self.db,9,1000);self.db.execute("UPDATE maintenance_jobs SET next_run=100000 WHERE name!='backup:9'")
        first=claim_job(self.db,9,1000);self.assertEqual(first['name'],'backup:9')
        self.assertIsNone(claim_job(self.db,9,1181))
        second=claim_job(self.db,9,1482);self.assertEqual(second['attempts'],2)
        self.assertFalse(finish_job(self.db,first,result={},now=1483))
        self.assertTrue(finish_job(self.db,second,result={'ok':True},now=1483))

    def test_failures_stop_after_three_attempts_until_manual_request(self):
        seed_jobs(self.db,9,1000);self.db.execute("UPDATE maintenance_jobs SET next_run=100000 WHERE name!='backup:9'")
        now=1000
        for _ in range(3):
            job=claim_job(self.db,9,now);finish_job(self.db,job,error=OSError('private path must not be stored'),now=now)
            now=self.db.execute("SELECT next_run FROM maintenance_jobs WHERE name='backup:9'").fetchone()[0]
        self.assertIsNone(claim_job(self.db,9,now+1))
        record=self.db.execute("SELECT * FROM maintenance_jobs WHERE name='backup:9'").fetchone()
        self.assertEqual(record['status'],'failed');self.assertEqual(record['error'],'OSError')
        request_run(self.db,9,'backup')
        self.assertEqual(self.db.execute("SELECT attempts FROM maintenance_jobs WHERE name='backup:9'").fetchone()[0],0)

    def test_parallel_claims_do_not_run_same_job(self):
        seed_jobs(self.db,9)
        barrier=threading.Barrier(2)
        def claim():
            db=open_database(self.path)
            try:barrier.wait();return claim_job(db,9)['name']
            finally:db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(claim);b=pool.submit(claim);self.assertNotEqual(a.result(),b.result())

    def test_restart_keeps_schedule_and_never_creates_notifications(self):
        for _ in range(3):self.assertEqual(run_tick(self.path,9)['status'],'completed')
        self.assertIsNone(run_tick(self.path,9))
        self.assertEqual(self.db.execute('SELECT count(*) FROM backup_records').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM system_snapshots').fetchone()[0],2)
        self.assertEqual(self.db.execute('SELECT count(*) FROM notification_queue').fetchone()[0],0)

    def test_health_detects_stale_queue_without_resending_ambiguous_delivery(self):
        lead=self.collect();self.db.execute("UPDATE notification_queue SET status='ambiguous'")
        report=health_report(self.db,9)
        self.assertEqual(report['queues']['notification_queue']['states']['ambiguous'],1)
        self.assertFalse(report['automatic_ambiguous_retry'])
        self.assertEqual(self.db.execute('SELECT status FROM notification_queue').fetchone()[0],'ambiguous')

    def test_manual_run_conflict_and_api_idempotency(self):
        app=CRMApp(self.path,9,'x'*40);auth='Bearer '+'x'*40
        first=app.request('POST','/api/operations/run',auth,{'kind':'backup'},'operations-request-key-1')
        self.assertEqual(first,app.request('POST','/api/operations/run',auth,{'kind':'backup'},'operations-request-key-1'))
        job=claim_job(self.db,9)
        with self.assertRaises(Conflict):request_run(self.db,9,job['name'].split(':')[0])
        with self.assertRaises(Unauthorized):app.request('GET','/api/insights',None)
        self.assertEqual(json.loads(CRMApp(self.path,10,'x'*40).request('GET','/api/operations',auth)[1])['jobs'],[])

    def test_telegram_reason_is_owner_scoped_idempotent_and_keeps_original_buttons(self):
        lead=self.crm.create({'title':'Проверить заявку'})['id'];telegram=Mock()
        update={'callback_query':{'id':'reject-1','from':{'id':9},'data':f'fb:rejected:{lead}',
                'message':{'message_id':33,'chat':{'id':9},'reply_markup':{'inline_keyboard':[[{'text':'Оригинал','url':'https://t.me/test/1'}]]}}}}
        with patch('bot_menu.MONITOR_DB',self.path):
            dispatch(telegram,update,9,27)
            edits=[c for c in telegram.call.call_args_list if c.args[0]=='editMessageReplyMarkup']
            self.assertEqual(edits[0].kwargs['reply_markup']['inline_keyboard'][0][0]['text'],'Оригинал')
            update['callback_query'].update(id='reason-1',data=f'fbr:spam:{lead}')
            dispatch(telegram,update,9,27);dispatch(telegram,update,9,27)
        self.assertEqual(self.crm.card(lead)['lead_feedback'][-1]['reason'],'Реклама / рассылка')
        self.assertEqual(self.db.execute('SELECT count(*) FROM lead_feedback').fetchone()[0],2)

    def test_migration_is_transactional_and_preserves_existing_data(self):
        path=Path(self.temp.name)/'legacy.sqlite3';db=open_database(path)
        try:
            with patch('operations_migrations.migrate_operations'):migrate(db)
            CRM(db,9).create({'title':'Старый проект'})
            class FailingConnection:
                def __getattr__(self,key):return getattr(db,key)
                def execute(self,sql,*args):
                    if 'CREATE TABLE backup_records' in sql:raise sqlite3.OperationalError('test')
                    return db.execute(sql,*args)
            with self.assertRaises(sqlite3.OperationalError):migrate_operations(FailingConnection())
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='quality_suggestions'").fetchone())
            self.assertIsNone(db.execute('SELECT 1 FROM core_migrations WHERE version=5').fetchone())
            self.assertEqual(db.execute('SELECT title FROM leads').fetchone()[0],'Старый проект')
            migrate(db);migrate(db)
            self.assertEqual(db.execute('SELECT count(*) FROM leads').fetchone()[0],1)
            self.assertTrue(list((path.parent/'backups').glob('before-operations-*.sqlite3')))
        finally:db.close()


if __name__=='__main__':unittest.main()
