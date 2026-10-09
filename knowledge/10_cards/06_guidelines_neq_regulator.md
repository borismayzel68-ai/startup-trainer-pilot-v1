# NORM-06 Клинреки ≠ кнопка регулятора

- id: NORM-06
- title: Клинические рекомендации — отдельный трек
- type: norm_card
- domain: clinical / access
- jurisdiction: RU / global
- stage_fit: early_clinic / pivotal / market
- product_class: any
- priority: P0
- status: canonical
- nodes: plan_next_question, analyze_answer
- retrieve_filter: questions + evidence_standard
- tags: guidelines
- ingest_date: 2026-08-23
- sources: канон штаба; рубрикатор клинреков РФ — справочник, не кнопка
- do_not_use_for: обещать включение в рекомендации или ОМС как следствие подачи досье
- chunk_hints: professional society; evidence track; not a regulator button

## summary_norm

Попадание в клинические рекомендации — трек профессиональной среды и доказательств: дизайн, исходы, компаратор со standard of care, публикация, согласие сообщества.

Это не действие регулятора и не пункт личного кабинета регистратора.

## agent_must

- Если основатель говорит «зарегистрируем и нас включат» — пометить риск и gap access, не регуляторный факт.
- Спросить: какой comparator, какой endpoint важен клиницисту и плательщику, есть ли данные этого уровня.

## agent_must_not

- Обещать сроки включения в рекомендации.
- Путать клинреки, стандарты медпомощи, тариф ОМС и регистрационное удостоверение.

## example_question

Вы рассчитываете на клинические рекомендации. Какой сейчас standard of care и какой endpoint вашего исследования должен совпасть с тем, что смотрит профильное общество?

## example_coach

Не заявление в Минздрав «включите нас». Артефакт: таблица «наш endpoint vs endpoint в действующих рекомендациях / SoC» плюс уровень данных (NORM-14, NORM-15).

## score_implication

Слот access. Обещание «нас включат после РУ» без данных = weak / missing, не fact.
