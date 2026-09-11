# Orbita: Проведи анализ НТ по NT-123. test_id=nt-run-2291 previous_test_id=nt-run-2187 [01a08f1e-44eb-7141-a49f-0051d38e73bd]

Страница собрана автоматически агентом анализа нагрузочного тестирования Orbita (модель deepseek-v4-flash). Обновлено: 2026-09-11T10:19:13. Правки руками затрёт следующий прогон треда.

## Задача

Проведи анализ НТ по NT-123.
test_id=nt-run-2291
previous_test_id=nt-run-2187

## 03. NT Report

# NT Report

## Task

- jira_key: NT-123

- test_id: nt-run-2291

- test_status: completed

- environment: nt01

- namespace: nt01

- target_service: order-service

- started_at: 2026-09-10T15:45:00+00:00

- finished_at: 2026-09-10T16:06:00+00:00

## Test profile

```json
{
  "target_rps": 2000.0,
  "duration_seconds": 1260,
  "ramp_up_seconds": null,
  "virtual_users": null,
  "scenario": "checkout_peak_2000rps",
  "test_type": null
}
```

## Result

FAILED

Вердикт вычислен кодом по доступным SLA. Проверяются максимумы временных рядов за заданный период; p95/p99 ряда не являются перцентилями всех запросов теста.

## Precheck

```json
{
  "success": true,
  "checks": [
    {
      "name": "historical_window",
      "success": true
    },
    {
      "name": "required_parameters",
      "success": true,
      "missing": []
    }
  ]
}
```

## SLA

- p95: peak=2466.350097129284; limit=500.0; unit=ms

- p99: peak=4747.1012303043335; limit=1200.0; unit=ms

- error_rate: peak=0.03695006130634469; limit=0.01; unit=ratio

```json
[
  {
    "service": "order-service",
    "metric": "error_rate",
    "limit": 0.01,
    "peak": 0.03695006130634469,
    "first_at": 1789056120.0,
    "last_at": 1789056330.0,
    "count": 8,
    "unit": "ratio",
    "source": "prometheus"
  },
  {
    "service": "order-service",
    "metric": "p95",
    "limit": 500.0,
    "peak": 2466.350097129284,
    "first_at": 1789056120.0,
    "last_at": 1789056330.0,
    "count": 8,
    "unit": "ms",
    "source": "prometheus"
  },
  {
    "service": "order-service",
    "metric": "p99",
    "limit": 1200.0,
    "peak": 4747.1012303043335,
    "first_at": 1789056150.0,
    "last_at": 1789056330.0,
    "count": 7,
    "unit": "ms",
    "source": "prometheus"
  }
]
```

## Maximum stable load

1829.8444444444442 RPS

Наблюдаемая устойчивая нагрузка: минимум RPS в непрерывном окне соблюдения всех заданных SLA. Это нижняя оценка по наблюдениям, а не доказанный предел мощности.

## Main anomalies

Сервисов с данными: 80.

```json
[
  {
    "service": "order-service",
    "score": 0.999726,
    "severity": "critical",
    "metrics": [
      "cpu",
      "cpu_throttling",
      "error_rate",
      "http_4xx",
      "http_5xx",
      "memory",
      "network",
      "p95",
      "p99",
      "pod_restarts",
      "rps"
    ]
  },
  {
    "service": "checkout-api",
    "score": 0.99332,
    "severity": "critical",
    "metrics": [
      "cpu",
      "error_rate",
      "http_4xx",
      "http_5xx",
      "memory",
      "network",
      "p95",
      "p99",
      "rps"
    ]
  },
  {
    "service": "gateway",
    "score": 0.99332,
    "severity": "critical",
    "metrics": [
      "cpu",
      "error_rate",
      "http_4xx",
      "http_5xx",
      "memory",
      "network",
      "p95",
      "p99",
      "rps"
    ]
  },
  {
    "service": "payment-service",
    "score": 0.983693,
    "severity": "critical",
    "metrics": [
      "cpu",
      "error_rate",
      "http_4xx",
      "http_5xx",
      "memory",
      "network",
      "p95",
      "p99",
      "rps"
    ]
  },
  {
    "service": "inventory-service",
    "score": 0.97452,
    "severity": "critical",
    "metrics": [
      "cpu",
      "error_rate",
      "http_4xx",
      "http_5xx",
      "memory",
      "network",
      "p95",
      "p99",
      "rps"
    ]
  },
  {
    "service": "legacy-sync-job",
    "score": 0.899337,
    "severity": "critical",
    "metrics": [
      "error_rate",
      "http_4xx",
      "http_5xx",
      "memory",
      "network",
      "p95",
      "p99",
      "pod_restarts",
      "rps"
    ]
  },
  {
    "service": "image-resizer",
    "score": 0.892626,
    "severity": "critical",
    "metrics": [
      "cpu",
      "cpu_throttling",
      "error_rate",
      "http_5xx",
      "memory",
      "network",
      "p95",
      "p99",
      "rps"
    ]
  },
  {
    "service": "media-api",
    "score": 0.874171,
    "severity": "critical",
    "metrics": [
      "error_rate",
      "http_4xx",
      "http_5xx",
      "memory",
      "network",
      "p95",
      "p99",
      "rps"
    ]
  },
  {
    "service": "catalog-api",
    "score": 0.865782,
    "severity": "critical",
    "metrics": [
      "cpu",
      "error_rate",
      "http_4xx",
      "http_5xx",
      "memory",
      "network",
      "p95",
      "p99",
      "rps"
    ]
  },
  {
    "service": "clickstream-collector",
    "score": 0.865782,
    "severity": "critical",
    "metrics": [
      "cpu",
      "error_rate",
      "http_4xx",
      "http_5xx",
      "memory",
      "network",
      "p95",
      "p99",
      "rps"
    ]
  },
  {
    "service": "review-service",
    "score": 0.865782,
    "severity": "critical",
    "metrics": [
      "cpu",
      "error_rate",
      "http_4xx",
      "http_5xx",
      "memory",
      "network",
      "p95",
      "p99",
      "rps"
    ]
  },
  {
    "service": "subscription-service",
    "score": 0.865782,
    "severity": "critical",
    "metrics": [
      "cpu",
      "error_rate",
      "http_4xx",
      "http_5xx",
      "memory",
      "network",
      "p95",
      "p99",
      "rps"
    ]
  },
  {
    "service": "user-profile",
    "score": 0.865782,
    "severity": "critical",
    "metrics": [
      "cpu",
      "error_rate",
      "http_4xx",
      "http_5xx",
      "memory",
      "network",
      "p95",
      "p99",
      "rps"
    ]
  },
  {
    "service": "audit-log",
    "score": 0.832228,
    "severity": "warning",
    "metrics": [
      "error_rate",
      "http_4xx",
      "http_5xx",
      "memory",
      "network",
      "p95",
      "p99",
      "rps"
    ]
  },
  {
    "service": "billing-adapter",
    "score": 0.832228,
    "severity": "warning",
    "metrics": [
      "error_rate",
      "http_4xx",
      "http_5xx",
      "memory",
      "network",
      "p95",
      "p99",
      "rps"
    ]
  },
  {
    "service": "cashback-service",
    "score": 0.832228,
    "severity": "warning",
    "metrics": [
      "error_rate",
      "http_4xx",
      "http_5xx",
      "memory",
      "network",
      "p95",
      "p99",
      "rps"
    ]
  },
  {
    "service": "config-service",
    "score": 0.832228,
    "severity": "warning",
    "metrics": [
      "error_rate",
      "http_4xx",
      "http_5xx",
      "memory",
      "network",
      "p95",
      "p99",
      "rps"
    ]
  },
  {
    "service": "device-registry",
    "score": 0.832228,
    "severity": "warning",
    "metrics": [
      "error_rate",
      "http_4xx",
      "http_5xx",
      "memory",
      "network",
      "p95",
      "p99",
      "rps"
    ]
  },
  {
    "service": "email-sender",
    "score": 0.832228,
    "severity": "warning",
    "metrics": [
      "error_rate",
      "http_4xx",
      "http_5xx",
      "memory",
      "network",
      "p95",
      "p99",
      "rps"
    ]
  },
  {
    "service": "etl-runner",
    "score": 0.832228,
    "severity": "warning",
    "metrics": [
      "error_rate",
      "http_4xx",
      "http_5xx",
      "memory",
      "network",
      "p95",
      "p99",
      "rps"
    ]
  }
]
```

Показаны 20 сервисов с наибольшим score из 80 со срабатываниями.

## Timeline

```json
[
  {
    "at": 1789055100.0,
    "event": "baseline",
    "service": "checkout-api",
    "metric": "cpu"
  },
  {
    "at": 1789055100.0,
    "event": "baseline",
    "service": "gateway",
    "metric": "cpu"
  },
  {
    "at": 1789055100.0,
    "event": "baseline",
    "service": "inventory-service",
    "metric": "cpu"
  },
  {
    "at": 1789055100.0,
    "event": "baseline",
    "service": "order-service",
    "metric": "cpu"
  },
  {
    "at": 1789055100.0,
    "event": "baseline",
    "service": "payment-service",
    "metric": "cpu"
  },
  {
    "at": 1789055100.0,
    "event": "baseline",
    "service": "checkout-api",
    "metric": "http_4xx"
  },
  {
    "at": 1789055100.0,
    "event": "baseline",
    "service": "gateway",
    "metric": "http_4xx"
  },
  {
    "at": 1789055100.0,
    "event": "baseline",
    "service": "inventory-service",
    "metric": "http_4xx"
  },
  {
    "at": 1789055100.0,
    "event": "baseline",
    "service": "order-service",
    "metric": "http_4xx"
  },
  {
    "at": 1789055100.0,
    "event": "baseline",
    "service": "payment-service",
    "metric": "http_4xx"
  },
  {
    "at": 1789055100.0,
    "event": "baseline",
    "service": "checkout-api",
    "metric": "http_5xx"
  },
  {
    "at": 1789055100.0,
    "event": "baseline",
    "service": "gateway",
    "metric": "http_5xx"
  },
  {
    "at": 1789055100.0,
    "event": "baseline",
    "service": "inventory-service",
    "metric": "http_5xx"
  },
  {
    "at": 1789055100.0,
    "event": "baseline",
    "service": "order-service",
    "metric": "http_5xx"
  },
  {
    "at": 1789055100.0,
    "event": "baseline",
    "service": "payment-service",
    "metric": "http_5xx"
  },
  {
    "at": 1789055100.0,
    "event": "baseline",
    "service": "checkout-api",
    "metric": "network"
  },
  {
    "at": 1789055100.0,
    "event": "baseline",
    "service": "gateway",
    "metric": "network"
  },
  {
    "at": 1789055100.0,
    "event": "baseline",
    "service": "inventory-service",
    "metric": "network"
  },
  {
    "at": 1789055100.0,
    "event": "baseline",
    "service": "order-service",
    "metric": "network"
  },
  {
    "at": 1789055100.0,
    "event": "baseline",
    "service": "payment-service",
    "metric": "network"
  },
  {
    "at": 1789055100.0,
    "event": "baseline",
    "service": "checkout-api",
    "metric": "rps"
  },
  {
    "at": 1789055100.0,
    "event": "baseline",
    "service": "gateway",
    "metric": "rps"
  },
  {
    "at": 1789055100.0,
    "event": "baseline",
    "service": "inventory-service",
    "metric": "rps"
  },
  {
    "at": 1789055100.0,
    "event": "baseline",
    "service": "order-service",
    "metric": "rps"
  },
  {
    "at": 1789055100.0,
    "event": "baseline",
    "service": "payment-service",
    "metric": "rps"
  },
  {
    "at": 1789055100.0,
    "event": "test period started"
  },
  {
    "at": 1789055100.0,
    "event": "trend",
    "service": "gateway",
    "metric": "p95"
  },
  {
    "at": 1789055100.0,
    "event": "trend",
    "service": "order-service",
    "metric": "p99"
  },
  {
    "at": 1789055130.0,
    "event": "trend",
    "service": "checkout-api",
    "metric": "cpu"
  },
  {
    "at": 1789055130.0,
    "event": "trend",
    "service": "payment-service",
    "metric": "cpu"
  },
  {
    "at": 1789055130.0,
    "event": "trend",
    "service": "checkout-api",
    "metric": "http_4xx"
  },
  {
    "at": 1789055130.0,
    "event": "trend",
    "service": "inventory-service",
    "metric": "http_4xx"
  },
  {
    "at": 1789055130.0,
    "event": "trend",
    "service": "payment-service",
    "metric": "http_4xx"
  },
  {
    "at": 1789055130.0,
    "event": "trend",
    "service": "checkout-api",
    "metric": "http_5xx"
  },
  {
    "at": 1789055130.0,
    "event": "trend",
    "service": "gateway",
    "metric": "http_5xx"
  },
  {
    "at": 1789055130.0,
    "event": "trend",
    "service": "order-service",
    "metric": "http_5xx"
  },
  {
    "at": 1789055130.0,
    "event": "trend",
    "service": "checkout-api",
    "metric": "network"
  },
  {
    "at": 1789055130.0,
    "event": "trend",
    "service": "gateway",
    "metric": "network"
  },
  {
    "at": 1789055130.0,
    "event": "trend",
    "service": "inventory-service",
    "metric": "network"
  },
  {
    "at": 1789055130.0,
    "event": "trend",
    "service": "payment-service",
    "metric": "network"
  },
  {
    "at": 1789055130.0,
    "event": "trend",
    "service": "checkout-api",
    "metric": "rps"
  },
  {
    "at": 1789055130.0,
    "event": "trend",
    "service": "inventory-service",
    "metric": "rps"
  },
  {
    "at": 1789055130.0,
    "event": "trend",
    "service": "payment-service",
    "metric": "rps"
  },
  {
    "at": 1789055250.0,
    "event": "spike",
    "service": "gateway",
    "metric": "p95"
  },
  {
    "at": 1789055250.0,
    "event": "spike",
    "service": "order-service",
    "metric": "p99"
  },
  {
    "at": 1789055280.0,
    "event": "spike",
    "service": "order-service",
    "metric": "error_rate"
  },
  {
    "at": 1789055280.0,
    "event": "spike",
    "service": "checkout-api",
    "metric": "p95"
  },
  {
    "at": 1789055310.0,
    "event": "spike",
    "service": "payment-service",
    "metric": "p99"
  },
  {
    "at": 1789055370.0,
    "event": "spike",
    "service": "payment-service",
    "metric": "network"
  },
  {
    "at": 1789055400.0,
    "event": "spike",
    "service": "gateway",
    "metric": "network"
  },
  {
    "at": 1789055400.0,
    "event": "spike",
    "service": "order-service",
    "metric": "network"
  },
  {
    "at": 1789055400.0,
    "event": "spike",
    "service": "gateway",
    "metric": "rps"
  },
  {
    "at": 1789055430.0,
    "event": "spike",
    "service": "inventory-service",
    "metric": "rps"
  },
  {
    "at": 1789055460.0,
    "event": "spike",
    "service": "checkout-api",
    "metric": "http_5xx"
  },
  {
    "at": 1789055490.0,
    "event": "spike",
    "service": "order-service",
    "metric": "http_4xx"
  },
  {
    "at": 1789055490.0,
    "event": "spike",
    "service": "inventory-service",
    "metric": "memory"
  },
  {
    "at": 1789055490.0,
    "event": "spike",
    "service": "inventory-service",
    "metric": "network"
  },
  {
    "at": 1789055520.0,
    "event": "spike",
    "service": "inventory-service",
    "metric": "p95"
  },
  {
    "at": 1789055520.0,
    "event": "spike",
    "service": "inventory-service",
    "metric": "p99"
  },
  {
    "at": 1789055520.0,
    "event": "trend",
    "service": "gateway",
    "metric": "http_4xx"
  },
  {
    "at": 1789055520.0,
    "event": "trend",
    "service": "order-service",
    "metric": "http_4xx"
  },
  {
    "at": 1789055520.0,
    "event": "trend",
    "service": "order-service",
    "metric": "network"
  },
  {
    "at": 1789055520.0,
    "event": "trend",
    "service": "gateway",
    "metric": "rps"
  },
  {
    "at": 1789055520.0,
    "event": "trend",
    "service": "order-service",
    "metric": "rps"
  },
  {
    "at": 1789055550.0,
    "event": "spike",
    "service": "payment-service",
    "metric": "rps"
  },
  {
    "at": 1789055550.0,
    "event": "trend",
    "service": "order-service",
    "metric": "p95"
  },
  {
    "at": 1789055580.0,
    "event": "spike",
    "service": "checkout-api",
    "metric": "memory"
  },
  {
    "at": 1789055580.0,
    "event": "trend",
    "service": "gateway",
    "metric": "cpu"
  },
  {
    "at": 1789055580.0,
    "event": "trend",
    "service": "checkout-api",
    "metric": "p95"
  },
  {
    "at": 1789055580.0,
    "event": "trend",
    "service": "gateway",
    "metric": "p99"
  },
  {
    "at": 1789055610.0,
    "event": "spike",
    "service": "checkout-api",
    "metric": "http_4xx"
  },
  {
    "at": 1789055610.0,
    "event": "spike",
    "service": "gateway",
    "metric": "http_4xx"
  },
  {
    "at": 1789055610.0,
    "event": "spike",
    "service": "inventory-service",
    "metric": "http_4xx"
  },
  {
    "at": 1789055610.0,
    "event": "spike",
    "service": "payment-service",
    "metric": "http_4xx"
  },
  {
    "at": 1789055610.0,
    "event": "spike",
    "service": "order-service",
    "metric": "p95"
  },
  {
    "at": 1789055610.0,
    "event": "spike",
    "service": "checkout-api",
    "metric": "p99"
  },
  {
    "at": 1789055610.0,
    "event": "spike",
    "service": "gateway",
    "metric": "p99"
  },
  {
    "at": 1789055610.0,
    "event": "spike",
    "service": "checkout-api",
    "metric": "rps"
  },
  {
    "at": 1789055610.0,
    "event": "spike",
    "service": "order-service",
    "metric": "rps"
  },
  {
    "at": 1789055640.0,
    "event": "spike",
    "service": "gateway",
    "metric": "http_5xx"
  },
  {
    "at": 1789055640.0,
    "event": "spike",
    "service": "order-service",
    "metric": "memory"
  },
  {
    "at": 1789055640.0,
    "event": "spike",
    "service": "checkout-api",
    "metric": "network"
  },
  {
    "at": 1789055670.0,
    "event": "spike",
    "service": "order-service",
    "metric": "http_5xx"
  },
  {
    "at": 1789055700.0,
    "event": "spike",
    "service": "order-service",
    "metric": "cpu"
  },
  {
    "at": 1789055730.0,
    "event": "spike",
    "service": "payment-service",
    "metric": "http_5xx"
  },
  {
    "at": 1789055790.0,
    "event": "spike",
    "service": "gateway",
    "metric": "memory"
  },
  {
    "at": 1789055850.0,
    "event": "trend",
    "service": "checkout-api",
    "metric": "error_rate"
  },
  {
    "at": 1789055850.0,
    "event": "trend",
    "service": "order-service",
    "metric": "error_rate"
  },
  {
    "at": 1789055850.0,
    "event": "trend",
    "service": "checkout-api",
    "metric": "p99"
  },
  {
    "at": 1789055880.0,
    "event": "spike",
    "service": "payment-service",
    "metric": "error_rate"
  },
  {
    "at": 1789055880.0,
    "event": "trend",
    "service": "gateway",
    "metric": "error_rate"
  },
  {
    "at": 1789055910.0,
    "event": "spike",
    "service": "checkout-api",
    "metric": "error_rate"
  },
  {
    "at": 1789055910.0,
    "event": "spike",
    "service": "payment-service",
    "metric": "memory"
  },
  {
    "at": 1789055940.0,
    "event": "spike",
    "service": "gateway",
    "metric": "error_rate"
  },
  {
    "at": 1789056030.0,
    "event": "spike",
    "service": "order-service",
    "metric": "cpu_throttling"
  },
  {
    "at": 1789056120.0,
    "event": "sla_violation",
    "service": "order-service",
    "metric": "error_rate"
  },
  {
    "at": 1789056120.0,
    "event": "sla_violation",
    "service": "order-service",
    "metric": "p95"
  },
  {
    "at": 1789056150.0,
    "event": "sla_violation",
    "service": "order-service",
    "metric": "p99"
  },
  {
    "at": 1789056180.0,
    "event": "spike",
    "service": "order-service",
    "metric": "pod_restarts"
  },
  {
    "at": 1789056210.0,
    "event": "spike",
    "service": "payment-service",
    "metric": "p95"
  },
  {
    "at": 1789056270.0,
    "event": "spike",
    "service": "inventory-service",
    "metric": "error_rate"
  },
  {
    "at": 1789056360.0,
    "event": "test period ended"
  }
]
```

Показаны 102 из 639 событий: остальные относятся к сервисам вне фокуса анализа.

## Root cause analysis

- Гипотеза (likely), order-service: order-service исчерпал CPU-лимит: утилизация упиралась в потолок, появилось CPU-throttling, число реплик просело, был рестарт пода. На фоне этого p95/p99 выросли на порядок выше значений остальных участников потока и выше SLA, а error_rate превысил лимит — то есть деградация совпадает по времени с насыщением CPU самого тестируемого сервиса (наиболее вероятная точка насыщения в цепочке).
  Evidence: metric:order-service:cpu, metric:order-service:cpu_throttling, metric:order-service:p95, metric:order-service:p99, metric:order-service:error_rate, metric:order-service:replicas, metric:order-service:pod_restarts, finding:1, finding:2, finding:0

- Гипотеза (possible), order-service: Причиной возросшей стоимости обработки заказа на единицу нагрузки может быть переход на новую схему сериализации заказов в релизе 1.42: по контексту задачи этот профиль ещё не измерялся, а компоненты задачи включают order-service. Это объясняло бы именно CPU-bound насыщение, а не утечку памяти или сеть. Доказательство пока контекстное, а не метрическое.
  Evidence: tool:call_00_Wc4FFGV5ULrugC0edzwh7114, metric:order-service:cpu, metric:order-service:cpu_throttling

- Гипотеза (likely), order-service: Ёмкости конфигурации не хватает для целевых 2000 RPS: максимальная стабильная нагрузка прогона оказалась ниже целевого RPS, а реплики не масштабировались под нагрузку. Это согласуется с насыщением самого order-service при нормально работающих нижестоящих сервисах (payment-service и inventory-service без throttling, с низкими временами ответа и ошибками).
  Evidence: tool:call_01_dLk4M3z0oDlEewO1cTkA3204, metric:order-service:replicas, metric:payment-service:cpu_throttling, metric:payment-service:p99, metric:inventory-service:p99, finding:15

- Гипотеза (possible), checkout-api: Рост времен ответа и ошибок у checkout-api (а также gateway) синхронен по времени со всплесками у order-service и не сопровождается ростом CPU/throttling у самих этих сервисов, что согласуется с распространением деградации вверх по потоку от насыщенного order-service. Направление зависимостей в предоставленных данных не раскрыто, поэтому это лишь вероятная версия, а не подтверждённая причина.
  Evidence: metric:checkout-api:p95, metric:checkout-api:p99, metric:checkout-api:error_rate, metric:checkout-api:cpu_throttling, finding:44, finding:408, finding:485, metric:gateway:p99, finding:59, finding:500

## Evidence

```json
{
  "metric:checkout-api:cpu": {
    "service": "checkout-api",
    "metric": "cpu",
    "count": 42,
    "median": 0.6555555555555554,
    "p95": 0.7999999999999999,
    "p99": 0.7999999999999999,
    "min": 0.16666666666666666,
    "max": 0.7999999999999999,
    "mad": 0.1333333333333333,
    "unit": "ratio",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:checkout-api:cpu_throttling": {
    "service": "checkout-api",
    "metric": "cpu_throttling",
    "count": 42,
    "median": 0.0,
    "p95": 0.0,
    "p99": 0.0,
    "min": 0.0,
    "max": 0.0,
    "mad": 0.0,
    "unit": "ratio",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:checkout-api:error_rate": {
    "service": "checkout-api",
    "metric": "error_rate",
    "count": 42,
    "median": 0.002344537493477698,
    "p95": 0.02173697532062821,
    "p99": 0.02540292179595338,
    "min": 0.0021287379624936645,
    "max": 0.027805040564024528,
    "mad": 6.322832567945739e-05,
    "unit": "ratio",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:checkout-api:http_4xx": {
    "service": "checkout-api",
    "metric": "http_4xx",
    "count": 42,
    "median": 16.288888888888888,
    "p95": 20.12555555555555,
    "p99": 20.19088888888889,
    "min": 3.333333333333333,
    "max": 20.2,
    "mad": 3.766666666666664,
    "unit": "requests/s",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:checkout-api:http_5xx": {
    "service": "checkout-api",
    "metric": "http_5xx",
    "count": 42,
    "median": 4.755555555555555,
    "p95": 54.23111111111109,
    "p99": 63.16466666666661,
    "min": 0.875,
    "max": 68.62222222222222,
    "mad": 1.355555555555555,
    "unit": "requests/s",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:checkout-api:memory": {
    "service": "checkout-api",
    "metric": "memory",
    "count": 42,
    "median": 0.31637179323782527,
    "p95": 0.33064609387268623,
    "p99": 0.3317243020236492,
    "min": 0.28981207062800723,
    "max": 0.3319988089303176,
    "mad": 0.00732036276410028,
    "unit": "ratio",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:checkout-api:network": {
    "service": "checkout-api",
    "metric": "network",
    "count": 42,
    "median": 4879221.477777777,
    "p95": 6061697.646666666,
    "p99": 6075299.250888889,
    "min": 988233.4375,
    "max": 6078246.977777777,
    "mad": 1130504.9777777777,
    "unit": "bytes/s",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:checkout-api:p95": {
    "service": "checkout-api",
    "metric": "p95",
    "count": 42,
    "median": 222.83354821839123,
    "p95": 778.6731984337163,
    "p99": 948.6432160478162,
    "min": 142.02902963865202,
    "max": 959.8604415123067,
    "mad": 63.01089410557552,
    "unit": "ms",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:checkout-api:p99": {
    "service": "checkout-api",
    "metric": "p99",
    "count": 42,
    "median": 258.7089373326271,
    "p95": 1129.8241537818546,
    "p99": 1183.0476451549243,
    "min": 209.6852367688024,
    "max": 1185.303730017762,
    "mad": 26.516062742673753,
    "unit": "ms",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:checkout-api:pod_restarts": {
    "service": "checkout-api",
    "metric": "pod_restarts",
    "count": 42,
    "median": 0.0,
    "p95": 0.0,
    "p99": 0.0,
    "min": 0.0,
    "max": 0.0,
    "mad": 0.0,
    "unit": "count",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:checkout-api:replicas": {
    "service": "checkout-api",
    "metric": "replicas",
    "count": 42,
    "median": 6.0,
    "p95": 6.0,
    "p99": 6.0,
    "min": 6.0,
    "max": 6.0,
    "mad": 0.0,
    "unit": "count",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:checkout-api:rps": {
    "service": "checkout-api",
    "metric": "rps",
    "count": 42,
    "median": 2034.8444444444442,
    "p95": 2512.042222222222,
    "p99": 2528.902222222222,
    "min": 411.04166666666663,
    "max": 2534.733333333333,
    "mad": 467.12222222222215,
    "unit": "requests/s",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:gateway:cpu": {
    "service": "gateway",
    "metric": "cpu",
    "count": 42,
    "median": 0.5444444444444443,
    "p95": 0.6444444444444444,
    "p99": 0.6666666666666666,
    "min": 0.14583333333333331,
    "max": 0.6666666666666666,
    "mad": 0.10000000000000009,
    "unit": "ratio",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:gateway:cpu_throttling": {
    "service": "gateway",
    "metric": "cpu_throttling",
    "count": 42,
    "median": 0.0,
    "p95": 0.0,
    "p99": 0.0,
    "min": 0.0,
    "max": 0.0,
    "mad": 0.0,
    "unit": "ratio",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:gateway:error_rate": {
    "service": "gateway",
    "metric": "error_rate",
    "count": 42,
    "median": 0.0022056379828997294,
    "p95": 0.018737604183240428,
    "p99": 0.021862046034401478,
    "min": 0.002044720005276697,
    "max": 0.023866938725035553,
    "mad": 5.2173366882686534e-05,
    "unit": "ratio",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:gateway:http_4xx": {
    "service": "gateway",
    "metric": "http_4xx",
    "count": 42,
    "median": 16.42222222222222,
    "p95": 19.97444444444444,
    "p99": 20.133333333333333,
    "min": 3.375,
    "max": 20.133333333333333,
    "mad": 3.5222222222222204,
    "unit": "requests/s",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:gateway:http_5xx": {
    "service": "gateway",
    "metric": "http_5xx",
    "count": 42,
    "median": 4.422222222222222,
    "p95": 47.07555555555553,
    "p99": 54.86022222222217,
    "min": 0.9375,
    "max": 60.04444444444444,
    "mad": 1.1888888888888887,
    "unit": "requests/s",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:gateway:memory": {
    "service": "gateway",
    "metric": "memory",
    "count": 42,
    "median": 0.3588256947696209,
    "p95": 0.3755408922675997,
    "p99": 0.3767478817421943,
    "min": 0.32330726366490126,
    "max": 0.37719045486301184,
    "mad": 0.01005588797852397,
    "unit": "ratio",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:gateway:network": {
    "service": "gateway",
    "metric": "network",
    "count": 42,
    "median": 4917891.444444444,
    "p95": 6011706.279999999,
    "p99": 6028220.124222221,
    "min": 1041562.8541666666,
    "max": 6035795.11111111,
    "mad": 1084064.9555555554,
    "unit": "bytes/s",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:gateway:p95": {
    "service": "gateway",
    "metric": "p95",
    "count": 42,
    "median": 231.25188884960906,
    "p95": 788.1919486649069,
    "p99": 971.1834664987531,
    "min": 147.38500315059858,
    "max": 980.2045322486925,
    "mad": 42.16751952852211,
    "unit": "ms",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:gateway:p99": {
    "service": "gateway",
    "metric": "p99",
    "count": 42,
    "median": 296.05907568744476,
    "p95": 1140.843992673149,
    "p99": 1188.916584843561,
    "min": 226.3258232235701,
    "max": 1190.6998256827426,
    "mad": 55.428172319534866,
    "unit": "ms",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:gateway:pod_restarts": {
    "service": "gateway",
    "metric": "pod_restarts",
    "count": 42,
    "median": 0.0,
    "p95": 0.0,
    "p99": 0.0,
    "min": 0.0,
    "max": 0.0,
    "mad": 0.0,
    "unit": "count",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:gateway:replicas": {
    "service": "gateway",
    "metric": "replicas",
    "count": 42,
    "median": 6.0,
    "p95": 6.0,
    "p99": 6.0,
    "min": 6.0,
    "max": 6.0,
    "mad": 0.0,
    "unit": "count",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:gateway:rps": {
    "service": "gateway",
    "metric": "rps",
    "count": 42,
    "median": 2050.7999999999997,
    "p95": 2497.9488888888886,
    "p99": 2518.1862222222217,
    "min": 429.0,
    "max": 2519.844444444444,
    "mad": 444.288888888889,
    "unit": "requests/s",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:inventory-service:cpu": {
    "service": "inventory-service",
    "metric": "cpu",
    "count": 42,
    "median": 0.5222222222222221,
    "p95": 0.6,
    "p99": 0.6131111111111109,
    "min": 0.16666666666666666,
    "max": 0.6222222222222221,
    "mad": 0.07777777777777783,
    "unit": "ratio",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:inventory-service:cpu_throttling": {
    "service": "inventory-service",
    "metric": "cpu_throttling",
    "count": 42,
    "median": 0.0,
    "p95": 0.0,
    "p99": 0.0,
    "min": 0.0,
    "max": 0.0,
    "mad": 0.0,
    "unit": "ratio",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:inventory-service:error_rate": {
    "service": "inventory-service",
    "metric": "error_rate",
    "count": 42,
    "median": 0.0003916949718057647,
    "p95": 0.00044187112624045265,
    "p99": 0.00044510695426908835,
    "min": 0.00017286084701815038,
    "max": 0.0004469641722929262,
    "mad": 3.0110584186092182e-05,
    "unit": "ratio",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:inventory-service:http_4xx": {
    "service": "inventory-service",
    "metric": "http_4xx",
    "count": 42,
    "median": 9.73333333333333,
    "p95": 11.997777777777776,
    "p99": 12.12311111111111,
    "min": 1.9583333333333333,
    "max": 12.177777777777777,
    "mad": 2.211111111111112,
    "unit": "requests/s",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:inventory-service:http_5xx": {
    "service": "inventory-service",
    "metric": "http_5xx",
    "count": 42,
    "median": 0.4111111111111111,
    "p95": 0.6,
    "p99": 0.6444444444444444,
    "min": 0.041666666666666664,
    "max": 0.6444444444444444,
    "mad": 0.09999999999999998,
    "unit": "requests/s",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:inventory-service:memory": {
    "service": "inventory-service",
    "metric": "memory",
    "count": 42,
    "median": 0.32459288090467453,
    "p95": 0.3335797680076212,
    "p99": 0.33440371012315157,
    "min": 0.3101634867489338,
    "max": 0.3345224913209677,
    "mad": 0.005140502471476793,
    "unit": "ratio",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:inventory-service:network": {
    "service": "inventory-service",
    "metric": "network",
    "count": 42,
    "median": 2947591.188888889,
    "p95": 3610011.556666666,
    "p99": 3622129.954666666,
    "min": 586321.2291666666,
    "max": 3628747.755555555,
    "mad": 657342.5777777773,
    "unit": "bytes/s",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:inventory-service:p95": {
    "service": "inventory-service",
    "metric": "p95",
    "count": 42,
    "median": 24.5204979677152,
    "p95": 24.728404540923666,
    "p99": 24.767584686953406,
    "min": 24.225514475061043,
    "max": 24.77406072815635,
    "mad": 0.1301494476907834,
    "unit": "ms",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:inventory-service:p99": {
    "service": "inventory-service",
    "metric": "p99",
    "count": 42,
    "median": 41.23374113201727,
    "p95": 43.342772693481,
    "p99": 43.650227247790454,
    "min": 36.15760869565226,
    "max": 43.698727687048965,
    "mad": 1.7505555220070015,
    "unit": "ms",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:inventory-service:pod_restarts": {
    "service": "inventory-service",
    "metric": "pod_restarts",
    "count": 42,
    "median": 0.0,
    "p95": 0.0,
    "p99": 0.0,
    "min": 0.0,
    "max": 0.0,
    "mad": 0.0,
    "unit": "count",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:inventory-service:replicas": {
    "service": "inventory-service",
    "metric": "replicas",
    "count": 42,
    "median": 3.0,
    "p95": 3.0,
    "p99": 3.0,
    "min": 3.0,
    "max": 3.0,
    "mad": 0.0,
    "unit": "count",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:inventory-service:rps": {
    "service": "inventory-service",
    "metric": "rps",
    "count": 42,
    "median": 1220.2888888888888,
    "p95": 1499.51,
    "p99": 1519.6257777777776,
    "min": 241.04166666666666,
    "max": 1523.4888888888886,
    "mad": 276.15555555555545,
    "unit": "requests/s",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:order-service:cpu": {
    "service": "order-service",
    "metric": "cpu",
    "count": 41,
    "median": 0.8222222222222221,
    "p95": 0.9999999999999999,
    "p99": 0.9999999999999999,
    "min": 0.22916666666666666,
    "max": 0.9999999999999999,
    "mad": 0.15555555555555545,
    "unit": "ratio",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 1,
    "partial": false,
    "max_gap": 60.0
  },
  "metric:order-service:cpu_throttling": {
    "service": "order-service",
    "metric": "cpu_throttling",
    "count": 42,
    "median": 0.0,
    "p95": 0.04963888888888886,
    "p99": 0.18007222222222222,
    "min": 0.0,
    "max": 0.18166666666666667,
    "mad": 0.0,
    "unit": "ratio",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:order-service:error_rate": {
    "service": "order-service",
    "metric": "error_rate",
    "count": 42,
    "median": 0.0018144205125141268,
    "p95": 0.029035433231190108,
    "p99": 0.03467502228153899,
    "min": 0.0017564870259481038,
    "max": 0.03695006130634469,
    "mad": 3.860805818164893e-05,
    "unit": "ratio",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:order-service:http_4xx": {
    "service": "order-service",
    "metric": "http_4xx",
    "count": 42,
    "median": 13.866666666666664,
    "p95": 16.93333333333333,
    "p99": 17.150444444444442,
    "min": 2.875,
    "max": 17.177777777777777,
    "mad": 3.0666666666666664,
    "unit": "requests/s",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:order-service:http_5xx": {
    "service": "order-service",
    "metric": "http_5xx",
    "count": 42,
    "median": 3.133333333333333,
    "p95": 61.199999999999974,
    "p99": 72.26177777777771,
    "min": 0.625,
    "max": 79.02222222222221,
    "mad": 0.8666666666666663,
    "unit": "requests/s",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:order-service:memory": {
    "service": "order-service",
    "metric": "memory",
    "count": 42,
    "median": 0.47421257570385933,
    "p95": 0.5133462545927614,
    "p99": 0.5152831699186936,
    "min": 0.3997862827964127,
    "max": 0.5155331832356751,
    "mad": 0.02488809823989868,
    "unit": "ratio",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:order-service:network": {
    "service": "order-service",
    "metric": "network",
    "count": 42,
    "median": 4151243.388888888,
    "p95": 5078065.35,
    "p99": 5174390.266222222,
    "min": 858813.6666666666,
    "max": 5181127.222222222,
    "mad": 917441.7111111116,
    "unit": "bytes/s",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:order-service:p95": {
    "service": "order-service",
    "metric": "p95",
    "count": 42,
    "median": 219.03530403438486,
    "p95": 1177.797499555815,
    "p99": 2308.8088516593184,
    "min": 115.1022304832714,
    "max": 2466.350097129284,
    "mad": 80.74258887646441,
    "unit": "ms",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:order-service:p99": {
    "service": "order-service",
    "metric": "p99",
    "count": 42,
    "median": 284.21990421694284,
    "p95": 1940.4197676577307,
    "p99": 4707.122006893339,
    "min": 146.14312267657994,
    "max": 4747.1012303043335,
    "mad": 89.38700551029899,
    "unit": "ms",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:order-service:pod_restarts": {
    "service": "order-service",
    "metric": "pod_restarts",
    "count": 42,
    "median": 0.0,
    "p95": 1.0,
    "p99": 1.0,
    "min": 0.0,
    "max": 1.0,
    "mad": 0.0,
    "unit": "count",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:order-service:replicas": {
    "service": "order-service",
    "metric": "replicas",
    "count": 42,
    "median": 4.0,
    "p95": 4.0,
    "p99": 4.0,
    "min": 3.0,
    "max": 4.0,
    "mad": 0.0,
    "unit": "count",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:order-service:rps": {
    "service": "order-service",
    "metric": "rps",
    "count": 42,
    "median": 1739.3777777777775,
    "p95": 2115.322222222222,
    "p99": 2142.293333333333,
    "min": 354.2291666666667,
    "max": 2144.8444444444444,
    "mad": 375.0444444444445,
    "unit": "requests/s",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:payment-service:cpu": {
    "service": "payment-service",
    "metric": "cpu",
    "count": 42,
    "median": 0.47777777777777775,
    "p95": 0.5777777777777777,
    "p99": 0.5777777777777777,
    "min": 0.14583333333333331,
    "max": 0.5777777777777777,
    "mad": 0.08888888888888888,
    "unit": "ratio",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:payment-service:cpu_throttling": {
    "service": "payment-service",
    "metric": "cpu_throttling",
    "count": 42,
    "median": 0.0,
    "p95": 0.0,
    "p99": 0.0,
    "min": 0.0,
    "max": 0.0,
    "mad": 0.0,
    "unit": "ratio",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:payment-service:error_rate": {
    "service": "payment-service",
    "metric": "error_rate",
    "count": 42,
    "median": 0.0005885227616640229,
    "p95": 0.0006903110419328295,
    "p99": 0.0009913546921415275,
    "min": 0.0,
    "max": 0.0010832559579077683,
    "mad": 3.6422576148335676e-05,
    "unit": "ratio",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:payment-service:http_4xx": {
    "service": "payment-service",
    "metric": "http_4xx",
    "count": 42,
    "median": 5.488888888888889,
    "p95": 6.905555555555554,
    "p99": 6.924222222222221,
    "min": 1.0833333333333333,
    "max": 6.933333333333333,
    "mad": 1.2777777777777768,
    "unit": "requests/s",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:payment-service:http_5xx": {
    "service": "payment-service",
    "metric": "http_5xx",
    "count": 42,
    "median": 0.38888888888888884,
    "p95": 0.5111111111111111,
    "p99": 0.5464444444444443,
    "min": 0.0,
    "max": 0.5555555555555555,
    "mad": 0.08888888888888888,
    "unit": "requests/s",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:payment-service:memory": {
    "service": "payment-service",
    "metric": "memory",
    "count": 42,
    "median": 0.3976594372652471,
    "p95": 0.4052828389685601,
    "p99": 0.40781445120461285,
    "min": 0.38292375952005386,
    "max": 0.40833684615790844,
    "mad": 0.0038863210938870907,
    "unit": "ratio",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:payment-service:network": {
    "service": "payment-service",
    "metric": "network",
    "count": 42,
    "median": 1663219.7333333332,
    "p95": 2047153.112222222,
    "p99": 2053245.3377777776,
    "min": 323022.8333333333,
    "max": 2053566.7777777775,
    "mad": 376656.1888888888,
    "unit": "bytes/s",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:payment-service:p95": {
    "service": "payment-service",
    "metric": "p95",
    "count": 42,
    "median": 57.04262866611933,
    "p95": 61.479178767781725,
    "p99": 61.69433925439159,
    "min": 49.62891379976808,
    "max": 61.73415777562236,
    "mad": 2.971951854596753,
    "unit": "ms",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:payment-service:p99": {
    "service": "payment-service",
    "metric": "p99",
    "count": 42,
    "median": 73.00491899122136,
    "p95": 74.12539775139834,
    "p99": 74.18795999986206,
    "min": 70.43781094527363,
    "max": 74.19616876818621,
    "mad": 0.7192590705209483,
    "unit": "ms",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:payment-service:pod_restarts": {
    "service": "payment-service",
    "metric": "pod_restarts",
    "count": 42,
    "median": 0.0,
    "p95": 0.0,
    "p99": 0.0,
    "min": 0.0,
    "max": 0.0,
    "mad": 0.0,
    "unit": "count",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:payment-service:replicas": {
    "service": "payment-service",
    "metric": "replicas",
    "count": 42,
    "median": 4.0,
    "p95": 4.0,
    "p99": 4.0,
    "min": 4.0,
    "max": 4.0,
    "mad": 0.0,
    "unit": "count",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "metric:payment-service:rps": {
    "service": "payment-service",
    "metric": "rps",
    "count": 42,
    "median": 692.2222222222222,
    "p95": 853.441111111111,
    "p99": 859.9313333333332,
    "min": 134.625,
    "max": 862.1999999999999,
    "mad": 158.38888888888886,
    "unit": "requests/s",
    "source": "prometheus",
    "start": 1789055100.0,
    "end": 1789056330.0,
    "invalid_points": 0,
    "partial": false,
    "max_gap": 30.0
  },
  "finding:0": {
    "service": "order-service",
    "metric": "error_rate",
    "limit": 0.01,
    "peak": 0.03695006130634469,
    "first_at": 1789056120.0,
    "last_at": 1789056330.0,
    "count": 8,
    "unit": "ratio",
    "source": "prometheus"
  },
  "finding:1": {
    "service": "order-service",
    "metric": "p95",
    "limit": 500.0,
    "peak": 2466.350097129284,
    "first_at": 1789056120.0,
    "last_at": 1789056330.0,
    "count": 8,
    "unit": "ms",
    "source": "prometheus"
  },
  "finding:2": {
    "service": "order-service",
    "metric": "p99",
    "limit": 1200.0,
    "peak": 4747.1012303043335,
    "first_at": 1789056150.0,
    "last_at": 1789056330.0,
    "count": 7,
    "unit": "ms",
    "source": "prometheus"
  },
  "finding:6": {
    "service": "checkout-api",
    "metric": "cpu",
    "unit": "ratio",
    "source": "prometheus",
    "kind": "baseline",
    "first_at": 1789055100.0,
    "baseline": 0.23333333333333334,
    "current": 0.6555555555555554,
    "absolute": 0.4222222222222221,
    "percent": 180.9523809523809
  },
  "finding:7": {
    "service": "checkout-api",
    "metric": "cpu",
    "unit": "ratio",
    "source": "prometheus",
    "kind": "trend",
    "first_at": 1789055130.0,
    "count": 1
  },
  "finding:9": {
    "service": "gateway",
    "metric": "cpu",
    "unit": "ratio",
    "source": "prometheus",
    "kind": "baseline",
    "first_at": 1789055100.0,
    "baseline": 0.2,
    "current": 0.5444444444444443,
    "absolute": 0.3444444444444443,
    "percent": 172.22222222222211
  },
  "finding:10": {
    "service": "gateway",
    "metric": "cpu",
    "unit": "ratio",
    "source": "prometheus",
    "kind": "trend",
    "first_at": 1789055580.0,
    "count": 1
  },
  "finding:12": {
    "service": "inventory-service",
    "metric": "cpu",
    "unit": "ratio",
    "source": "prometheus",
    "kind": "baseline",
    "first_at": 1789055100.0,
    "baseline": 0.23333333333333334,
    "current": 0.5222222222222221,
    "absolute": 0.2888888888888888,
    "percent": 123.80952380952377
  },
  "finding:15": {
    "service": "order-service",
    "metric": "cpu",
    "unit": "ratio",
    "source": "prometheus",
    "kind": "baseline",
    "first_at": 1789055100.0,
    "baseline": 0.3333333333333333,
    "current": 0.8222222222222221,
    "absolute": 0.48888888888888876,
    "percent": 146.66666666666663
  },
  "finding:16": {
    "service": "order-service",
    "metric": "cpu",
    "unit": "ratio",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055700.0,
    "count": 1,
    "examples": [
      {
        "at": 1789055700.0,
        "value": 0.8222222222222221,
        "robust_z": 4.72142824999999
      }
    ]
  },
  "finding:17": {
    "service": "payment-service",
    "metric": "cpu",
    "unit": "ratio",
    "source": "prometheus",
    "kind": "baseline",
    "first_at": 1789055100.0,
    "baseline": 0.2,
    "current": 0.47777777777777775,
    "absolute": 0.27777777777777773,
    "percent": 138.88888888888886
  },
  "finding:18": {
    "service": "payment-service",
    "metric": "cpu",
    "unit": "ratio",
    "source": "prometheus",
    "kind": "trend",
    "first_at": 1789055130.0,
    "count": 2
  },
  "finding:28": {
    "service": "order-service",
    "metric": "cpu_throttling",
    "unit": "ratio",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789056030.0,
    "count": 8,
    "examples": [
      {
        "at": 1789056030.0,
        "value": 0.003333333333333334,
        "robust_z": null
      },
      {
        "at": 1789056060.0,
        "value": 0.003333333333333333,
        "robust_z": null
      },
      {
        "at": 1789056090.0,
        "value": 0.003333333333333333,
        "robust_z": null
      }
    ]
  },
  "finding:44": {
    "service": "checkout-api",
    "metric": "error_rate",
    "unit": "ratio",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055910.0,
    "count": 8,
    "examples": [
      {
        "at": 1789055910.0,
        "value": 0.0024688271508484638,
        "robust_z": 12.372631443833873
      },
      {
        "at": 1789055940.0,
        "value": 0.0029602312369181698,
        "robust_z": 8.443969570934458
      },
      {
        "at": 1789055970.0,
        "value": 0.0032962069595133528,
        "robust_z": 11.705575263528791
      }
    ]
  },
  "finding:45": {
    "service": "checkout-api",
    "metric": "error_rate",
    "unit": "ratio",
    "source": "prometheus",
    "kind": "trend",
    "first_at": 1789055850.0,
    "count": 2
  },
  "finding:59": {
    "service": "gateway",
    "metric": "error_rate",
    "unit": "ratio",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055940.0,
    "count": 6,
    "examples": [
      {
        "at": 1789055940.0,
        "value": 0.0027172570525171684,
        "robust_z": 7.617559325749332
      },
      {
        "at": 1789055970.0,
        "value": 0.002997707635337683,
        "robust_z": 10.329570902648763
      },
      {
        "at": 1789056000.0,
        "value": 0.003271662808272906,
        "robust_z": 4.496403424885946
      }
    ]
  },
  "finding:60": {
    "service": "gateway",
    "metric": "error_rate",
    "unit": "ratio",
    "source": "prometheus",
    "kind": "trend",
    "first_at": 1789055880.0,
    "count": 1
  },
  "finding:64": {
    "service": "inventory-service",
    "metric": "error_rate",
    "unit": "ratio",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789056270.0,
    "count": 1,
    "examples": [
      {
        "at": 1789056270.0,
        "value": 0.00043487388657289383,
        "robust_z": 4.5821094227151535
      }
    ]
  },
  "finding:75": {
    "service": "order-service",
    "metric": "error_rate",
    "unit": "ratio",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055280.0,
    "count": 12,
    "examples": [
      {
        "at": 1789055280.0,
        "value": 0.0018114986193918288,
        "robust_z": 4.574828395298284
      },
      {
        "at": 1789055610.0,
        "value": 0.0018208487178190279,
        "robust_z": 6.982695921959253
      },
      {
        "at": 1789055670.0,
        "value": 0.0017632488559873492,
        "robust_z": 8.607832541310772
      }
    ]
  },
  "finding:76": {
    "service": "order-service",
    "metric": "error_rate",
    "unit": "ratio",
    "source": "prometheus",
    "kind": "trend",
    "first_at": 1789055850.0,
    "count": 2
  },
  "finding:78": {
    "service": "payment-service",
    "metric": "error_rate",
    "unit": "ratio",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055880.0,
    "count": 3,
    "examples": [
      {
        "at": 1789055880.0,
        "value": 0.0006546950991395435,
        "robust_z": 5.938578419093879
      },
      {
        "at": 1789055910.0,
        "value": 0.0006187161639597834,
        "robust_z": 3.586446291231913
      },
      {
        "at": 1789056030.0,
        "value": 0.0005466052934407365,
        "robust_z": 12.30273697529639
      }
    ]
  },
  "finding:113": {
    "service": "checkout-api",
    "metric": "http_4xx",
    "unit": "requests/s",
    "source": "prometheus",
    "kind": "baseline",
    "first_at": 1789055100.0,
    "baseline": 4.0,
    "current": 16.288888888888888,
    "absolute": 12.288888888888888,
    "percent": 307.2222222222222
  },
  "finding:114": {
    "service": "checkout-api",
    "metric": "http_4xx",
    "unit": "requests/s",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055610.0,
    "count": 6,
    "examples": [
      {
        "at": 1789055610.0,
        "value": 12.444444444444443,
        "robust_z": 4.946258166666684
      },
      {
        "at": 1789055640.0,
        "value": 13.91111111111111,
        "robust_z": 14.164284750000055
      },
      {
        "at": 1789055670.0,
        "value": 14.888888888888888,
        "robust_z": 12.333526857142903
      }
    ]
  },
  "finding:115": {
    "service": "checkout-api",
    "metric": "http_4xx",
    "unit": "requests/s",
    "source": "prometheus",
    "kind": "trend",
    "first_at": 1789055130.0,
    "count": 4
  },
  "finding:123": {
    "service": "gateway",
    "metric": "http_4xx",
    "unit": "requests/s",
    "source": "prometheus",
    "kind": "baseline",
    "first_at": 1789055100.0,
    "baseline": 4.0,
    "current": 16.42222222222222,
    "absolute": 12.42222222222222,
    "percent": 310.55555555555554
  },
  "finding:124": {
    "service": "gateway",
    "metric": "http_4xx",
    "unit": "requests/s",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055610.0,
    "count": 8,
    "examples": [
      {
        "at": 1789055610.0,
        "value": 12.577777777777776,
        "robust_z": 4.215560937500014
      },
      {
        "at": 1789055640.0,
        "value": 13.688888888888888,
        "robust_z": 10.117346250000043
      },
      {
        "at": 1789055670.0,
        "value": 15.244444444444444,
        "robust_z": 11.803570625000047
      }
    ]
  },
  "finding:125": {
    "service": "gateway",
    "metric": "http_4xx",
    "unit": "requests/s",
    "source": "prometheus",
    "kind": "trend",
    "first_at": 1789055520.0,
    "count": 3
  },
  "finding:126": {
    "service": "inventory-service",
    "metric": "http_4xx",
    "unit": "requests/s",
    "source": "prometheus",
    "kind": "baseline",
    "first_at": 1789055100.0,
    "baseline": 2.4,
    "current": 9.73333333333333,
    "absolute": 7.33333333333333,
    "percent": 305.55555555555543
  },
  "finding:127": {
    "service": "inventory-service",
    "metric": "http_4xx",
    "unit": "requests/s",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055610.0,
    "count": 7,
    "examples": [
      {
        "at": 1789055610.0,
        "value": 7.577777777777777,
        "robust_z": 5.733162875000013
      },
      {
        "at": 1789055640.0,
        "value": 8.2,
        "robust_z": 9.667686416666703
      },
      {
        "at": 1789055670.0,
        "value": 8.799999999999999,
        "robust_z": 4.519081325000016
      }
    ]
  },
  "finding:128": {
    "service": "inventory-service",
    "metric": "http_4xx",
    "unit": "requests/s",
    "source": "prometheus",
    "kind": "trend",
    "first_at": 1789055130.0,
    "count": 4
  },
  "finding:134": {
    "service": "order-service",
    "metric": "http_4xx",
    "unit": "requests/s",
    "source": "prometheus",
    "kind": "baseline",
    "first_at": 1789055100.0,
    "baseline": 3.4,
    "current": 13.866666666666664,
    "absolute": 10.466666666666663,
    "percent": 307.8431372549019
  },
  "finding:135": {
    "service": "order-service",
    "metric": "http_4xx",
    "unit": "requests/s",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055490.0,
    "count": 10,
    "examples": [
      {
        "at": 1789055490.0,
        "value": 10.133333333333333,
        "robust_z": 5.395918
      },
      {
        "at": 1789055580.0,
        "value": 10.288888888888888,
        "robust_z": 4.0469385
      },
      {
        "at": 1789055610.0,
        "value": 10.599999999999998,
        "robust_z": 4.0469384999999996
      }
    ]
  },
  "finding:136": {
    "service": "order-service",
    "metric": "http_4xx",
    "unit": "requests/s",
    "source": "prometheus",
    "kind": "trend",
    "first_at": 1789055520.0,
    "count": 3
  },
  "finding:137": {
    "service": "payment-service",
    "metric": "http_4xx",
    "unit": "requests/s",
    "source": "prometheus",
    "kind": "baseline",
    "first_at": 1789055100.0,
    "baseline": 1.3666666666666667,
    "current": 5.488888888888889,
    "absolute": 4.122222222222222,
    "percent": 301.62601626016254
  },
  "finding:138": {
    "service": "payment-service",
    "metric": "http_4xx",
    "unit": "requests/s",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055610.0,
    "count": 9,
    "examples": [
      {
        "at": 1789055610.0,
        "value": 4.422222222222222,
        "robust_z": 4.721428250000013
      },
      {
        "at": 1789055640.0,
        "value": 4.5777777777777775,
        "robust_z": 13.489795000000054
      },
      {
        "at": 1789055670.0,
        "value": 4.977777777777777,
        "robust_z": 4.991224150000016
      }
    ]
  },
  "finding:139": {
    "service": "payment-service",
    "metric": "http_4xx",
    "unit": "requests/s",
    "source": "prometheus",
    "kind": "trend",
    "first_at": 1789055130.0,
    "count": 3
  },
  "finding:166": {
    "service": "checkout-api",
    "metric": "http_5xx",
    "unit": "requests/s",
    "source": "prometheus",
    "kind": "baseline",
    "first_at": 1789055100.0,
    "baseline": 1.1333333333333333,
    "current": 4.755555555555555,
    "absolute": 3.6222222222222213,
    "percent": 319.6078431372548
  },
  "finding:167": {
    "service": "checkout-api",
    "metric": "http_5xx",
    "unit": "requests/s",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055460.0,
    "count": 10,
    "examples": [
      {
        "at": 1789055460.0,
        "value": 3.311111111111111,
        "robust_z": 4.046938500000013
      },
      {
        "at": 1789055640.0,
        "value": 3.9999999999999996,
        "robust_z": 8.093877000000026
      },
      {
        "at": 1789055670.0,
        "value": 4.355555555555555,
        "robust_z": 8.318706916666699
      }
    ]
  },
  "finding:168": {
    "service": "checkout-api",
    "metric": "http_5xx",
    "unit": "requests/s",
    "source": "prometheus",
    "kind": "trend",
    "first_at": 1789055130.0,
    "count": 6
  },
  "finding:179": {
    "service": "gateway",
    "metric": "http_5xx",
    "unit": "requests/s",
    "source": "prometheus",
    "kind": "baseline",
    "first_at": 1789055100.0,
    "baseline": 1.0666666666666667,
    "current": 4.422222222222222,
    "absolute": 3.355555555555555,
    "percent": 314.5833333333333
  },
  "finding:180": {
    "service": "gateway",
    "metric": "http_5xx",
    "unit": "requests/s",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055640.0,
    "count": 10,
    "examples": [
      {
        "at": 1789055640.0,
        "value": 3.644444444444444,
        "robust_z": 10.791836000000027
      },
      {
        "at": 1789055670.0,
        "value": 4.088888888888889,
        "robust_z": 23.60714124999961
      },
      {
        "at": 1789055700.0,
        "value": 4.3999999999999995,
        "robust_z": 10.56700608333337
      }
    ]
  },
  "finding:181": {
    "service": "gateway",
    "metric": "http_5xx",
    "unit": "requests/s",
    "source": "prometheus",
    "kind": "trend",
    "first_at": 1789055130.0,
    "count": 3
  },
  "finding:183": {
    "service": "inventory-service",
    "metric": "http_5xx",
    "unit": "requests/s",
    "source": "prometheus",
    "kind": "baseline",
    "first_at": 1789055100.0,
    "baseline": 0.1,
    "current": 0.4111111111111111,
    "absolute": 0.3111111111111111,
    "percent": 311.11111111111114
  },
  "finding:192": {
    "service": "order-service",
    "metric": "http_5xx",
    "unit": "requests/s",
    "source": "prometheus",
    "kind": "baseline",
    "first_at": 1789055100.0,
    "baseline": 0.7333333333333334,
    "current": 3.133333333333333,
    "absolute": 2.3999999999999995,
    "percent": 327.27272727272714
  },
  "finding:193": {
    "service": "order-service",
    "metric": "http_5xx",
    "unit": "requests/s",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055670.0,
    "count": 9,
    "examples": [
      {
        "at": 1789055670.0,
        "value": 2.7999999999999994,
        "robust_z": 7.41938725000002
      },
      {
        "at": 1789055910.0,
        "value": 3.5999999999999996,
        "robust_z": 5.39591800000002
      },
      {
        "at": 1789055940.0,
        "value": 5.066666666666666,
        "robust_z": 18.43605316666661
      }
    ]
  },
  "finding:194": {
    "service": "order-service",
    "metric": "http_5xx",
    "unit": "requests/s",
    "source": "prometheus",
    "kind": "trend",
    "first_at": 1789055130.0,
    "count": 7
  },
  "finding:196": {
    "service": "payment-service",
    "metric": "http_5xx",
    "unit": "requests/s",
    "source": "prometheus",
    "kind": "baseline",
    "first_at": 1789055100.0,
    "baseline": 0.1,
    "current": 0.38888888888888884,
    "absolute": 0.28888888888888886,
    "percent": 288.88888888888886
  },
  "finding:197": {
    "service": "payment-service",
    "metric": "http_5xx",
    "unit": "requests/s",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055730.0,
    "count": 4,
    "examples": [
      {
        "at": 1789055730.0,
        "value": 0.46666666666666656,
        "robust_z": 4.046938499999992
      },
      {
        "at": 1789055880.0,
        "value": 0.46666666666666656,
        "robust_z": 810035143137390.9
      },
      {
        "at": 1789055910.0,
        "value": 0.44444444444444436,
        "robust_z": 540023428758260.56
      }
    ]
  },
  "finding:235": {
    "service": "checkout-api",
    "metric": "memory",
    "unit": "ratio",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055580.0,
    "count": 2,
    "examples": [
      {
        "at": 1789055580.0,
        "value": 0.30309063879152137,
        "robust_z": 6.683154646529839
      },
      {
        "at": 1789055910.0,
        "value": 0.316313390309612,
        "robust_z": 8.693145178923073
      }
    ]
  },
  "finding:249": {
    "service": "gateway",
    "metric": "memory",
    "unit": "ratio",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055790.0,
    "count": 3,
    "examples": [
      {
        "at": 1789055790.0,
        "value": 0.36880542431026697,
        "robust_z": 5.562685966010064
      },
      {
        "at": 1789055850.0,
        "value": 0.3568702060729265,
        "robust_z": 20.844401435311443
      },
      {
        "at": 1789056030.0,
        "value": 0.3711680183187127,
        "robust_z": 3.676520101984371
      }
    ]
  },
  "finding:253": {
    "service": "inventory-service",
    "metric": "memory",
    "unit": "ratio",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055490.0,
    "count": 3,
    "examples": [
      {
        "at": 1789055490.0,
        "value": 0.31545870192348957,
        "robust_z": 5.670521057166219
      },
      {
        "at": 1789055580.0,
        "value": 0.3194095343351364,
        "robust_z": 4.924043900339486
      },
      {
        "at": 1789055670.0,
        "value": 0.3258021818473935,
        "robust_z": 4.537106040415729
      }
    ]
  },
  "finding:266": {
    "service": "order-service",
    "metric": "memory",
    "unit": "ratio",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055640.0,
    "count": 4,
    "examples": [
      {
        "at": 1789055640.0,
        "value": 0.4632121045142412,
        "robust_z": 4.02939235289771
      },
      {
        "at": 1789055910.0,
        "value": 0.4963035061955452,
        "robust_z": 3.873238521611872
      },
      {
        "at": 1789056150.0,
        "value": 0.5136755686253309,
        "robust_z": 6.298111912510952
      }
    ]
  },
  "finding:268": {
    "service": "payment-service",
    "metric": "memory",
    "unit": "ratio",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055910.0,
    "count": 2,
    "examples": [
      {
        "at": 1789055910.0,
        "value": 0.39557746425271034,
        "robust_z": 5.09297883085586
      },
      {
        "at": 1789056330.0,
        "value": 0.4013987248763442,
        "robust_z": 4.573546533506674
      }
    ]
  },
  "finding:312": {
    "service": "checkout-api",
    "metric": "network",
    "unit": "bytes/s",
    "source": "prometheus",
    "kind": "baseline",
    "first_at": 1789055100.0,
    "baseline": 1197439.4666666668,
    "current": 4879221.477777777,
    "absolute": 3681782.0111111104,
    "percent": 307.47124289799393
  },
  "finding:313": {
    "service": "checkout-api",
    "metric": "network",
    "unit": "bytes/s",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055640.0,
    "count": 4,
    "examples": [
      {
        "at": 1789055640.0,
        "value": 4182601.0888888882,
        "robust_z": 7.269027369671326
      },
      {
        "at": 1789055670.0,
        "value": 4410410.577777777,
        "robust_z": 5.637344807555032
      },
      {
        "at": 1789055940.0,
        "value": 5372314.088888888,
        "robust_z": 4.1041960237546125
      }
    ]
  },
  "finding:314": {
    "service": "checkout-api",
    "metric": "network",
    "unit": "bytes/s",
    "source": "prometheus",
    "kind": "trend",
    "first_at": 1789055130.0,
    "count": 3
  },
  "finding:328": {
    "service": "gateway",
    "metric": "network",
    "unit": "bytes/s",
    "source": "prometheus",
    "kind": "baseline",
    "first_at": 1789055100.0,
    "baseline": 1196455.3666666667,
    "current": 4917891.444444444,
    "absolute": 3721436.0777777773,
    "percent": 311.0384374927186
  },
  "finding:329": {
    "service": "gateway",
    "metric": "network",
    "unit": "bytes/s",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055400.0,
    "count": 12,
    "examples": [
      {
        "at": 1789055400.0,
        "value": 3571364.9333333327,
        "robust_z": 9.503661612141407
      },
      {
        "at": 1789055430.0,
        "value": 3643172.3777777776,
        "robust_z": 9.109609232790127
      },
      {
        "at": 1789055610.0,
        "value": 3756283.3999999994,
        "robust_z": 10.541744357392226
      }
    ]
  },
  "finding:330": {
    "service": "gateway",
    "metric": "network",
    "unit": "bytes/s",
    "source": "prometheus",
    "kind": "trend",
    "first_at": 1789055130.0,
    "count": 3
  },
  "finding:334": {
    "service": "inventory-service",
    "metric": "network",
    "unit": "bytes/s",
    "source": "prometheus",
    "kind": "baseline",
    "first_at": 1789055100.0,
    "baseline": 720692.2,
    "current": 2947591.188888889,
    "absolute": 2226898.9888888886,
    "percent": 308.9944623916963
  },
  "finding:335": {
    "service": "inventory-service",
    "metric": "network",
    "unit": "bytes/s",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055490.0,
    "count": 10,
    "examples": [
      {
        "at": 1789055490.0,
        "value": 2182215.3777777776,
        "robust_z": 6.034555276935706
      },
      {
        "at": 1789055550.0,
        "value": 2135793.0888888887,
        "robust_z": 4.048885253383999
      },
      {
        "at": 1789055640.0,
        "value": 2442164.2666666666,
        "robust_z": 7.487430076816362
      }
    ]
  },
  "finding:336": {
    "service": "inventory-service",
    "metric": "network",
    "unit": "bytes/s",
    "source": "prometheus",
    "kind": "trend",
    "first_at": 1789055130.0,
    "count": 3
  },
  "finding:353": {
    "service": "order-service",
    "metric": "network",
    "unit": "bytes/s",
    "source": "prometheus",
    "kind": "baseline",
    "first_at": 1789055100.0,
    "baseline": 1020767.6666666667,
    "current": 4151243.388888888,
    "absolute": 3130475.722222221,
    "percent": 306.6785738271707
  },
  "finding:354": {
    "service": "order-service",
    "metric": "network",
    "unit": "bytes/s",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055400.0,
    "count": 6,
    "examples": [
      {
        "at": 1789055400.0,
        "value": 2983563.7111111106,
        "robust_z": 3.571564011883781
      },
      {
        "at": 1789055640.0,
        "value": 3415955.866666666,
        "robust_z": 4.499041008628979
      },
      {
        "at": 1789055670.0,
        "value": 3861631.844444444,
        "robust_z": 11.861608992077894
      }
    ]
  },
  "finding:355": {
    "service": "order-service",
    "metric": "network",
    "unit": "bytes/s",
    "source": "prometheus",
    "kind": "trend",
    "first_at": 1789055520.0,
    "count": 5
  },
  "finding:357": {
    "service": "payment-service",
    "metric": "network",
    "unit": "bytes/s",
    "source": "prometheus",
    "kind": "baseline",
    "first_at": 1789055100.0,
    "baseline": 407669.3666666667,
    "current": 1663219.7333333332,
    "absolute": 1255550.3666666665,
    "percent": 307.98251458841514
  },
  "finding:358": {
    "service": "payment-service",
    "metric": "network",
    "unit": "bytes/s",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055370.0,
    "count": 11,
    "examples": [
      {
        "at": 1789055370.0,
        "value": 1236288.7555555555,
        "robust_z": 4.215269346655905
      },
      {
        "at": 1789055550.0,
        "value": 1209380.6888888888,
        "robust_z": 15.175059749212604
      },
      {
        "at": 1789055610.0,
        "value": 1274975.8666666665,
        "robust_z": 5.021390785160708
      }
    ]
  },
  "finding:359": {
    "service": "payment-service",
    "metric": "network",
    "unit": "bytes/s",
    "source": "prometheus",
    "kind": "trend",
    "first_at": 1789055130.0,
    "count": 3
  },
  "finding:408": {
    "service": "checkout-api",
    "metric": "p95",
    "unit": "ms",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055280.0,
    "count": 12,
    "examples": [
      {
        "at": 1789055280.0,
        "value": 161.76904506437768,
        "robust_z": 4.219884686334485
      },
      {
        "at": 1789055610.0,
        "value": 166.63672163426023,
        "robust_z": 4.7073662976326975
      },
      {
        "at": 1789055640.0,
        "value": 188.54007316262056,
        "robust_z": 11.300608330324362
      }
    ]
  },
  "finding:409": {
    "service": "checkout-api",
    "metric": "p95",
    "unit": "ms",
    "source": "prometheus",
    "kind": "trend",
    "first_at": 1789055580.0,
    "count": 2
  },
  "finding:423": {
    "service": "gateway",
    "metric": "p95",
    "unit": "ms",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055250.0,
    "count": 13,
    "examples": [
      {
        "at": 1789055250.0,
        "value": 187.3517322372285,
        "robust_z": 9.79430125091748
      },
      {
        "at": 1789055610.0,
        "value": 194.45705573437223,
        "robust_z": 4.438765990588293
      },
      {
        "at": 1789055640.0,
        "value": 208.14345991561171,
        "robust_z": 13.575185111202442
      }
    ]
  },
  "finding:424": {
    "service": "gateway",
    "metric": "p95",
    "unit": "ms",
    "source": "prometheus",
    "kind": "trend",
    "first_at": 1789055100.0,
    "count": 7
  },
  "finding:428": {
    "service": "inventory-service",
    "metric": "p95",
    "unit": "ms",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055520.0,
    "count": 5,
    "examples": [
      {
        "at": 1789055520.0,
        "value": 24.317798231849668,
        "robust_z": 3.635349232768128
      },
      {
        "at": 1789055670.0,
        "value": 24.481747272235417,
        "robust_z": 3.59861123002006
      },
      {
        "at": 1789055700.0,
        "value": 24.564453655884748,
        "robust_z": 7.973938625387767
      }
    ]
  },
  "finding:439": {
    "service": "order-service",
    "metric": "p95",
    "unit": "ms",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055610.0,
    "count": 11,
    "examples": [
      {
        "at": 1789055610.0,
        "value": 140.18542633903294,
        "robust_z": 7.839274618318778
      },
      {
        "at": 1789055640.0,
        "value": 145.9535552815454,
        "robust_z": 15.181541071164256
      },
      {
        "at": 1789055670.0,
        "value": 159.1596082583378,
        "robust_z": 12.569920904884697
      }
    ]
  },
  "finding:440": {
    "service": "order-service",
    "metric": "p95",
    "unit": "ms",
    "source": "prometheus",
    "kind": "trend",
    "first_at": 1789055550.0,
    "count": 5
  },
  "finding:442": {
    "service": "payment-service",
    "metric": "p95",
    "unit": "ms",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789056210.0,
    "count": 2,
    "examples": [
      {
        "at": 1789056210.0,
        "value": 61.73415777562236,
        "robust_z": 3.8951221265835345
      },
      {
        "at": 1789056270.0,
        "value": 59.53438661710038,
        "robust_z": 4.900403862804448
      }
    ]
  },
  "finding:485": {
    "service": "checkout-api",
    "metric": "p99",
    "unit": "ms",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055610.0,
    "count": 13,
    "examples": [
      {
        "at": 1789055610.0,
        "value": 235.72951021412754,
        "robust_z": 4.654467104448671
      },
      {
        "at": 1789055640.0,
        "value": 240.62121715996022,
        "robust_z": 12.760165854404944
      },
      {
        "at": 1789055670.0,
        "value": 243.7374623871614,
        "robust_z": 6.6857995874099
      }
    ]
  },
  "finding:486": {
    "service": "checkout-api",
    "metric": "p99",
    "unit": "ms",
    "source": "prometheus",
    "kind": "trend",
    "first_at": 1789055850.0,
    "count": 2
  },
  "finding:500": {
    "service": "gateway",
    "metric": "p99",
    "unit": "ms",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055610.0,
    "count": 12,
    "examples": [
      {
        "at": 1789055610.0,
        "value": 241.9542605454853,
        "robust_z": 5.0675868527661185
      },
      {
        "at": 1789055640.0,
        "value": 245.36106088004823,
        "robust_z": 13.695735314464207
      },
      {
        "at": 1789055670.0,
        "value": 247.55571727462927,
        "robust_z": 6.116850080542219
      }
    ]
  },
  "finding:501": {
    "service": "gateway",
    "metric": "p99",
    "unit": "ms",
    "source": "prometheus",
    "kind": "trend",
    "first_at": 1789055580.0,
    "count": 3
  },
  "finding:505": {
    "service": "inventory-service",
    "metric": "p99",
    "unit": "ms",
    "source": "prometheus",
    "kind": "spike",
    "first_at": 1789055520.0,
    "count": 6,
    "examples": [
      {
        "at": 1789055520.0,
        "value": 38.10543224299059,
        "robust_z": 5.1243559869703805
      },
      {
        "at": 1789055610.0,
        "value": 38.948275862068805,
        "robust_z": 4.675852525526893
      },
      {
        "at": 1789055670.0,
        "value": 40.75168665667179,
        "robust_z": 3.8418024055417215
      }
    ]
  },
  "tool:call_00_Wc4FFGV5ULrugC0edzwh7114": {
    "success": true,
    "text": "NT-123: НТ checkout flow перед релизом 1.42 (2000 RPS)\nСсылка: http://localhost:8081/browse/NT-123\nТип: Task\nСтатус: In Progress\nПриоритет: High\nИсполнитель: Алексей Перфов\nАвтор: Мария Кью\nКомпоненты: order-service, checkout-api\nМетки: performance, checkout, release-1.42\nОбновлена: 2026-09-10\n\nОписание:\nh2. Задача\n\nПровести нагрузочное тестирование checkout flow перед релизом 1.42.\nОсновной интерес — поведение order-service после перехода на новую\nсхему сериализации заказов.\n\nh2. Параметры НТ\n\n* Окружение: nt01\n* Namespace: nt01\n* Тестируемый сервис: order-service\n* Точка входа (стенд): http://192.168.0.158:8087/api/checkout\n* Точка входа изнутри кластера: http://checkout-app:8080/api/checkout\n* Health-check: http://192.168.0.158:8087/healthz\n* Тип теста: load (ступенчатый)\n* Целевой RPS: 2000\n* Длительность: 21 минут\n* Сценарий k6: checkout_peak_2000rps\n* Ступени: 1000 -> 1600 -> 1800 -> 2000 RPS\n\nh2. SLA\n\n* p95 < 500 мс\n* p99 < 1200 мс\n* error rate < 1%\n* CPU utilization < 85% от лимита\n\nh2. Ссылки\n\n* Топология и зависимости: [NT/order-service — архитектура и зависимости|http://192.168.0.158:8082/pages/NT-ARCH]\n* SLA/SLO контура: [NT/SLA-SLO checkout flow|http://192.168.0.158:8082/pages/NT-SLA]\n* Предыдущий прогон: NT-118\n\nh2. Definition of Done\n\n* Отчёт с вердиктом PASS/FAIL по SLA\n* Определена максимальная стабильная нагрузка\n* Указана вероятная причина деградации (если есть)\n\nСвязи:\n- relates NT-118: НТ checkout flow, релиз 1.41 (1900 RPS)\n- blocks ORD-4471: order-service: перевести сериализацию заказов на новый формат\n- relates OPS-908: orders.events: рост consumer lag на nt01 во время нагрузочных прогонов\n\nКомментарии (от старых к новым):\n- 2026-09-10 Алексей Перфов: Прогон завершён в 16:06 UTC. Нужен разбор: на верхней ступени поехали времена ответа.\n- 2026-09-10 Дмитрий Опс: Напоминаю: в релизе 1.42 в order-service поменяли сериализацию заказов (ORD-4471). На нагрузочном профиле это ещё не мерили.\n- 2026-09-10 Алексей Перфов: Прогон запущен: test_id=nt-run-2291, старт 15:45 UTC, план 21 минут. Ступени 1000/1600/1800/2000 RPS.\n- 2026-09-10 Мария Кью: Стенд подготовлен, данные прогреты. Запускаем в 15:45 UTC.",
    "truncated": false,
    "note": "context text is not verified metric evidence",
    "evidence_id": "tool:call_00_Wc4FFGV5ULrugC0edzwh7114"
  },
  "tool:call_01_dLk4M3z0oDlEewO1cTkA3204": {
    "success": true,
    "data": {
      "test_id": "nt-run-2291",
      "scenario": "checkout_peak_2000rps",
      "jira_key": "NT-123",
      "environment": "nt01",
      "namespace": "nt01",
      "target_service": "order-service",
      "target_rps": 2000.0,
      "duration_seconds": 1260,
      "started_at": 1789055100.0,
      "finished_at": 1789056360.0,
      "baseline_start": 1789052700.0,
      "baseline_end": 1789055100.0,
      "test_status": "completed",
      "sla_p95_ms": 500.0,
      "sla_p99_ms": 1200.0,
      "sla_error_rate": 0.01
    },
    "missing_parameters": [],
    "evidence_id": "tool:call_01_dLk4M3z0oDlEewO1cTkA3204"
  },
  "tool:call_02_EMf38ut07CG02aOxxGpD9820": {
    "success": true,
    "text": "по запросу 'order-service CPU limits autoscaling nt01 load test' страниц не найдено",
    "truncated": false,
    "note": "context text is not verified metric evidence",
    "evidence_id": "tool:call_02_EMf38ut07CG02aOxxGpD9820"
  },
  "tool:call_01_zx5Lc4iygh4MHq1cxR5i7856": {
    "success": true,
    "data": [],
    "historical": false,
    "evidence_id": "tool:call_01_zx5Lc4iygh4MHq1cxR5i7856"
  },
  "tool:call_02_qsGxkW29WYmnXxikqAP69183": {
    "success": true,
    "data": {
      "kind": "PodMetricsList",
      "apiVersion": "metrics.k8s.io/v1beta1",
      "metadata": {},
      "items": [
        {
          "kind": "PodMetrics",
          "apiVersion": "metrics.k8s.io/v1beta1",
          "metadata": {
            "name": "order-service-699cfbb59-rzn2b",
            "namespace": "nt01",
            "creationTimestamp": "2026-09-11T06:18:54Z"
          },
          "timestamp": "2026-09-11T06:18:54Z",
          "window": "30s",
          "containers": [
            {
              "name": "app",
              "usage": {
                "cpu": "316m",
                "memory": "815Mi"
              }
            }
          ]
        },
        {
          "kind": "PodMetrics",
          "apiVersion": "metrics.k8s.io/v1beta1",
          "metadata": {
            "name": "order-service-699cfbb59-7s6sx",
            "namespace": "nt01",
            "creationTimestamp": "2026-09-11T06:18:54Z"
          },
          "timestamp": "2026-09-11T06:18:54Z",
          "window": "30s",
          "containers": [
            {
              "name": "app",
              "usage": {
                "cpu": "316m",
                "memory": "815Mi"
              }
            }
          ]
        },
        {
          "kind": "PodMetrics",
          "apiVersion": "metrics.k8s.io/v1beta1",
          "metadata": {
            "name": "order-service-699cfbb59-h49zb",
            "namespace": "nt01",
            "creationTimestamp": "2026-09-11T06:18:54Z"
          },
          "timestamp": "2026-09-11T06:18:54Z",
          "window": "30s",
          "containers": [
            {
              "name": "app",
              "usage": {
                "cpu": "316m",
                "memory": "815Mi"
              }
            }
          ]
        },
        {
          "kind": "PodMetrics",
          "apiVersion": "metrics.k8s.io/v1beta1",
          "metadata": {
            "name": "order-service-699cfbb59-ztcsg",
            "namespace": "nt01",
            "creationTimestamp": "2026-09-11T06:18:54Z"
          },
          "timestamp": "2026-09-11T06:18:54Z",
          "window": "30s",
          "containers": [
            {
              "name": "app",
              "usage": {
                "cpu": "316m",
                "memory": "815Mi"
              }
            }
          ]
        }
      ]
    },
    "historical": false,
    "evidence_id": "tool:call_02_qsGxkW29WYmnXxikqAP69183"
  },
  "tool:call_00_TZbfApWvXfH0SLQN0LPl8477": {
    "success": true,
    "text": "по запросу 'order-service архитектура и зависимости checkout flow' страниц не найдено",
    "truncated": false,
    "note": "context text is not verified metric evidence",
    "evidence_id": "tool:call_00_TZbfApWvXfH0SLQN0LPl8477"
  },
  "tool:call_01_n4yxszYLCf6qBgfyVmKy0821": {
    "success": true,
    "text": "по запросу 'ORD-4471 order-service сериализация заказов' задач не найдено",
    "truncated": false,
    "note": "context text is not verified metric evidence",
    "evidence_id": "tool:call_01_n4yxszYLCf6qBgfyVmKy0821"
  },
  "tool:call_02_3VFkQI6dxKSNR52LUgj60540": {
    "success": true,
    "text": "по запросу 'OPS-908 orders.events consumer lag nt01' задач не найдено",
    "truncated": false,
    "note": "context text is not verified metric evidence",
    "evidence_id": "tool:call_02_3VFkQI6dxKSNR52LUgj60540"
  }
}
```

## Baseline comparison

```json
{
  "checkout-api": {
    "cpu": {
      "baseline": 0.23333333333333334,
      "current": 0.6555555555555554,
      "absolute": 0.4222222222222221,
      "percent": 180.9523809523809
    },
    "cpu_throttling": {
      "baseline": 0.0,
      "current": 0.0,
      "absolute": 0.0,
      "percent": null
    },
    "error_rate": {
      "baseline": 0.002296610759273436,
      "current": 0.002344537493477698,
      "absolute": 4.7926734204262276e-05,
      "percent": 2.0868461932758926
    },
    "http_4xx": {
      "baseline": 4.0,
      "current": 16.288888888888888,
      "absolute": 12.288888888888888,
      "percent": 307.2222222222222
    },
    "http_5xx": {
      "baseline": 1.1333333333333333,
      "current": 4.755555555555555,
      "absolute": 3.6222222222222213,
      "percent": 319.6078431372548
    },
    "memory": {
      "baseline": 0.28938334435224533,
      "current": 0.31637179323782527,
      "absolute": 0.02698844888557994,
      "percent": 9.326192889915898
    },
    "network": {
      "baseline": 1197439.4666666668,
      "current": 4879221.477777777,
      "absolute": 3681782.0111111104,
      "percent": 307.47124289799393
    },
    "p95": {
      "baseline": 142.41257793439954,
      "current": 222.83354821839123,
      "absolute": 80.4209702839917,
      "percent": 56.47041255094515
    },
    "p99": {
      "baseline": 211.10928961748635,
      "current": 258.7089373326271,
      "absolute": 47.59964771514075,
      "percent": 22.547396091089887
    },
    "pod_restarts": {
      "baseline": 0.0,
      "current": 0.0,
      "absolute": 0.0,
      "percent": null
    },
    "replicas": {
      "baseline": 6.0,
      "current": 6.0,
      "absolute": 0.0,
      "percent": 0.0
    },
    "rps": {
      "baseline": 500.1666666666667,
      "current": 2034.8444444444442,
      "absolute": 1534.6777777777775,
      "percent": 306.8332777962901
    }
  },
  "gateway": {
    "cpu": {
      "baseline": 0.2,
      "current": 0.5444444444444443,
      "absolute": 0.3444444444444443,
      "percent": 172.22222222222211
    },
    "cpu_throttling": {
      "baseline": 0.0,
      "current": 0.0,
      "absolute": 0.0,
      "percent": null
    },
    "error_rate": {
      "baseline": 0.002139752591106653,
      "current": 0.0022056379828997294,
      "absolute": 6.588539179307618e-05,
      "percent": 3.0791126070795447
    },
    "http_4xx": {
      "baseline": 4.0,
      "current": 16.42222222222222,
      "absolute": 12.42222222222222,
      "percent": 310.55555555555554
    },
    "http_5xx": {
      "baseline": 1.0666666666666667,
      "current": 4.422222222222222,
      "absolute": 3.355555555555555,
      "percent": 314.5833333333333
    },
    "memory": {
      "baseline": 0.3247562227770686,
      "current": 0.3588256947696209,
      "absolute": 0.03406947199255228,
      "percent": 10.490783425554104
    },
    "network": {
      "baseline": 1196455.3666666667,
      "current": 4917891.444444444,
      "absolute": 3721436.0777777773,
      "percent": 311.0384374927186
    },
    "p95": {
      "baseline": 147.89357490864796,
      "current": 231.25188884960906,
      "absolute": 83.3583139409611,
      "percent": 56.36371559241198
    },
    "p99": {
      "baseline": 227.43954248366043,
      "current": 296.05907568744476,
      "absolute": 68.61953320378433,
      "percent": 30.17044989382796
    },
    "pod_restarts": {
      "baseline": 0.0,
      "current": 0.0,
      "absolute": 0.0,
      "percent": null
    },
    "replicas": {
      "baseline": 6.0,
      "current": 6.0,
      "absolute": 0.0,
      "percent": 0.0
    },
    "rps": {
      "baseline": 498.7,
      "current": 2050.7999999999997,
      "absolute": 1552.0999999999997,
      "percent": 311.2291959093643
    }
  },
  "inventory-service": {
    "cpu": {
      "baseline": 0.23333333333333334,
      "current": 0.5222222222222221,
      "absolute": 0.2888888888888888,
      "percent": 123.80952380952377
    },
    "cpu_throttling": {
      "baseline": 0.0,
      "current": 0.0,
      "absolute": 0.0,
      "percent": null
    },
    "error_rate": {
      "baseline": 0.00032930845225027445,
      "current": 0.0003916949718057647,
      "absolute": 6.238651955549026e-05,
      "percent": 18.944706438350543
    },
    "http_4xx": {
      "baseline": 2.4,
      "current": 9.73333333333333,
      "absolute": 7.33333333333333,
      "percent": 305.55555555555543
    },
    "http_5xx": {
      "baseline": 0.1,
      "current": 0.4111111111111111,
      "absolute": 0.3111111111111111,
      "percent": 311.11111111111114
    },
    "memory": {
      "baseline": 0.30821395199745893,
      "current": 0.32459288090467453,
      "absolute": 0.016378928907215595,
      "percent": 5.3141425951251655
    },
    "network": {
      "baseline": 720692.2,
      "current": 2947591.188888889,
      "absolute": 2226898.9888888886,
      "percent": 308.9944623916963
    },
    "p95": {
      "baseline": 24.19914788345245,
      "current": 24.5204979677152,
      "absolute": 0.32135008426275036,
      "percent": 1.3279396688281402
    },
    "p99": {
      "baseline": 35.527070063694495,
      "current": 41.23374113201727,
      "absolute": 5.706671068322777,
      "percent": 16.062881228571918
    },
    "pod_restarts": {
      "baseline": 0.0,
      "current": 0.0,
      "absolute": 0.0,
      "percent": null
    },
    "replicas": {
      "baseline": 3.0,
      "current": 3.0,
      "absolute": 0.0,
      "percent": 0.0
    },
    "rps": {
      "baseline": 300.6666666666667,
      "current": 1220.2888888888888,
      "absolute": 919.622222222222,
      "percent": 305.861049519586
    }
  },
  "order-service": {
    "cpu": {
      "baseline": 0.3333333333333333,
      "current": 0.8222222222222221,
      "absolute": 0.48888888888888876,
      "percent": 146.66666666666663
    },
    "cpu_throttling": {
      "baseline": 0.0,
      "current": 0.0,
      "absolute": 0.0,
      "percent": null
    },
    "error_rate": {
      "baseline": 0.0017421602787456448,
      "current": 0.0018144205125141268,
      "absolute": 7.226023376848199e-05,
      "percent": 4.147737418310866
    },
    "http_4xx": {
      "baseline": 3.4,
      "current": 13.866666666666664,
      "absolute": 10.466666666666663,
      "percent": 307.8431372549019
    },
    "http_5xx": {
      "baseline": 0.7333333333333334,
      "current": 3.133333333333333,
      "absolute": 2.3999999999999995,
      "percent": 327.27272727272714
    },
    "memory": {
      "baseline": 0.39940284471958876,
      "current": 0.47421257570385933,
      "absolute": 0.07480973098427057,
      "percent": 18.73039513196074
    },
    "network": {
      "baseline": 1020767.6666666667,
      "current": 4151243.388888888,
      "absolute": 3130475.722222221,
      "percent": 306.6785738271707
    },
    "p95": {
      "baseline": 117.57936507936512,
      "current": 219.03530403438486,
      "absolute": 101.45593895501975,
      "percent": 86.28719749127562
    },
    "p99": {
      "baseline": 146.86611374407573,
      "current": 284.21990421694284,
      "absolute": 137.3537904728671,
      "percent": 93.52313271679232
    },
    "pod_restarts": {
      "baseline": 0.0,
      "current": 0.0,
      "absolute": 0.0,
      "percent": null
    },
    "replicas": {
      "baseline": 4.0,
      "current": 4.0,
      "absolute": 0.0,
      "percent": 0.0
    },
    "rps": {
      "baseline": 424.93333333333334,
      "current": 1739.3777777777775,
      "absolute": 1314.4444444444441,
      "percent": 309.32956803681617
    }
  },
  "payment-service": {
    "cpu": {
      "baseline": 0.2,
      "current": 0.47777777777777775,
      "absolute": 0.27777777777777773,
      "percent": 138.88888888888886
    },
    "cpu_throttling": {
      "baseline": 0.0,
      "current": 0.0,
      "absolute": 0.0,
      "percent": null
    },
    "error_rate": {
      "baseline": 0.0005685048322910744,
      "current": 0.0005885227616640229,
      "absolute": 2.0017929372948494e-05,
      "percent": 3.5211537767016403
    },
    "http_4xx": {
      "baseline": 1.3666666666666667,
      "current": 5.488888888888889,
      "absolute": 4.122222222222222,
      "percent": 301.62601626016254
    },
    "http_5xx": {
      "baseline": 0.1,
      "current": 0.38888888888888884,
      "absolute": 0.28888888888888886,
      "percent": 288.88888888888886
    },
    "memory": {
      "baseline": 0.38562203757464886,
      "current": 0.3976594372652471,
      "absolute": 0.01203739969059825,
      "percent": 3.121553883773576
    },
    "network": {
      "baseline": 407669.3666666667,
      "current": 1663219.7333333332,
      "absolute": 1255550.3666666665,
      "percent": 307.98251458841514
    },
    "p95": {
      "baseline": 49.85621165644171,
      "current": 57.04262866611933,
      "absolute": 7.186417009677619,
      "percent": 14.414286145925196
    },
    "p99": {
      "baseline": 71.01785714285714,
      "current": 73.00491899122136,
      "absolute": 1.9870618483642204,
      "percent": 2.797974943635815
    },
    "pod_restarts": {
      "baseline": 0.0,
      "current": 0.0,
      "absolute": 0.0,
      "percent": null
    },
    "replicas": {
      "baseline": 4.0,
      "current": 4.0,
      "absolute": 0.0,
      "percent": 0.0
    },
    "rps": {
      "baseline": 170.2,
      "current": 692.2222222222222,
      "absolute": 522.0222222222221,
      "percent": 306.71105888497186
    }
  }
}
```

Показаны 5 из 80 сервисов: остальные вне фокуса анализа. Полные ряды остались в состоянии прогона.

## Previous test comparison

```json
{
  "test_id": "nt-run-2187",
  "note": "stable load compared using current SLA limits",
  "scenario_differs": {
    "current": "checkout_peak_2000rps",
    "previous": "checkout_peak_1900rps"
  },
  "stable_rps": {
    "baseline": 1942.2333333333333,
    "current": 1829.8444444444442,
    "absolute": -112.38888888888914,
    "percent": -5.7865801683056866
  }
}
```

```json
{
  "checkout-api": {
    "cpu": {
      "baseline": 0.5333333333333333,
      "current": 0.6555555555555554,
      "absolute": 0.12222222222222212,
      "percent": 22.91666666666665
    },
    "cpu_throttling": {
      "baseline": 0.0,
      "current": 0.0,
      "absolute": 0.0,
      "percent": null
    },
    "error_rate": {
      "baseline": 0.002308458570173046,
      "current": 0.002344537493477698,
      "absolute": 3.607892330465207e-05,
      "percent": 1.562901053145066
    },
    "http_4xx": {
      "baseline": 15.6,
      "current": 16.288888888888888,
      "absolute": 0.6888888888888882,
      "percent": 4.4159544159544115
    },
    "http_5xx": {
      "baseline": 4.416666666666667,
      "current": 4.755555555555555,
      "absolute": 0.3388888888888877,
      "percent": 7.672955974842739
    },
    "memory": {
      "baseline": 0.3147684093564749,
      "current": 0.31637179323782527,
      "absolute": 0.0016033838813503953,
      "percent": 0.509385260302461
    },
    "network": {
      "baseline": 4651920.9,
      "current": 4879221.477777777,
      "absolute": 227300.57777777687,
      "percent": 4.886166008922827
    },
    "p95": {
      "baseline": 173.36397039386713,
      "current": 222.83354821839123,
      "absolute": 49.469577824524094,
      "percent": 28.535097409302363
    },
    "p99": {
      "baseline": 237.17208035950296,
      "current": 258.7089373326271,
      "absolute": 21.536856973124145,
      "percent": 9.080688140222408
    },
    "pod_restarts": {
      "baseline": 0.0,
      "current": 0.0,
      "absolute": 0.0,
      "percent": null
    },
    "replicas": {
      "baseline": 6.0,
      "current": 6.0,
      "absolute": 0.0,
      "percent": 0.0
    },
    "rps": {
      "baseline": 1953.2833333333333,
      "current": 2034.8444444444442,
      "absolute": 81.5611111111109,
      "percent": 4.175590387694783
    }
  },
  "gateway": {
    "cpu": {
      "baseline": 0.43333333333333335,
      "current": 0.5444444444444443,
      "absolute": 0.11111111111111094,
      "percent": 25.6410256410256
    },
    "cpu_throttling": {
      "baseline": 0.0,
      "current": 0.0,
      "absolute": 0.0,
      "percent": null
    },
    "error_rate": {
      "baseline": 0.0021634379316426548,
      "current": 0.0022056379828997294,
      "absolute": 4.220005125707459e-05,
      "percent": 1.9506014311689979
    },
    "http_4xx": {
      "baseline": 15.416666666666666,
      "current": 16.42222222222222,
      "absolute": 1.0055555555555546,
      "percent": 6.522522522522517
    },
    "http_5xx": {
      "baseline": 4.166666666666666,
      "current": 4.422222222222222,
      "absolute": 0.25555555555555554,
      "percent": 6.133333333333334
    },
    "memory": {
      "baseline": 0.3571198359131813,
      "current": 0.3588256947696209,
      "absolute": 0.0017058588564395905,
      "percent": 0.47767127022714534
    },
    "network": {
      "baseline": 4623537.616666667,
      "current": 4917891.444444444,
      "absolute": 294353.82777777687,
      "percent": 6.366420091764947
    },
    "p95": {
      "baseline": 198.2043956927601,
      "current": 231.25188884960906,
      "absolute": 33.04749315684896,
      "percent": 16.673441091627666
    },
    "p99": {
      "baseline": 242.85389390244956,
      "current": 296.05907568744476,
      "absolute": 53.205181784995204,
      "percent": 21.90830911954282
    },
    "pod_restarts": {
      "baseline": 0.0,
      "current": 0.0,
      "absolute": 0.0,
      "percent": null
    },
    "replicas": {
      "baseline": 6.0,
      "current": 6.0,
      "absolute": 0.0,
      "percent": 0.0
    },
    "rps": {
      "baseline": 1925.1666666666665,
      "current": 2050.7999999999997,
      "absolute": 125.63333333333321,
      "percent": 6.525841918448613
    }
  },
  "inventory-service": {
    "cpu": {
      "baseline": 0.4666666666666667,
      "current": 0.5222222222222221,
      "absolute": 0.05555555555555547,
      "percent": 11.904761904761886
    },
    "cpu_throttling": {
      "baseline": 0.0,
      "current": 0.0,
      "absolute": 0.0,
      "percent": null
    },
    "error_rate": {
      "baseline": 0.0004006208300826646,
      "current": 0.0003916949718057647,
      "absolute": -8.925858276899869e-06,
      "percent": -2.2280065355209056
    },
    "http_4xx": {
      "baseline": 9.25,
      "current": 9.73333333333333,
      "absolute": 0.48333333333333073,
      "percent": 5.225225225225197
    },
    "http_5xx": {
      "baseline": 0.43333333333333335,
      "current": 0.4111111111111111,
      "absolute": -0.022222222222222254,
      "percent": -5.128205128205136
    },
    "memory": {
      "baseline": 0.32393551617860794,
      "current": 0.32459288090467453,
      "absolute": 0.0006573647260665894,
      "percent": 0.20293073566658215
    },
    "network": {
      "baseline": 2784833.716666667,
      "current": 2947591.188888889,
      "absolute": 162757.47222222202,
      "percent": 5.844423358139894
    },
    "p95": {
      "baseline": 24.36050513609964,
      "current": 24.5204979677152,
      "absolute": 0.15999283161556122,
      "percent": 0.6567714040480593
    },
    "p99": {
      "baseline": 38.859195132306056,
      "current": 41.23374113201727,
      "absolute": 2.3745459997112164,
      "percent": 6.110641230798703
    },
    "pod_restarts": {
      "baseline": 0.0,
      "current": 0.0,
      "absolute": 0.0,
      "percent": null
    },
    "replicas": {
      "baseline": 3.0,
      "current": 3.0,
      "absolute": 0.0,
      "percent": 0.0
    },
    "rps": {
      "baseline": 1161.3,
      "current": 1220.2888888888888,
      "absolute": 58.98888888888882,
      "percent": 5.079556435795128
    }
  },
  "order-service": {
    "cpu": {
      "baseline": 0.75,
      "current": 0.8222222222222221,
      "absolute": 0.07222222222222208,
      "percent": 9.62962962962961
    },
    "cpu_throttling": {
      "baseline": 0.0,
      "current": 0.0,
      "absolute": 0.0,
      "percent": null
    },
    "error_rate": {
      "baseline": 0.00177906695277007,
      "current": 0.0018144205125141268,
      "absolute": 3.535355974405678e-05,
      "percent": 1.9871966981911526
    },
    "http_4xx": {
      "baseline": 13.2,
      "current": 13.866666666666664,
      "absolute": 0.6666666666666643,
      "percent": 5.050505050505033
    },
    "http_5xx": {
      "baseline": 2.9166666666666665,
      "current": 3.133333333333333,
      "absolute": 0.21666666666666634,
      "percent": 7.428571428571418
    },
    "memory": {
      "baseline": 0.4710513330064714,
      "current": 0.47421257570385933,
      "absolute": 0.0031612426973879337,
      "percent": 0.6711036517424533
    },
    "network": {
      "baseline": 3852636.4499999997,
      "current": 4151243.388888888,
      "absolute": 298606.93888888834,
      "percent": 7.750716756284865
    },
    "p95": {
      "baseline": 140.86619730384274,
      "current": 219.03530403438486,
      "absolute": 78.16910673054213,
      "percent": 55.49174196981729
    },
    "p99": {
      "baseline": 205.26237799668883,
      "current": 284.21990421694284,
      "absolute": 78.95752622025401,
      "percent": 38.46663328704479
    },
    "pod_restarts": {
      "baseline": 0.0,
      "current": 0.0,
      "absolute": 0.0,
      "percent": null
    },
    "replicas": {
      "baseline": 4.0,
      "current": 4.0,
      "absolute": 0.0,
      "percent": 0.0
    },
    "rps": {
      "baseline": 1654.8166666666666,
      "current": 1739.3777777777775,
      "absolute": 84.5611111111109,
      "percent": 5.109998757834861
    }
  },
  "payment-service": {
    "cpu": {
      "baseline": 0.4,
      "current": 0.47777777777777775,
      "absolute": 0.07777777777777772,
      "percent": 19.444444444444432
    },
    "cpu_throttling": {
      "baseline": 0.0,
      "current": 0.0,
      "absolute": 0.0,
      "percent": null
    },
    "error_rate": {
      "baseline": 0.0005956351092869865,
      "current": 0.0005885227616640229,
      "absolute": -7.1123476229636e-06,
      "percent": -1.1940779702320665
    },
    "http_4xx": {
      "baseline": 5.216666666666667,
      "current": 5.488888888888889,
      "absolute": 0.27222222222222214,
      "percent": 5.218317358892437
    },
    "http_5xx": {
      "baseline": 0.4,
      "current": 0.38888888888888884,
      "absolute": -0.011111111111111183,
      "percent": -2.7777777777777954
    },
    "memory": {
      "baseline": 0.3969430588185787,
      "current": 0.3976594372652471,
      "absolute": 0.0007163784466683865,
      "percent": 0.18047385657795428
    },
    "network": {
      "baseline": 1592137.5333333332,
      "current": 1663219.7333333332,
      "absolute": 71082.19999999995,
      "percent": 4.464576615512652
    },
    "p95": {
      "baseline": 53.84739547351039,
      "current": 57.04262866611933,
      "absolute": 3.1952331926089386,
      "percent": 5.933867672728567
    },
    "p99": {
      "baseline": 72.24388087950001,
      "current": 73.00491899122136,
      "absolute": 0.7610381117213478,
      "percent": 1.0534291658427513
    },
    "pod_restarts": {
      "baseline": 0.0,
      "current": 0.0,
      "absolute": 0.0,
      "percent": null
    },
    "replicas": {
      "baseline": 4.0,
      "current": 4.0,
      "absolute": 0.0,
      "percent": 0.0
    },
    "rps": {
      "baseline": 652.0666666666666,
      "current": 692.2222222222222,
      "absolute": 40.155555555555566,
      "percent": 6.158197866612141
    }
  }
}
```

Показаны 5 из 29 сервисов прошлого прогона: остальные вне фокуса анализа. Полные ряды остались в состоянии прогона.

## Recommendations

- Предложение LLM, требует проверки: Проверить (по историческим метрикам периода теста) фактическое соотношение CPU limit/request и работу HPA у order-service на nt01: был ли потолок лимита достигнут и масштабировались ли реплики под ступени 1800/2000 RPS.

- Предложение LLM, требует проверки: Снять профиль CPU (flame graph / прогон с профилировщиком) на пути сериализации заказа после изменения ORD-4471 и сравнить с предыдущим поведением; при возможности выполнить изолированный прогон со старой схемой сериализации, чтобы отделить эффект изменения от общей ёмкости.

- Предложение LLM, требует проверки: Проверить связь со смежной задачей OPS-908: посмотреть orders.events consumer lag во время этого прогона — не является ли рост лага следствием насыщения order-service (или его независимым фактором).

- Предложение LLM, требует проверки: Сравнить с предыдущим прогоном NT-118 (1900 RPS): были ли тогда throttling/рестарты, чтобы понять, регресс это или исчерпание ёмкости стенда.

- Предложение LLM, требует проверки: Уточнить топологию и порядок вызовов checkout flow (документ NT/order-service — архитектура и зависимости и NT/SLA-SLO): источник страницы сейчас недоступен, без него нельзя утверждать направление каскада.

- Предложение LLM, требует проверки: Перепроверить корректность сравнения с baseline: сопоставление «baseline vs current» по ряду метрик (network, 4xx, 5xx) даёт одинаковый прирост около 300% у разных сервисов, что похоже на артефакт ступенчатого разгона нагрузки, а не на независимую деградацию; для выводов использовать сравнение по стабильным ступеням (1800/2000 RPS).

- Предложение LLM, требует проверки: Проверить, не был ли потерян под/рестарт order-service на критическом участке и не привёл ли он к недоступности реплики в момент пиковых задержек.

- Предложение LLM, требует проверки: После устранения причин повторить целевые ступени 1800/1900/2000 RPS для поиска точки перегиба и подтверждения максимальной стабильной нагрузки.

## Unverified assumptions

```json
[]
```

```json
[]
```

Precheck относится к данным завершённого теста. Текущие DNS/HTTP/pods не подтверждают их состояние в прошлом. Запуск и остановка НТ этим графом не выполнялись.

## Sources

```json
[
  {
    "kind": "input",
    "id": "operator"
  },
  {
    "kind": "jira",
    "id": "NT-123",
    "url": "http://localhost:8081/browse/NT-123"
  },
  {
    "kind": "jira",
    "id": "NT-118",
    "url": "http://localhost:8081/browse/NT-118"
  },
  {
    "kind": "jira",
    "id": "ORD-4471",
    "url": "http://localhost:8081/browse/ORD-4471"
  },
  {
    "kind": "load_testing",
    "id": "nt-run-2291"
  }
]
```

```json
{
  "baseline_start": 1789052700.0,
  "baseline_end": 1789055100.0,
  "metric_sources": [
    "prometheus"
  ]
}
```

## Расход токенов по треду

| Метрика | Значение |
| --- | --- |
| Вызовов LLM | 4 |
| Вход из кеша, токенов | 89344 |
| Вход пересчитан, токенов | 2475 |
| Выход, токенов | 3863 |
| Cache hit rate | 97.3% |
| Стоимость | $0.007439 |
