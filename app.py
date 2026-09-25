import os, io, json, uuid, time, re
from flask import Flask, request, jsonify, render_template, send_file
from pypdf import PdfReader
from openai import OpenAI

app = Flask(__name__)
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6")
INVITE_CODE = os.environ.get("INVITE_CODE", "PILOT-S1-EXT-001")
ADMIN_KEY = os.environ.get("ADMIN_KEY", "change-me")
MAX_TURNS = 12
SESSIONS = {}
ALLOWED_STATUS = {"fact","hypothesis","missing","weak"}
ALLOWED_REC = {"not_ready","iterate","ready_for_investor_conversation"}

def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def extract_pdf(file_storage):
    raw = file_storage.read()
    reader = PdfReader(io.BytesIO(raw))
    text = "\n".join((p.extract_text() or "") for p in reader.pages)
    return text[:30000]

def call_json(system, user):
    r = client.responses.create(
        model=MODEL,
        input=[
            {"role":"system","content":[{"type":"input_text","text":system}]},
            {"role":"user","content":[{"type":"input_text","text":user}]},
        ],
    )
    txt = r.output_text.strip()
    try:
        return json.loads(txt)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", txt)
        if not m:
            raise
        return json.loads(m.group(0))
def source_text(s):
    parts=[s.get("description",""),s.get("deck_text",""),s.get("hypothesis_a",""),s.get("hypothesis_b","")]
    for qa in s.get("qa",[]): parts.append(qa.get("answer",""))
    return "\n".join(x for x in parts if x)

def mode_text(s):
    if s["mode"]=="A": return "Проверяется текущая бизнес-модель."
    if s["mode"]=="B": return "Проверяется новая гипотеза: "+s.get("hypothesis_b","")
    return "Сравниваются две гипотезы без выбора победителя. A: "+s.get("hypothesis_a","")+" B: "+s.get("hypothesis_b","")

def next_question(s):
    history="\n".join([f"Q: {x['question']}\nA: {x['answer']}\nSTATUS: {x['analysis'].get('status','')}" for x in s.get("qa",[])[-6:]])
    system="""Ты — Startup Readiness Trainer для Life Sciences, MedTech и Digital Health.
Задача — подготовить founder к разговору с инвестором, а не принять инвестиционное решение.
Один короткий вопрос за ход, максимум 12 ходов. Не выдумывай факты и цифры.
Статусы только fact|hypothesis|missing|weak. Claim без первички = weak или hypothesis.
Проверяй problem/product/why now/customer/payer/market/business model/unit economics/traction/competition/moat/team/ownership-IP/ask/use of funds/risks.
Для healthcare дополнительно evidence/regulatory/data-privacy/validation.
В режиме сравнения не выбирай победителя и не используй 'лучше/хуже'.
Верни только JSON: {"question":"...","why":"...","topic":"..."}"""
    user=f"""Проект: {s['project_name']}
{mode_text(s)}
Материалы:
{source_text(s)[:18000]}
Последние ответы:
{history}
Задай следующий вопрос максимальной ценности и не повторяйся."""
    out=call_json(system,user)
    q=str(out.get("question") or "").strip()
    if not q:
        q="Какой один факт или документ сильнее всего подтверждает, что заявленная бизнес-модель работает у реальных пользователей?"
    return {"question":q,"why":str(out.get("why") or ""),"topic":str(out.get("topic") or "evidence")}
def analyze_answer(s, question, answer):
    system="""Ты evidence reviewer стартапа. Не повышай claim до факта без дословной опоры.
Если founder пишет 'нет данных' или эквивалент — status=missing.
Верни только JSON:
{"status":"fact|hypothesis|missing|weak","quote":"короткая дословная цитата","contradiction":"...","coach":"какой конкретный факт/документ/тест нужен дальше","stop":false}"""
    user=f"""Вопрос: {question}
Ответ founder: {answer}
Контекст:
{source_text(s)[:14000]}"""
    out=call_json(system,user)
    status=str(out.get("status") or "weak")
    if status not in ALLOWED_STATUS: status="weak"
    quote=str(out.get("quote") or "")
    corpus=source_text(s).lower()
    if status=="fact" and (not quote or quote.lower() not in corpus):
        status="weak"
    return {"status":status,"quote":quote[:500],"contradiction":str(out.get("contradiction") or "")[:800],"coach":str(out.get("coach") or "")[:1000],"stop":bool(out.get("stop"))}
def final_report(s):
    system="""Сформируй Startup Readiness report. Это НЕ инвестиционное решение фонда.
Не используй INVEST/PASS/REJECT/GO как решение о капитале. Не обещай инвестиции.
Не выдумывай числа. В режиме C сравни A и B без выбора победителя.
Верни только JSON:
{"thesis":"...","facts":["..."],"hypotheses":["..."],"weak":["..."],"missing":["..."],"gaps":["..."],"next_tests":["..."],"recommendation":"not_ready|iterate|ready_for_investor_conversation","comparison":{"A":{},"B":{}}}"""
    user=f"""Проект: {s['project_name']}
Режим: {s['mode']}. {mode_text(s)}
Материалы и ответы:
{source_text(s)[:22000]}
История QA:
{json.dumps(s.get('qa',[]),ensure_ascii=False)[:16000]}"""
    out=call_json(system,user)
    rec=str(out.get("recommendation") or "iterate")
    if rec not in ALLOWED_REC: rec="iterate"
    return {
        "thesis":str(out.get("thesis") or ""),
        "facts":list(out.get("facts") or [])[:8],
        "hypotheses":list(out.get("hypotheses") or [])[:8],
        "weak":list(out.get("weak") or [])[:8],
        "missing":list(out.get("missing") or [])[:8],
        "gaps":list(out.get("gaps") or [])[:5],
        "next_tests":list(out.get("next_tests") or [])[:6],
        "recommendation":rec,
        "comparison":out.get("comparison") if s["mode"]=="C" else None,
        "disclaimer":"Это оценка готовности пакета к разговору с инвестором, а не инвестиционная рекомендация Artgen и не обещание инвестиций."
    }
@app.get("/")
def home():
    return render_template("index.html")

@app.get("/health")
def health():
    return jsonify({"ok":True,"version":"s1-ext-pilot-v1.0","model":MODEL})

@app.get("/robots.txt")
def robots():
    return "User-agent: *\nDisallow: /\n",200,{"Content-Type":"text/plain"}

@app.post("/api/start")
def start():
    if request.form.get("invite_code") != INVITE_CODE:
        return jsonify({"error":"Неверный код доступа"}),403
    if request.form.get("consent") != "true":
        return jsonify({"error":"Нужно согласие на обработку материалов"}),400
    deck_text=""
    f=request.files.get("deck")
    if f:
        if f.mimetype!="application/pdf":
            return jsonify({"error":"Разрешён только PDF"}),400
        deck_text=extract_pdf(f)
    mode=request.form.get("mode","A")
    if mode not in ("A","B","C"): mode="A"
    sid=str(uuid.uuid4())
    s={"id":sid,"project_name":request.form.get("project_name","Проект")[:160],"mode":mode,
       "hypothesis_a":request.form.get("hypothesis_a","")[:2500],"hypothesis_b":request.form.get("hypothesis_b","")[:2500],
       "description":request.form.get("description","")[:12000],"deck_text":deck_text,"qa":[],"turn":0,
       "done":False,"report":None,"created_at":now(),"updated_at":now()}
    s["current_question"]=next_question(s)
    SESSIONS[sid]=s
    return jsonify({k:v for k,v in s.items() if k!="deck_text"})

@app.post("/api/session/<sid>/answer")
def answer(sid):
    s=SESSIONS.get(sid)
    if not s: return jsonify({"error":"Сессия не найдена"}),404
    ans=str((request.json or {}).get("answer") or "").strip()
    if not ans: return jsonify({"error":"Введите ответ"}),400
    if re.match(r"^(стоп|stop|хватит|закончить|готово)$",ans,re.I):
        s["done"]=True; s["report"]=final_report(s)
    else:
        q=s["current_question"]
        a=analyze_answer(s,q["question"],ans)
        s["qa"].append({"question":q["question"],"why":q.get("why",""),"answer":ans,"analysis":a,"at":now()})
        s["turn"]+=1
        if s["turn"]>=MAX_TURNS or a.get("stop"):
            s["done"]=True; s["report"]=final_report(s)
        else:
            s["current_question"]=next_question(s)
    s["updated_at"]=now()
    return jsonify({k:v for k,v in s.items() if k!="deck_text"})
@app.get("/api/session/<sid>")
def get_session(sid):
    s=SESSIONS.get(sid)
    if not s: return jsonify({"error":"Сессия не найдена"}),404
    return jsonify({k:v for k,v in s.items() if k!="deck_text"})

@app.delete("/api/session/<sid>")
def delete_session(sid):
    SESSIONS.pop(sid,None)
    return jsonify({"ok":True})

@app.post("/api/session/<sid>/feedback")
def feedback(sid):
    s=SESSIONS.get(sid)
    if not s: return jsonify({"error":"Сессия не найдена"}),404
    s["feedback"]=request.json or {}
    return jsonify({"ok":True})

@app.get("/admin")
def admin():
    if request.args.get("key")!=ADMIN_KEY: return "Unauthorized",401
    return "<pre>"+json.dumps(list(SESSIONS.values()),ensure_ascii=False,indent=2).replace("&","&amp;").replace("<","&lt;")+"</pre>"

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT","10000")))
