from __future__ import annotations

from io import BytesIO
import json
from unittest.mock import patch
import unittest

from pkb_proto.magi_client import call_member, choose_model

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

if __name__ == "__main__":
    unittest.main()
