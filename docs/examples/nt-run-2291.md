# Orbita: Проведи анализ НТ по NT-123. test_id=nt-run-2291 previous_test_id=nt-run-2187 [01a0cfef-130b-7bf3-85fb-227dcbc2764a]

Страница собрана автоматически агентом анализа нагрузочного тестирования Orbita. Обновлено: 2026-09-24T08:02:20. Правки руками затрёт следующий прогон треда.

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

## Execution

COMPLETED

Выполнение теста и вердикт SLA — разные вещи: прерванный тест не получает PASSED, а завершившийся не обязан в SLA укладываться.

## Result

FAILED

| поле | значение |
| --- | --- |
| сервис | order-service |
| окружение | nt01 / nt01 |
| период | 2026-09-10T15:45:00+00:00 — 2026-09-10T16:06:00+00:00 (1260 с) |
| источники | prometheus |
| проверено метрик | error_rate, p95, p99 |
| чем измерен | максимумы рядов за период целиком, не плато |
| симулированные источники | prometheus |
| устойчивая RPS | None (INCONCLUSIVE) |

Причины:

- order-service/error_rate: пик 0.03695006130634469 при пороге 0.01

- order-service/p95: пик 2466.350097129284 при пороге 500.0

- order-service/p99: пик 4747.1012303043335 при пороге 1200.0

Вердикт вычислен кодом по доступным SLA и не меняется формулировками модели. Проверяются максимумы временных рядов за заданный период; p95/p99 ряда не являются перцентилями всех запросов теста и не описывают устойчивое плато.

## Diagnostic completeness

PARTIAL

```json
[
  "неполные диагностические ряды: order-service/cpu",
  "источники объявлены симулированными: prometheus; ряды не измеряют реакцию цели на этот прогон"
]
```

Полнота диагностики оценивается отдельно: отсутствие baseline или данных зависимости не отменяет проверку полных SLA-рядов целевого сервиса.

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

- p95: peak=2466.350097129284; limit=<= 500.0; unit=ms

- p99: peak=4747.1012303043335; limit=<= 1200.0; unit=ms

- error_rate: peak=0.03695006130634469; limit=<= 0.01; unit=ratio

```json
[
  {
    "service": "order-service",
    "metric": "error_rate",
    "limit": 0.01,
    "comparator": "<=",
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
    "comparator": "<=",
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
    "comparator": "<=",
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

Не установлена.

Наблюдаемая устойчивая нагрузка: минимум RPS на плато после периода установления, вся измеренная часть которого соблюдала SLA. Это нижняя оценка по наблюдениям, а не доказанный предел мощности.

```json
{
  "status": "INCONCLUSIVE",
  "maximum_stable_rps": null,
  "truncated": false,
  "settling_seconds": 30,
  "stable_seconds": 180,
  "rps_tolerance": 0.1,
  "reason": "симулированная телеметрия не подтверждает устойчивую RPS"
}
```

## Load phases

```json
[
  {
    "start": 1789055100.0,
    "end": 1789055100.0,
    "duration_seconds": 0.0,
    "kind": "transition",
    "rps_min": 417.5,
    "rps_max": 417.5,
    "rps_median": 417.5
  },
  {
    "start": 1789055130.0,
    "end": 1789055130.0,
    "duration_seconds": 0.0,
    "kind": "transition",
    "rps_min": 354.2291666666667,
    "rps_max": 354.2291666666667,
    "rps_median": 354.2291666666667
  },
  {
    "start": 1789055160.0,
    "end": 1789055160.0,
    "duration_seconds": 0.0,
    "kind": "transition",
    "rps_min": 712.711111111111,
    "rps_max": 712.711111111111,
    "rps_median": 712.711111111111
  },
  {
    "start": 1789055190.0,
    "end": 1789055190.0,
    "duration_seconds": 0.0,
    "kind": "transition",
    "rps_min": 915.8222222222221,
    "rps_max": 915.8222222222221,
    "rps_median": 915.8222222222221
  },
  {
    "start": 1789055220.0,
    "end": 1789055280.0,
    "duration_seconds": 60.0,
    "kind": "transition",
    "rps_min": 1155.7555555555555,
    "rps_max": 1271.0444444444443,
    "rps_median": 1263.5333333333333
  },
  {
    "start": 1789055310.0,
    "end": 1789055610.0,
    "duration_seconds": 300.0,
    "kind": "plateau",
    "rps_min": 1256.0666666666666,
    "rps_max": 1318.0666666666664,
    "rps_median": 1275.2444444444443,
    "measured_start": 1789055340.0,
    "samples": 10,
    "missing_metrics": [],
    "violated_metrics": [],
    "sla_status": "PASSED",
    "metrics": {
      "cpu_throttling": {
        "count": 10,
        "median": 0.0,
        "p95": 0.0,
        "p99": 0.0,
        "min": 0.0,
        "max": 0.0,
        "mad": 0.0,
        "unit": "ratio"
      },
      "error_rate": {
        "count": 10,
        "median": 0.001796906083642789,
        "p95": 0.0018329191847224478,
        "p99": 0.0018385510007539122,
        "min": 0.0017774370055414215,
        "max": 0.0018399589547617784,
        "mad": 1.7803206599249148e-05,
        "unit": "ratio"
      },
      "http_4xx": {
        "count": 10,
        "median": 10.244444444444444,
        "p95": 10.479999999999999,
        "p99": 10.575999999999999,
        "min": 10.066666666666666,
        "max": 10.599999999999998,
        "mad": 0.08888888888888857,
        "unit": "requests/s"
      },
      "http_5xx": {
        "count": 10,
        "median": 2.311111111111111,
        "p95": 2.3799999999999994,
        "p99": 2.3959999999999995,
        "min": 2.2666666666666666,
        "max": 2.3999999999999995,
        "mad": 0.04444444444444429,
        "unit": "requests/s"
      },
      "memory": {
        "count": 10,
        "median": 0.44932447746396065,
        "p95": 0.4557895179372281,
        "p99": 0.45715080841444433,
        "min": 0.43926157895475626,
        "max": 0.4574911310337484,
        "mad": 0.0034300878178328276,
        "unit": "ratio"
      },
      "network": {
        "count": 10,
        "median": 3082344.0999999996,
        "p95": 3148539.6655555554,
        "p99": 3150979.737555555,
        "min": 2983563.7111111106,
        "max": 3151589.755555555,
        "mad": 46659.02222222206,
        "unit": "bytes/s"
      },
      "p95": {
        "count": 10,
        "median": 138.7507005961125,
        "p95": 139.97481998430362,
        "p99": 140.14330506808707,
        "min": 138.23467834031206,
        "max": 140.18542633903294,
        "mad": 0.3606289995656624,
        "unit": "ms"
      },
      "p99": {
        "count": 10,
        "median": 196.77561113698496,
        "p95": 201.87319081707443,
        "p99": 202.54691539113716,
        "min": 194.60650887573868,
        "max": 202.71534653465284,
        "mad": 1.5889620372926032,
        "unit": "ms"
      },
      "pod_restarts": {
        "count": 10,
        "median": 0.0,
        "p95": 0.0,
        "p99": 0.0,
        "min": 0.0,
        "max": 0.0,
        "mad": 0.0,
        "unit": "count"
      },
      "replicas": {
        "count": 10,
        "median": 4.0,
        "p95": 4.0,
        "p99": 4.0,
        "min": 4.0,
        "max": 4.0,
        "mad": 0.0,
        "unit": "count"
      },
      "rps": {
        "count": 10,
        "median": 1278.411111111111,
        "p95": 1305.9766666666665,
        "p99": 1315.6486666666665,
        "min": 1256.0666666666666,
        "max": 1318.0666666666664,
        "mad": 10.600000000000136,
        "unit": "requests/s"
      }
    }
  },
  {
    "start": 1789055640.0,
    "end": 1789055640.0,
    "duration_seconds": 0.0,
    "kind": "transition",
    "rps_min": 1437.9999999999998,
    "rps_max": 1437.9999999999998,
    "rps_median": 1437.9999999999998
  },
  {
    "start": 1789055670.0,
    "end": 1789055700.0,
    "duration_seconds": 30.0,
    "kind": "transition",
    "rps_min": 1587.9777777777776,
    "rps_max": 1713.9111111111108,
    "rps_median": 1650.9444444444443
  },
  {
    "start": 1789055730.0,
    "end": 1789055970.0,
    "duration_seconds": 240.0,
    "kind": "plateau",
    "rps_min": 1764.8444444444442,
    "rps_max": 1923.9999999999998,
    "rps_median": 1788.7777777777776,
    "measured_start": 1789055760.0,
    "samples": 8,
    "missing_metrics": [],
    "violated_metrics": [],
    "sla_status": "PASSED",
    "metrics": {
      "cpu_throttling": {
        "count": 8,
        "median": 0.0,
        "p95": 0.0,
        "p99": 0.0,
        "min": 0.0,
        "max": 0.0,
        "mad": 0.0,
        "unit": "ratio"
      },
      "error_rate": {
        "count": 8,
        "median": 0.0018346521072415108,
        "p95": 0.002992784545721001,
        "p99": 0.003139559450146741,
        "min": 0.0018005993603465208,
        "max": 0.0031762531762531765,
        "mad": 2.838436244252836e-05,
        "unit": "ratio"
      },
      "http_4xx": {
        "count": 8,
        "median": 14.399999999999999,
        "p95": 15.366666666666665,
        "p99": 15.428888888888887,
        "min": 14.088888888888887,
        "max": 15.444444444444443,
        "mad": 0.25555555555555554,
        "unit": "requests/s"
      },
      "http_5xx": {
        "count": 8,
        "median": 3.3,
        "p95": 5.745555555555555,
        "p99": 6.037999999999999,
        "min": 3.177777777777777,
        "max": 6.111111111111111,
        "mad": 0.11111111111111138,
        "unit": "requests/s"
      },
      "memory": {
        "count": 8,
        "median": 0.4820147883147001,
        "p95": 0.4950291297864169,
        "p99": 0.49604863091371953,
        "min": 0.47359542921185493,
        "max": 0.4963035061955452,
        "mad": 0.005709273740649223,
        "unit": "ratio"
      },
      "network": {
        "count": 8,
        "median": 4348701.433333333,
        "p95": 4577313.398888888,
        "p99": 4577770.750888888,
        "min": 4177773.177777777,
        "max": 4577885.088888888,
        "mad": 95426.2666666666,
        "unit": "bytes/s"
      },
      "p95": {
        "count": 8,
        "median": 232.11138923999675,
        "p95": 319.7507157911886,
        "p99": 326.643928127238,
        "min": 226.58721461187213,
        "max": 328.3672312112503,
        "mad": 5.4074411479085,
        "unit": "ms"
      },
      "p99": {
        "count": 8,
        "median": 339.4828586843132,
        "p95": 438.00461217818906,
        "p99": 454.82568065614,
        "min": 318.5800000000008,
        "max": 459.03094777562774,
        "mad": 20.4716397226876,
        "unit": "ms"
      },
      "pod_restarts": {
        "count": 8,
        "median": 0.0,
        "p95": 0.0,
        "p99": 0.0,
        "min": 0.0,
        "max": 0.0,
        "mad": 0.0,
        "unit": "count"
      },
      "replicas": {
        "count": 8,
        "median": 4.0,
        "p95": 4.0,
        "p99": 4.0,
        "min": 4.0,
        "max": 4.0,
        "mad": 0.0,
        "unit": "count"
      },
      "rps": {
        "count": 8,
        "median": 1799.1111111111109,
        "p95": 1919.263333333333,
        "p99": 1923.0526666666665,
        "min": 1764.8444444444442,
        "max": 1923.9999999999998,
        "mad": 31.888888888888914,
        "unit": "requests/s"
      }
    }
  },
  {
    "start": 1789056000.0,
    "end": 1789056120.0,
    "duration_seconds": 120.0,
    "kind": "transition",
    "rps_min": 1941.4888888888886,
    "rps_max": 2077.822222222222,
    "rps_median": 1976.0888888888887
  },
  {
    "start": 1789056150.0,
    "end": 1789056150.0,
    "duration_seconds": 0.0,
    "kind": "transition",
    "rps_min": 2144.8444444444444,
    "rps_max": 2144.8444444444444,
    "rps_median": 2144.8444444444444
  },
  {
    "start": 1789056180.0,
    "end": 1789056210.0,
    "duration_seconds": 30.0,
    "kind": "transition",
    "rps_min": 1939.1111111111109,
    "rps_max": 1956.7555555555552,
    "rps_median": 1947.933333333333
  },
  {
    "start": 1789056240.0,
    "end": 1789056330.0,
    "duration_seconds": 90.0,
    "kind": "transition",
    "rps_min": 2106.555555555555,
    "rps_max": 2138.622222222222,
    "rps_median": 2114.422222222222
  }
]
```

## Main anomalies

Сервисов с данными: 80.

| сервис | score | важность | метрики со срабатываниями |
| --- | --- | --- | --- |
| order-service | 0.989 | critical | cpu, error_rate, memory, p95, p99, pod_restarts |
| image-resizer | 0.9056 | critical | cpu, cpu_throttling, error_rate, memory, p95, p99 |
| export-service | 0.5904 | warning | error_rate, memory, p95, p99 |
| feature-flags | 0.5904 | warning | error_rate, memory, p95, p99 |
| session-cleaner | 0.5904 | warning | error_rate, memory, p95, p99 |
| ab-test-service | 0.488 | warning | memory, p95, p99 |
| address-service | 0.488 | warning | error_rate, p95, p99 |
| billing-adapter | 0.488 | warning | error_rate, memory, p95 |
| bot-service | 0.488 | warning | error_rate, p95, p99 |
| checkout-api | 0.488 | warning | error_rate, p95, p99 |
| clickstream-collector | 0.488 | warning | cpu, error_rate, p99 |
| gateway | 0.488 | warning | error_rate, p95, p99 |
| kyc-service | 0.488 | warning | error_rate, memory, p95 |
| label-printer | 0.488 | warning | error_rate, memory, p99 |
| map-tiles | 0.488 | warning | error_rate, memory, p95 |
| media-api | 0.488 | warning | error_rate, p95, p99 |
| metrics-forwarder | 0.488 | warning | error_rate, memory, p95 |
| pricing-service | 0.488 | warning | error_rate, p95, p99 |
| promo-service | 0.488 | warning | memory, p95, p99 |
| recommendation-api | 0.488 | warning | error_rate, p95, p99 |

Показаны 20 сервисов с наибольшим score из 68 со срабатываниями.

## Timeline

| время | событие | сервис | метрика |
| --- | --- | --- | --- |
| 2026-09-10 15:45:00Z | test period started | — | — |
| 2026-09-10 15:51:30Z | spike | export-service | error_rate |
| 2026-09-10 15:51:30Z | spike | export-service | p99 |
| 2026-09-10 15:52:00Z | spike | export-service | memory |
| 2026-09-10 15:52:00Z | spike | session-cleaner | memory |
| 2026-09-10 15:52:30Z | saturation | image-resizer | cpu |
| 2026-09-10 15:52:30Z | spike | image-resizer | cpu |
| 2026-09-10 15:52:30Z | spike | image-resizer | cpu_throttling |
| 2026-09-10 15:52:30Z | spike | feature-flags | error_rate |
| 2026-09-10 15:52:30Z | spike | image-resizer | error_rate |
| 2026-09-10 15:52:30Z | spike | feature-flags | memory |
| 2026-09-10 15:52:30Z | spike | image-resizer | p95 |
| 2026-09-10 15:52:30Z | spike | image-resizer | p99 |
| 2026-09-10 15:53:00Z | saturation | image-resizer | cpu_throttling |
| 2026-09-10 15:53:00Z | spike | session-cleaner | error_rate |
| 2026-09-10 15:53:00Z | spike | export-service | p95 |
| 2026-09-10 15:53:00Z | spike | session-cleaner | p95 |
| 2026-09-10 15:53:00Z | spike | session-cleaner | p99 |
| 2026-09-10 15:53:30Z | spike | order-service | error_rate |
| 2026-09-10 15:53:30Z | spike | order-service | p95 |
| 2026-09-10 15:53:30Z | spike | feature-flags | p99 |
| 2026-09-10 15:53:30Z | spike | order-service | p99 |
| 2026-09-10 15:58:30Z | spike | order-service | error_rate |
| 2026-09-10 15:58:30Z | spike | order-service | memory |
| 2026-09-10 15:58:30Z | spike | feature-flags | p95 |
| 2026-09-10 15:58:30Z | spike | feature-flags | p99 |
| 2026-09-10 15:59:00Z | saturation | order-service | cpu |
| 2026-09-10 15:59:00Z | spike | export-service | memory |
| 2026-09-10 15:59:00Z | spike | order-service | p95 |
| 2026-09-10 15:59:00Z | spike | order-service | p99 |
| 2026-09-10 15:59:30Z | spike | image-resizer | memory |
| 2026-09-10 16:02:00Z | sla_violation | order-service | error_rate |
| 2026-09-10 16:02:00Z | sla_violation | order-service | p95 |
| 2026-09-10 16:02:30Z | sla_violation | order-service | p99 |
| 2026-09-10 16:03:00Z | counter_increase | order-service | pod_restarts |
| 2026-09-10 16:06:00Z | test period ended | — | — |

Показаны 36 из 216 событий: остальные относятся к сервисам вне фокуса анализа.

## Root cause analysis

- Гипотеза (possible), order-service: На целевом сервисе наблюдаются нарушения SLA по задержке и всплески p95/p99; деградация может быть следствием насыщения CPU или внутренних очередей.
  Evidence: finding:1, finding:2, metric:order-service:p95, metric:order-service:p99, finding:8, metric:order-service:cpu, metric:order-service:cpu_throttling

  Механизм: latency_regression. Причинная связь не установлена.

  Проверенные наблюдения:

| evidence_id | метрика | максимум | пик | предел | первое | вид | % |
| --- | --- | --- | --- | --- | --- | --- | --- |
| finding:1 | p95 | — | 2466 | 500 | 2026-09-10 16:02:00Z | — | — |
| finding:2 | p99 | — | 4747 | 1200 | 2026-09-10 16:02:30Z | — | — |

  Противоречащие данные: 

  Следующая проверка: Сравнить задержку на устойчивом плато с предыдущим прогоном и проверить корреляцию с CPU, throttling и очередями.

- Гипотеза (possible), order-service: Есть нарушения SLA по error_rate и всплески ошибок; возможны ошибки обработки или downstream-зависимостей.
  Evidence: finding:0, finding:50, finding:51, metric:order-service:error_rate, metric:order-service:http_5xx

  Механизм: error_increase. Причинная связь не установлена.

  Проверенные наблюдения:

| evidence_id | метрика | максимум | пик | предел | первое | вид | % |
| --- | --- | --- | --- | --- | --- | --- | --- |
| finding:0 | error_rate | — | 0.03695 | 0.01 | 2026-09-10 16:02:00Z | — | — |
| finding:50 | error_rate | — | — | — | 2026-09-10 15:53:30Z | spike | — |
| finding:51 | error_rate | — | — | — | 2026-09-10 15:58:30Z | spike | — |

  Противоречащие данные: 

  Следующая проверка: Проверить коды ошибок, логи и downstream-вызовы на той же ступени нагрузки.

- Гипотеза (possible), order-service: CPU упирается в диагностический порог; возможна нехватка CPU при целевой нагрузке.
  Evidence: finding:8, metric:order-service:cpu, metric:order-service:cpu_throttling

  Механизм: cpu_saturation. Причинная связь не установлена.

  Проверенные наблюдения:

| evidence_id | метрика | максимум | пик | предел | первое | вид | % |
| --- | --- | --- | --- | --- | --- | --- | --- |
| finding:8 | cpu | — | 1 | 0.9 | 2026-09-10 15:59:00Z | saturation | — |

  Противоречащие данные: 

  Следующая проверка: Проверить CPU limits/requests, throttling и поведение на устойчивом плато; убедиться, что ряд не неполный.

- Гипотеза (possible), order-service: Зафиксирован рост счётчика рестартов подов; возможен сбой или потеря готовности.
  Evidence: finding:213, metric:order-service:pod_restarts

  Механизм: restarts. Причинная связь не установлена.

  Проверенные наблюдения:

| evidence_id | метрика | максимум | пик | предел | первое | вид | % |
| --- | --- | --- | --- | --- | --- | --- | --- |
| finding:213 | pod_restarts | — | — | — | 2026-09-10 16:03:00Z | counter_increase | — |

  Противоречащие данные: 

  Следующая проверка: Проверить события подов, readiness и причины рестартов.

```json
{
  "status": "HYPOTHESES",
  "accepted": 4,
  "rejected": [
    {
      "index": 4,
      "reason": "invalid evidence references"
    }
  ],
  "ignored_references": [],
  "note": "проверены наблюдения; причинная связь требует независимой проверки"
}
```

## Evidence

**Ряды метрик (60)**

| evidence_id | сервис | метрика | единица | медиана | p95 | максимум | точек | источник | качество |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| metric:order-service:cpu | order-service | cpu | ratio | 0.8222 | 1 | 1 | 41 | prometheus | негодных точек: 1 |
| metric:order-service:cpu_throttling | order-service | cpu_throttling | ratio | 0 | 0.04964 | 0.1817 | 42 | prometheus | — |
| metric:order-service:error_rate | order-service | error_rate | ratio | 0.001814 | 0.02904 | 0.03695 | 42 | prometheus | — |
| metric:order-service:http_4xx | order-service | http_4xx | requests/s | 13.87 | 16.93 | 17.18 | 42 | prometheus | — |
| metric:order-service:http_5xx | order-service | http_5xx | requests/s | 3.133 | 61.2 | 79.02 | 42 | prometheus | — |
| metric:order-service:memory | order-service | memory | ratio | 0.4742 | 0.5133 | 0.5155 | 42 | prometheus | — |
| metric:order-service:network | order-service | network | bytes/s | 4.151e+06 | 5.078e+06 | 5.181e+06 | 42 | prometheus | — |
| metric:order-service:p95 | order-service | p95 | ms | 219 | 1178 | 2466 | 42 | prometheus | — |
| metric:order-service:p99 | order-service | p99 | ms | 284.2 | 1940 | 4747 | 42 | prometheus | — |
| metric:order-service:pod_restarts | order-service | pod_restarts | count | 0 | 1 | 1 | 42 | prometheus | — |
| metric:order-service:replicas | order-service | replicas | count | 4 | 4 | 4 | 42 | prometheus | — |
| metric:order-service:rps | order-service | rps | requests/s | 1739 | 2115 | 2145 | 42 | prometheus | — |
| metric:export-service:cpu | export-service | cpu | ratio | 0.4 | 0.4667 | 0.4667 | 42 | prometheus | — |
| metric:export-service:cpu_throttling | export-service | cpu_throttling | ratio | 0 | 0 | 0 | 42 | prometheus | — |
| metric:export-service:error_rate | export-service | error_rate | ratio | 0.00165 | 0.003391 | 0.00346 | 42 | prometheus | — |
| metric:export-service:http_4xx | export-service | http_4xx | requests/s | 0.06667 | 0.1 | 0.1333 | 42 | prometheus | — |
| metric:export-service:http_5xx | export-service | http_5xx | requests/s | 0.03333 | 0.06667 | 0.06667 | 42 | prometheus | — |
| metric:export-service:memory | export-service | memory | ratio | 0.4451 | 0.4532 | 0.4545 | 42 | prometheus | — |
| metric:export-service:network | export-service | network | bytes/s | 4.807e+04 | 5.106e+04 | 5.139e+04 | 42 | prometheus | — |
| metric:export-service:p95 | export-service | p95 | ms | 89.44 | 91.06 | 91.51 | 42 | prometheus | — |
| metric:export-service:p99 | export-service | p99 | ms | 98.76 | 99.33 | 99.66 | 42 | prometheus | — |
| metric:export-service:pod_restarts | export-service | pod_restarts | count | 0 | 0 | 0 | 42 | prometheus | — |
| metric:export-service:replicas | export-service | replicas | count | 2 | 2 | 2 | 42 | prometheus | — |
| metric:export-service:rps | export-service | rps | requests/s | 20.25 | 20.67 | 20.97 | 42 | prometheus | — |
| metric:feature-flags:cpu | feature-flags | cpu | ratio | 0.2667 | 0.2667 | 0.2667 | 42 | prometheus | — |
| metric:feature-flags:cpu_throttling | feature-flags | cpu_throttling | ratio | 0 | 0 | 0 | 42 | prometheus | — |
| metric:feature-flags:error_rate | feature-flags | error_rate | ratio | 0.002708 | 0.003925 | 0.003987 | 42 | prometheus | — |
| metric:feature-flags:http_4xx | feature-flags | http_4xx | requests/s | 0.2 | 0.2333 | 0.2333 | 42 | prometheus | — |
| metric:feature-flags:http_5xx | feature-flags | http_5xx | requests/s | 0.1333 | 0.2 | 0.2 | 42 | prometheus | — |
| metric:feature-flags:memory | feature-flags | memory | ratio | 0.3794 | 0.3844 | 0.3864 | 42 | prometheus | — |
| metric:feature-flags:network | feature-flags | network | bytes/s | 1.226e+05 | 1.27e+05 | 1.28e+05 | 42 | prometheus | — |
| metric:feature-flags:p95 | feature-flags | p95 | ms | 23.75 | 23.77 | 23.78 | 42 | prometheus | — |
| metric:feature-flags:p99 | feature-flags | p99 | ms | 24.75 | 24.77 | 24.78 | 42 | prometheus | — |
| metric:feature-flags:pod_restarts | feature-flags | pod_restarts | count | 0 | 0 | 0 | 42 | prometheus | — |
| metric:feature-flags:replicas | feature-flags | replicas | count | 2 | 2 | 2 | 42 | prometheus | — |
| metric:feature-flags:rps | feature-flags | rps | requests/s | 51.3 | 52.6 | 53.33 | 42 | prometheus | — |
| metric:image-resizer:cpu | image-resizer | cpu | ratio | 0.5 | 1 | 1 | 42 | prometheus | — |
| metric:image-resizer:cpu_throttling | image-resizer | cpu_throttling | ratio | 0 | 0.2365 | 0.2433 | 42 | prometheus | — |
| metric:image-resizer:error_rate | image-resizer | error_rate | ratio | 0.0007215 | 0.03064 | 0.03103 | 42 | prometheus | — |
| metric:image-resizer:http_4xx | image-resizer | http_4xx | requests/s | 0.2 | 0.2 | 0.2 | 42 | prometheus | — |
| metric:image-resizer:http_5xx | image-resizer | http_5xx | requests/s | 0.03333 | 1.432 | 1.467 | 42 | prometheus | — |
| metric:image-resizer:memory | image-resizer | memory | ratio | 0.5208 | 0.5301 | 0.5314 | 42 | prometheus | — |
| metric:image-resizer:network | image-resizer | network | bytes/s | 1.114e+05 | 1.176e+05 | 1.208e+05 | 42 | prometheus | — |
| metric:image-resizer:p95 | image-resizer | p95 | ms | 38.29 | 452.7 | 459.2 | 42 | prometheus | — |
| metric:image-resizer:p99 | image-resizer | p99 | ms | 47.85 | 853.8 | 869.6 | 42 | prometheus | — |
| metric:image-resizer:pod_restarts | image-resizer | pod_restarts | count | 0 | 0 | 0 | 42 | prometheus | — |
| metric:image-resizer:replicas | image-resizer | replicas | count | 1 | 1 | 1 | 42 | prometheus | — |
| metric:image-resizer:rps | image-resizer | rps | requests/s | 46.35 | 47.66 | 48.03 | 42 | prometheus | — |
| metric:session-cleaner:cpu | session-cleaner | cpu | ratio | 0.5 | 0.5 | 0.5 | 42 | prometheus | — |
| metric:session-cleaner:cpu_throttling | session-cleaner | cpu_throttling | ratio | 0 | 0 | 0 | 42 | prometheus | — |
| metric:session-cleaner:error_rate | session-cleaner | error_rate | ratio | 0.003115 | 0.006349 | 0.006557 | 42 | prometheus | — |
| metric:session-cleaner:http_4xx | session-cleaner | http_4xx | requests/s | 0.05 | 0.06667 | 0.06667 | 42 | prometheus | — |
| metric:session-cleaner:http_5xx | session-cleaner | http_5xx | requests/s | 0.03333 | 0.06667 | 0.06667 | 42 | prometheus | — |
| metric:session-cleaner:memory | session-cleaner | memory | ratio | 0.5798 | 0.5891 | 0.5917 | 42 | prometheus | — |
| metric:session-cleaner:network | session-cleaner | network | bytes/s | 2.558e+04 | 2.603e+04 | 2.614e+04 | 42 | prometheus | — |
| metric:session-cleaner:p95 | session-cleaner | p95 | ms | 41.36 | 42.79 | 43.14 | 42 | prometheus | — |
| metric:session-cleaner:p99 | session-cleaner | p99 | ms | 48.71 | 49.36 | 49.39 | 42 | prometheus | — |
| metric:session-cleaner:pod_restarts | session-cleaner | pod_restarts | count | 0 | 0 | 0 | 42 | prometheus | — |
| metric:session-cleaner:replicas | session-cleaner | replicas | count | 2 | 2 | 2 | 42 | prometheus | — |
| metric:session-cleaner:rps | session-cleaner | rps | requests/s | 10.63 | 11 | 11.13 | 42 | prometheus | — |

**Срабатывания порогов (34)**

| evidence_id | сервис | метрика | предел | пик | срабатываний | первое | последнее | источник |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| finding:0 | order-service | error_rate | 0.01 | 0.03695 | 8 | 2026-09-10 16:02:00Z | 2026-09-10 16:05:30Z | prometheus |
| finding:1 | order-service | p95 | 500 | 2466 | 8 | 2026-09-10 16:02:00Z | 2026-09-10 16:05:30Z | prometheus |
| finding:2 | order-service | p99 | 1200 | 4747 | 7 | 2026-09-10 16:02:30Z | 2026-09-10 16:05:30Z | prometheus |
| finding:8 | order-service | cpu | 0.9 | 1 | 13 | 2026-09-10 15:59:00Z | — | prometheus |
| finding:50 | order-service | error_rate | — | — | 1 | 2026-09-10 15:53:30Z | — | prometheus |
| finding:51 | order-service | error_rate | — | — | 3 | 2026-09-10 15:58:30Z | — | prometheus |
| finding:98 | order-service | memory | — | — | 1 | 2026-09-10 15:58:30Z | — | prometheus |
| finding:139 | order-service | p95 | — | — | 1 | 2026-09-10 15:53:30Z | — | prometheus |
| finding:140 | order-service | p95 | — | — | 2 | 2026-09-10 15:59:00Z | — | prometheus |
| finding:190 | order-service | p99 | — | — | 1 | 2026-09-10 15:53:30Z | — | prometheus |
| finding:191 | order-service | p99 | — | — | 2 | 2026-09-10 15:59:00Z | — | prometheus |
| finding:213 | order-service | pod_restarts | — | — | 1 | 2026-09-10 16:03:00Z | — | prometheus |
| finding:6 | image-resizer | cpu | 0.9 | 1 | 6 | 2026-09-10 15:52:30Z | — | prometheus |
| finding:7 | image-resizer | cpu | — | — | 3 | 2026-09-10 15:52:30Z | — | prometheus |
| finding:9 | image-resizer | cpu_throttling | 0.2 | 0.2433 | 5 | 2026-09-10 15:53:00Z | — | prometheus |
| finding:10 | image-resizer | cpu_throttling | — | — | 3 | 2026-09-10 15:52:30Z | — | prometheus |
| finding:32 | export-service | error_rate | — | — | 3 | 2026-09-10 15:51:30Z | — | prometheus |
| finding:33 | feature-flags | error_rate | — | — | 2 | 2026-09-10 15:52:30Z | — | prometheus |
| finding:37 | image-resizer | error_rate | — | — | 3 | 2026-09-10 15:52:30Z | — | prometheus |
| finding:64 | session-cleaner | error_rate | — | — | 1 | 2026-09-10 15:53:00Z | — | prometheus |
| finding:85 | export-service | memory | — | — | 2 | 2026-09-10 15:52:00Z | — | prometheus |
| finding:86 | export-service | memory | — | — | 1 | 2026-09-10 15:59:00Z | — | prometheus |
| finding:87 | feature-flags | memory | — | — | 1 | 2026-09-10 15:52:30Z | — | prometheus |
| finding:89 | image-resizer | memory | — | — | 1 | 2026-09-10 15:59:30Z | — | prometheus |
| finding:106 | session-cleaner | memory | — | — | 1 | 2026-09-10 15:52:00Z | — | prometheus |
| finding:124 | export-service | p95 | — | — | 1 | 2026-09-10 15:53:00Z | — | prometheus |
| finding:125 | feature-flags | p95 | — | — | 1 | 2026-09-10 15:58:30Z | — | prometheus |
| finding:132 | image-resizer | p95 | — | — | 3 | 2026-09-10 15:52:30Z | — | prometheus |
| finding:152 | session-cleaner | p95 | — | — | 1 | 2026-09-10 15:53:00Z | — | prometheus |
| finding:179 | export-service | p99 | — | — | 1 | 2026-09-10 15:51:30Z | — | prometheus |
| finding:180 | feature-flags | p99 | — | — | 1 | 2026-09-10 15:53:30Z | — | prometheus |
| finding:181 | feature-flags | p99 | — | — | 1 | 2026-09-10 15:58:30Z | — | prometheus |
| finding:186 | image-resizer | p99 | — | — | 3 | 2026-09-10 15:52:30Z | — | prometheus |
| finding:203 | session-cleaner | p99 | — | — | 1 | 2026-09-10 15:53:00Z | — | prometheus |

**Улики инструментов (11)**

```json
{
  "tool:chatcmpl-tool-95756650f7f2ae5b": {
    "success": true,
    "service": "order-service",
    "namespace": "nt01",
    "metrics": [],
    "total": 0,
    "shown": 0,
    "hints": [
      "duration_seconds"
    ],
    "note": "names and labels existed during the test period; units are not verified",
    "evidence_id": "tool:chatcmpl-tool-95756650f7f2ae5b"
  },
  "tool:chatcmpl-tool-9f88c295056fffd0": {
    "success": true,
    "service": "order-service",
    "namespace": "nt01",
    "metrics": [],
    "total": 0,
    "shown": 0,
    "hints": [
      "duration_seconds"
    ],
    "note": "names and labels existed during the test period; units are not verified",
    "evidence_id": "tool:chatcmpl-tool-9f88c295056fffd0"
  },
  "tool:chatcmpl-tool-a1189fd266ab9d51": {
    "success": true,
    "service": "order-service",
    "namespace": "nt01",
    "metrics": [],
    "total": 0,
    "shown": 0,
    "hints": [
      "duration_seconds"
    ],
    "note": "names and labels existed during the test period; units are not verified",
    "evidence_id": "tool:chatcmpl-tool-a1189fd266ab9d51"
  },
  "tool:chatcmpl-tool-a50cad86882f75d7": {
    "success": true,
    "service": "order-service",
    "namespace": "nt01",
    "metrics": [
      {
        "metric": "http_request_duration_seconds_bucket",
        "type": "",
        "help": "",
        "labels": [
          "le",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "http_request_duration_seconds_count",
        "type": "",
        "help": "",
        "labels": [
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "http_request_duration_seconds_sum",
        "type": "",
        "help": "",
        "labels": [
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_cpu_cfs_periods_total",
        "type": "",
        "help": "",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_cpu_cfs_throttled_periods_total",
        "type": "",
        "help": "",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_cpu_cfs_throttled_seconds_total",
        "type": "",
        "help": "",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_cpu_usage_seconds_total",
        "type": "",
        "help": "",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_memory_rss",
        "type": "gauge",
        "help": "Size of RSS in bytes",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_memory_usage_bytes",
        "type": "gauge",
        "help": "Current memory usage in bytes",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_memory_working_set_bytes",
        "type": "gauge",
        "help": "Current working set in bytes",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_network_receive_bytes_total",
        "type": "",
        "help": "",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_network_transmit_bytes_total",
        "type": "",
        "help": "",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "http_requests_in_flight",
        "type": "gauge",
        "help": "Number of HTTP requests currently in flight",
        "labels": [
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "http_requests_total",
        "type": "",
        "help": "",
        "labels": [
          "code",
          "method",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "kube_pod_container_resource_limits",
        "type": "gauge",
        "help": "The container resource limits",
        "labels": [
          "container",
          "namespace",
          "pod",
          "resource",
          "service",
          "unit"
        ]
      },
      {
        "metric": "kube_pod_container_resource_requests",
        "type": "gauge",
        "help": "The container resource requests",
        "labels": [
          "container",
          "namespace",
          "pod",
          "resource",
          "service",
          "unit"
        ]
      },
      {
        "metric": "kube_pod_container_status_restarts_total",
        "type": "",
        "help": "",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "kube_pod_info",
        "type": "gauge",
        "help": "Information about pod",
        "labels": [
          "created_by_kind",
          "namespace",
          "node",
          "pod",
          "service"
        ]
      },
      {
        "metric": "kube_pod_status_phase",
        "type": "gauge",
        "help": "The pods current phase",
        "labels": [
          "namespace",
          "phase",
          "pod",
          "service"
        ]
      },
      {
        "metric": "kube_pod_status_ready",
        "type": "gauge",
        "help": "Describes whether the pod is ready to serve requests",
        "labels": [
          "condition",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "up",
        "type": "gauge",
        "help": "1 if the target is reachable",
        "labels": [
          "instance",
          "job",
          "namespace",
          "pod",
          "service"
        ]
      }
    ],
    "total": 21,
    "shown": 21,
    "hints": [
      "duration_seconds"
    ],
    "note": "names and labels existed during the test period; units are not verified",
    "evidence_id": "tool:chatcmpl-tool-a50cad86882f75d7"
  },
  "tool:chatcmpl-tool-be351fcdec26b474": {
    "success": true,
    "service": "order-service",
    "namespace": "nt01",
    "metrics": [],
    "total": 0,
    "shown": 0,
    "hints": [
      "duration_seconds"
    ],
    "note": "names and labels existed during the test period; units are not verified",
    "evidence_id": "tool:chatcmpl-tool-be351fcdec26b474"
  },
  "tool:chatcmpl-tool-a8b1fea500abeaab": {
    "success": true,
    "service": "order-service",
    "namespace": "nt01",
    "metrics": [
      {
        "metric": "http_request_duration_seconds_bucket",
        "type": "",
        "help": "",
        "labels": [
          "le",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "http_request_duration_seconds_count",
        "type": "",
        "help": "",
        "labels": [
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "http_request_duration_seconds_sum",
        "type": "",
        "help": "",
        "labels": [
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_cpu_cfs_periods_total",
        "type": "",
        "help": "",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_cpu_cfs_throttled_periods_total",
        "type": "",
        "help": "",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_cpu_cfs_throttled_seconds_total",
        "type": "",
        "help": "",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_cpu_usage_seconds_total",
        "type": "",
        "help": "",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_memory_rss",
        "type": "gauge",
        "help": "Size of RSS in bytes",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_memory_usage_bytes",
        "type": "gauge",
        "help": "Current memory usage in bytes",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_memory_working_set_bytes",
        "type": "gauge",
        "help": "Current working set in bytes",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_network_receive_bytes_total",
        "type": "",
        "help": "",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_network_transmit_bytes_total",
        "type": "",
        "help": "",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "http_requests_in_flight",
        "type": "gauge",
        "help": "Number of HTTP requests currently in flight",
        "labels": [
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "http_requests_total",
        "type": "",
        "help": "",
        "labels": [
          "code",
          "method",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "kube_pod_container_resource_limits",
        "type": "gauge",
        "help": "The container resource limits",
        "labels": [
          "container",
          "namespace",
          "pod",
          "resource",
          "service",
          "unit"
        ]
      },
      {
        "metric": "kube_pod_container_resource_requests",
        "type": "gauge",
        "help": "The container resource requests",
        "labels": [
          "container",
          "namespace",
          "pod",
          "resource",
          "service",
          "unit"
        ]
      },
      {
        "metric": "kube_pod_container_status_restarts_total",
        "type": "",
        "help": "",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "kube_pod_info",
        "type": "gauge",
        "help": "Information about pod",
        "labels": [
          "created_by_kind",
          "namespace",
          "node",
          "pod",
          "service"
        ]
      },
      {
        "metric": "kube_pod_status_phase",
        "type": "gauge",
        "help": "The pods current phase",
        "labels": [
          "namespace",
          "phase",
          "pod",
          "service"
        ]
      },
      {
        "metric": "kube_pod_status_ready",
        "type": "gauge",
        "help": "Describes whether the pod is ready to serve requests",
        "labels": [
          "condition",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "up",
        "type": "gauge",
        "help": "1 if the target is reachable",
        "labels": [
          "instance",
          "job",
          "namespace",
          "pod",
          "service"
        ]
      }
    ],
    "total": 21,
    "shown": 21,
    "hints": [
      "duration_seconds"
    ],
    "note": "names and labels existed during the test period; units are not verified",
    "evidence_id": "tool:chatcmpl-tool-a8b1fea500abeaab"
  },
  "tool:chatcmpl-tool-b3e5ed8ca80815b5": {
    "success": true,
    "service": "order-service",
    "namespace": "nt01",
    "metrics": [],
    "total": 0,
    "shown": 0,
    "hints": [
      "duration_seconds"
    ],
    "note": "names and labels existed during the test period; units are not verified",
    "evidence_id": "tool:chatcmpl-tool-b3e5ed8ca80815b5"
  },
  "tool:chatcmpl-tool-93414fc960e07e15": {
    "success": true,
    "service": "order-service",
    "namespace": "nt01",
    "metrics": [
      {
        "metric": "http_request_duration_seconds_bucket",
        "type": "",
        "help": "",
        "labels": [
          "le",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "http_request_duration_seconds_count",
        "type": "",
        "help": "",
        "labels": [
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "http_request_duration_seconds_sum",
        "type": "",
        "help": "",
        "labels": [
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_cpu_cfs_periods_total",
        "type": "",
        "help": "",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_cpu_cfs_throttled_periods_total",
        "type": "",
        "help": "",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_cpu_cfs_throttled_seconds_total",
        "type": "",
        "help": "",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_cpu_usage_seconds_total",
        "type": "",
        "help": "",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_memory_rss",
        "type": "gauge",
        "help": "Size of RSS in bytes",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_memory_usage_bytes",
        "type": "gauge",
        "help": "Current memory usage in bytes",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_memory_working_set_bytes",
        "type": "gauge",
        "help": "Current working set in bytes",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_network_receive_bytes_total",
        "type": "",
        "help": "",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_network_transmit_bytes_total",
        "type": "",
        "help": "",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "http_requests_in_flight",
        "type": "gauge",
        "help": "Number of HTTP requests currently in flight",
        "labels": [
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "http_requests_total",
        "type": "",
        "help": "",
        "labels": [
          "code",
          "method",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "kube_pod_container_resource_limits",
        "type": "gauge",
        "help": "The container resource limits",
        "labels": [
          "container",
          "namespace",
          "pod",
          "resource",
          "service",
          "unit"
        ]
      },
      {
        "metric": "kube_pod_container_resource_requests",
        "type": "gauge",
        "help": "The container resource requests",
        "labels": [
          "container",
          "namespace",
          "pod",
          "resource",
          "service",
          "unit"
        ]
      },
      {
        "metric": "kube_pod_container_status_restarts_total",
        "type": "",
        "help": "",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "kube_pod_info",
        "type": "gauge",
        "help": "Information about pod",
        "labels": [
          "created_by_kind",
          "namespace",
          "node",
          "pod",
          "service"
        ]
      },
      {
        "metric": "kube_pod_status_phase",
        "type": "gauge",
        "help": "The pods current phase",
        "labels": [
          "namespace",
          "phase",
          "pod",
          "service"
        ]
      },
      {
        "metric": "kube_pod_status_ready",
        "type": "gauge",
        "help": "Describes whether the pod is ready to serve requests",
        "labels": [
          "condition",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "up",
        "type": "gauge",
        "help": "1 if the target is reachable",
        "labels": [
          "instance",
          "job",
          "namespace",
          "pod",
          "service"
        ]
      }
    ],
    "total": 21,
    "shown": 21,
    "hints": [
      "duration_seconds"
    ],
    "note": "names and labels existed during the test period; units are not verified",
    "evidence_id": "tool:chatcmpl-tool-93414fc960e07e15"
  },
  "tool:chatcmpl-tool-96fc71ae6d2a9fbc": {
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
      "test_status": "completed"
    },
    "missing_parameters": [],
    "evidence_id": "tool:chatcmpl-tool-96fc71ae6d2a9fbc"
  },
  "tool:chatcmpl-tool-b27bacd393ca554d": {
    "success": true,
    "service": "order-service",
    "namespace": "nt01",
    "metrics": [
      {
        "metric": "http_request_duration_seconds_bucket",
        "type": "",
        "help": "",
        "labels": [
          "le",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "http_request_duration_seconds_count",
        "type": "",
        "help": "",
        "labels": [
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "http_request_duration_seconds_sum",
        "type": "",
        "help": "",
        "labels": [
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_cpu_cfs_periods_total",
        "type": "",
        "help": "",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_cpu_cfs_throttled_periods_total",
        "type": "",
        "help": "",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_cpu_cfs_throttled_seconds_total",
        "type": "",
        "help": "",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_cpu_usage_seconds_total",
        "type": "",
        "help": "",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_memory_rss",
        "type": "gauge",
        "help": "Size of RSS in bytes",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_memory_usage_bytes",
        "type": "gauge",
        "help": "Current memory usage in bytes",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_memory_working_set_bytes",
        "type": "gauge",
        "help": "Current working set in bytes",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_network_receive_bytes_total",
        "type": "",
        "help": "",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "container_network_transmit_bytes_total",
        "type": "",
        "help": "",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "http_requests_in_flight",
        "type": "gauge",
        "help": "Number of HTTP requests currently in flight",
        "labels": [
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "http_requests_total",
        "type": "",
        "help": "",
        "labels": [
          "code",
          "method",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "kube_pod_container_resource_limits",
        "type": "gauge",
        "help": "The container resource limits",
        "labels": [
          "container",
          "namespace",
          "pod",
          "resource",
          "service",
          "unit"
        ]
      },
      {
        "metric": "kube_pod_container_resource_requests",
        "type": "gauge",
        "help": "The container resource requests",
        "labels": [
          "container",
          "namespace",
          "pod",
          "resource",
          "service",
          "unit"
        ]
      },
      {
        "metric": "kube_pod_container_status_restarts_total",
        "type": "",
        "help": "",
        "labels": [
          "container",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "kube_pod_info",
        "type": "gauge",
        "help": "Information about pod",
        "labels": [
          "created_by_kind",
          "namespace",
          "node",
          "pod",
          "service"
        ]
      },
      {
        "metric": "kube_pod_status_phase",
        "type": "gauge",
        "help": "The pods current phase",
        "labels": [
          "namespace",
          "phase",
          "pod",
          "service"
        ]
      },
      {
        "metric": "kube_pod_status_ready",
        "type": "gauge",
        "help": "Describes whether the pod is ready to serve requests",
        "labels": [
          "condition",
          "namespace",
          "pod",
          "service"
        ]
      },
      {
        "metric": "up",
        "type": "gauge",
        "help": "1 if the target is reachable",
        "labels": [
          "instance",
          "job",
          "namespace",
          "pod",
          "service"
        ]
      }
    ],
    "total": 21,
    "shown": 21,
    "hints": [
      "duration_seconds"
    ],
    "note": "names and labels existed during the test period; units are not verified",
    "evidence_id": "tool:chatcmpl-tool-b27bacd393ca554d"
  },
  "tool:chatcmpl-tool-b80c8f0f6e438e9f": {
    "success": true,
    "text": "по запросу 'NT-ARCH order-service архитектура и зависимости checkout' страниц не найдено",
    "truncated": false,
    "note": "context text is not verified metric evidence",
    "evidence_id": "tool:chatcmpl-tool-b80c8f0f6e438e9f"
  }
}
```

## Baseline comparison

| сервис | метрика | baseline | текущее | разница | % |
| --- | --- | --- | --- | --- | --- |
| export-service | cpu | 0.4 | 0.4 | 0 | 0 |
| export-service | cpu_throttling | 0 | 0 | 0 | — |
| export-service | error_rate | 0.001656 | 0.00165 | -5.46e-06 | -0.3298 |
| export-service | http_4xx | 0.06667 | 0.06667 | 0 | 0 |
| export-service | http_5xx | 0.03333 | 0.03333 | 0 | 0 |
| export-service | memory | 0.4467 | 0.4451 | -0.001635 | -0.3661 |
| export-service | network | 4.804e+04 | 4.807e+04 | 27.88 | 0.05804 |
| export-service | p95 | 89.63 | 89.44 | -0.186 | -0.2076 |
| export-service | p99 | 98.79 | 98.76 | -0.02801 | -0.02835 |
| export-service | pod_restarts | 0 | 0 | 0 | — |
| export-service | replicas | 2 | 2 | 0 | 0 |
| export-service | rps | 20.07 | 20.25 | 0.1833 | 0.9136 |
| feature-flags | cpu | 0.2667 | 0.2667 | 0 | 0 |
| feature-flags | cpu_throttling | 0 | 0 | 0 | — |
| feature-flags | error_rate | 0.002732 | 0.002708 | -2.404e-05 | -0.8798 |
| feature-flags | http_4xx | 0.2 | 0.2 | 0 | 0 |
| feature-flags | http_5xx | 0.1333 | 0.1333 | 0 | 0 |
| feature-flags | memory | 0.3793 | 0.3794 | 7.936e-05 | 0.02092 |
| feature-flags | network | 1.224e+05 | 1.226e+05 | 193.4 | 0.1579 |
| feature-flags | p95 | 23.75 | 23.75 | 0 | 0 |
| feature-flags | p99 | 24.75 | 24.75 | 0 | 0 |
| feature-flags | pod_restarts | 0 | 0 | 0 | — |
| feature-flags | replicas | 2 | 2 | 0 | 0 |
| feature-flags | rps | 51.1 | 51.3 | 0.2 | 0.3914 |
| image-resizer | cpu | 0.4667 | 0.5 | 0.03333 | 7.143 |
| image-resizer | cpu_throttling | 0 | 0 | 0 | — |
| image-resizer | error_rate | 0.0007174 | 0.0007215 | 4.141e-06 | 0.5772 |
| image-resizer | http_4xx | 0.2 | 0.2 | 0 | 0 |
| image-resizer | http_5xx | 0.03333 | 0.03333 | 0 | 0 |
| image-resizer | memory | 0.522 | 0.5208 | -0.001202 | -0.2302 |
| image-resizer | network | 1.109e+05 | 1.114e+05 | 448.8 | 0.4047 |
| image-resizer | p95 | 37.74 | 38.29 | 0.5472 | 1.45 |
| image-resizer | p99 | 47.74 | 47.85 | 0.1087 | 0.2277 |
| image-resizer | pod_restarts | 0 | 0 | 0 | — |
| image-resizer | replicas | 1 | 1 | 0 | 0 |
| image-resizer | rps | 46.17 | 46.35 | 0.1833 | 0.3971 |
| order-service | cpu | 0.3333 | 0.8222 | 0.4889 | 146.7 |
| order-service | cpu_throttling | 0 | 0 | 0 | — |
| order-service | error_rate | 0.001742 | 0.001814 | 7.226e-05 | 4.148 |
| order-service | http_4xx | 3.4 | 13.87 | 10.47 | 307.8 |
| order-service | http_5xx | 0.7333 | 3.133 | 2.4 | 327.3 |
| order-service | memory | 0.3994 | 0.4742 | 0.07481 | 18.73 |
| order-service | network | 1.021e+06 | 4.151e+06 | 3.13e+06 | 306.7 |
| order-service | p95 | 117.6 | 219 | 101.5 | 86.29 |
| order-service | p99 | 146.9 | 284.2 | 137.4 | 93.52 |
| order-service | pod_restarts | 0 | 0 | 0 | — |
| order-service | replicas | 4 | 4 | 0 | 0 |
| order-service | rps | 424.9 | 1739 | 1314 | 309.3 |
| session-cleaner | cpu | 0.5 | 0.5 | 0 | 0 |
| session-cleaner | cpu_throttling | 0 | 0 | 0 | — |
| session-cleaner | error_rate | 0.003115 | 0.003115 | 3.023e-08 | 0.0009705 |
| session-cleaner | http_4xx | 0.03333 | 0.05 | 0.01667 | 50 |
| session-cleaner | http_5xx | 0.03333 | 0.03333 | 0 | 0 |
| session-cleaner | memory | 0.5811 | 0.5798 | -0.001364 | -0.2348 |
| session-cleaner | network | 2.553e+04 | 2.558e+04 | 49.93 | 0.1956 |
| session-cleaner | p95 | 41.51 | 41.36 | -0.1552 | -0.3739 |
| session-cleaner | p99 | 48.63 | 48.71 | 0.08428 | 0.1733 |
| session-cleaner | pod_restarts | 0 | 0 | 0 | — |
| session-cleaner | replicas | 2 | 2 | 0 | 0 |
| session-cleaner | rps | 10.67 | 10.63 | -0.03333 | -0.3125 |

Показаны 5 из 80 сервисов: остальные вне фокуса анализа. Агрегаты доступны в состоянии прогона; исходные точки перечитываются из источников.

## Previous test comparison

```json
{
  "test_id": "nt-run-2187",
  "status": "NOT_COMPARABLE",
  "matched_phases": [],
  "regressions": [],
  "limitations": [
    "не подтверждено совпадение environment_fingerprint",
    "не подтверждено совпадение scenario"
  ],
  "scenario_differs": {
    "current": "checkout_peak_2000rps",
    "previous": "checkout_peak_1900rps"
  }
}
```

regressions измерены на сопоставимых ступенях: это наблюдения одинаковой нагрузки в двух прогонах, а не установленная причина. Различия по всему периоду ниже описательные: прогоны держали там разную нагрузку.

| сервис | метрика | baseline | текущее | разница | % |
| --- | --- | --- | --- | --- | --- |
| order-service | cpu | 0.75 | 0.8222 | 0.07222 | 9.63 |
| order-service | cpu_throttling | 0 | 0 | 0 | — |
| order-service | error_rate | 0.001779 | 0.001814 | 3.535e-05 | 1.987 |
| order-service | http_4xx | 13.2 | 13.87 | 0.6667 | 5.051 |
| order-service | http_5xx | 2.917 | 3.133 | 0.2167 | 7.429 |
| order-service | memory | 0.4711 | 0.4742 | 0.003161 | 0.6711 |
| order-service | network | 3.853e+06 | 4.151e+06 | 2.986e+05 | 7.751 |
| order-service | p95 | 140.9 | 219 | 78.17 | 55.49 |
| order-service | p99 | 205.3 | 284.2 | 78.96 | 38.47 |
| order-service | pod_restarts | 0 | 0 | 0 | — |
| order-service | replicas | 4 | 4 | 0 | 0 |
| order-service | rps | 1655 | 1739 | 84.56 | 5.11 |

Показаны 1 из 29 сервисов прошлого прогона: остальные вне фокуса анализа. Агрегаты доступны в состоянии прогона; исходные точки перечитываются из источников.

## Recommendations

- Предложение LLM, требует проверки: Не делать вывод о регрессии без сравнения с предыдущим прогоном на сопоставимом устойчивом плато.

- Предложение LLM, требует проверки: Проверить order-service на устойчивом плато: задержку, error_rate, CPU, throttling и ошибки сервера.

- Предложение LLM, требует проверки: Проверить dependencies и downstream-вызовы order-service, так как dependencies в контексте не предоставлены.

- Предложение LLM, требует проверки: Проверить image-resizer как отдельный критический сервис; не считать его причиной order-service без подтверждённой зависимости.

- Предложение LLM, требует проверки: Повторить тест с полными метриками и явными SLA-порогами, если диагностические ряды неполные.

## Unverified assumptions

```json
[]
```

```json
{
  "stop_reason": ""
}
```

```json
[]
```

Precheck относится к данным завершённого теста. Текущие DNS/HTTP/pods не подтверждают их состояние в прошлом. Запуск и остановка НТ этим графом не выполнялись.

## Analysis policy

```json
{
  "version": "nt-analysis-v3",
  "step_seconds": 30,
  "settling_seconds": 30,
  "stable_seconds": 180,
  "plateau_tolerance": 0.1,
  "tool_approval": "generated",
  "query_map_sha256": "066dbabc9fe407f03818ad3de8b7c4e4bb5e4eeba8d835f70339a363e377f8bf",
  "sla_semantics": "maximum of service time series; explicit comparator, inclusive by default"
}
```

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
