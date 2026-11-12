"""Обслуживание внутри текущего процесса. Никаких Telegram-рассылок и удалений."""
import hashlib
import json
import shutil
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from crm import Conflict, NotFound, identifier
from insights import Insights
from reliability import open_database, transaction

INTERVALS={'health':60,'backup':24*3600,'insights':24*3600}
MAX_FAILURES=3
JOB_LEASE=180


def seed_jobs(db,owner,now=None):
    now=time.time() if now is None else now
    with transaction(db):
        for kind in INTERVALS:
            db.execute('INSERT OR IGNORE INTO maintenance_jobs(name,owner_id,status,next_run) VALUES (?,?,?,?)',
                       (f'{kind}:{owner}',identifier(owner),'pending',now))


def claim_job(db,owner,now=None):
    now=time.time() if now is None else now
    with transaction(db):
        stale=db.execute("SELECT * FROM maintenance_jobs WHERE owner_id=? AND status='running' AND lease_until<?",(owner,now)).fetchall()
        for row in stale:
            state='failed' if row['attempts']>=MAX_FAILURES else 'retry_wait'
            db.execute('''UPDATE maintenance_jobs SET status=?,next_run=?,token=NULL,lease_until=NULL,error='lease_expired'
                WHERE name=? AND token=?''',(state,now+300,row['name'],row['token']))
            db.execute("UPDATE maintenance_runs SET status='interrupted',finished_at=?,error='lease_expired' WHERE job_name=? AND status='running'",(now,row['name']))
        row=db.execute("SELECT * FROM maintenance_jobs WHERE owner_id=? AND status IN ('pending','completed','retry_wait') AND next_run<=? AND attempts<? ORDER BY next_run,name LIMIT 1",(owner,now,MAX_FAILURES)).fetchone()
        if row is None:return None
        token=uuid.uuid4().hex
        db.execute("UPDATE maintenance_jobs SET status='running',attempts=attempts+1,started_at=?,lease_until=?,token=?,error=NULL WHERE name=?",(now,now+JOB_LEASE,token,row['name']))
        run_id=db.execute("INSERT INTO maintenance_runs(job_name,owner_id,started_at,status) VALUES (?,?,?,'running')",(row['name'],owner,now)).lastrowid
        return {**dict(db.execute('SELECT * FROM maintenance_jobs WHERE name=?',(row['name'],)).fetchone()),'run_id':run_id}


def finish_job(db,job,result=None,error=None,now=None):
    now=time.time() if now is None else now;kind=job['name'].split(':',1)[0]
    with transaction(db):
        current=db.execute('SELECT token,status FROM maintenance_jobs WHERE name=?',(job['name'],)).fetchone()
        if current is None or current['token']!=job['token'] or current['status']!='running':return False
        if error is None:
            state='completed';due=now+INTERVALS[kind];attempts=0;code=None
        else:
            state='failed' if job['attempts']>=MAX_FAILURES else 'retry_wait'
            due=now+min(300*2**(job['attempts']-1),3600);attempts=job['attempts'];code=type(error).__name__
        content=json.dumps(result,ensure_ascii=False) if result is not None else None
        db.execute('''UPDATE maintenance_jobs SET status=?,attempts=?,finished_at=?,next_run=?,lease_until=NULL,token=NULL,
            error=?,result=?,last_success=CASE WHEN ? IS NULL THEN ? ELSE last_success END WHERE name=?''',
            (state,attempts,now,due,code,content,code,now,job['name']))
        db.execute('UPDATE maintenance_runs SET status=?,finished_at=?,error=?,result=? WHERE id=?',(state,now,code,content,job['run_id']))
        return True


def health_report(db,owner,now=None):
    now=time.time() if now is None else now;alerts=[];heartbeats=[]
    for component in ('monitor','processing','notifications'):
        row=db.execute('SELECT * FROM core_heartbeat WHERE component=?',(component,)).fetchone()
        age=max(0,now-row['updated_at']) if row else None
        state='unknown' if row is None else 'stale' if age>90 else 'stopped' if row['detail']=='stopped' else 'running'
        heartbeats.append({'component':component,'age_seconds':age,'state':state})
        if state!='running':alerts.append({'kind':'heartbeat','component':component,'state':state})
    queues={}
    for table in ('processing_queue','notification_queue'):
        condition=" AND recipient=?" if table=='notification_queue' else " AND raw_id IN (SELECT id FROM raw_messages WHERE json_extract(payload,'$.recipient')=?)"
        rows=dict(db.execute(f'SELECT status,count(*) FROM {table} WHERE 1=1{condition} GROUP BY status',(owner,)))
        stale=db.execute(f"SELECT count(*) FROM {table} WHERE status='processing' AND lease_until<?{condition}",(now,owner)).fetchone()[0]
        exhausted=db.execute(f"SELECT count(*) FROM {table} WHERE status IN ('pending','retry_wait') AND attempts>=5{condition}",(owner,)).fetchone()[0]
        ambiguous=rows.get('ambiguous',0)
        queues[table]={'states':rows,'expired_leases':stale,'exhausted_waiting':exhausted}
        if stale or exhausted or rows.get('failed',0) or ambiguous:
            alerts.append({'kind':'queue','queue':table,'expired_leases':stale,'exhausted_waiting':exhausted,'failed':rows.get('failed',0),'ambiguous':ambiguous})
    sources=dict(db.execute('SELECT status,count(*) FROM source_health GROUP BY status'))
    if sources.get('failed',0):alerts.append({'kind':'sources','failed':sources['failed']})
    cooldowns=[]
    for row in db.execute("SELECT component,detail FROM core_heartbeat WHERE component LIKE 'cooldown:%'"):
        try:until=float(row['detail'])
        except (TypeError,ValueError):continue
        if until>now:cooldowns.append({'method':row['component'].split(':',1)[1],'until':until})
    if cooldowns:alerts.append({'kind':'telegram_cooldown','count':len(cooldowns)})
    last_backup=db.execute('SELECT created_at,path FROM backup_records WHERE owner_id=? ORDER BY id DESC LIMIT 1',(owner,)).fetchone()
    if last_backup is None or now-last_backup['created_at']>26*3600:alerts.append({'kind':'backup','state':'missing' if last_backup is None else 'stale'})
    failed_jobs=db.execute("SELECT count(*) FROM maintenance_jobs WHERE owner_id=? AND status='failed'",(owner,)).fetchone()[0]
    if failed_jobs:alerts.append({'kind':'maintenance_failed','count':failed_jobs})
    scheduler=db.execute("SELECT detail FROM core_heartbeat WHERE component='maintenance'").fetchone()
    if scheduler and scheduler['detail']=='paused_after_errors':alerts.append({'kind':'maintenance_paused'})
    database=Path(db.execute('PRAGMA database_list').fetchone()[2])
    free=shutil.disk_usage(database.parent).free
    if free<128*1024**2:alerts.append({'kind':'disk_space'})
    return {'checked_at':now,'status':'attention' if alerts else 'healthy','heartbeats':heartbeats,'queues':queues,
            'sources':sources,'cooldowns':cooldowns,'last_backup':dict(last_backup) if last_backup else None,'alerts':alerts,
            'storage':{'database_bytes':database.stat().st_size if database.is_file() else None,'free_bytes':free},
            'automatic_deletion':False,'automatic_ambiguous_retry':False}


def digest(path):
    result=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):result.update(block)
    return result.hexdigest()


def create_backup(db,owner,job_name=None,now=None):
    if db.in_transaction:raise RuntimeError('Резервная копия не создаётся внутри транзакции записи.')
    now=time.time() if now is None else now
    database=Path(db.execute('PRAGMA database_list').fetchone()[2])
    if not database.is_file():raise ValueError('Для копии нужна файловая база.')
    folder=database.parent/'backups';folder.mkdir(exist_ok=True)
    size=database.stat().st_size
    if shutil.disk_usage(folder).free<max(128*1024**2,size*3):raise OSError('Недостаточно свободного места для проверенной копии.')
    name=f'scheduled-{int(now)}-{uuid.uuid4().hex[:12]}'
    partial=folder/(name+'.partial');destination=folder/(name+'.sqlite3')
    partial.touch(exist_ok=False);deadline=time.monotonic()+45
    def progress(status,remaining,total):
        if time.monotonic()>deadline:raise TimeoutError('Копирование превысило допустимое время.')
    with closing(sqlite3.connect(partial)) as copy:
        db.backup(copy,pages=128,progress=progress,sleep=.01)
        if copy.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or copy.execute('PRAGMA foreign_key_check').fetchone():
            raise RuntimeError('Проверка резервной копии не пройдена.')
    if destination.exists():raise FileExistsError('Путь резервной копии уже занят.')
    partial.rename(destination)
    record={'path':destination.name,'created_at':now,'size':destination.stat().st_size,'sha256':digest(destination),'integrity':'ok'}
    with transaction(db):
        record['id']=db.execute('INSERT INTO backup_records(owner_id,path,created_at,size,sha256,integrity,job_name) VALUES (?,?,?,?,?,?,?)',
            (owner,record['path'],now,record['size'],record['sha256'],'ok',job_name)).lastrowid
    return record


def verify_backup(db,owner,backup_id):
    record=db.execute('SELECT * FROM backup_records WHERE id=? AND owner_id=?',(identifier(backup_id),owner)).fetchone()
    if record is None:raise NotFound('Проверенная копия не найдена.')
    database=Path(db.execute('PRAGMA database_list').fetchone()[2]);folder=(database.parent/'backups').resolve()
    path=(folder/record['path']).resolve()
    if path.parent!=folder or not path.is_file():raise ValueError('Файл копии недоступен.')
    if digest(path)!=record['sha256']:raise ValueError('Контрольная сумма копии не совпадает.')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as copy:
        integrity=copy.execute('PRAGMA integrity_check').fetchone()[0]
        if integrity!='ok' or copy.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('Копия повреждена.')
    return {'id':backup_id,'integrity':integrity,'sha256_match':True,'restored':False}


def run_tick(database,owner,now=None):
    """Вызывается в одном потоке asyncio.to_thread, не создаёт процессов или отправок."""
    now=time.time() if now is None else now
    with closing(open_database(database)) as db:
        seed_jobs(db,owner,now);job=claim_job(db,owner,now)
        if job is None:return None
        kind=job['name'].split(':',1)[0]
        try:
            if kind=='backup':result=create_backup(db,owner,job['name'],now)
            elif kind=='health':result=health_report(db,owner,now)
            else:
                service=Insights(db,owner);result={'report':service.report(now=now),'suggestions':service.generate_suggestions(now)}
            if kind!='backup':
                with transaction(db):db.execute('INSERT INTO system_snapshots(owner_id,kind,created_at,report) VALUES (?,?,?,?)',(owner,kind,now,json.dumps(result,ensure_ascii=False)))
            finish_job(db,job,result=result,now=time.time())
            return {'job':kind,'status':'completed'}
        except Exception as error:
            finish_job(db,job,error=error,now=time.time())
            return {'job':kind,'status':'failed' if job['attempts']>=MAX_FAILURES else 'retry_wait','error':type(error).__name__}


def operations_report(db,owner):
    jobs=[]
    for row in db.execute('SELECT name,status,attempts,next_run,started_at,finished_at,last_success,error FROM maintenance_jobs WHERE owner_id=? ORDER BY name',(owner,)):
        jobs.append({**dict(row),'kind':row['name'].split(':',1)[0]})
    backups=[dict(row) for row in db.execute('SELECT id,path,created_at,size,sha256,integrity FROM backup_records WHERE owner_id=? ORDER BY id DESC LIMIT 30',(owner,))]
    runs=[dict(row) for row in db.execute('SELECT id,job_name,started_at,finished_at,status,error FROM maintenance_runs WHERE owner_id=? ORDER BY id DESC LIMIT 30',(owner,))]
    return {'jobs':jobs,'backups':backups,'runs':runs,'health':health_report(db,owner),'telegram_reports':False,'automatic_deletion':False}


def request_run(db,owner,kind):
    if kind not in INTERVALS:raise ValueError('Неизвестное задание обслуживания.')
    with transaction(db):
        seed_jobs(db,owner);name=f'{kind}:{owner}'
        row=db.execute('SELECT status FROM maintenance_jobs WHERE name=? AND owner_id=?',(name,owner)).fetchone()
        if row['status']=='running':raise Conflict('Задание уже выполняется.')
        db.execute("UPDATE maintenance_jobs SET status='pending',attempts=0,next_run=?,error=NULL WHERE name=?",(time.time(),name))
        from reliability import heartbeat
        heartbeat(db,f'maintenance_resume:{owner}')
    return {'queued':True,'job':kind,'message':'Задание поставлено в очередь. Оно выполнится при работающем мониторинге.'}
