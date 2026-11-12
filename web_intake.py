"""Приём заявок сайта: SQLite, валидация, honeypot и ограничение частоты."""
import hashlib
import json
import re
import time
from crm import CRM, Conflict, text
from reliability import transaction


class RateLimited(ValueError):
    pass


def submit(db, owner, data, client_hash):
    if not isinstance(data, dict) or set(data) - {'name', 'contact', 'company', 'service', 'description', 'budget', 'timeline', 'website', 'consent', 'request_id'}:
        raise ValueError('Проверь поля заявки.')
    key = data.get('request_id')
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', key):
        raise ValueError('Нужен идентификатор заявки.')
    if data.get('website'):
        raise ValueError('Заявка отклонена защитой от спама.')
    if data.get('consent') is not True:
        raise ValueError('Подтверди согласие на обработку заявки.')
    values = {k: text(data.get(k), limit) for k, limit in [('contact', 500), ('description', 5000)]}
    if len(values['description']) < 15:
        raise ValueError('Опиши задачу подробнее: минимум 15 символов.')
    for k in ('name', 'service', 'company', 'budget', 'timeline'):
        if data.get(k):
            values[k] = text(data[k], 500)
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    now = time.time()
    with transaction(db):
        existing = db.execute('SELECT * FROM web_intakes WHERE owner_id=? AND request_id=?', (owner, key)).fetchone()
        if existing:
            if existing['request_hash'] != digest:
                raise Conflict('Идентификатор заявки уже использован.')
            return {'accepted': True, 'reference': existing['id']}
        count = db.execute('SELECT count(*) FROM web_intake_attempts WHERE client_hash=? AND created_at>?', (client_hash, now-3600)).fetchone()[0]
        if count >= 5:
            raise RateLimited('Слишком много заявок. Повтори через час или напиши в Telegram.')
        crm = CRM(db, owner)
        client = crm.create_client({'name': values.get('name'), 'contact': values['contact'], 'platform': 'website'})
        labels = {'service': 'Направление', 'description': 'Задача', 'name': 'Имя', 'contact': 'Контакт', 'company': 'Компания', 'budget': 'Бюджет со слов клиента', 'timeline': 'Желаемый срок'}
        original = '\n'.join(labels[k]+': '+v for k,v in values.items())
        service = values.get('service', 'Проект с сайта')
        lead = crm.create({'title': service+' — '+values.get('name', values['contact']), 'original_text': original,
                           'platform': 'website', 'source_key': 'byte-studio', 'source_name': 'Сайт Byte Studio',
                           'external_message_id': key, 'client_id': client['id'], 'categories': [values['service']] if values.get('service') else []})
        intake_id = db.execute('INSERT INTO web_intakes(owner_id,request_id,request_hash,lead_id,created_at) VALUES (?,?,?,?,?)', (owner,key,digest,lead['id'],now)).lastrowid
        db.execute('INSERT INTO web_intake_attempts(client_hash,created_at,outcome) VALUES (?,?,?)', (client_hash,now,'accepted'))
        payload = {'chat_id': owner, 'text': 'Новая заявка с сайта Byte Studio\n\n'+original[:3500], 'lead_id': lead['id'], 'parse_mode': None}
        db.execute('INSERT INTO notification_queue(lead_id,recipient,payload) VALUES (?,?,?)', (lead['id'],owner,json.dumps(payload,ensure_ascii=False)))
        return {'accepted': True, 'reference': intake_id}
