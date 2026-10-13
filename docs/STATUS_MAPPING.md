# STATUS_MAPPING — единый словарь статусов (MOS-2)

Словарь: `NOT_SCREENED, SCREENING, ACTIVE, DD, IC, INVESTED, PASS, ON_HOLD`.
Реализация: `dealflow/status.py` (`map_zone`, `map_core`, `TRANSITIONS`).

**Правило:** статус не выдумывается. При неоднозначности — `NOT_SCREENED` + флаг ручной проверки (`needs_review`).

## 1. Зоны плана миграции dealflow

| Зона | Этап (текст) | Статус | Флаг |
|---|---|---|---|
| `00 — INBOX / НОВЫЕ` | — | NOT_SCREENED | нет |
| `10 — ACTIVE DEALFLOW / SCREENING & DD` | `SCREENING` | SCREENING | нет |
| `10 — ACTIVE DEALFLOW / SCREENING & DD` | `ACTIVE` | ACTIVE | нет |
| `10 — ACTIVE DEALFLOW / SCREENING & DD` | `DD` | DD | нет |
| `10 — ACTIVE DEALFLOW / SCREENING & DD` | иной текст | NOT_SCREENED | **да** |
| любая другая зона | — | NOT_SCREENED | **да** |

Прочие зоны плана миграции в репозитории не описаны (плана здесь нет, реальные данные не коммитятся),
поэтому перечислить их нельзя: до уточнения владельцем они попадают в «неизвестное» → флаг.

## 2. Поля CORE-дашборда

| Условие | Статус | Флаг |
|---|---|---|
| `IM-xxx` (vc_id) | INVESTED | нет |
| `NOT YET SCREENED` | NOT_SCREENED | нет |
| `ANALYZED` + `CONDITIONAL` + `MONITOR` | ON_HOLD | нет |
| `ANALYZED` + `CONDITIONAL` + `REQUEST_EVIDENCE` | DD | нет |
| `ANALYZED` + `CONDITIONAL` + (действия нет) | ACTIVE | нет |
| `ANALYZED` + `UNATTRACTIVE` + `MONITOR` | ON_HOLD | нет |
| `ANALYZED` + `UNATTRACTIVE` + `CLOSE` | PASS | нет |
| любая другая комбинация `ANALYZED` | NOT_SCREENED | **да** |
| иное значение | NOT_SCREENED | **да** |

Допущения по комбинациям (CONDITIONAL+MONITOR → ON_HOLD, CONDITIONAL+REQUEST_EVIDENCE → DD, CONDITIONAL без действия → ACTIVE)
в задаче прямо не заданы — требуют подтверждения менеджера/владельца.

## 3. Допустимые переходы

| Из | В |
|---|---|
| NOT_SCREENED | SCREENING, PASS, ON_HOLD |
| SCREENING | ACTIVE, PASS, ON_HOLD |
| ACTIVE | DD, PASS, ON_HOLD |
| DD | ACTIVE, IC, PASS, ON_HOLD |
| IC | DD, INVESTED, PASS, ON_HOLD |
| ON_HOLD | SCREENING, ACTIVE, DD, PASS |
| PASS | SCREENING |
| INVESTED | — (конечный) |
