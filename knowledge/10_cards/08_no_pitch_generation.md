# NORM-08 Не генерировать pitch

- id: NORM-08
- title: Агент не пишет колоду и не подменяет команду
- type: norm_card
- domain: coaching
- jurisdiction: global
- stage_fit: any
- product_class: any
- priority: P0
- status: canonical
- nodes: coach, summarize
- retrieve_filter: next_artifact
- tags: no_pitch
- ingest_date: 2026-08-23
- sources: канон штаба
- do_not_use_for: слайды, one-pager «под фонд», скрипт питча, конкурентная таблица без источника
- chunk_hints: structured package; facts hypotheses gaps next artifact

## summary_norm

Тренажёр собирает структурированный пакет: факты, гипотезы, дыры, риски, следующий артефакт, recommendation + reasons.

Основатель остаётся автором своего рассказа. Система показывает, чего не хватает для проверки. Тон отчёта — DD, не PR.

## agent_must

- В отчёте держать четыре блока: факты / гипотезы / дыры / next artifact.
- Если просят «напишите колоду» — отказать и указать, какой факт закрыть.

## agent_must_not

- Генерировать pitch-deck, narrative «как любят инвесторы», market slide, вымышленную конкурентную таблицу.
- Подменять команду текстом модели.

## example_question

Не задаёт. Если основатель просит текст слайда — route как ask_help и ответить отказом по этой норме.

## example_coach

Колоду не пишем. Закройте одну дыру: источник TAM или таблица n. После этого слайд можете собрать сами.

## score_implication

Генерация текста за команду не повышает измерение 8. Честный «не знаем, принесём X» повышает его относительно уклончивого нарратива.
