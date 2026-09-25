# Фрагмент действующей документации orders-api

Выгружено из внутренней wiki 1 сентября 2026. Актуальность не гарантирую —
страницу последний раз правили в феврале.

## Возврат заказа целиком

```
POST /orders/{order_id}/refund
```

Тело запроса пустое. Возвращает заказ в новом статусе.

Ответ 200:

```json
{
  "order_id": "ord-10231",
  "status": "refunded",
  "refunded_amount": 5000,
  "refund_id": "rfn-88120"
}
```

Коды ответов:

- `200` — возврат принят;
- `404` — заказа нет;
- `409` — заказ не в статусе `delivered`;
- `502` — эквайер не ответил.

Ретраев нет. При `502` оператор жмёт кнопку ещё раз.

## Статусы заказа

| Статус | Что означает |
|---|---|
| `created` | создан, не оплачен |
| `paid` | оплачен, не собран |
| `shipped` | передан в доставку |
| `delivered` | вручён клиенту |
| `cancelled` | отменён до оплаты |
| `refunded` | возвращён полностью |

Переходы: `created → paid → shipped → delivered`. Отмена возможна из
`created` и `paid`. Возврат — только из `delivered`.

## Модель заказа

```json
{
  "order_id": "ord-10231",
  "customer_id": "cus-4471",
  "status": "delivered",
  "total": 5000,
  "discount": 500,
  "payment_id": "pay-77341",
  "items": [
    {"item_id": "itm-1", "sku": "SKU-100", "title": "Кружка", "price": 1500, "qty": 2},
    {"item_id": "itm-2", "sku": "SKU-205", "title": "Чайник", "price": 2000, "qty": 1}
  ]
}
```

## События

Сервис публикует в Kafka:

- `order.paid` — топик `orders.events`;
- `order.shipped` — топик `orders.events`;
- `order.delivered` — топик `orders.events`;
- `order.refunded` — топик `orders.events`.

Потребители `orders.events`: сервис уведомлений, витрина аналитики,
интеграция с 1С.
