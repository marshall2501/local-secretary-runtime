from __future__ import annotations

from io import BytesIO
import json
from unittest.mock import patch
import unittest

from pkb_proto.magi_client import call_member, choose_model
from pkb_proto.magi_dialogue import (
    CATEGORIES, start_dialogue, continue_with_observation, validate_turn,
)

from pkb_proto.ritsuko_magi_protocol import (
    ALLOWED_VALUES,
    build_request_envelope,
    default_resource_catalog,
    validate_analysis_result,
)

class RitsukoMagiProtocolTests(unittest.TestCase):
    def request(self):
        return build_request_envelope(
            task_id="task-1",cycle=1,member_name="MELCHIOR",
            user_raw="メインPCのGPUの種類は？",
        )

    def valid_response(self):
        return {
            "protocol_version":"1.0","message_type":"analysis_result",
            "task_id":"task-1","cycle":1,"magi_member":"MELCHIOR",
            "analysis":{
                "intent":{"type":"information_lookup","description":"メインPCのGPUを知りたい"},
                "target":{"status":"resolved","items":[
                    {"raw_reference":"メインPC","interpreted_as":"ユーザー本人のメインPC"},
                    {"raw_reference":"GPU","interpreted_as":"メインPCに現在搭載されているGPU"},
                ]},
                "requested_result":{"type":"information","description":"現在搭載されているGPU"},
                "understanding":{"status":"complete","ambiguities":[],"uninterpretable_parts":[]},
                "information_sufficiency":{"status":"insufficient","missing_information":[
                    {"description":"現在搭載されているGPU情報","reason":"ユーザー原文には実データがない"}
                ]},
                "likely_information_sources":[{"source":"pkb","reason":"本人固有の現在構成情報だから"}],
                "knowledge_candidates":[],
                "information_requests":[{
                    "request_id":"REQ-001","source_preferences":["pkb"],
                    "request":"メインPCに現在搭載されているGPUを確認する",
                    "requested_facts":["GPUの製品ファミリ","メーカー","モデル"],
                    "reason":"ユーザーへの回答に必要","blocking":True,
                }],
                "uncertainties":[],
            },
            "recommended_next":{
                "type":"request_information","reason":"回答材料が不足している",
                "answer_candidate":None,"action_candidate":None,"question_for_user":None,
            },
        }

    def test_preferred_model_uses_gemma_when_installed(self):
        installed=['qwen3.5:9b', 'gemma3:12b', 'llama3.1:8b']
        self.assertEqual(choose_model(installed), 'gemma3:12b')
        self.assertEqual(choose_model(installed, requested='qwen3.5:9b'), 'qwen3.5:9b')

    def test_invalid_json_diagnostic_excludes_thinking_content(self):
        outer = {
            'message': {'content': '', 'thinking': 'private model reasoning'},
            'done_reason': 'length', 'eval_count': 1800,
        }
        with patch('pkb_proto.magi_client.urlopen',
                   return_value=BytesIO(json.dumps(outer).encode('utf-8'))):
            result=call_member(self.request(), member_name='MELCHIOR',
                               model='qwen3.5:9b')
        self.assertEqual(result['status'], 'invalid')
        self.assertEqual(result['validation_errors'], ['invalid_json'])
        self.assertEqual(result['diagnostic']['raw_length'], 0)
        self.assertEqual(result['diagnostic']['thinking_length'], len('private model reasoning'))
        self.assertEqual(result['diagnostic']['done_reason'], 'length')
        self.assertEqual(result['diagnostic']['eval_count'], 1800)
        self.assertNotIn('private model reasoning', json.dumps(result))

    def test_ritsuko_builds_full_cycle1_envelope(self):
        request=self.request()
        self.assertEqual(request["message_type"],"analyze")
        self.assertEqual(request["user_input"]["raw"],"メインPCのGPUの種類は？")
        self.assertEqual(request["magi_member"],{"name":"MELCHIOR"})
        self.assertNotIn("model",request["magi_member"])
        self.assertTrue(request["resource_catalog"]["pkb"]["available"])
        self.assertIn("required_analysis",request["request_contract"])
        self.assertEqual(request["response_contract"]["allowed_values"],ALLOWED_VALUES)
        self.assertIn("required_output_shape",request["response_contract"])

    def test_cycle2_uses_continue_analysis(self):
        request=build_request_envelope(
            task_id="task-1",cycle=2,member_name="MELCHIOR",
            user_raw="メインPCのGPUの種類は？",
            observations=[{"source":"pkb","status":"ok"}],
        )
        self.assertEqual(request["message_type"],"continue_analysis")
        self.assertEqual(len(request["observations"]),1)

    def test_valid_analysis_result_passes(self):
        self.assertEqual(validate_analysis_result(self.valid_response(),self.request()),[])

    def test_wrong_enum_is_rejected(self):
        response=self.valid_response()
        response["analysis"]["intent"]["type"]="pkb_search"
        self.assertIn("$.analysis.intent.type:invalid_enum",
                      validate_analysis_result(response,self.request()))

    def test_task_member_and_cycle_are_bound_to_request(self):
        response=self.valid_response()
        response["task_id"]="other"; response["magi_member"]="CASPER"; response["cycle"]=2
        errors=validate_analysis_result(response,self.request())
        self.assertIn("$.task_id:mismatch",errors)
        self.assertIn("$.magi_member:mismatch",errors)
        self.assertIn("$.cycle:mismatch",errors)

    def test_duplicate_information_request_id_is_rejected(self):
        response=self.valid_response()
        response["analysis"]["information_requests"].append(
            dict(response["analysis"]["information_requests"][0])
        )
        errors=validate_analysis_result(response,self.request())
        self.assertTrue(any("request_id:duplicate" in error for error in errors))

    def test_request_id_schema_and_generic_source_guidance(self):
        from pkb_proto.ritsuko_magi_protocol import (
            MAGI_RESPONSE_SCHEMA, SYSTEM_INSTRUCTION,
        )
        request_item_schema = MAGI_RESPONSE_SCHEMA["properties"]["analysis"][
            "properties"]["information_requests"]["items"]
        self.assertEqual(request_item_schema["properties"]["request_id"]["minLength"], 1)
        self.assertIn("likely_information_sources", SYSTEM_INSTRUCTION)
        self.assertIn("request_id", SYSTEM_INSTRUCTION)
        response=self.valid_response()
        response["analysis"]["information_requests"][0]["request_id"]="  "
        self.assertIn("$.analysis.information_requests[0].request_id:invalid",
                      validate_analysis_result(response,self.request()))

    def test_resource_catalog_is_ritsuko_mediated(self):
        catalog=default_resource_catalog()
        self.assertEqual(catalog["pkb"]["access"],"read_only_via_ritsuko")
        self.assertEqual(catalog["web"]["access"],"read_only_via_ritsuko")
        self.assertEqual(catalog["user"]["access"],"ask_user")

class GuidedDialogueTests(unittest.TestCase):
    def scripted(self, *responses):
        answers=list(responses)
        seen=[]
        def call(envelope, *, model, timeout):
            seen.append(envelope)
            return {"status":"ok", "response":answers.pop(0),
                    "errors":[], "diagnostic":{"eval_count":10}}
        return call, seen

    def classification(self, category="INFORMATION", multiple=False):
        return {
            "category":category, "secondary_category":None,
            "understood_request":"ユーザーが必要な情報を知りたい",
            "reason":"質問の目的を分類", "confidence":"high",
            "multiple_requests":multiple,
            "clarification_question":("何についてですか？" if category=="UNCLEAR" else None),
        }

    def detail(self, *, state="NEED_INFORMATION", source="pkb", what="本人の構成"):
        return {
            "understood_request":"求める情報を調べる",
            "state":state,"reason":"取得済みの情報では回答できない",
            "information_requests":(
                [{"source":source,"what":what,"reason":"回答の根拠が必要"}]
                if state=="NEED_INFORMATION" else []
            ),
            "question_for_user":None,
            "answer_candidate":("観測された候補" if state=="READY" else None),
            "action_candidate":None,
        }

    def test_classification_drives_a_followup_question_and_core_owns_ids(self):
        caller, seen=self.scripted(
            self.classification(), self.detail(source="pkb",what="本人のメインPC GPU")
        )
        session=start_dialogue("メインPCのGPUの種類は？",model="gemma3:12b",caller=caller)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0]["stage"], "classify")
        self.assertIn("大まかな分類だけ",seen[0]["question_from_ritsuko"])
        self.assertNotIn("次の1手",seen[0]["question_from_ritsuko"])
        self.assertEqual(seen[1]["stage"], "analyze")
        self.assertEqual(seen[1]["task_context"]["classification"]["category"],"INFORMATION")
        self.assertIn("本人固有の情報",seen[1]["question_from_ritsuko"])
        self.assertEqual(session["status"], "waiting_information")
        self.assertEqual(session["next_step"], "review_information_requests")
        self.assertEqual(session["pending_requests"][0]["source"],"pkb")
        self.assertTrue(session["pending_requests"][0]["request_id"].startswith("REQ-"))
        self.assertNotIn("request_id",session["detail"]["information_requests"][0])
        self.assertFalse(session["tool_read_executed"])
        self.assertFalse(session["legacy_router_used"])

    def test_same_category_can_ask_for_web_without_ritsuko_source_router(self):
        caller,seen=self.scripted(
            self.classification(),
            self.detail(source="web",what="公式の新しい公開情報"),
        )
        session=start_dialogue("新製品の最新情報は？",model="gemma3:12b",caller=caller)
        self.assertEqual([t["stage"] for t in session["turns"]],["classify","analyze"])
        self.assertEqual(session["pending_requests"][0]["source"],"web")
        self.assertEqual(seen[1]["task_context"]["classification"]["category"],"INFORMATION")


    def test_conditional_ready_without_observation_gets_an_llm_followup(self):
        proposed_plan=self.detail(state="READY")
        proposed_plan["answer_candidate"]="記録を確認すれば回答できます。"
        corrected=self.detail(state="NEED_INFORMATION",source="pkb",what="本人の現在の機器構成")
        caller,seen=self.scripted(self.classification(),proposed_plan,corrected)
        session=start_dialogue("私の機器情報は？",model="gemma3:12b",caller=caller)
        self.assertEqual([x["stage"] for x in seen],
                         ["classify","analyze","review_ready"])
        self.assertIn("実際の回答",seen[2]["question_from_ritsuko"])
        self.assertEqual(seen[2]["task_context"]["previous_detail"]["state"],"READY")
        self.assertEqual(session["status"],"waiting_information")
        self.assertEqual(session["pending_requests"][0]["source"],"pkb")
        self.assertEqual(session["detail"]["state"],"NEED_INFORMATION")
        self.assertEqual(len(session["turns"]),3)

    def test_direct_answer_ready_is_reviewed_when_no_observation(self):
        ready=self.detail(state="READY")
        ready["answer_candidate"]="これは一般的な説明です。"
        caller,seen=self.scripted(self.classification(),ready,ready)
        session=start_dialogue("一般的な知識を説明して",model="gemma3:12b",caller=caller)
        self.assertEqual(session["status"],"candidate_ready")
        self.assertEqual(session["next_step"],"review_answer_candidate")
        self.assertEqual(len(seen),3)

    def test_ready_requires_actual_answer_field_and_no_pending_requests(self):
        ready=self.detail(state="READY")
        ready["answer_candidate"]=None
        self.assertIn("answer_candidate:required_for_ready",
                      validate_turn("analyze",ready))
        ready["answer_candidate"]="答えそのもの"
        ready["information_requests"]=[{
            "source":"pkb","what":"未取得情報","reason":"まだ必要"
        }]
        self.assertIn("information_requests:unexpected_for_ready",
                      validate_turn("analyze",ready))

    def test_unclear_classification_stops_and_does_not_immediately_guess_tools(self):
        caller,seen=self.scripted(self.classification("UNCLEAR"))
        session=start_dialogue("あれ",model="gemma3:12b",caller=caller)
        self.assertEqual(session["status"],"waiting_user")
        self.assertEqual(session["next_step"],"classification_clarification")
        self.assertEqual(len(seen),1)

    def test_same_task_can_continue_after_manual_observation(self):
        caller,seen=self.scripted(
            self.classification(), self.detail(source="pkb"),
            self.detail(state="READY"),
        )
        first=start_dialogue("私の構成は？",model="gemma3:12b",caller=caller)
        again=continue_with_observation(
            first,"架空の試験観測",caller=caller,
        )
        self.assertEqual(first["status"],"waiting_information")
        self.assertEqual(again["status"],"candidate_ready")
        self.assertEqual(again["next_step"],"review_answer_candidate")
        self.assertEqual(again["task_id"],first["task_id"])
        self.assertEqual(len(again["turns"]),3)
        self.assertEqual(seen[-1]["turn"],3)
        self.assertEqual(seen[-1]["observations"][0]["verified"],False)
        self.assertEqual(len(again["observations"]),1)
        self.assertFalse(again["tool_read_executed"])

    def test_invalid_schema_stops_without_a_followup(self):
        caller,seen=self.scripted({"category":"INFORMATION"})
        session=start_dialogue("メインPCについて",model="gemma3:12b",caller=caller)
        self.assertEqual(session["status"],"stopped")
        self.assertEqual(session["next_step"],"magi_invalid")
        self.assertEqual(len(seen),1)
        self.assertIn("response:field_mismatch",session["turns"][0]["errors"])

    def test_schema_category_types_and_missing_information_require_requests(self):
        self.assertIn("KNOWLEDGE",CATEGORIES)
        c=self.classification()
        c["multiple_requests"]="false"
        self.assertIn("multiple_requests:invalid_type",validate_turn("classify",c))
        self.assertIn("information_requests:required",validate_turn(
            "analyze",self.detail(state="NEED_INFORMATION") |
            {"information_requests":[]},
        ))

if __name__ == "__main__":
    unittest.main()
