import os, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("OPENAI_API_KEY","test-key")
os.environ.setdefault("INVITE_CODE","PILOT-S1-EXT-001")
import app as appmod
from routing_v2 import *

rows=[]
def add(tid,status,detail=""):
    rows.append({"test_id":tid,"status":status,"detail":detail})

COMMON_SET=set(COMMON)
routing_cases={
"THERAPEUTIC":(["science_mechanism","clinical","regulatory","cmc_manufacturing","ip_fto"],["diagnostics_validity"],"therapeutic product for treatment"),
"MEDICAL_DEVICE":(["regulatory","clinical","cmc_manufacturing","ai_data_cybersecurity","pilots_workflow","market_access_procurement_reimbursement","ip_fto"],["diagnostics_validity"],"medical device used in clinic with embedded software"),
"SOFTWARE_POTENTIAL_MD":(["regulatory","ai_data_cybersecurity","pilots_workflow"],["diagnostics_validity"],"clinical decision support software"),
"IVD":(["diagnostics_validity","regulatory","pilots_workflow","market_access_procurement_reimbursement","ip_fto"],[],"in vitro diagnostic on human sample"),
"LAB_SERVICE":([],["diagnostics_validity"],"laboratory service"),
"WELLNESS_INFORMATION":(["regulatory","ai_data_cybersecurity","pilots_workflow"],["diagnostics_validity"],"information storage and visualization service"),
"RESEARCH_ONLY":([],["diagnostics_validity"],"research product only"),
}
n=1
for pc,(base_inc,exc,iu) in routing_cases.items():
    for j in ("RF","US","MULTI"):
        targets=["RF","EU"] if j=="MULTI" else []
        selected,_,_,markets=route(j,targets,iu,pc)
        s=set(selected); inc=list(base_inc)
        if j in ("RF","MULTI") and "regulatory" not in inc: inc.append("regulatory")
        ok=COMMON_SET.issubset(s) and set(inc).issubset(s) and set(exc).isdisjoint(s)
        add(f"G-{n:02d}","PASS" if ok else "FAIL",f"selected={selected}; markets={markets}")
        n+=1

appmod.app.testing=True
client=appmod.app.test_client()
def base():
    return {"invite_code":"PILOT-S1-EXT-001","project_id":"VC-178","project_name":"Doctum","founder_name":"Test Founder","founder_email":"founder@example.com","jurisdiction":"RF","intended_use":"storage and visualization of laboratory history","product_class":"WELLNESS_INFORMATION","mode":"B","consent":"true"}

# D-01
x=base(); r=client.post("/api/start",data=x); j=r.get_json() or {}
ok=r.status_code==200 and not j.get("classification_check_required") and "diagnostics_validity" not in j.get("selected_modules",[])
add("D-01","PASS" if ok else "FAIL",json.dumps({"status":r.status_code,"flag":j.get("classification_check_required"),"selected":j.get("selected_modules")},ensure_ascii=False))
# D-02
x=base(); x["intended_use"]="interpretation of results and personalized recommendations"; r=client.post("/api/start",data=x); j=r.get_json() or {}
ok=r.status_code==200 and j.get("classification_check_required") is True and j.get("product_class")=="WELLNESS_INFORMATION" and "diagnostics_validity" not in j.get("selected_modules",[])
add("D-02","PASS" if ok else "FAIL",f"status={r.status_code}; flag={j.get('classification_check_required')}")
# D-03
x=base(); x["product_class"]="SOFTWARE_POTENTIAL_MD"; x["intended_use"]="interpretation of results and personalized recommendations"; r=client.post("/api/start",data=x); j=r.get_json() or {}
ok=r.status_code==200 and j.get("classification_check_required") is True and "diagnostics_validity" not in j.get("selected_modules",[]) and {"regulatory","ai_data_cybersecurity","pilots_workflow"}.issubset(set(j.get("selected_modules",[])))
add("D-03","PASS" if ok else "FAIL",f"status={r.status_code}; selected={j.get('selected_modules')}")
# D-04
x=base(); x.update({"jurisdiction":"MULTI","target_markets":"RF, EU","intended_use":"interpretation of results"}); r=client.post("/api/start",data=x); j=r.get_json() or {}
sess={"active_markets":j.get("active_markets",[]),"description":"","deck_text":"","intended_use":x["intended_use"],"qa":[]}
eu=guard_question(sess,{"question":"What is the EMA/MDR route?"}); us=guard_question(sess,{"question":"What is the FDA route?"})
ok=r.status_code==200 and j.get("classification_check_required") is True and "jurisdiction_leak" not in eu and us.get("jurisdiction_leak")=="FDA"
add("D-04","PASS" if ok else "FAIL",f"markets={j.get('active_markets')}; eu={eu}; us={us}")
# D-05
x=base(); x.update({"jurisdiction":"US","intended_use":"interpretation of results"}); r=client.post("/api/start",data=x); j=r.get_json() or {}
sess={"active_markets":j.get("active_markets",[]),"description":"","deck_text":"","intended_use":x["intended_use"],"qa":[]}
us=guard_question(sess,{"question":"What is the FDA route?"}); eu=guard_question(sess,{"question":"What is the EMA route?"})
ok=r.status_code==200 and "jurisdiction_leak" not in us and eu.get("jurisdiction_leak")=="EMA"
add("D-05","PASS" if ok else "FAIL",f"markets={j.get('active_markets')}; us={us}; eu={eu}")
# D-06
x=base(); x.pop("jurisdiction"); r=client.post("/api/start",data=x); ok=r.status_code==400
x2=base(); x2["jurisdiction"]="Russia2"; r2=client.post("/api/start",data=x2); ok=ok and r2.status_code==400
add("D-06","PASS" if ok else "FAIL",f"missing={r.status_code}; invalid={r2.status_code}")
# D-07
x=base(); x["intended_use"]="   "; r=client.post("/api/start",data=x); add("D-07","PASS" if r.status_code==400 else "FAIL",f"status={r.status_code}")
# D-08
x=base(); x["product_class"]="IVD2"; r=client.post("/api/start",data=x); add("D-08","PASS" if r.status_code==400 else "FAIL",f"status={r.status_code}")

# A5 N1/N2 require a real model run; no local key and no preview deploy authorized.
add("A5-N1","NOT_RUN","real-model test; local OPENAI_API_KEY intentionally unavailable; preview not authorized")
add("A5-N2","NOT_RUN","real-model test; local OPENAI_API_KEY intentionally unavailable; preview not authorized")
rf={"active_markets":["RF"],"description":"","deck_text":"","intended_use":"","qa":[]}
out=guard_question(rf,{"question":"FDA 510(k)?"}); add("A5-N3","PASS" if out.get("jurisdiction_leak")=="FDA" else "FAIL",str(out))
multi={"active_markets":["RF","EU"],"description":"","deck_text":"","intended_use":"","qa":[]}
fda=guard_question(multi,{"question":"FDA route?"}); ema=guard_question(multi,{"question":"EMA route?"})
add("A5-N4","PASS" if fda.get("jurisdiction_leak")=="FDA" and "jurisdiction_leak" not in ema else "FAIL",f"fda={fda}; ema={ema}")
raised={"active_markets":["RF"],"description":"Plan FDA 510(k) in 2027","deck_text":"","intended_use":"","qa":[]}
out=guard_question(raised,{"question":"Clarify FDA route"}); add("A5-N5","PASS" if out.get("jurisdiction_leak")=="FDA" else "FAIL",str(out))
us={"active_markets":["US"],"description":"","deck_text":"","intended_use":"","qa":[]}
out=guard_question(us,{"question":"FDA route?"}); add("A5-P1","PASS" if "jurisdiction_leak" not in out else "FAIL",str(out))
out=guard_question(multi,{"question":"EMA/MDR/CE route?"}); add("A5-P2","PASS" if "jurisdiction_leak" not in out else "FAIL",str(out))
out=guard_question(raised,{"question":"Clarify FDA 510(k) route","source_ref":"source_material"}); add("A5-P3","PASS" if out.get("route_raised_by")=="material" and out.get("source_ref")=="source_material" else "FAIL",str(out))
founder={"active_markets":["RF"],"description":"","deck_text":"","intended_use":"","qa":[{"answer":"We plan EU CE"}]}
out=guard_question(founder,{"question":"Clarify MDR/CE route","source_ref":"qa:1"}); add("A5-P4","PASS" if out.get("route_raised_by")=="founder" and out.get("source_ref")=="qa:1" else "FAIL",str(out))

pid_cases=[("PID-01","VC-178","VC-178","PASS"),("PID-02","VC-178 ","VC-178","PASS"),("PID-03","VC-100a","VC-100a","RECORD"),("PID-04","VC-100b","VC-100b","RECORD"),("PID-05","Doctum",None,"PASS"),("PID-06","",None,"PASS")]
for tid,val,expected,status in pid_cases:
    actual=normalize_project_id(val)
    ok=actual==expected
    add(tid,status if ok else "FAIL",f"actual={actual!r}; expected={expected!r}")

assert len(rows)==44, len(rows)
summary={}
for row in rows: summary[row["status"]]=summary.get(row["status"],0)+1
out={"total":len(rows),"summary":summary,"rows":rows}
Path("oracle_44_results.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
md=["# S1-EXT-003A вЂ” ORACLE 44 EXECUTION RESULTS","",f"Total: {len(rows)} В· "+ " В· ".join(f"{k}: {v}" for k,v in summary.items()),"","| test_id | status | detail |","|---|---|---|"]
for row in rows:
    detail=str(row["detail"]).replace("|","/").replace("\n"," ")
    md.append(f"| {row['test_id']} | {row['status']} | {detail} |")
Path("oracle_44_results.md").write_text("\n".join(md),encoding="utf-8")
print(json.dumps({"total":len(rows),"summary":summary},ensure_ascii=False))
