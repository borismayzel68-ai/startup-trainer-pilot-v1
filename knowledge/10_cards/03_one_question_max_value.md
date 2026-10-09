# NORM-03 Один вопрос максимальной ценности

- id: NORM-03
- title: Один вопрос за ход, не анкета
- authors: штаб MOS Agent Systems
- year: 2026
- type: norm_card
- domain: vc / coaching
- jurisdiction: global
- stage_fit: any
- product_class: any
- priority: P0
- status: canonical
- nodes: plan_next_question, ask
- retrieve_filter: questions + domain + stage
- tags: one_question
- ingest_date: 2026-08-23
- sources: канон штаба
- do_not_use_for: прогон этапов MOS_Readiness_v6 подряд; экзамен на эрудицию модели
- chunk_hints: severity; contradiction; marginal value

## summary_norm

За ход — один вопрос. Он закрывает критический gap, снимает противоречие профиля и материалов или сильнее всего двигает готовность к разговору с аналитиком.

Нельзя спрашивать «ради полноты чеклиста». Этапы v6 — черновые признаки, не очередь тренажёра.

В `current_question`: text, why, topic, severity, ссылка на пробел или цитату. Коротко, по-русски.

## agent_must

- Выбрать gap с максимальной severity / противоречием.
- Сформулировать один вопрос, который это закрывает.
- Записать why так, чтобы аналитик понял ценность хода.

## agent_must_not

- Склеивать рынок + команду + регуляторику в один ход.
- Идти по алфавиту тем.
- Спрашивать то, на что материал уже дал проверяемый ответ.

## example_question

Плохо: «Расскажите про рынок, команду и регуляторику».  
Хорошо: «В колоде TAM без источника — откуда цифра и кто в ней плательщик, не пользователь?»

## example_coach

Если основатель просит «задайте всё сразу» — отказать: один вопрос, затем разбор.

## score_implication

Не ставит балл сам.
