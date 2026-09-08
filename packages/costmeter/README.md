# costmeter

[Документация Orbita](../../docs/README.md) · [Настройка стоимости](../../docs/CONFIGURATION.md) · [История версий](CHANGELOG.md)

Учёт стоимости вызовов LLM с разбивкой по кешу — для любого графа или цепочки
LangChain.

Пакет нормализует usage разных провайдеров, рассчитывает стоимость по заданному тарифу и показывает долю кеша. Он подключается как callback к уже существующему приложению LangChain.

Ниже `app` — ваш граф или цепочка, а `payload` — её вход:

```python
from costmeter import CostMeter

meter = CostMeter(provider="deepseek", model="deepseek-v4-flash")
result = app.invoke(payload, config={"callbacks": [meter]})
print(meter.report())
```

Пример формата отчёта; суммы иллюстративные, не текущий тариф:

```text
вызовов LLM: 5 | вход 9942 ток. (из кеша 7296, пересчитано 2646) | выход 1198 ток.
  cache hit rate:  73.4%  |  стоимость $0.000726  (без кеша было бы $0.001727)
  тариф: prices.toml (2026-08-29)
```

Модуль не знает ни про ноды, ни про состояние графа: он ловит `on_llm_end` и
копит четыре счётчика. Прикручивается к чужому коду одной строкой в `config`.

## Четыре статьи расхода

| Счётчик | Что это | Цена в таблице |
|---|---|---|
| `cache_hit` | вход, отданный из кеша провайдера | `cache_read` |
| `cache_miss` | вход, посчитанный заново | `input` |
| `cache_write` | вход, записанный в кеш | `cache_write` |
| `output` | генерация | `output` |

Третья статья существует не у всех: у Anthropic запись в кеш стоит **дороже**
обычного входа, у DeepSeek её нет вовсе. Записанные токены вычитаются из
`cache_miss`, иначе один и тот же вход считался бы дважды.

## Провайдеры

Провайдеры считают одно и то же по-разному. Где у кого лежат счётчики — таблица
правил в [`usage.py`](src/costmeter/usage.py):

| Провайдер | Вход из кеша | Пересчитано | Запись в кеш |
|---|---|---|---|
| DeepSeek | `prompt_cache_hit_tokens` | `prompt_cache_miss_tokens` | — |
| OpenAI | `prompt_tokens_details.cached_tokens` | `prompt_tokens` минус кеш | — |
| Anthropic | `cache_read_input_tokens` | `input_tokens` | `cache_creation_input_tokens` |
| фолбэк | `input_token_details.cache_read` | `input_tokens` минус кеш | `input_token_details.cache_creation` |

Источники дополняют друг друга, а не заменяют: провайдер может отдать общий
объём входа, но не разбивку по кешу, и наоборот. Последним подключается
нормализованный `usage_metadata` LangChain — он одинаков у всех, но теряет
вендорные детали.

**Добавить провайдера** — это строка в `RULES` и фикстура в
`tests/test_costmeter_usage.py`. Ветвлений в разборе нет.

## Тарифы

Цены живут в [`prices.toml`](src/costmeter/prices.toml), по паре (провайдер, модель), в
долларах за 1M токенов. Дата снятия лежит в самом файле: тарифы устаревают, и
это должно быть видно.

```toml
[prices.deepseek."deepseek-v4-flash"]
input = 0.44
cache_read = 0.014
cache_write = 0.0
output = 1.32
```

Числа в таблице — датированный снимок пакета, а не автоматически обновляемый прайс. Перед использованием бюджета сверьте цену с тарифом своего провайдера и при необходимости задайте `PRICE_*`. Числовой пример выше описывает формат конфигурации.

Модели нет в таблице — **предупреждение и нулевая стоимость**. Молчаливый
неверный счёт хуже отсутствующего: отсутствующий видно сразу.

> Таблица поставляется с одной заполненной моделью. Тариф, вписанный по памяти,
> хуже отсутствующего, поэтому остальные строки не выдуманы — сверьтесь
> с прайсом своего провайдера и допишите.

Переопределить, не трогая таблицу, — четыре переменные окружения. Пригодится
для стенда, корпоративной скидки или свежего прайса, до которого не дошли руки:

| Переменная | Статья |
|---|---|
| `PRICE_CACHE_HIT_PER_MTOK` | `cache_read` |
| `PRICE_CACHE_MISS_PER_MTOK` | `input` |
| `PRICE_CACHE_WRITE_PER_MTOK` | `cache_write` |
| `PRICE_OUTPUT_PER_MTOK` | `output` |

Можно и передать тариф прямо: `CostMeter(prices=Prices(input=..., ...))`.

## Лог по вызовам

`log_path` — JSONL, одна строка на вызов модели. Файл дописывается, поэтому
несколько прогонов ложатся в один лог и разбираются по `thread_id`.

```python
meter = CostMeter(provider="deepseek", model="deepseek-v4-flash",
                  log_path="calls.jsonl")
```

```json
{"at": "2026-08-28T10:24:18+00:00", "provider": "deepseek", "model": "deepseek-v4-flash",
 "cache_hit": 3456, "cache_miss": 228, "cache_write": 0, "output": 190, "calls": 1,
 "hit_rate": 93.8, "cost_usd": 0.00022104, "naive_cost_usd": 0.00056896, "thread_id": "t-1"}
```

Из такого лога график стоимости по ходам строится без ручной обработки:

```bash
jq -r '[.at, .cost_usd] | @tsv' calls.jsonl
```

## Бюджет

`budget_usd` — потолок расхода. Сам handler ничего не останавливает: он только
знает, что потолок пройден (`meter.over_budget`). Решение принимает тот, кто его
завёл. Пример остановки графа — `budget_gate` в
[`agent/routes.py`](../../src/agent/routes.py): ворота перед каждым обращением к модели,
и при превышении граф уходит в конец с сообщением вместо вызова.

## API

| | |
|---|---|
| `CostMeter(provider, model, prices, log_path, budget_usd)` | callback handler |
| `meter.usage` | суммарный `Usage` по всем вызовам |
| `meter.cost` / `meter.naive_cost` / `meter.saved` | деньги |
| `meter.calls` | список `Call` — по одному на вызов модели |
| `meter.report()` / `meter.as_dict()` | для человека / для машины |
| `meter.reset()` | обнулить |
| `Usage` | четыре счётчика плюс `calls`; складывается через `+` |
| `normalize(raw, normalized, provider)` | разбор ответа в `Usage` |
| `for_model(provider, model)` | тариф из таблицы плюс переопределения |

## Установка

```bash
python -m pip install -e ./packages/costmeter
```

Команда выполняется из корня репозитория Orbita. Runtime-зависимость пакета — `langchain-core`; `agent`, `langgraph` и `requests` ему не нужны. Для примера выше необходимо собственное приложение `app` и его вход `payload`. Полная установка Orbita через `requirements.txt` уже включает этот пакет.

## Релиз

Пакет собирается отдельно от агента и версионируется отдельно
([CHANGELOG.md](CHANGELOG.md)):

```bash
python -m pip install build twine
python -m build packages/costmeter
python -m twine check packages/costmeter/dist/*
```

Для загрузки на PyPI владелец репозитория должен явно задать GitHub Actions
variable `ENABLE_PYPI_PUBLISH=true` и настроить доверенного издателя PyPI.
По умолчанию загрузка отключена, в том числе в копиях репозитория для коллег.
Дальше — тег `costmeter-v0.1.0` и push: публикацию делает
[workflow](../../.github/workflows/publish.yml) через доверенную публикацию
PyPI, без токенов в репозитории. Загрузка руками, если нужно:
`python -m twine upload packages/costmeter/dist/*`.
