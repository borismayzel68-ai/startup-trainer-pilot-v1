import os, io, json, uuid, time, re, hashlib
from flask import Flask, request, jsonify, render_template
from pypdf import PdfReader
from openai import OpenAI
from routing_v2 import (
    JURISDICTIONS, PRODUCT_CLASSES, normalize_project_id, norm_jurisdiction,
    norm_product_class, parse_target_markets, route, vocab, profile_question,
    guard_question, raised_routes
)
from question_engine_v2 import (
    QUICK_TRIAGE_MAX_TURNS, ensure_coverage, seed_source_coverage,
    next_question as engine_next_question, record_answer, decide_progress,
    coverage_summary, explicit_no_data,
)
from session_store import DurableSessionStore

app = Flask(__name__)
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6")
INVITE_CODE = os.environ.get("INVITE_CODE", "PILOT-S1-EXT-001")
ADMIN_KEY = os.environ.get("ADMIN_KEY", "change-me")
SESSION_RETENTION_DAYS = int(os.environ.get("SESSION_RETENTION_DAYS", "30"))
SESSION_DATABASE_URL = (
    os.environ.get("SESSION_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
    or "sqlite:///trainer_sessions.db"
)
SESSIONS = DurableSessionStore(
    SESSION_DATABASE_URL,
    retention_days=SESSION_RETENTION_DAYS,
)
ALLOWED_STATUS = {"fact","hypothesis","missing","weak"}
ALLOWED_REC = {"not_ready","iterate","ready_for_investor_conversation"}

def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def extract_pdf(file_storage):
    raw = file_storage.read()
    reader = PdfReader(io.BytesIO(raw))
    text = "\n".join((p.extract_text() or "") for p in reader.pages)
    manifest = {
        "present": True,
        "filename": str(file_storage.filename or "")[:300],
        "mime_type": str(file_storage.mimetype or ""),
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    return text[:30000], manifest

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
    parts=[
        s.get("description",""), s.get("deck_text",""), s.get("intended_use",""),
        s.get("stage",""), s.get("user",""), s.get("buyer",""), s.get("payer",""),
        s.get("hypothesis_a",""), s.get("hypothesis_b","")
    ]
    for qa in s.get("qa",[]):
        parts.append(qa.get("answer",""))
    return "\n".join(x for x in parts if x)

def mode_text(s):
    if s["mode"]=="A": return "\u041f\u0440\u043e\u0432\u0435\u0440\u044f\u0435\u0442\u0441\u044f \u0442\u0435\u043a\u0443\u0449\u0430\u044f \u0431\u0438\u0437\u043d\u0435\u0441-\u043c\u043e\u0434\u0435\u043b\u044c."
    if s["mode"]=="B": return "\u041f\u0440\u043e\u0432\u0435\u0440\u044f\u0435\u0442\u0441\u044f \u043d\u043e\u0432\u0430\u044f \u0433\u0438\u043f\u043e\u0442\u0435\u0437\u0430: "+s.get("hypothesis_b","")
    return "\u0421\u0440\u0430\u0432\u043d\u0438\u0432\u0430\u044e\u0442\u0441\u044f \u0434\u0432\u0435 \u0433\u0438\u043f\u043e\u0442\u0435\u0437\u044b \u0431\u0435\u0437 \u0432\u044b\u0431\u043e\u0440\u0430 \u043f\u043e\u0431\u0435\u0434\u0438\u0442\u0435\u043b\u044f. A: "+s.get("hypothesis_a","")+" B: "+s.get("hypothesis_b","")

def routing_context(s):
    return {
        "project_id":s.get("project_id"),
        "jurisdiction":s.get("jurisdiction"),
        "target_markets":s.get("target_markets",[]),
        "intended_use":s.get("intended_use"),
        "product_class":s.get("product_class"),
        "stage":s.get("stage"),
        "user":s.get("user"),
        "buyer":s.get("buyer"),
        "payer":s.get("payer"),
        "selected_modules":s.get("selected_modules",[]),
        "classification_check_required":s.get("classification_check_required",False),
        "jurisdiction_vocab":s.get("jurisdiction_vocab",[]),
        "raised_foreign_routes":raised_routes(s),
    }

def scan_source_coverage(s):
    if s.get("source_coverage_scanned"):
        return
    ensure_coverage(s)
    corpus=source_text(s)
    slots=[
        {"module":x["module"],"slot":x["slot"]}
        for x in s.get("coverage",{}).values()
    ]
    system="""Extract only evidence explicitly present in the supplied source.
Return JSON only: {"covered":[{"module":"...","slot":"...","status":"fact|hypothesis|missing|weak","quote":"verbatim source quote"}]}.
Do not invent coverage. A quote must be verbatim. Use only listed module/slot pairs."""
    user=f"""Allowed slots:
{json.dumps(slots,ensure_ascii=False)}
Source:
{corpus[:22000]}"""
    out=call_json(system,user)
    seed_source_coverage(s,out.get("covered",[]),corpus)

def next_question(s):
    pq=profile_question(s)
    if pq:
        return pq
    scan_source_coverage(s)
    obj=engine_next_question(s)
    if obj is None:
        return None
    obj["topic"]=obj["module"]
    return guard_question(s,obj)

def analyze_answer(s, question, answer):
    if explicit_no_data(answer):
        return {
            "status":"missing",
            "quote":str(answer)[:500],
            "contradiction":"",
            "coach":"Зафиксировано отсутствие данных; слот не будет задан повторно.",
            "stop":False,
        }
    system="""You are an evidence reviewer. Do not upgrade a claim to fact without verbatim support. All founder-facing coach/explanation text MUST be in Russian.
If founder says there is no data, status=missing.
Return JSON only:
{"status":"fact|hypothesis|missing|weak","quote":"short verbatim quote","contradiction":"...","coach":"specific next fact/document/test","stop":false}"""
    user=f"""Question: {question}
Founder answer: {answer}
Context:
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
    system="""Create a Startup Readiness report. This is NOT an investment decision. All report narrative MUST be in Russian.
Do not use INVEST/PASS/REJECT/GO as a capital decision. Do not promise investment. Do not invent numbers.
Return JSON only:
{"thesis":"...","facts":["..."],"hypotheses":["..."],"weak":["..."],"missing":["..."],"gaps":["..."],"next_tests":["..."],"recommendation":"not_ready|iterate|ready_for_investor_conversation","comparison":{"A":{},"B":{}}}"""
    user=f"""Project: {s['project_name']}
Mode: {s['mode']}. {mode_text(s)}
Routing context:
{json.dumps(routing_context(s),ensure_ascii=False)}
Materials and answers:
{source_text(s)[:22000]}
QA history:
{json.dumps(s.get('qa',[]),ensure_ascii=False)[:16000]}"""
    out=call_json(system,user)
    rec=str(out.get("recommendation") or "iterate")
    if rec not in ALLOWED_REC: rec="iterate"
    return {
        "project_id":s["project_id"],
        "jurisdiction":s["jurisdiction"],
        "product_class":s["product_class"],
        "intended_use":s["intended_use"],
        "classification_check_required":s.get("classification_check_required",False),
        "phase":s.get("phase"),
        "stop_reason":s.get("stop_reason"),
        "coverage":coverage_summary(s),
        "thesis":str(out.get("thesis") or ""),
        "facts":list(out.get("facts") or [])[:8],
        "hypotheses":list(out.get("hypotheses") or [])[:8],
        "weak":list(out.get("weak") or [])[:8],
        "missing":list(out.get("missing") or [])[:8],
        "gaps":list(out.get("gaps") or [])[:5],
        "next_tests":list(out.get("next_tests") or [])[:6],
        "recommendation":rec,
        "comparison":out.get("comparison") if s["mode"]=="C" else None,
        "disclaimer":"\u042d\u0442\u043e \u043e\u0446\u0435\u043d\u043a\u0430 \u0433\u043e\u0442\u043e\u0432\u043d\u043e\u0441\u0442\u0438 \u043f\u0430\u043a\u0435\u0442\u0430 \u043a \u0440\u0430\u0437\u0433\u043e\u0432\u043e\u0440\u0443 \u0441 \u0438\u043d\u0432\u0435\u0441\u0442\u043e\u0440\u043e\u043c, \u0430 \u043d\u0435 \u0438\u043d\u0432\u0435\u0441\u0442\u0438\u0446\u0438\u043e\u043d\u043d\u0430\u044f \u0440\u0435\u043a\u043e\u043c\u0435\u043d\u0434\u0430\u0446\u0438\u044f Artgen \u0438 \u043d\u0435 \u043e\u0431\u0435\u0449\u0430\u043d\u0438\u0435 \u0438\u043d\u0432\u0435\u0441\u0442\u0438\u0446\u0438\u0439."
    }

@app.get("/")
def home():
    return render_template("index.html")

@app.get("/health")
def health():
    return jsonify({
        "ok":True,
        "version":"s1-ext-pilot-v1.3-durable-state",
        "model":MODEL,
        "storage":SESSIONS.backend,
    })

@app.get("/robots.txt")
def robots():
    return "User-agent: *\nDisallow: /\n",200,{"Content-Type":"text/plain"}

def start_error(message,code,status=400,allowed=None):
    body={"error":message,"code":code}
    if allowed is not None: body["allowed"]=allowed
    return jsonify(body),status

@app.post("/api/start")
def start():
    if request.form.get("invite_code") != INVITE_CODE:
        return start_error("\u041d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 \u043a\u043e\u0434 \u0434\u043e\u0441\u0442\u0443\u043f\u0430","invite_code_invalid",403)
    if request.form.get("consent") != "true":
        return start_error("\u041d\u0443\u0436\u043d\u043e \u0441\u043e\u0433\u043b\u0430\u0441\u0438\u0435 \u043d\u0430 \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u043a\u0443 \u043c\u0430\u0442\u0435\u0440\u0438\u0430\u043b\u043e\u0432","consent_required")

    project_id=normalize_project_id(request.form.get("project_id"))
    if not project_id:
        return start_error("project_id is required and must be a canonical VC-ID","project_id_required")
    project_name=str(request.form.get("project_name") or "").strip()
    if not project_name:
        return start_error("project_name is required","project_name_required")
    founder_name=str(request.form.get("founder_name") or "").strip()
    if not founder_name:
        return start_error("founder_name is required","founder_name_required")
    founder_email=str(request.form.get("founder_email") or "").strip()
    if not founder_email:
        return start_error("founder_email is required","founder_email_required")

    jurisdiction=norm_jurisdiction(request.form.get("jurisdiction"))
    if not jurisdiction:
        return start_error("jurisdiction is required","jurisdiction_invalid",400,sorted(JURISDICTIONS))
    intended_use=str(request.form.get("intended_use") or "").strip()
    if not intended_use:
        return start_error("intended_use is required","intended_use_required")
    product_class=norm_product_class(request.form.get("product_class"))
    if not product_class:
        return start_error("product_class is required","product_class_invalid",400,sorted(PRODUCT_CLASSES))

    target_markets=parse_target_markets(request.form.getlist("target_markets") or request.form.get("target_markets"))
    if jurisdiction=="MULTI" and len(target_markets)<2:
        return start_error("MULTI requires at least two target_markets","target_markets_required")
    if jurisdiction!="MULTI":
        target_markets=[]

    mode=str(request.form.get("mode") or "").strip()
    if mode not in ("A","B","C"):
        return start_error("mode must be A, B or C","mode_invalid",400,["A","B","C"])

    deck_text=""
    input_manifest={"deck":{"present":False}}
    f=request.files.get("deck")
    if f:
        if f.mimetype!="application/pdf":
            return start_error("\u0420\u0430\u0437\u0440\u0435\u0448\u0451\u043d \u0442\u043e\u043b\u044c\u043a\u043e PDF","deck_mime_invalid")
        deck_text,input_manifest["deck"]=extract_pdf(f)

    selected,reasons,classification_required,active_markets=route(jurisdiction,target_markets,intended_use,product_class)
    sid=str(uuid.uuid4())
    s={
        "id":sid,
        "project_id":project_id,
        "project_name":project_name[:160],
        "founder_name":founder_name[:160],
        "founder_email":founder_email[:320],
        "jurisdiction":jurisdiction,
        "target_markets":target_markets,
        "intended_use":intended_use[:4000],
        "product_class":product_class,
        "stage":str(request.form.get("stage") or "").strip()[:300],
        "user":str(request.form.get("user") or "").strip()[:500],
        "buyer":str(request.form.get("buyer") or "").strip()[:500],
        "payer":str(request.form.get("payer") or "").strip()[:500],
        "routing_steps":["jurisdiction","intended_use","product_class"],
        "selected_modules":selected,
        "module_reasons":reasons,
        "classification_check_required":classification_required,
        "active_markets":active_markets,
        "jurisdiction_vocab":vocab(active_markets),
        "mode":mode,
        "hypothesis_a":request.form.get("hypothesis_a","")[:2500],
        "hypothesis_b":request.form.get("hypothesis_b","")[:2500],
        "description":request.form.get("description","")[:12000],
        "deck_text":deck_text,
        "input_manifest":input_manifest,
        "qa":[],
        "turn":0,
        "phase":"quick_triage",
        "coverage":{},
        "source_coverage_scanned":False,
        "stop_reason":None,
        "event_status":"not_emitted",
        "done":False,
        "report":None,
        "created_at":now(),
        "updated_at":now()
    }
    ensure_coverage(s)
    SESSIONS[sid]=s
    s["current_question"]=next_question(s)
    SESSIONS[sid]=s
    return jsonify({k:v for k,v in s.items() if k!="deck_text"})

@app.post("/api/session/<sid>/answer")
def answer(sid):
    s=SESSIONS.get(sid)
    if not s: return jsonify({"error":"\u0421\u0435\u0441\u0441\u0438\u044f \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430"}),404
    ans=str((request.json or {}).get("answer") or "").strip()
    if not ans: return jsonify({"error":"\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043e\u0442\u0432\u0435\u0442"}),400
    founder_stop=bool(re.match(r"^(\u0441\u0442\u043e\u043f|stop|\u0445\u0432\u0430\u0442\u0438\u0442|\u0437\u0430\u043a\u043e\u043d\u0447\u0438\u0442\u044c|\u0433\u043e\u0442\u043e\u0432\u043e)$",ans,re.I))
    if founder_stop:
        decision=decide_progress(s,founder_stop=True)
    else:
        q=s["current_question"]
        if q.get("profile_field"):
            s[q["profile_field"]]=ans[:1000]
        a=analyze_answer(s,q["question"],ans)
        s["qa"].append({
            "question":q["question"],"why":q.get("why",""),"answer":ans,
            "analysis":a,"at":now(),"profile_field":q.get("profile_field"),
            "module":q.get("module"),"slot":q.get("slot"),"kind":q.get("kind"),
        })
        record_answer(s,q,ans,a)
        s["turn"]+=1
        decision=decide_progress(s)
    s["done"]=decision["done"]
    s["stop_reason"]=decision["stop_reason"]
    s["updated_at"]=now()
    SESSIONS[sid]=s
    if s["done"]:
        s["current_question"]=None
        s["report"]=final_report(s)
    else:
        s["current_question"]=next_question(s)
        if s["current_question"] is None:
            s["done"]=True
            s["stop_reason"]="marginal_value_low"
            s["report"]=final_report(s)
    s["updated_at"]=now()
    SESSIONS[sid]=s
    return jsonify({k:v for k,v in s.items() if k!="deck_text"})

@app.get("/api/session/<sid>")
def get_session(sid):
    s=SESSIONS.get(sid)
    if not s: return jsonify({"error":"\u0421\u0435\u0441\u0441\u0438\u044f \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430"}),404
    return jsonify({k:v for k,v in s.items() if k!="deck_text"})

@app.delete("/api/session/<sid>")
def delete_session(sid):
    SESSIONS.pop(sid,None)
    return jsonify({"ok":True})

@app.post("/api/session/<sid>/feedback")
def feedback(sid):
    s=SESSIONS.get(sid)
    if not s: return jsonify({"error":"\u0421\u0435\u0441\u0441\u0438\u044f \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430"}),404
    s["feedback"]=request.json or {}
    s["updated_at"]=now()
    SESSIONS[sid]=s
    return jsonify({"ok":True})

@app.get("/admin")
def admin():
    if request.args.get("key")!=ADMIN_KEY: return "Unauthorized",401
    return "<pre>"+json.dumps(list(SESSIONS.values()),ensure_ascii=False,indent=2).replace("&","&amp;").replace("<","&lt;")+"</pre>"

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT","10000")))
