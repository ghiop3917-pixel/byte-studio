"""Последняя проверка релевантности и повторов до сетевой отправки."""
import json
import re
import time
from lead_analysis import analyze
from reliability import transaction, event


def author_key(raw):
    if raw is None:
        return None
    author = json.loads(raw['payload']).get('message', {}).get('from', {})
    if author.get('id'):
        return 'id:' + str(author['id'])
    if author.get('username'):
        return 'username:' + author['username'].casefold()
    return None


def normalized(content):
    return re.sub(r'\s+', ' ', re.sub(r'[\u200b-\u200f\ufeff]', '', content)).strip().casefold()


def delivery_decision(db, job, directions):
    lead = db.execute('SELECT * FROM leads WHERE id=? AND recipient=?', (job['lead_id'], job['recipient'])).fetchone()
    if lead is None:
        return False, 'filtered:missing_lead'
    # Явные заявки сайта и ручные карточки не являются рекламой из Telegram-чатов.
    if lead['platform'] != 'telegram':
        return True, None
    raw = db.execute('SELECT r.* FROM raw_messages r JOIN lead_messages m ON m.raw_id=r.id WHERE m.lead_id=? AND m.recipient=? ORDER BY r.id DESC LIMIT 1', (lead['id'], job['recipient'])).fetchone()
    author = json.loads(raw['payload']).get('message', {}).get('from', {}) if raw else {}
    content = raw['original_text'] if raw else lead['original_text'] or lead['title']
    result = analyze(content, directions, author)
    approved = db.execute("SELECT 1 FROM message_reviews r JOIN lead_messages m ON m.raw_id=r.raw_id AND m.recipient=r.recipient WHERE m.lead_id=? AND m.recipient=? AND r.status='approved'", (lead['id'], job['recipient'])).fetchone()
    if result['bucket'] != 'lead' and not approved:
        return False, 'filtered:' + result['intent']
    identity = author_key(raw)
    if identity:
        previous = db.execute('''SELECT n.id,r.payload,r.original_text FROM notification_queue n
            JOIN lead_messages m ON m.lead_id=n.lead_id AND m.recipient=n.recipient
            JOIN raw_messages r ON r.id=m.raw_id
            WHERE n.recipient=? AND n.id<>? AND r.received_at>? AND
            (n.status='completed' OR (n.status='processing' AND n.id<?))
            ORDER BY n.id DESC LIMIT 2000''', (job['recipient'],job['id'],time.time()-48*3600,job['id']))
        for candidate in previous:
            if author_key(candidate)==identity and normalized(candidate['original_text'])==normalized(content):
                return False, 'duplicate:notification:' + str(candidate['id'])
    return True, None


def suppress_notification(db, job, reason):
    with transaction(db):
        changed = db.execute("UPDATE notification_queue SET status='suppressed',error=?,token=NULL,lease_until=NULL WHERE id=? AND token=? AND status='processing'", (reason,job['id'],job['token'])).rowcount
        if changed:
            event(db,'notification_filter',job['id'],reason)
