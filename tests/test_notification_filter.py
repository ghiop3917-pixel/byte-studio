import unittest

from delivery_filter import delivery_decision, suppress_notification
from lead_analysis import analyze
from reliability import claim, ingest, migrate, open_database, process_one


class NotificationFilterTests(unittest.TestCase):
    def setUp(self):
        self.db = open_database(":memory:")
        migrate(self.db)

    def tearDown(self):
        self.db.close()

    def collect(self, content, chat=-1001, author=101):
        message = {
            "chat": {"id": chat, "type": "supergroup", "title": "Тест"},
            "message_id": 1,
            "text": content,
            "from": {"id": author, "username": "testcustomer"},
        }
        raw_id = ingest(self.db, message, 9)
        process_one(self.db, lambda _: ("dev",))
        return raw_id

    def test_rhetorical_advertisements_do_not_generate_notifications(self):
        examples = [
            "Ищете легкую подработку без заморочек? Каждый день новые задания! Переходите в бот: @EasyMoney_bot",
            (
                "Ищешь занятость без офиса? Нужен только телефон и интернет. Оплата к"
                "аждый день. Ноль вложений. Бот: перейти."
            ),
            (
                "Ищете клиентов в США? Мы знаем, как увеличить ваш доход с помощью ра"
                "ссылок по чатам! Собственный софт, личный кабинет. Пишите в ЛС!"
            ),
            "Ищете разработчика сайта? Мы поможем! Создание сайтов, пишите в ЛС.",
            "Ищешь заработок? Нужен только телефон. Все задания в нашем боте.",
        ]
        for index, example in enumerate(examples):
            with self.subTest(text=example):
                self.assertNotEqual(analyze(example)["bucket"], "lead")
                self.collect(example, chat=-1000 - index)
        self.assertEqual(self.db.execute("SELECT count(*) FROM notification_queue").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM raw_messages").fetchone()[0], 5)

    def test_real_orders_are_still_allowed(self):
        examples = [
            "Нужен сайт с каталогом для мебельного магазина. Бюджет 70000 рублей.",
            "Ищу разработчика Telegram-бота для записи клиентов",
            "Ищем подрядчика для настройки CRM и автоматизации заявок",
            "Looking for a developer to build a website for my bakery",
            "Need a mobile app for booking appointments",
        ]
        for example in examples:
            with self.subTest(text=example):
                self.assertEqual(analyze(example)["bucket"], "lead")

    def test_same_customer_crosspost_is_not_notified_twice(self):
        self.collect("Нужен сайт для кафе", chat=-1001)
        first = claim(self.db, "notification_queue")
        self.assertTrue(delivery_decision(self.db, first, ("dev",))[0])
        self.db.execute("UPDATE notification_queue SET status='completed' WHERE id=?", (first["id"],))
        self.collect("Нужен сайт для кафе", chat=-1002)
        second = claim(self.db, "notification_queue")
        allowed, reason = delivery_decision(self.db, second, ("dev",))
        self.assertFalse(allowed)
        self.assertTrue(reason.startswith("duplicate:"))
        suppress_notification(self.db, second, reason)
        self.assertEqual(
            self.db.execute("SELECT status FROM notification_queue WHERE id=?", (second["id"],)).fetchone()[0],
            "suppressed",
        )
        self.assertEqual(self.db.execute("SELECT count(*) FROM raw_messages").fetchone()[0], 2)

    def test_different_customers_are_not_merged_by_same_short_request(self):
        self.collect("Нужен сайт для кафе", author=101)
        first = claim(self.db, "notification_queue")
        self.db.execute("UPDATE notification_queue SET status='completed' WHERE id=?", (first["id"],))
        self.collect("Нужен сайт для кафе", chat=-1002, author=102)
        second = claim(self.db, "notification_queue")
        self.assertTrue(delivery_decision(self.db, second, ("dev",))[0])

    def test_legacy_advertisement_is_stopped_before_delivery(self):
        self.collect("Нужен сайт для кафе")
        job = claim(self.db, "notification_queue")
        self.db.execute(
            (
                "UPDATE raw_messages SET original_text='Ищете клиентов? Мы знаем как "
                "увеличить ваш доход. Почему выбирают нас? Наш бот поможет!' WHERE id"
                "=1"
            )
        )
        allowed, reason = delivery_decision(self.db, job, ("dev",))
        self.assertFalse(allowed)
        self.assertEqual(reason, "filtered:promotion")


if __name__ == "__main__":
    unittest.main()
