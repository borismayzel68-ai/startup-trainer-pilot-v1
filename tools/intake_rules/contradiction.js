'use strict';
/**
 * Правило противоречий для разбора текста проекта (intake). Чистый JS: без DOM, сети, зависимостей.
 * Refs #114. Авторская самопроверка, не приёмка.
 *
 * isContradiction(a, b) -> {verdict: "contradiction"|"clarify"|"none", reason}
 *
 * «contradiction» только если совпали ТРИ вещи: показатель, объект, этап/период.
 *   - показатель: лексикон (себестоимость, цена, аренда, вес...) или «число/N <кого считаем>»;
 *   - объект: что измеряется (датчик/упаковка/устройство...) либо считаемое существительное (клиника, пациент...);
 *   - этап: порядковые (первый/второй), «этап/пилот/фаза», год, «последний/следующий».
 * Если с одной стороны что-то названо, а с другой нет -> «clarify». Если названо и различается -> «none».
 * Допущение: если этап/объект не назван ни там, ни там, считаем их общими (текст про одно и то же «сейчас»).
 */

const MAX_TEXT_CHARS = 20000;   // предел для findContradictions (≈250 предложений, ≈31 тыс. пар)
const MAX_FRAGMENT_CHARS = 2000; // предел для одного фрагмента в isContradiction
const TOO_LONG_TEXT = 'Текст слишком длинный для автоматической проверки противоречий (больше ' +
  MAX_TEXT_CHARS + ' знаков). Разбейте его на части или проверьте вручную.';

const COUNTED = ['клиник', 'лаборатор', 'пациент', 'сотрудник', 'клиент', 'пользовател', 'врач', 'больниц', 'партнер', 'площадк'];
const OBJECTS = ['устройств', 'датчик', 'упаковк', 'прибор', 'продукт', 'изделие', 'платформ', 'сервис'];
const METRICS = [
  ['себестоим', 'cost'], ['розничн', 'price'], ['цен', 'price'], ['зарплат', 'salary'], ['аренд', 'rent'],
  ['вес', 'weight'], ['масс', 'weight'], ['чек', 'avg_check'], ['подписк', 'subscription'], ['выручк', 'revenue'],
  ['прибыл', 'profit'], ['маржинальн', 'margin'], ['затрат', 'expenses'], ['расход', 'expenses'], ['стоимост', 'generic'],
];
const SCOPE = [['перв', 'ord1'], ['втор', 'ord2'], ['трет', 'ord3'], ['четверт', 'ord4'], ['последн', 'last'],
  ['следующ', 'next'], ['этап', 'phase'], ['пилот', 'phase'], ['фаз', 'phase'],
  ['ежемесячн', 'per_month'], ['ежегодн', 'per_year'], ['ежеквартальн', 'per_quarter'], ['еженедельн', 'per_week'],
  ['ежедневн', 'per_day'], ['квартал', 'quarter'], ['январ', 'm01'], ['феврал', 'm02'], ['март', 'm03'], ['апрел', 'm04'],
  ['июн', 'm06'], ['июл', 'm07'], ['август', 'm08'], ['сентябр', 'm09'], ['октябр', 'm10'], ['ноябр', 'm11'], ['декабр', 'm12']];
const MODEL_WORDS = ['модел', 'верси', 'вариант'];
const UNIT_TOK = /^(тыс|млн|млрд|руб|тенге|долл|евро|usd|eur|кг|г|т|р)/;
const UNIT_KG = { кг: 1, г: 0.001, т: 1000 };
const MULT = [['тыс', 1e3], ['млн', 1e6], ['млрд', 1e9]];
const NEG = new Set(['не', 'нет', 'без', 'ни']);
const STOP = new Set(['и', 'в', 'на', 'с', 'о', 'об', 'по', 'к', 'у', 'а', 'но', 'что', 'это', 'как', 'для', 'из', 'от', 'или', 'же', 'ли', 'бы', 'еще', 'уже', 'также']);

function norm(s) { return String(s).toLowerCase().replace(/ё/g, 'е'); }
function stemIn(tok, list) { for (const e of list) { const st = Array.isArray(e) ? e[0] : e; if (tok.startsWith(st)) return Array.isArray(e) ? e[1] : st; } return null; }

function tokenize(text) {
  const t = norm(text).replace(/(\d)[  ](\d{3})(?!\d)/g, '$1$2').replace(/(\d),(\d)/g, '$1.$2');
  return t.match(/\d+(?:\.\d+)?|\p{L}+|%/gu) || [];
}

function analyze(text) {
  const toks = tokenize(text);
  const f = { toks, num: null, metrics: new Set(), objects: new Set(), nouns: new Set(), scope: new Set(), quant: [], neg: 0, content: new Set() };
  let hasCountWord = false;
  f.ctx = new Set();
  toks.forEach((tk, i) => {
    if (/^\d/.test(tk)) {
      if (/^(19|20)\d\d$/.test(tk)) { f.scope.add('y' + tk); return; }
      if (i > 0 && stemIn(toks[i - 1], MODEL_WORDS)) { f.scope.add('m:' + tk); return; } // «модель 2»: это имя, не значение
      if (!f.num) {
        let v = parseFloat(tk), j = i + 1, unit = '';
        for (const [m, k] of MULT) if (toks[j] && toks[j].startsWith(m)) { v *= k; j++; break; }
        if (toks[j] === '%') unit = '%';
        else if (toks[j] && toks[j] in UNIT_KG) { v *= UNIT_KG[toks[j]]; unit = 'kg'; }
        else if (toks[j] && /^(руб|р$|тенге|usd|eur|долл|евро)/.test(toks[j])) unit = 'money';
        f.num = { v, unit, i };
      }
      return;
    }
    if (/^(числ|количеств)/.test(tk)) hasCountWord = true;
    const sc = stemIn(tk, SCOPE); if (sc) f.scope.add(sc);
    if (stemIn(tk, MODEL_WORDS) && toks[i + 1] && !/^\d/.test(toks[i + 1]) && toks[i + 1].length <= 2) f.scope.add('m:' + toks[i + 1]); // «модель А»
    const cn = stemIn(tk, COUNTED); if (cn) { f.nouns.add(cn); }
    const ob = stemIn(tk, OBJECTS); if (ob) { f.objects.add(ob); f.nouns.add(ob); }
    const mt = stemIn(tk, METRICS); if (mt) f.metrics.add(mt);
    if (/^(один|одн)/.test(tk) || tk.startsWith('единственн')) f.quant.push({ q: 'one', at: i });
    else if (/^нескольк/.test(tk) || tk.startsWith('многих')) f.quant.push({ q: 'many', at: i });
    if (NEG.has(tk)) f.neg++; else if (!STOP.has(tk) && tk.length > 2 && !/^\d/.test(tk)) { f.content.add(tk.slice(0, 5)); if (!UNIT_TOK.test(tk) && !stemIn(tk, MODEL_WORDS)) f.ctx.add(tk.slice(0, 5)); }
  });
  // что считается: существительное сразу после числа или любое считаемое при «число/количество»
  if (f.num) {
    let counted = null;
    for (let j = f.num.i + 1; j <= f.num.i + 3 && j < toks.length; j++) { const c = stemIn(toks[j], COUNTED); if (c) { counted = c; break; } }
    if (!counted && hasCountWord) for (const n of f.nouns) if (COUNTED.includes(n)) { counted = n; break; }
    if (counted) { f.metrics = new Set(['count:' + counted]); f.objects = new Set(); }
  }
  // количественные слова привязываем к ближайшему считаемому существительному справа
  f.quant = f.quant.map(x => { for (let j = x.at + 1; j <= x.at + 2 && j < toks.length; j++) { const c = stemIn(toks[j], COUNTED); if (c) return { q: x.q, noun: c }; } return null; }).filter(Boolean);
  return f;
}

const setEq = (a, b) => a.size === b.size && [...a].every(x => b.has(x));
const inter = (a, b) => [...a].filter(x => b.has(x));
const res = (verdict, reason) => ({ verdict, reason });

function cmpScope(a, b) { // same | one | diff
  if (a.size === 0 && b.size === 0) return 'same';
  if (a.size === 0 || b.size === 0) return 'one';
  return setEq(a, b) ? 'same' : 'diff';
}

// Показатель не назван ни там, ни там: сравниваем окружение (слова вокруг числа) и этап.
function compareUnnamed(a, b) {
  const sc = cmpScope(a.scope, b.scope);
  if (sc === 'diff') return res('none', 'Разный этап/период/модель.');
  const shared = inter(a.ctx, b.ctx).length, uni = new Set([...a.ctx, ...b.ctx]).size, mn = Math.min(a.ctx.size, b.ctx.size);
  const ratio = sc === 'same' ? shared / (uni || 1) : shared / (mn || 1);
  if (mn < 2 || ratio < 0.75) {
    return inter(a.nouns, b.nouns).length ? res('clarify', 'Числа разные, но показатель не назван: уточните, что именно измеряется.') : res('none', 'Разные числа без общего показателя и объекта.');
  }
  if (sc === 'one') return res('clarify', 'Этап или период назван только в одном из утверждений: уточните.');
  if (a.num.unit && b.num.unit && a.num.unit !== b.num.unit) return res('clarify', 'Разные единицы измерения: приведите к одной.');
  if (a.num.v === b.num.v) return res('none', 'Значения совпадают.');
  return res('contradiction', 'Одно и то же утверждение (окружение совпадает, этап не различается), но разные значения: ' + a.num.v + ' и ' + b.num.v + '.');
}

function compareNumeric(a, b) {
  const ma = a.metrics, mb = b.metrics;
  if (ma.size === 0 && mb.size === 0) return compareUnnamed(a, b);
  if (ma.size === 0 || mb.size === 0) {
    return inter(a.nouns, b.nouns).length ? res('clarify', 'Числа разные, но показатель не назван: уточните, что именно измеряется.') : res('none', 'Разные числа без общего показателя и объекта.');
  }
  if (inter(ma, mb).length === 0) {
    if (ma.has('generic') || mb.has('generic')) return res('clarify', 'Слово «стоимость» может означать и цену, и себестоимость: уточните показатель.');
    return res('none', 'Разные показатели (' + [...ma] + ' и ' + [...mb] + ').');
  }
  if (!setEq(ma, mb)) return res('clarify', 'Показатели совпадают лишь частично: уточните.');
  if (![...ma][0].startsWith('count:')) {
    if (a.objects.size && b.objects.size && !setEq(a.objects, b.objects)) return res('none', 'Разные объекты: ' + [...a.objects] + ' и ' + [...b.objects] + '.');
    if (a.objects.size !== b.objects.size) return res('clarify', 'Объект назван только в одном из утверждений: уточните.');
  }
  const sc = cmpScope(a.scope, b.scope);
  if (sc === 'diff') return res('none', 'Разный этап/период/порядковый номер.');
  if (sc === 'one') return res('clarify', 'Этап или период назван только в одном из утверждений: уточните.');
  if (a.num.unit && b.num.unit && a.num.unit !== b.num.unit) return res('clarify', 'Разные единицы измерения: приведите к одной.');
  if (a.num.v === b.num.v) return res('none', 'Значения совпадают.');
  return res('contradiction', 'Один показатель, один объект и этап, но разные значения: ' + a.num.v + ' и ' + b.num.v + '.');
}

function compareQuant(a, b) {
  for (const qa of a.quant) for (const qb of b.quant) {
    if (qa.noun !== qb.noun || qa.q === qb.q) continue;
    const sc = cmpScope(a.scope, b.scope);
    if (sc === 'diff') return res('none', 'Разные этапы: «один» и «несколько» относятся к разным стадиям.');
    if (sc === 'one') return res('clarify', 'Этап назван только в одном из утверждений: «один» и «несколько» могут относиться к разным стадиям.');
    return res('contradiction', 'Один и тот же объект (' + qa.noun + ') назван и единственным, и множественным на одном этапе.');
  }
  return null;
}

function comparePolarity(a, b) {
  if (a.content.size < 2 || b.content.size < 2 || (a.neg > 0) === (b.neg > 0)) return null;
  const i = inter(a.content, b.content).length, u = new Set([...a.content, ...b.content]).size;
  return i / u >= 0.75 ? res('contradiction', 'Одно и то же утверждение с отрицанием и без него.') : null;
}

function compareFacts(a, b) {
  if (a.num && b.num) return compareNumeric(a, b);
  return compareQuant(a, b) || comparePolarity(a, b) || res('none', 'Явного противоречия не найдено.');
}

function isContradiction(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string') return res('none', 'Нужны два текстовых утверждения.');
  if (a.length > MAX_FRAGMENT_CHARS || b.length > MAX_FRAGMENT_CHARS) return res('clarify', 'Фрагмент длиннее ' + MAX_FRAGMENT_CHARS + ' знаков: разбейте его на отдельные утверждения.');
  return compareFacts(analyze(a), analyze(b));
}

function splitSentences(text) { return String(text).split(/(?<=[.!?;])\s+|\n+/).map(s => s.trim()).filter(s => s.length > 3); }

/** Все пары предложений, где вердикт не «none». Длинный текст -> понятная строка, без расчёта. */
function findContradictions(text) {
  if (String(text).length > MAX_TEXT_CHARS) return { error: TOO_LONG_TEXT, pairs: [] };
  const s = splitSentences(text), facts = s.map(analyze), pairs = [];
  for (let i = 0; i < s.length; i++) for (let j = i + 1; j < s.length; j++) {
    const r = compareFacts(facts[i], facts[j]);
    if (r.verdict !== 'none') pairs.push({ a: s[i], b: s[j], verdict: r.verdict, reason: r.reason });
  }
  return { error: null, pairs };
}

// --- refine: фильтр поверх старого разбора -------------------------------------------------------
// Старые правила главные. Модуль проверяет только две их ветки (ложные K01/K02/K03/K08/K12):
//   «numeric»  — разные числа при одном слове;  «one_many» — один ↔ несколько.
// Любая другая причина (oldWhy) проходит без изменений: «contradiction».
// Ветка определяется по тексту oldWhy (регистр не важен) либо по явному коду 'numeric' / 'one_many'.
function classifyOldWhy(oldWhy) {
  const w = norm(oldWhy == null ? '' : (typeof oldWhy === 'object' ? (oldWhy.kind || oldWhy.why || '') : oldWhy));
  if (/нескольк|one.?many|several|множествен|единственн/.test(w)) return 'one_many';
  if (/числ|number|numeric/.test(w)) return 'numeric';
  return null;
}

function refine(a, b, oldWhy) {
  const pass = (why) => res('contradiction', why);
  const kind = classifyOldWhy(oldWhy);
  if (!kind) return pass('Смысловое правило прежнего разбора: оставлено без изменений.');
  if (typeof a !== 'string' || typeof b !== 'string') return pass('Нет двух текстовых утверждений: оставлено решение прежнего правила.');
  if (a.length > MAX_FRAGMENT_CHARS || b.length > MAX_FRAGMENT_CHARS) return res('clarify', 'Фрагмент длиннее ' + MAX_FRAGMENT_CHARS + ' знаков: разбейте его на отдельные утверждения.');
  const fa = analyze(a), fb = analyze(b);
  if (kind === 'numeric') {
    if (!fa.num || !fb.num) return pass('Числа не удалось разобрать: оставлено решение прежнего правила.');
    return compareNumeric(fa, fb);
  }
  return compareQuant(fa, fb) || pass('«Один/несколько»: объект не распознан, оставлено решение прежнего правила.');
}

module.exports = { refine, classifyOldWhy, isContradiction, findContradictions, MAX_TEXT_CHARS, MAX_FRAGMENT_CHARS, TOO_LONG_TEXT };
