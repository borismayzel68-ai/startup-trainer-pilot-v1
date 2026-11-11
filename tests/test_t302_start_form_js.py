"""T-302 (#114, FEAT-003): startSession() — индикатор, блокировка, любой ответ. AC-002, AC-003.
JS из templates/index.html исполняется в node с заглушками DOM/fetch. Только синтетика."""
import json
import os
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node не установлен")

HTML = os.path.join(os.path.dirname(__file__), "..", "templates", "index.html")

HARNESS = r"""
const src = process.argv[2], scenario = process.argv[3];
const text = require('fs').readFileSync(src, 'utf8');
const a = text.indexOf('let startBusy=false;'), b = text.indexOf('function renderState(){');
const code = text.slice(a, b);
const els = {};
function el(id) {
  if (!els[id]) els[id] = {id: id, value: 'v-' + id, textContent: id === 'startBtn' ? 'Начать' : '', disabled: false,
    checked: false, files: [], classList: {add() {}, remove() {}}};
  return els[id];
}
global.document = {getElementById: el};
global.FormData = class { constructor() { this.d = []; } append(k, v) { this.d.push([k, v]); } };
global.renderState = () => { global.rendered = true; };
let calls = 0, release = null;
const seen = [];
function resp(ok, status, body) { return {ok: ok, status: status, text: async () => body}; }
global.fetch = () => {
  calls++;
  seen.push({btnDisabled: el('startBtn').disabled});
  if (scenario === 'network') return Promise.reject(new TypeError('fail'));
  if (scenario === 'html500') return Promise.resolve(resp(false, 500, '<html>Traceback secret-body</html>'));
  if (scenario === 'json403') return Promise.resolve(resp(false, 403, JSON.stringify({error: 'Нет прав'})));
  if (scenario === 'ok') return Promise.resolve(resp(true, 200, JSON.stringify({id: 's1'})));
  return new Promise(res => { release = () => res(resp(true, 200, JSON.stringify({id: 's1'}))); });
};
(async () => {
  eval(code + '; global.startSession = startSession;');
  const first = global.startSession();
  if (scenario === 'double') { global.startSession(); global.startSession(); }
  const mid = {disabled: el('startBtn').disabled, label: el('startBtn').textContent};
  if (release) release();
  await first;
  console.log(JSON.stringify({calls: calls, mid: mid, seen: seen,
    end: {disabled: el('startBtn').disabled, label: el('startBtn').textContent},
    err: el('startErr').textContent, value: el('founderEmail').value, rendered: !!global.rendered}));
})();
"""

FAIL = "Не удалось отправить, попробуйте ещё раз"


def run(scenario, tmp_path):
    h = tmp_path / "harness.js"
    h.write_text(HARNESS, encoding="utf-8")
    out = subprocess.run(["node", str(h), os.path.abspath(HTML), scenario], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_html_500_shows_text_and_unblocks(tmp_path):
    r = run("html500", tmp_path)
    assert r["err"] == FAIL
    assert "secret-body" not in r["err"]
    assert not r["end"]["disabled"] and r["end"]["label"] == "Начать"
    assert r["value"] == "v-founderEmail" and not r["rendered"]  # значения формы сохранены


def test_network_failure(tmp_path):
    r = run("network", tmp_path)
    assert r["err"] == FAIL
    assert not r["end"]["disabled"] and r["end"]["label"] == "Начать" and r["value"] == "v-founderEmail"


def test_double_click_sends_one_request_with_indicator(tmp_path):
    r = run("double", tmp_path)
    assert r["calls"] == 1
    assert r["mid"] == {"disabled": True, "label": "Отправляем…"}
    assert r["seen"][0]["btnDisabled"] is True
    assert not r["end"]["disabled"] and r["rendered"]


def test_json_error_and_success_unchanged(tmp_path):
    r = run("json403", tmp_path)
    assert r["err"] == "Нет прав" and not r["end"]["disabled"]
    r = run("ok", tmp_path)
    assert r["err"] == "" and r["rendered"]


def test_template_start_function_uses_no_innerhtml():
    html = open(HTML, encoding="utf-8").read()
    body = html[html.index("async function startSession"):html.index("function renderState")]
    assert "innerHTML" not in body and "finally" in body and 'id="startBtn"' in html
