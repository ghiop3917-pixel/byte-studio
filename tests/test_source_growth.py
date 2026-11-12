import unittest
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from telethon import types
from source_growth import assess_messages


class GrowthSelectionTests(unittest.TestCase):
    def messages(self,text,days=0):
        now=datetime.now(timezone.utc)
        return [SimpleNamespace(date=now-timedelta(days=days),raw_text=text if n==0 else 'Обсуждение бизнеса и новостей',
                  from_id=types.PeerUser(100+n%5),sender_id=100+n%5,sender=SimpleNamespace(username=None,bot=False)) for n in range(12)]

    def test_active_group_requires_a_real_customer_request(self):
        report=assess_messages(self.messages('Нужен сайт с каталогом для мебельного магазина'),('dev',))
        self.assertTrue(report['accepted']);self.assertEqual(report['authors'],5)

    def test_advertisements_are_not_proof_of_orders(self):
        report=assess_messages(self.messages('Ищете клиентов? Мы знаем как увеличить ваш доход! Рассылки по чатам. Наш бот поможет.'),('dev',))
        self.assertTrue(report['active']);self.assertFalse(report['accepted']);self.assertEqual(report['lead_examples'],0)

    def test_old_or_one_author_group_is_not_active_enough(self):
        self.assertFalse(assess_messages(self.messages('Нужен сайт для кафе',days=3),('dev',))['accepted'])
        messages=self.messages('Нужен сайт для кафе')
        for item in messages:item.sender_id=100
        self.assertFalse(assess_messages(messages,('dev',))['accepted'])

    def test_directions_are_not_broadened(self):
        self.assertFalse(assess_messages(self.messages('Нужен дизайн логотипа'),('dev',))['accepted'])


if __name__=='__main__':unittest.main()
