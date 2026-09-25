import re

JURISDICTIONS={"RF","EAEU","EU","US","GCC","MULTI"}
PRODUCT_CLASSES={"THERAPEUTIC","MEDICAL_DEVICE","SOFTWARE_POTENTIAL_MD","IVD","LAB_SERVICE","WELLNESS_INFORMATION","RESEARCH_ONLY"}
ALIASES={"RF":"RF","EAEU":"EAEU","EU":"EU","US":"US","GCC":"GCC","MULTI":"MULTI",
"\u0420\u0424":"RF","\u0415\u0410\u042d\u0421":"EAEU","\u0415\u0421":"EU","\u0421\u0428\u0410":"US","\u041d\u0415\u0421\u041a\u041e\u041b\u042c\u041a\u041e":"MULTI"}

COMMON=["problem_unmet_need","product_intended_use","customer_buyer_payer","market","business_model","traction","team_governance","financing_milestone","competition_moat"]
COND={
"THERAPEUTIC":["science_mechanism","clinical","regulatory","ip_fto","cmc_manufacturing"],
"MEDICAL_DEVICE":["regulatory","clinical","cmc_manufacturing","pilots_workflow","market_access_procurement_reimbursement","ip_fto"],
"SOFTWARE_POTENTIAL_MD":["regulatory","ai_data_cybersecurity","pilots_workflow"],
"IVD":["diagnostics_validity","regulatory","pilots_workflow","market_access_procurement_reimbursement","ip_fto"],
"LAB_SERVICE":[],
"WELLNESS_INFORMATION":["ai_data_cybersecurity","regulatory","pilots_workflow"],
"RESEARCH_ONLY":[]
}

TRIGGERS=["interpret","recommend","diagnos","prognos","clinical decision","treatment selection",
"\u0438\u043d\u0442\u0435\u0440\u043f\u0440\u0435\u0442","\u0440\u0435\u043a\u043e\u043c\u0435\u043d\u0434","\u0434\u0438\u0430\u0433\u043d\u043e\u0441\u0442","\u043f\u0440\u043e\u0433\u043d\u043e\u0437","\u043a\u043b\u0438\u043d\u0438\u0447\u0435\u0441\u043a","\u0432\u044b\u0431\u043e\u0440 \u043b\u0435\u0447\u0435\u043d\u0438\u044f"]

TERM_RULES=[
(re.compile(r"\bfda\b",re.I),{"US"},"FDA"),
(re.compile(r"510\s*\(k\)",re.I),{"US"},"510(k)"),
(re.compile(r"\bpma\b",re.I),{"US"},"PMA"),
(re.compile(r"\bde\s+novo\b",re.I),{"US"},"De Novo"),
(re.compile(r"\bema\b",re.I),{"EU"},"EMA"),
(re.compile(r"\bmdr\b",re.I),{"EU"},"MDR"),
(re.compile(r"\bivdr\b",re.I),{"EU"},"IVDR"),
(re.compile(r"\bce(?:\s+mark)?\b",re.I),{"EU"},"CE"),
(re.compile(r"\bsamd\b",re.I),{"US","EU"},"SaMD"),
]

PROFILE_FIELDS=[
("stage","\u041d\u0430 \u043a\u0430\u043a\u043e\u0439 \u0441\u0442\u0430\u0434\u0438\u0438 \u0441\u0435\u0439\u0447\u0430\u0441 \u043f\u0440\u043e\u0435\u043a\u0442: \u0438\u0434\u0435\u044f, PoC, \u0434\u043e\u043a\u043b\u0438\u043d\u0438\u043a\u0430, \u043a\u043b\u0438\u043d\u0438\u043a\u0430, \u0440\u0435\u0433\u0438\u0441\u0442\u0440\u0430\u0446\u0438\u044f, \u043f\u0438\u043b\u043e\u0442\u044b \u0438\u043b\u0438 \u043f\u0440\u043e\u0434\u0430\u0436\u0438?"),
("user","\u041a\u0442\u043e \u043d\u0435\u043f\u043e\u0441\u0440\u0435\u0434\u0441\u0442\u0432\u0435\u043d\u043d\u043e \u043f\u043e\u043b\u044c\u0437\u0443\u0435\u0442\u0441\u044f \u043f\u0440\u043e\u0434\u0443\u043a\u0442\u043e\u043c?"),
("buyer","\u041a\u0442\u043e \u043f\u0440\u0438\u043d\u0438\u043c\u0430\u0435\u0442 \u0440\u0435\u0448\u0435\u043d\u0438\u0435 \u043e \u043f\u043e\u043a\u0443\u043f\u043a\u0435 \u0438\u043b\u0438 \u0432\u043d\u0435\u0434\u0440\u0435\u043d\u0438\u0438?"),
("payer","\u041a\u0442\u043e \u0444\u0430\u043a\u0442\u0438\u0447\u0435\u0441\u043a\u0438 \u043f\u043b\u0430\u0442\u0438\u0442 \u0437\u0430 \u043f\u0440\u043e\u0434\u0443\u043a\u0442 \u0438\u043b\u0438 \u0443\u0441\u043b\u0443\u0433\u0443?")]

def normalize_project_id(value):
    v=str(value or "").strip()
    if not v.upper().startswith("VC-") or len(v)<=3:
        return None
    return "VC-"+v[3:]

def norm_jurisdiction(value):
    return ALIASES.get(str(value or "").strip().upper())

def norm_product_class(value):
    v=str(value or "").strip().upper()
    return v if v in PRODUCT_CLASSES else None

def parse_target_markets(values):
    if values is None: return []
    items=values if isinstance(values,list) else [values]
    tokens=[]
    for item in items:
        tokens.extend(re.split(r"[,;\s]+",str(item)))
    out=[]
    for raw in tokens:
        if not raw: continue
        n=norm_jurisdiction(raw)
        if n and n!="MULTI" and n not in out:
            out.append(n)
    return out

def classification_check(intended_use):
    t=str(intended_use or "").lower()
    return any(k in t for k in TRIGGERS)

def route(jurisdiction,target_markets,intended_use,product_class):
    markets=set(target_markets or [])
    if jurisdiction and jurisdiction!="MULTI":
        markets.add(jurisdiction)
    selected=list(COMMON)
    reasons={m:"common" for m in COMMON}
    for m in COND.get(product_class,[]):
        if m not in selected:
            selected.append(m); reasons[m]=f"product_class:{product_class}"
    iu=str(intended_use or "").lower()
    if product_class=="MEDICAL_DEVICE" and any(k in iu for k in ("software"," ai ","digital","cyber","\u043f\u043e","\u0438\u0438")):
        if "ai_data_cybersecurity" not in selected:
            selected.append("ai_data_cybersecurity"); reasons["ai_data_cybersecurity"]="software_present"
    check=classification_check(intended_use) and product_class in {"WELLNESS_INFORMATION","SOFTWARE_POTENTIAL_MD","LAB_SERVICE"}
    if check and "regulatory" not in selected:
        selected.append("regulatory"); reasons["regulatory"]="intended_use_classification_trigger"
    if markets & {"RF","EAEU"} and "regulatory" not in selected:
        selected.append("regulatory"); reasons["regulatory"]="rf_eaeu_regulatory_baseline"
    return selected,reasons,check,sorted(markets)

def vocab(markets):
    m=set(markets or []); out=[]
    if m & {"RF","EAEU"}:
        out+=["\u0420\u043e\u0441\u0437\u0434\u0440\u0430\u0432\u043d\u0430\u0434\u0437\u043e\u0440","\u043c\u0435\u0434\u0438\u0446\u0438\u043d\u0441\u043a\u043e\u0435 \u0438\u0437\u0434\u0435\u043b\u0438\u0435 \u0420\u0424/\u0415\u0410\u042d\u0421","\u041e\u041c\u0421","\u0414\u041c\u0421","44-\u0424\u0417","223-\u0424\u0417"]
    if "US" in m: out+=["FDA"]
    if "EU" in m: out+=["EMA","MDR","IVDR","CE"]
    if "GCC" in m: out+=["GCC regulatory route: TBD"]
    return out

def profile_question(session):
    for field,q in PROFILE_FIELDS:
        if not str(session.get(field) or "").strip():
            return {"question":q,"why":"Нужно для маршрутизации и triage.","topic":"profile","profile_field":field}
    return None

def _route_source(session, markets):
    material="\n".join([session.get("description",""),session.get("deck_text",""),session.get("intended_use","")])
    for pattern,rule_markets,label in TERM_RULES:
        if rule_markets & set(markets) and pattern.search(material):
            return "material","source_material",label
    for i,qa in enumerate(session.get("qa",[])):
        answer=str(qa.get("answer",""))
        for pattern,rule_markets,label in TERM_RULES:
            if rule_markets & set(markets) and pattern.search(answer):
                return "founder",f"qa:{i+1}",label
    return None,None,None

def raised_routes(session):
    out=[]
    for market in ("US","EU"):
        rb,ref,label=_route_source(session,{market})
        if rb: out.append({"market":market,"route_raised_by":rb,"source_ref":ref,"term":label})
    return out

def guard_question(session,obj):
    out=dict(obj or {})
    q=str(out.get("question") or "")
    active=set(session.get("active_markets") or [])
    for pattern,markets,label in TERM_RULES:
        if not pattern.search(q): continue
        if active & markets: continue
        raised_by,ref,_=_route_source(session,markets)
        supplied_ref=str(out.get("source_ref") or "").strip()
        if raised_by and supplied_ref and supplied_ref==ref:
            out["route_raised_by"]=raised_by
            out["source_ref"]=ref
            return out
        out["jurisdiction_leak"]=label
        out["question"]="\u041a\u0430\u043a\u043e\u0439 \u0440\u0435\u0433\u0443\u043b\u044f\u0442\u043e\u0440\u043d\u044b\u0439 \u043c\u0430\u0440\u0448\u0440\u0443\u0442 \u0438 \u043a\u0430\u043a\u043e\u0435 \u0434\u043e\u043a\u0430\u0437\u0430\u0442\u0435\u043b\u044c\u0441\u0442\u0432\u043e \u043f\u0440\u0438\u043c\u0435\u043d\u0438\u043c\u044b \u0438\u043c\u0435\u043d\u043d\u043e \u0432 \u0437\u0430\u044f\u0432\u043b\u0435\u043d\u043d\u043e\u0439 \u044e\u0440\u0438\u0441\u0434\u0438\u043a\u0446\u0438\u0438 \u043f\u0440\u043e\u0435\u043a\u0442\u0430?"
        out["why"]="\u0423\u0431\u0440\u0430\u043d \u043d\u0435\u0440\u0435\u043b\u0435\u0432\u0430\u043d\u0442\u043d\u044b\u0439 \u0437\u0430\u0440\u0443\u0431\u0435\u0436\u043d\u044b\u0439 regulatory default."
        out["topic"]="regulatory"
        out.pop("source_ref",None)
        return out
    return out

