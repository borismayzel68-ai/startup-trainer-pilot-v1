# NORM-09 Правила стопа

- id: NORM-09
- title: Основатель / marginal value / лимит
- type: norm_card
- domain: coaching
- jurisdiction: global
- stage_fit: any
- product_class: any
- priority: P0
- status: canonical
- nodes: route_intent, decide
- retrieve_filter: —
- tags: stop, max_turns
- ingest_date: 2026-08-23
- sources: канон штаба; ТЗ trainer
- do_not_use_for: держать сессию «чтобы выбрать все темы»; отсеивать основателя ради экономии токенов
- chunk_hints: founder stop; agent stop; max_turns=12

## summary_norm

Стоп, если верно любое:

1. Основатель сказал «хватит / готово / заканчиваем» — безусловно, не переспрашивать трижды.
2. Критические gap закрыты или явно помечены, ценность следующего вопроса низкая.
3. Достигнут `max_turns` (дефолт 12).

Цифровое время дешёвое: цель — качество пакета, не экономия ходов. Анкета из 12 формальных вопросов без ценности запрещена (NORM-03).

После стопа — только `summarize`. `stop_reason`: `founder` | `agent_low_value` | `max_turns`.

## agent_must

- Распознать stop-фразу в `route_intent` и сразу идти в summarize.
- В `decide` оценить, есть ли ещё критический gap; если нет — `agent_low_value`.
- Не продолжать граф после decide=stop.

## agent_must_not

- Игнорировать стоп основателя «чтобы добить регуляторику».
- Останавливать сессию на 3-м ходе только потому, что пакет «слабый» — слабый пакет как раз нужно структурировать.

## example_question

Не задаёт после стопа.

## example_coach

Не применимо. В отчёте указать stop_reason и список незакрытых дыр.

## score_implication

Стоп не обнуляет score. Фиксирует снимок на момент выхода.
