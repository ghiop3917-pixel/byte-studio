"""Экспорт CRM с защитой от формул в пользовательском тексте."""

import csv
from io import BytesIO, StringIO

from crm import STATUSES

COLUMNS = {
    "id": "ID", "title": "Задача", "platform": "Платформа", "status": "Статус",
    "priority": "Приоритет", "original_text": "Оригинальное сообщение", "url": "Ссылка",
    "discovered_at": "Дата обнаружения UTC", "client_id": "ID заказчика", "score": "Оценка",
    "client_name": "Заказчик", "contact": "Контакт", "source": "Источник", "categories": "Категории",
    "confirmed_budget": "Подтверждённый бюджет", "estimated_budget_min": "Предполагаемый бюджет от",
    "estimated_budget_max": "Предполагаемый бюджет до", "recommended_price": "Рекомендуемая цена",
    "minimum_price": "Минимальная цена", "desired_price": "Желаемая цена",
    "currency": "Валюта", "estimated_timeline": "Предполагаемые сроки",
}


def rows(service, filters):
    from datetime import datetime, timezone
    result = service.list_leads(limit=10000, **filters)
    if result["total"] > 10000:
        raise ValueError("Экспорт ограничен 10000 карточек. Уточни фильтры.")
    yield list(COLUMNS.values())
    for lead in result["items"]:
        lead = dict(lead)
        lead["status"] = STATUSES[lead["status"]]
        if lead["discovered_at"] is not None:
            lead["discovered_at"] = datetime.fromtimestamp(lead["discovered_at"], timezone.utc).isoformat()
        yield [lead[key] if lead[key] is not None else "" for key in COLUMNS]


def export(service, file_format, **filters):
    if set(filters) - {"search", "status", "platform", "category", "priority", "include_merged"}:
        raise ValueError("Неизвестные фильтры экспорта.")
    if file_format == "csv":
        output = StringIO(newline="")
        writer = csv.writer(output)
        for row in rows(service, filters):
            writer.writerow([
                "'" + str(value) if str(value).lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n"))
                else value for value in row
            ])
        return output.getvalue().encode("utf-8-sig")
    if file_format == "xlsx":
        from openpyxl import Workbook
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Лиды"
        for row in rows(service, filters):
            if any(isinstance(value, str) and len(value) > 32767 for value in row):
                raise ValueError("Текст превышает лимит ячейки XLSX. Используй CSV.")
            row = [
                "".join(char if ord(char) >= 32 or char in "\t\n\r" else "�" for char in value)
                if isinstance(value, str) else value for value in row
            ]
            sheet.append(row)
            for cell in sheet[sheet.max_row]:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = min(
                60, max(12, max(len(str(cell.value or "")) for cell in column) + 2)
            )
        output = BytesIO()
        workbook.save(output)
        workbook.close()
        return output.getvalue()
    raise ValueError("Поддерживаются форматы csv и xlsx.")
