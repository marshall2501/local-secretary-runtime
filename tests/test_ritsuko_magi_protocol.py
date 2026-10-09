from __future__ import annotations

from io import BytesIO
import json
from unittest.mock import patch
import unittest

from ritsuko.magi.client import call_member, choose_model
from ritsuko.magi.dialogue import (
    CATEGORIES, PROMPT_VERSION, start_dialogue, continue_with_observation,
    continue_with_user_clarification, panel_member_specs,
    select_weighted_consensus, validate_turn, _call_ollama_guided,
    _call_openai_guided, _call_gemini_guided, call_guided_panel,
    _normalized_member_specs,
)

from ritsuko.magi.protocol import (
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
        with patch('ritsuko.magi.client.urlopen',
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
        from ritsuko.magi.protocol import (
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

    def test_resource_catalog_matches_executable_state_driven_sources(self):
        catalog=default_resource_catalog()
        self.assertEqual(catalog["pkb"]["access"],"read_only_via_ritsuko")
        self.assertTrue(catalog["web"]["available"])
        self.assertTrue(catalog["finance"]["available"])
        self.assertEqual(catalog["finance"]["confidentiality"],"private")
        self.assertFalse(catalog["files"]["available"])
        self.assertFalse(catalog["task_history"]["available"])
        self.assertEqual(catalog["user"]["access"],"ask_user")

    def test_sync_ollama_guided_disables_thinking(self):
        outer = {
            "message": {
                "content": json.dumps({
                    "category": "INFORMATION",
                    "understood_request": "GPUを知りたい",
                    "reason": "情報照会",
                    "confidence": "high",
                    "multiple_requests": False,
                }, ensure_ascii=False)
            },
            "done_reason": "stop",
        }
        captured = {}

        def fake_urlopen(req, timeout):
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            return BytesIO(json.dumps(outer, ensure_ascii=False).encode("utf-8"))

        with patch("ritsuko.magi.dialogue.urlopen", side_effect=fake_urlopen):
            result = _call_ollama_guided(
                {"stage": "classify"},
                model="qwen3.5:4b",
                timeout=1,
            )

        self.assertEqual(result["status"], "ok")
        self.assertIs(captured["payload"]["think"], False)

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
            "category":category,
            "understood_request":(
                "何かの状態確認を求めているが参照対象を特定できない"
                if category=="UNCLEAR" else "ユーザーが必要な情報を知りたい"
            ),
            "reason":"ユーザーが求める最終結果を基準に分類",
            "confidence":"high",
            "multiple_requests":multiple,
        }

    def detail(self, *, state="NEED_INFORMATION", source="pkb", what="本人の構成"):
        return {
            "understood_request":"求める情報を調べる",
            "state":state,"reason":"取得済みの情報では回答できない",
            "information_requests":(
                [{"source":source,"what":what,"reason":"回答の根拠が必要"}]
                if state=="NEED_INFORMATION" else []
            ),
            "question_for_user":("対象をもう少し具体的に教えてください" if state=="NEED_CLARIFICATION" else None),
            "answer_candidate":("観測された候補" if state=="READY" else None),
            "knowledge_candidate":("スマホはPixel 10" if state=="KNOWLEDGE_CANDIDATE" else None),
            "action_candidate":None,
        }

    def test_user_stop_after_current_turn_prevents_followup(self):
        stop = {"requested": False}
        seen = []

        def caller(envelope, *, model, timeout):
            seen.append(envelope)
            stop["requested"] = True
            return {
                "status": "ok",
                "response": self.classification(),
                "errors": [],
                "diagnostic": {},
            }

        session = start_dialogue(
            "富士山の高さを教えて",
            model="gemma3:12b",
            caller=caller,
            stop_requested=lambda: stop["requested"],
        )
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(session["turns"]), 1)
        self.assertEqual(session["classification"]["category"], "INFORMATION")
        self.assertEqual(session["status"], "stopped")
        self.assertEqual(session["next_step"], "user_requested_stop")

    def test_turn_progress_callback_reports_each_started_turn(self):
        caller, seen = self.scripted(
            self.classification(), self.detail(source="web", what="公開情報")
        )
        turns = []
        start_dialogue(
            "富士山の高さを教えて",
            model="gemma3:12b",
            caller=caller,
            on_turn_start=turns.append,
        )
        self.assertEqual(turns, [1, 2])
        self.assertEqual(len(seen), 2)

    def test_classification_drives_a_followup_question_and_core_owns_ids(self):
        caller, seen=self.scripted(
            self.classification(), self.detail(source="pkb",what="本人のメインPC GPU")
        )
        session=start_dialogue("メインPCのGPUの種類は？",model="gemma3:12b",caller=caller)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0]["stage"], "classify")
        self.assertIn("後続判断の入口",seen[0]["question_from_ritsuko"])
        self.assertIn("ユーザーが求めている結果",seen[0]["question_from_ritsuko"])
        self.assertNotIn("resource_catalog",seen[0])
        self.assertNotIn("task_context",seen[0])
        self.assertNotIn("observations",seen[0])
        self.assertEqual(seen[1]["stage"], "analyze")
        self.assertEqual(seen[1]["question_purpose"], "identify_missing_information")
        self.assertEqual(seen[1]["prompt_version"], PROMPT_VERSION)
        self.assertEqual(seen[1]["task_context"]["classification"]["category"],"INFORMATION")
        self.assertIn("依頼を進めるために必要な事実",seen[1]["question_from_ritsuko"])
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
                         ["classify","analyze","analyze"])
        self.assertEqual(seen[2]["question_purpose"],"review_or_repair")
        self.assertIn("実回答か作業予定か",seen[2]["question_from_ritsuko"])
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

    def test_unclear_classification_first_tries_disambiguation_sources(self):
        caller,seen=self.scripted(
            self.classification("UNCLEAR"),
            self.detail(state="NEED_INFORMATION",source="task_history",what="直前に参照していたTask"),
        )
        session=start_dialogue("あれどうなった？",model="gemma3:12b",caller=caller)
        self.assertEqual(session["status"],"waiting_information")
        self.assertEqual(session["pending_requests"][0]["source"],"task_history")
        self.assertEqual(seen[1]["question_purpose"],"understand_or_disambiguate")
        self.assertIn("すぐ本人へ質問せず",seen[1]["question_from_ritsuko"])
        self.assertEqual(len(seen),2)

    def test_unclear_can_still_stop_for_real_user_clarification(self):
        caller,seen=self.scripted(
            self.classification("UNCLEAR"),
            self.detail(state="NEED_CLARIFICATION"),
        )
        session=start_dialogue("それお願い",model="gemma3:12b",caller=caller)
        self.assertEqual(session["status"],"waiting_user")
        self.assertEqual(session["next_step"],"consider_user_question")
        self.assertEqual(seen[1]["question_purpose"],"understand_or_disambiguate")

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
        self.assertEqual(seen[-1]["question_purpose"],"evaluate_observation")
        self.assertEqual(seen[-1]["prompt_version"],PROMPT_VERSION)
        self.assertIn("新しく追加されたObservation",seen[-1]["question_from_ritsuko"])
        self.assertEqual(seen[-1]["observations"][0]["verified"],False)
        self.assertEqual(len(again["observations"]),1)
        self.assertFalse(again["tool_read_executed"])

    def test_action_category_selects_action_question_purpose(self):
        proposal=self.detail(state="ACTION_PROPOSAL")
        proposal["action_candidate"]="設定変更を提案する"
        caller,seen=self.scripted(self.classification("ACTION"),proposal)
        session=start_dialogue("設定を変更して",model="gemma3:12b",caller=caller)
        self.assertEqual(seen[1]["question_purpose"],"formulate_action")
        self.assertEqual(session["status"],"proposal_ready")

    def test_repeated_request_after_observation_gets_one_repair_turn(self):
        initial=self.detail(source="pkb",what="本人の構成")
        repeated=self.detail(source="pkb",what="本人の構成")
        repaired=self.detail(state="READY")
        caller,seen=self.scripted(self.classification(),initial,repeated,repaired)
        first=start_dialogue("私の構成は？",model="gemma3:12b",caller=caller)
        again=continue_with_observation(first,"架空の構成情報",caller=caller)
        self.assertEqual([x["question_purpose"] for x in seen],[
            "classify","identify_missing_information","evaluate_observation","review_or_repair"
        ])
        self.assertEqual(again["status"],"candidate_ready")
        self.assertEqual(len(again["turns"]),4)

    def test_session_records_prompt_version_and_previous_question_purpose(self):
        caller,seen=self.scripted(self.classification(),self.detail(source="web"))
        session=start_dialogue("最新情報は？",model="gemma3:12b",caller=caller)
        self.assertEqual(session["prompt_version"],PROMPT_VERSION)
        self.assertEqual(session["last_question_purpose"],"identify_missing_information")
        self.assertNotIn("previous_turns",seen[1]["task_context"])
    def test_knowledge_category_uses_minimal_candidate_purpose(self):
        candidate=self.detail(state="KNOWLEDGE_CANDIDATE")
        caller,seen=self.scripted(self.classification("KNOWLEDGE"),candidate)
        session=start_dialogue("スマホをPixel 10に買い替えた",model="gemma3:12b",caller=caller)
        self.assertEqual(seen[1]["question_purpose"],"formulate_knowledge_candidate")
        self.assertIn("記録に必要な事実",seen[1]["question_from_ritsuko"])
        self.assertEqual(session["status"],"proposal_ready")
        self.assertEqual(session["detail"]["knowledge_candidate"],"スマホはPixel 10")

    def test_knowledge_candidate_requires_explicit_candidate_and_no_requests(self):
        candidate=self.detail(state="KNOWLEDGE_CANDIDATE")
        candidate["knowledge_candidate"]=None
        self.assertIn("knowledge_candidate:required",validate_turn("analyze",candidate))
        candidate["knowledge_candidate"]="スマホはPixel 10"
        candidate["information_requests"]=[{"source":"pkb","what":"追加属性","reason":"補足"}]
        self.assertIn("information_requests:unexpected_for_knowledge_candidate",validate_turn("analyze",candidate))

    def test_v4_prompt_has_freshness_and_user_last_resort_rules(self):
        caller,seen=self.scripted(self.classification(),self.detail(source="web",what="公式の最新公開情報"))
        start_dialogue("最新ドライバーは？",model="gemma3:12b",caller=caller)
        prompt=seen[1]["question_from_ritsuko"]
        self.assertIn("fresh external source",prompt)
        self.assertIn("source=userは通常のread sourceではありません",prompt)
        self.assertEqual(PROMPT_VERSION,"d19-state-driven-v5-datetime")

    def test_user_source_is_reviewed_once_before_waiting_on_user(self):
        first=self.detail(source="user",what="症状の詳細")
        reviewed=self.detail(source="user",what="症状の詳細")
        caller,seen=self.scripted(self.classification("PROBLEM"),first,reviewed)
        session=start_dialogue("PCが固まる",model="gemma3:12b",caller=caller)
        self.assertEqual([x["question_purpose"] for x in seen],[
            "classify","identify_missing_information","review_or_repair"
        ])
        self.assertTrue(session["user_source_reviewed"])
        self.assertEqual(session["status"],"waiting_user")
        self.assertEqual(session["next_step"],"ask_user_for_information")

    def test_unclear_with_two_observations_uses_repair_instead_of_deeper_search(self):
        first=self.detail(source="task_history",what="直近Task")
        second=self.detail(source="task_history",what="完了後の履歴")
        final=self.detail(state="READY")
        caller,seen=self.scripted(self.classification("UNCLEAR"),first,second,final)
        s1=start_dialogue("あれどうなった？",model="gemma3:12b",caller=caller)
        s2=continue_with_observation(s1,"直近候補TaskはA",caller=caller)
        s3=continue_with_observation(s2,"他に候補はない",caller=caller)
        self.assertEqual(seen[-1]["question_purpose"],"review_or_repair")
        self.assertIn("際限なく広げない",seen[-1]["question_from_ritsuko"])
        self.assertEqual(s3["status"],"candidate_ready")

    def test_need_information_on_last_turn_is_terminal_not_waiting(self):
        initial=self.detail(source="task_history",what="履歴1")
        next1=self.detail(source="task_history",what="履歴2")
        last=self.detail(source="task_history",what="履歴3")
        caller,seen=self.scripted(self.classification("UNCLEAR"),initial,next1,last)
        s1=start_dialogue("あれ？",model="gemma3:12b",caller=caller)
        s2=continue_with_observation(s1,"候補A",caller=caller)
        s3=continue_with_observation(s2,"候補はAだけ",caller=caller)
        self.assertEqual(len(s3["turns"]),4)
        self.assertEqual(s3["status"],"stopped")
        self.assertEqual(s3["next_step"],"max_turns_reached_with_pending_information")
        self.assertTrue(s3["pending_requests"])
    def test_classify_contract_has_only_current_fields_and_unclear_needs_no_user_question(self):
        c=self.classification("UNCLEAR")
        self.assertEqual(validate_turn("classify",c),[])
        old=dict(c)
        old["clarification_question"]="何についてですか？"
        self.assertIn("response:field_mismatch",validate_turn("classify",old))
        old=dict(c)
        old["secondary_category"]=None
        self.assertIn("response:field_mismatch",validate_turn("classify",old))

    def test_multiple_requests_are_detected_but_not_auto_split_or_executed(self):
        caller,seen=self.scripted(self.classification(multiple=True))
        session=start_dialogue("GPUを調べて。あと家計も見て。",model="gemma3:12b",caller=caller)
        self.assertEqual(len(seen),1)
        self.assertEqual(session["status"],"stopped")
        self.assertEqual(session["next_step"],"multiple_requests_detected")

    def test_system_distinguishes_available_sources_from_observations(self):
        from ritsuko.magi.dialogue import SYSTEM
        self.assertIn("利用可能であることは、その内容を取得済みという意味ではありません",SYSTEM)
        self.assertIn("Observation",SYSTEM)
        self.assertIn("今回指定された判断だけ",SYSTEM)

    def test_panel_cloud_members_are_opt_in_and_have_configurable_weights(self):
        with patch.dict("os.environ", {
            "LSA_MAGI_CLOUD_ENABLED":"0",
            "LSA_MAGI_MELCHIOR_WEIGHT":"1.5",
        }, clear=False):
            specs=panel_member_specs("gemma3:12b")
        self.assertTrue(specs[0]["enabled"])
        self.assertEqual(specs[0]["weight"],1.5)
        self.assertFalse(specs[1]["enabled"])
        self.assertFalse(specs[2]["enabled"])

    def test_openai_adapter_uses_responses_structured_output_without_storing(self):
        output=self.classification("INFORMATION")
        outer={
            "id":"resp-test","status":"completed",
            "output":[{
                "type":"message",
                "content":[{"type":"output_text","text":json.dumps(output)}],
            }],
            "usage":{"input_tokens":10,"output_tokens":5},
        }
        with patch.dict("os.environ", {
            "OPENAI_API_KEY":"test-key",
            "OPENAI_BASE_URL":"https://api.openai.com/v1",
        }, clear=False), patch(
            "ritsuko.magi.dialogue.urlopen",
            return_value=BytesIO(json.dumps(outer).encode("utf-8")),
        ) as mocked:
            result=_call_openai_guided(
                {"stage":"classify","user_input":{"raw":"test"}},
                model="gpt-test",timeout=5,
            )
        self.assertEqual(result["status"],"ok")
        request=mocked.call_args.args[0]
        payload=json.loads(request.data.decode("utf-8"))
        self.assertFalse(payload["store"])
        self.assertEqual(payload["text"]["format"]["type"],"json_schema")
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertNotIn("test-key",json.dumps(result))

    def test_gemini_adapter_uses_structured_json_and_hides_key(self):
        output=self.classification("INFORMATION")
        outer={
            "candidates":[{
                "content":{"parts":[{"text":json.dumps(output)}]},
            }],
            "usageMetadata":{
                "promptTokenCount":10,
                "candidatesTokenCount":5,
                "totalTokenCount":15,
            },
        }
        with patch.dict("os.environ", {"GEMINI_API_KEY":"gemini-test-key"}, clear=False), patch(
            "ritsuko.magi.dialogue.urlopen",
            return_value=BytesIO(json.dumps(outer).encode("utf-8")),
        ) as mocked:
            result=_call_gemini_guided(
                {"stage":"classify","user_input":{"raw":"test"}},
                model="gemini-test",timeout=5,
            )
        self.assertEqual(result["status"],"ok")
        request=mocked.call_args.args[0]
        payload=json.loads(request.data.decode("utf-8"))
        response_format=payload["generationConfig"]["responseFormat"]
        self.assertEqual(response_format["text"]["mimeType"],"APPLICATION_JSON")
        self.assertIn("schema",response_format["text"])
        gemini_schema=response_format["text"]["schema"]
        self.assertNotIn("minLength",gemini_schema["properties"]["understood_request"])
        self.assertNotIn("minLength",gemini_schema["properties"]["reason"])
        self.assertFalse(gemini_schema["additionalProperties"])
        self.assertNotIn("responseMimeType",payload["generationConfig"])
        self.assertNotIn("responseSchema",payload["generationConfig"])
        self.assertNotIn("temperature",payload["generationConfig"])
        self.assertNotIn("gemini-test-key",json.dumps(result))

    def test_normalized_member_specs_preserve_connection_snapshot(self):
        specs = _normalized_member_specs([{
            "name": "MELCHIOR",
            "profile_id": "11111111-1111-1111-1111-111111111111",
            "connection_id": "22222222-2222-2222-2222-222222222222",
            "connection_display_name": "OpenAI primary",
            "provider": "openai",
            "model": "cloud-a",
            "endpoint": "https://example.invalid/v1",
            "credential_ref": "env:KEY_A",
            "credential_env": "KEY_A",
            "weight": 1.0,
            "timeout_seconds": 30,
            "enabled": True,
        }], "")
        self.assertEqual(
            specs[0]["connection_id"],
            "22222222-2222-2222-2222-222222222222",
        )
        self.assertEqual(specs[0]["credential_ref"], "env:KEY_A")
        self.assertEqual(
            specs[0]["connection_display_name"],
            "OpenAI primary",
        )

    def test_panel_accepts_arbitrary_provider_assignment_per_member(self):
        specs=[
            {"name":"MELCHIOR","provider":"openai","model":"cloud-a",
             "endpoint":"https://example.invalid/v1","credential_env":"KEY_A",
             "weight":1.0,"timeout_seconds":30,"enabled":True},
            {"name":"CASPER","provider":"ollama","model":"local-b",
             "endpoint":"http://127.0.0.1:11434","credential_env":None,
             "weight":1.0,"timeout_seconds":30,"enabled":True},
            {"name":"BALTHASAR","provider":"gemini","model":"cloud-c",
             "endpoint":"https://example.invalid/v1beta","credential_env":"KEY_C",
             "weight":1.0,"timeout_seconds":30,"enabled":True},
        ]
        responses={
            "MELCHIOR":self.classification("INFORMATION"),
            "CASPER":self.classification("INFORMATION"),
            "BALTHASAR":self.classification("PROBLEM"),
        }
        def fake_member(spec,envelope,*,timeout):
            return {
                "name":spec["name"],"provider":spec["provider"],"model":spec["model"],
                "weight":spec["weight"],"timeout_seconds":spec["timeout_seconds"],
                "status":"ok","response":responses[spec["name"]],
                "errors":[],"diagnostic":{},
            }
        with patch("ritsuko.magi.dialogue._call_panel_member",side_effect=fake_member):
            result=call_guided_panel(
                {"stage":"classify","magi_member":"MAGI_PANEL"},
                member_specs=specs,timeout=30,
            )
        self.assertEqual(result["status"],"ok")
        self.assertEqual(result["response"]["category"],"INFORMATION")
        assignments=result["diagnostic"]["assignments"]
        self.assertEqual(
            [(x["name"],x["provider"]) for x in assignments],
            [("MELCHIOR","openai"),("CASPER","ollama"),("BALTHASAR","gemini")],
        )

    def test_dialogue_can_start_from_db_style_member_specs_without_legacy_model(self):
        specs=[
            {"name":"MELCHIOR","provider":"ollama","model":"local-a",
             "endpoint":"http://127.0.0.1:11434","credential_env":None,
             "weight":1.0,"timeout_seconds":30,"enabled":True},
            {"name":"CASPER","provider":"openai","model":"cloud-b",
             "endpoint":"https://api.openai.com/v1","credential_env":"OPENAI_API_KEY",
             "weight":1.0,"timeout_seconds":30,"enabled":False},
            {"name":"BALTHASAR","provider":"gemini","model":"cloud-c",
             "endpoint":"https://generativelanguage.googleapis.com/v1beta",
             "credential_env":"GEMINI_API_KEY",
             "weight":1.0,"timeout_seconds":30,"enabled":False},
        ]
        classify=self.classification("INFORMATION")
        detail=self.detail(source="pkb")
        calls=[]
        def fake_member(spec,envelope,*,timeout):
            calls.append((spec["name"],spec["provider"],envelope["stage"]))
            response=classify if envelope["stage"]=="classify" else detail
            return {
                "name":spec["name"],"provider":spec["provider"],"model":spec["model"],
                "weight":spec["weight"],"timeout_seconds":spec["timeout_seconds"],
                "status":"ok","response":response,"errors":[],"diagnostic":{},
            }
        with patch("ritsuko.magi.dialogue._call_panel_member",side_effect=fake_member):
            session=start_dialogue("私の構成は？",member_specs=specs,timeout=30)
        self.assertEqual(session["status"],"waiting_information")
        self.assertEqual(session["member_specs"][0]["provider"],"ollama")
        self.assertEqual(calls,[
            ("MELCHIOR","ollama","classify"),
            ("MELCHIOR","ollama","analyze"),
        ])

    def test_weighted_consensus_uses_two_of_three_matching_decisions(self):
        a=self.classification("INFORMATION")
        b=self.classification("INFORMATION")
        c=self.classification("INVESTIGATION")
        result=select_weighted_consensus("classify",[
            {"name":"MELCHIOR","weight":1.0,"status":"ok","response":a},
            {"name":"CASPER","weight":1.0,"status":"ok","response":b},
            {"name":"BALTHASAR","weight":1.0,"status":"ok","response":c},
        ])
        self.assertEqual(result["status"],"ok")
        self.assertEqual(result["response"]["category"],"INFORMATION")
        self.assertEqual(result["decision_signature"],
                         "category=INFORMATION;multiple_requests=false")

    def test_information_investigation_weight_tie_uses_two_member_compatible_majority(self):
        info=self.classification("INFORMATION")
        investigation=self.classification("INVESTIGATION")
        result=select_weighted_consensus("classify",[
            {"name":"MELCHIOR","weight":1.0,"status":"ok","response":investigation},
            {"name":"CASPER","weight":2.0,"status":"ok","response":info},
            {"name":"BALTHASAR","weight":1.0,"status":"ok","response":investigation},
        ])
        self.assertEqual(result["status"],"ok")
        self.assertEqual(result["response"]["category"],"INVESTIGATION")
        self.assertEqual(result["reason"],
                         "compatible_classification_tie_member_majority")
        self.assertEqual(result["votes"],{
            "category=INVESTIGATION;multiple_requests=false":2.0,
            "category=INFORMATION;multiple_requests=false":2.0,
        })
        self.assertEqual(result["selected_member"],"MELCHIOR")
        self.assertEqual(result["valid_members"],
                         ["MELCHIOR","CASPER","BALTHASAR"])

    def test_information_investigation_tie_requires_all_three_unique_slots(self):
        info=self.classification("INFORMATION")
        investigation=self.classification("INVESTIGATION")
        result=select_weighted_consensus("classify",[
            {"name":"MELCHIOR","weight":1.0,"status":"ok","response":investigation},
            {"name":"CASPER","weight":2.0,"status":"ok","response":info},
            {"name":"MELCHIOR","weight":1.0,"status":"ok","response":investigation},
        ])
        self.assertEqual(result["status"],"disagreement")
        self.assertEqual(result["reason"],"weighted_vote_tie")

    def test_information_investigation_tie_rejects_multiple_request_difference(self):
        info=self.classification("INFORMATION")
        investigation=self.classification("INVESTIGATION")
        investigation["multiple_requests"]=True
        result=select_weighted_consensus("classify",[
            {"name":"MELCHIOR","weight":1.0,"status":"ok","response":investigation},
            {"name":"CASPER","weight":2.0,"status":"ok","response":info},
            {"name":"BALTHASAR","weight":1.0,"status":"ok",
             "response":self.classification("INVESTIGATION")},
        ])
        self.assertEqual(result["status"],"ok")
        # There is no compatible 2/2 category tie here: the strict signature
        # keeps the multi-request decision distinct and preserves weighted vote.
        self.assertEqual(result["response"]["category"],"INFORMATION")
        self.assertEqual(result["reason"],"weighted_vote")

    def test_information_investigation_tie_does_not_cover_action(self):
        info=self.classification("INFORMATION")
        action=self.classification("ACTION")
        result=select_weighted_consensus("classify",[
            {"name":"MELCHIOR","weight":1.0,"status":"ok","response":action},
            {"name":"CASPER","weight":2.0,"status":"ok","response":info},
            {"name":"BALTHASAR","weight":1.0,"status":"ok","response":action},
        ])
        self.assertEqual(result["status"],"disagreement")
        self.assertEqual(result["reason"],"weighted_vote_tie")

    def test_information_investigation_tie_needs_three_valid_members(self):
        info=self.classification("INFORMATION")
        investigation=self.classification("INVESTIGATION")
        result=select_weighted_consensus("classify",[
            {"name":"MELCHIOR","weight":2.0,"status":"ok","response":investigation},
            {"name":"CASPER","weight":2.0,"status":"ok","response":info},
            {"name":"BALTHASAR","weight":1.0,"status":"unavailable","response":None},
        ])
        self.assertEqual(result["status"],"disagreement")
        self.assertEqual(result["reason"],"weighted_vote_tie")

    def test_weight_can_override_member_count(self):
        info=self.classification("INFORMATION")
        problem=self.classification("PROBLEM")
        result=select_weighted_consensus("classify",[
            {"name":"MELCHIOR","weight":1.0,"status":"ok","response":info},
            {"name":"CASPER","weight":2.5,"status":"ok","response":problem},
            {"name":"BALTHASAR","weight":1.0,"status":"ok","response":info},
        ])
        self.assertEqual(result["status"],"ok")
        self.assertEqual(result["response"]["category"],"PROBLEM")
        self.assertEqual(result["selected_member"],"CASPER")

    def test_three_way_weighted_tie_requires_clarification(self):
        result=select_weighted_consensus("classify",[
            {"name":"MELCHIOR","weight":1.0,"status":"ok",
             "response":self.classification("INFORMATION")},
            {"name":"CASPER","weight":1.0,"status":"ok",
             "response":self.classification("PROBLEM")},
            {"name":"BALTHASAR","weight":1.0,"status":"ok",
             "response":self.classification("INVESTIGATION")},
        ])
        self.assertEqual(result["status"],"disagreement")
        self.assertIsNone(result["response"])

    def test_panel_disagreement_can_resume_with_user_context(self):
        def disagree(envelope, *, model, timeout):
            return {
                "status":"disagreement","response":None,
                "errors":["weighted_vote_tie"],"diagnostic":{},
                "member_results":[],
                "consensus":{"status":"disagreement","reason":"weighted_vote_tie"},
            }
        session=start_dialogue("それ確認して",model="gemma3:12b",caller=disagree)
        self.assertEqual(session["status"],"waiting_user")
        self.assertEqual(session["next_step"],"magi_disagreement_requires_clarification")
        self.assertIn("具体的",session["user_question"])

        caller,seen=self.scripted(
            self.classification("INFORMATION"),
            self.detail(source="pkb",what="対象の現在値"),
        )
        resumed=continue_with_user_clarification(
            session,"メインPCのGPUについて確認したい",caller=caller,
        )
        self.assertEqual(resumed["classification"]["category"],"INFORMATION")
        self.assertIn("conversation_context",seen[0])
        self.assertEqual(seen[0]["conversation_context"][-1]["text"],
                         "メインPCのGPUについて確認したい")
        self.assertEqual(resumed["status"],"waiting_information")

    def test_analyze_request_does_not_send_full_previous_turn_history(self):
        caller,seen=self.scripted(
            self.classification(), self.detail(source="pkb"),
        )
        start_dialogue("私の構成は？",model="gemma3:12b",caller=caller)
        self.assertNotIn("previous_turns",seen[1]["task_context"])

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
