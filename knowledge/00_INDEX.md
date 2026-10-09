# Кабинет MVP — индекс P0

Слой: канон-карточки. Не корпус книг и не пакеты стартапов.  
Заморозка S0.5: 2026-08-23. Меняет набор только штаб.

Retrieve MVP: keyword по этим файлам. Drive и эмбеддинги — фаза 2.

Канонические фильтры узла (§7.3), не заменяются короткими тегами:

- plan_next_question → questions + domain + stage
- analyze_answer → evidence_standard + study_design
- coach → next_artifact + jurisdiction

Каждая карточка несёт поля §7.1. Источник historical/superseded — не в горячий retrieve без предупреждения.

| id | файл | узлы | фильтр §7.3 | доп. tags |
|---|---|---|---|---|
| NORM-01 | 10_cards/01_no_fact_is_gap.md | extract, analyze, score | evidence_standard | missing |
| NORM-02 | 10_cards/02_slide_claim_unverified.md | extract, analyze | evidence_standard | claim, weak |
| NORM-03 | 10_cards/03_one_question_max_value.md | plan, ask | questions + domain + stage | one_question |
| NORM-04 | 10_cards/04_coach_next_artifact.md | coach | next_artifact + jurisdiction | no_pitch |
| NORM-05 | 10_cards/05_regulator_neq_payer.md | plan, analyze, coach | questions / next_artifact + jurisdiction | regulator, payer |
| NORM-06 | 10_cards/06_guidelines_neq_regulator.md | plan, analyze | questions + evidence_standard | guidelines |
| NORM-07 | 10_cards/07_score_not_invest.md | update_readiness, summarize | — | score, disclaimer |
| NORM-08 | 10_cards/08_no_pitch_generation.md | coach, summarize | next_artifact | no_pitch |
| NORM-09 | 10_cards/09_stop_rules.md | route_intent, decide | — | stop, max_turns |
| NORM-10 | 10_cards/10_team_deal_selection.md | plan, score-5 | questions + domain + stage | team; вес Team = 0.12, не менять |
| NORM-11 | 10_cards/11_discovery_not_tam.md | plan, coach, extract | questions + next_artifact | discovery |
| NORM-12 | 10_cards/12_reg_path_map.md | plan, analyze | questions + domain + stage | regulatory |
