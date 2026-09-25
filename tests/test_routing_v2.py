import unittest
from routing_v2 import *

COMMON_SET=set(COMMON)

class RoutingOracleTests(unittest.TestCase):
    def check_case(self, product_class, jurisdiction, intended_use, must_include, must_exclude):
        targets=["RF","EU"] if jurisdiction=="MULTI" else []
        selected,_,flag,markets=route(jurisdiction,targets,intended_use,product_class)
        s=set(selected)
        self.assertTrue(COMMON_SET.issubset(s))
        self.assertTrue(set(must_include).issubset(s), (product_class,jurisdiction,s))
        self.assertTrue(set(must_exclude).isdisjoint(s), (product_class,jurisdiction,s))
        return s,flag,markets

    def test_golden_routing_oracle(self):
        cases={
          "THERAPEUTIC":(["science_mechanism","clinical","regulatory","cmc_manufacturing","ip_fto"],["diagnostics_validity"]),
          "MEDICAL_DEVICE":(["regulatory","clinical","cmc_manufacturing","ai_data_cybersecurity","pilots_workflow","market_access_procurement_reimbursement","ip_fto"],["diagnostics_validity"]),
          "SOFTWARE_POTENTIAL_MD":(["regulatory","ai_data_cybersecurity","pilots_workflow"],["diagnostics_validity"]),
          "IVD":(["diagnostics_validity","regulatory","pilots_workflow","market_access_procurement_reimbursement","ip_fto"],[]),
          "LAB_SERVICE":([],["diagnostics_validity"]),
          "WELLNESS_INFORMATION":(["regulatory","ai_data_cybersecurity","pilots_workflow"],["diagnostics_validity"]),
          "RESEARCH_ONLY":([],["diagnostics_validity"]),
        }
        intended={
          "THERAPEUTIC":"therapeutic product for treatment",
          "MEDICAL_DEVICE":"medical device used in clinic with embedded software",
          "SOFTWARE_POTENTIAL_MD":"clinical decision support software",
          "IVD":"in vitro diagnostic on human sample",
          "LAB_SERVICE":"laboratory service",
          "WELLNESS_INFORMATION":"information service",
          "RESEARCH_ONLY":"research product only",
        }
        for pc,(base_inc,exc) in cases.items():
            for j in ("RF","US","MULTI"):
                inc=list(base_inc)
                if j in ("RF","MULTI") and "regulatory" not in inc:
                    inc.append("regulatory")
                with self.subTest(pc=pc,j=j):
                    self.check_case(pc,j,intended[pc],inc,exc)

    def test_doctum_classification_trigger(self):
        s,flag,_=self.check_case("WELLNESS_INFORMATION","RF","storage and visualization",["regulatory","ai_data_cybersecurity","pilots_workflow"],["diagnostics_validity"])
        self.assertFalse(flag)
        _,flag,_=self.check_case("THERAPEUTIC","RF","clinical treatment",["regulatory","science_mechanism","clinical"],["diagnostics_validity"])
        self.assertFalse(flag)
        s,flag,_=self.check_case("WELLNESS_INFORMATION","RF","interpretation of results and personalized recommendations",["regulatory","ai_data_cybersecurity","pilots_workflow"],["diagnostics_validity"])
        self.assertTrue(flag)

    def test_target_markets_string(self):
        self.assertEqual(parse_target_markets(["RF, EU"]),["RF","EU"])

    def test_project_id_observed_cases(self):
        self.assertEqual(normalize_project_id("VC-178"),"VC-178")
        self.assertEqual(normalize_project_id("VC-178 "),"VC-178")
        self.assertEqual(normalize_project_id("VC-100a"),"VC-100a")
        self.assertEqual(normalize_project_id("VC-100b"),"VC-100b")
        self.assertIsNone(normalize_project_id("Doctum"))
        self.assertIsNone(normalize_project_id(""))

    def test_a5_negative_and_positive_controls(self):
        base={"active_markets":["RF"],"description":"","deck_text":"","intended_use":"","qa":[]}
        out=guard_question(base,{"question":"Do you plan FDA 510(k)?"})
        self.assertEqual(out.get("jurisdiction_leak"),"FDA")
        multi={"active_markets":["RF","EU"],"description":"","deck_text":"","intended_use":"","qa":[]}
        self.assertEqual(guard_question(multi,{"question":"What is the EMA route?"}).get("jurisdiction_leak"),None)
        self.assertEqual(guard_question(multi,{"question":"What is the FDA route?"}).get("jurisdiction_leak"),"FDA")
        us={"active_markets":["US"],"description":"","deck_text":"","intended_use":"","qa":[]}
        self.assertIsNone(guard_question(us,{"question":"What is the FDA route?"}).get("jurisdiction_leak"))

    def test_a5_source_raised_requires_provenance(self):
        material={"active_markets":["RF"],"description":"We plan FDA 510(k) in 2027","deck_text":"","intended_use":"","qa":[]}
        blocked=guard_question(material,{"question":"Clarify the FDA 510(k) route"})
        self.assertEqual(blocked.get("jurisdiction_leak"),"FDA")
        allowed=guard_question(material,{"question":"Clarify the FDA 510(k) route","source_ref":"source_material"})
        self.assertEqual(allowed.get("route_raised_by"),"material")
        self.assertEqual(allowed.get("source_ref"),"source_material")
        founder={"active_markets":["RF"],"description":"","deck_text":"","intended_use":"","qa":[{"answer":"We plan EU CE"}]}
        allowed=guard_question(founder,{"question":"Clarify MDR/CE route","source_ref":"qa:1"})
        self.assertEqual(allowed.get("route_raised_by"),"founder")
        self.assertEqual(allowed.get("source_ref"),"qa:1")

if __name__=="__main__":
    unittest.main()
