# NORM-11 Discovery, не TAM со слайда

- id: NORM-11
- title: Проблема проверяется клиентом
- type: norm_card
- domain: discovery / access
- jurisdiction: global
- stage_fit: any
- product_class: any
- priority: P0
- status: canonical
- nodes: extract_profile, plan_next_question, coach
- retrieve_filter: questions + next_artifact
- tags: discovery
- ingest_date: 2026-08-23
- sources: логика customer discovery (Blank) — норма вопроса, не пересказ книги
- do_not_use_for: генерация TAM/SAM/SOM; принятие слайда рынка как evidence
- chunk_hints: кто платит; с кем говорили; standard of care

## summary_norm

Сначала: у кого болит; кто платит (не пользователь); какой текущий путь; с кем из клиентов / врачей / закупщиков говорили; что услышали; что изменили в продукте.

TAM без метода и источника — gap (NORM-01), не «рынок огромный».

Для life science: indication, setting, comparator / standard of care, почему клиника должна сменить путь.

Бизнес-модель в профиле — набор гипотез (кто платит, цена, канал), не холст, который агент рисует за команду.

## agent_must

- Отделить пользователя, бенефициара исхода и плательщика.
- Спросить конкретные разговоры: кто, роль, что сказано, что изменено.

## agent_must_not

- Считать TAM «чтобы заполнить поле».
- Принимать «врачи заинтересовались» без имён площадок и следующего шага площадки.

## example_question

С кем из закупщиков или главврачей вы говорили в последние 90 дней — и что они назвали причиной не менять текущий путь?

## example_coach

Не слайд рынка. Список из 5 разговоров: роль, площадка, цитата про боль / оплату, что изменили в продукте после этого.

## score_implication

Измерения 1 и 3. TAM без метода держит рынок на 0–2, даже если цифра «внушительная».
