# NORM-02 Claim со слайда = unverified

- id: NORM-02
- title: Claim со слайда без первички не факт
- authors: штаб MOS Agent Systems
- year: 2026
- type: norm_card
- domain: vc
- jurisdiction: global
- stage_fit: any
- product_class: any
- priority: P0
- status: canonical
- nodes: extract_profile, analyze_answer
- retrieve_filter: evidence_standard
- tags: claim, weak
- ingest_date: 2026-08-23
- sources: канон штаба
- do_not_use_for: принимать формулировки deck как evidence; вводить пятый profile-status unverified; «улучшать» claim стилистически
- chunk_hints: fact vs hypothesis vs weak; evidence_span

## summary_norm

Pitch, one-pager и устный питч — нарратив. Статус `fact` только если есть первичка и `evidence_span`.

В профиле ровно четыре статуса: `fact` | `hypothesis` | `missing` | `weak`.  
Семантика «unverified» (сказано как факт, опоры нет) хранится как `weak`. Пятого статуса в стейте нет.

«Уникальная технология», «рынок $X млрд», «клинически доказано» без дизайна и n — `weak`.

Профиль — карточка полей, не эссе. У поля: value, evidence_span, status, source.

## agent_must

- Снять claim в поле профиля с status=`weak`, если нет первички.
- Если основатель сам помечает «это гипотеза» — `hypothesis`.
- Сохранить исходную формулировку, не переписывать «научнее».

## agent_must_not

- Ставить status=`fact` по красивому слайду.
- Добавлять в стейт пятый статус `unverified`.
- Дописывать компаратор, p-value, класс риска «для ясности».

## example_question

На слайде «клинически доказано». Где протокол или отчёт: дизайн, n, endpoint, компаратор?

## example_coach

Не переписывать фразу. Принести протокол или таблицу результатов. До этого claim в профиле `weak`.

## score_implication

Измерение «доказательства» и «материалы»: набор weak-claim без первички — не выше 2.
