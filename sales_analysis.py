"""Объяснимые расчёты и подсказки для переговоров, без сетевых вызовов."""
import json
import re
from decimal import Decimal, ROUND_HALF_UP
from crm import amount, currency, activity, identifier, NotFound
from reliability import transaction


def pricing(service, lead_id, data):
    service.lead(lead_id, writable=True)
    if set(data) - {'hours','hourly_rate','expenses','risk_percent','margin_percent','discount_percent','currency','save'}:
        raise ValueError('Неизвестное поле расчёта.')
    if type(data.get('save', False)) is not bool:
        raise ValueError('save должен быть логическим значением.')
    code = currency(data.get('currency'))
    if not code:
        raise ValueError('Укажи валюту расчёта.')
    def number(key, default=None):
        value = amount(data.get(key, default))
        if value is None:
            raise ValueError('Для расчёта нужны трудозатраты и ставка владельца.')
        return Decimal(value)
    hours, rate = number('hours'), number('hourly_rate')
    expenses, risk = number('expenses', '0'), number('risk_percent', '0')
    margin, discount = number('margin_percent', '0'), number('discount_percent', '0')
    if hours > 100000 or rate > 10000000 or risk > 100 or margin >= 100 or discount > 100:
        raise ValueError('Проверь трудозатраты, ставку и проценты.')
    cost = hours * rate + expenses
    minimum = cost * (1 + risk / 100)
    recommended = minimum / (1 - margin / 100)
    discounted = recommended * (1 - discount / 100)
    fmt = lambda n: str(n.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
    result = {'currency':code,'estimated_cost':fmt(cost),'minimum_price':fmt(minimum),
              'recommended_price':fmt(recommended),'discounted_price':fmt(discounted),
              'estimated_profit':fmt(discounted-cost),'below_minimum':discounted<minimum,
              'basis':'Расчёт по введённым часам, ставке, расходам, резерву риска и марже. Не рыночная оценка и не подтверждённый бюджет.',
              'saved':data.get('save',False)}
    if result['saved']:
        with transaction(service.db):
            service.update(lead_id, {'minimum_price':result['minimum_price'],'recommended_price':result['recommended_price'],'currency':code})
            activity(service.db, lead_id, 'pricing_calculated', {'inputs':data,'result':result})
    return result


def analyze_reply(service, lead_id, data):
    if set(data) != {'conversation_id'}:
        raise ValueError('Укажи conversation_id входящего ответа.')
    conversation_id = identifier(data['conversation_id'])
    service.lead(lead_id, writable=True)
    message = next((c for c in service.related('conversations',lead_id) if c['id']==conversation_id), None)
    if message is None or message['direction']!='incoming' or message['provenance']!='manual':
        raise NotFound('Нужен вручную зарегистрированный входящий ответ клиента.')
    previous = service.db.execute("SELECT details FROM activities WHERE lead_id=? AND kind='reply_analyzed' AND json_extract(details,'$.conversation_id')=? ORDER BY id DESC LIMIT 1", (lead_id,conversation_id)).fetchone()
    if previous:
        return json.loads(previous['details'])
    value=message['text'].casefold()
    if re.search(r'не\s+(?:актуаль|интерес|нуж|пиш)|отказ|нашли\s+исполн|not interested|no longer|stop contacting|found a developer',value):
        intent,status,answer='declined','rejected','Спасибо за ответ. Понял, больше не буду отвлекать по этой задаче.'
    elif re.search(r'дорог|дешев|скидк|бюджет|цен[ауы]|стоим|expensive|discount|budget|price|cost',value):
        intent,status,answer='price_objection','negotiation','Давайте уточним приоритетные функции. Можно обсудить меньший первый этап и отдельно согласовать стоимость, сохранив ключевой результат.'
    elif re.search(r'позже|следующ|верн[её]м|не\s+готов|не\s+можем\s+сейчас|not ready|later|next month',value):
        intent,status,answer='postponed','awaiting_decision','Понял. Когда будет удобно вернуться к обсуждению? Без согласованной даты не буду напоминать повторно.'
    elif re.search(r'соглас|начина|договор|готов|let.s start|agreed|contract',value):
        intent,status,answer='interested','negotiation','Предлагаю зафиксировать объём, критерии приёмки, этапы и условия оплаты. После согласования сможем перейти к работе.'
    else:
        intent,status,answer='uncertain',None,'Спасибо за подробности. Уточним ожидаемый результат первой версии и следующий шаг?'
    english=bool(re.search(r'[a-z]{3}',value)) and not re.search(r'[а-яё]',value)
    if english:
        answer={'declined':'Thanks for letting me know. I will not follow up on this request.', 'price_objection':'Which features matter most for the first version? We can discuss a smaller scope and agree on its price separately.', 'postponed':'When would you prefer to revisit this? I will wait for an agreed date before following up.', 'interested':'Shall we agree on the scope, acceptance criteria, milestones and payment terms first?', 'uncertain':'Thanks for the details. Could we clarify the first version and the next step?'}[intent]
    with transaction(service.db):
        draft_id=service.add_draft(lead_id,answer)
        result={'conversation_id':conversation_id,'intent':intent,'suggested_status':status,'draft_id':draft_id,'probability':None,'basis':'Эвристика по словам ответа. Проверь контекст; стадия и цена автоматически не меняются.'}
        activity(service.db,lead_id,'reply_analyzed',result)
    return result
