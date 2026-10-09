# NORM-14 Стандарты отчёта об исследовании

- id: NORM-14
- title: Тип исследования → какой стандарт смотреть
- type: norm_card
- domain: clinical
- jurisdiction: global
- stage_fit: preclinical / early_clinic / pivotal
- product_class: drug / biologic / device / ivd / samd
- priority: P0
- status: canonical
- nodes: analyze_answer, coach
- retrieve_filter: evidence_standard + study_design
- tags: consort, spirit, stard, tripod
- ingest_date: 2026-08-23
- sources: названия семейства EQUATOR — CONSORT 2025 (отчёт RCT), SPIRIT 2025 (протокол RCT), STARD 2015 (диагностическая точность), TRIPOD / TRIPOD+AI (прогностические и ML-модели). Не цитировать номера пунктов чеклиста, если полный текст не в корпусе.
- do_not_use_for: объявлять исследование «соответствует CONSORT»; ставить p-value; писать, что данные «публикуемы»
- chunk_hints: study_design routing; protocol vs results; diagnostic vs prediction

## summary_norm

Если основатель ссылается на «исследование» или «доказано», сначала определить дизайн, потом список того, чего не хватает для проверки.

Маршрутизатор:

- протокол планируемого RCT → смотреть полноту как SPIRIT (цели, endpoints, размер выборки, анализ);
- законченный RCT → как CONSORT (рандомизация, поток участников, primary endpoint, harms);
- диагностика (чувствительность/специфичность) → STARD (index test, reference standard, спектр пациентов);
- модель прогноза / ИИ → TRIPOD или TRIPOD+AI (когорта разработки, внешняя проверка, что именно предсказывают).

«Рандомизированное убедительное» без этих элементов — `weak`.

## agent_must

- Зафиксировать study_design отдельным полем.
- Спросить минимальный набор: дизайн, n (план/факт), endpoint, компаратор, ограничение.
- Для SaMD / digital health не принимать AUC на обучающей выборке как клиническое доказательство.

## agent_must_not

- Ставить статус `fact` по тезису «опубликовано».
- Выдумывать, что исследование «скорее всего double-blind».
- Проводить статистический разбор.

## example_question

Вы называете это клиническим доказательством. Это RCT, одноплечевое, диагностика или модель? Где n, primary endpoint и компаратор?

## example_coach

Не «оформите по CONSORT». Одна таблица: дизайн | n план/факт | endpoint | компаратор | что не собрали. Для ИИ-диагностики отдельно: была ли внешняя выборка.

## score_implication

Измерение 2. Нет дизайна и n — 0–1. Внутренние данные без компаратора — не выше 3.
