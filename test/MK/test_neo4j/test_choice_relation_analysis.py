import json
import sys
import tempfile
import unittest
from argparse import Namespace
from copy import deepcopy
from pathlib import Path


class ChoiceRelationAnalysisTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project_root = Path(__file__).resolve().parents[3]
        cls.neo4j_root = (
            cls.project_root / "etl" / "preprocessing" / "neo4j"
        )
        sys.path.insert(0, str(cls.neo4j_root))

        from choice_relation.analysis import (
            build_choice_relation_tasks,
            load_choice_relation_policy,
            load_problem_records,
            validate_choice_relation_decisions,
        )
        from choice_relation.executor import execute_choice_relation_tasks
        from choice_relation.evaluator import (
            build_choice_relation_evaluation_tasks,
            request_choice_relation_evaluation,
            validate_choice_relation_evaluations,
        )
        from choice_relation.evaluation import (
            evaluate_relation_predictions,
            load_relation_goldset,
        )
        from run_choice_relation_analysis import run_choice_relation_analysis

        cls.build_tasks = staticmethod(build_choice_relation_tasks)
        cls.load_policy = staticmethod(load_choice_relation_policy)
        cls.load_problems = staticmethod(load_problem_records)
        cls.validate_decisions = staticmethod(
            validate_choice_relation_decisions
        )
        cls.run_analysis = staticmethod(run_choice_relation_analysis)
        cls.execute_tasks = staticmethod(execute_choice_relation_tasks)
        cls.build_evaluation_tasks = staticmethod(
            build_choice_relation_evaluation_tasks
        )
        cls.validate_evaluations = staticmethod(
            validate_choice_relation_evaluations
        )
        cls.request_evaluation = staticmethod(
            request_choice_relation_evaluation
        )
        cls.evaluate_relations = staticmethod(evaluate_relation_predictions)
        cls.load_relation_goldset = staticmethod(load_relation_goldset)
        cls.config_path = (
            cls.neo4j_root / "config" / "choice_relation.json"
        )
        cls.policy = cls.load_policy(str(cls.config_path))

    def make_problem(
        self,
        problem_id: str = "problem-1",
        data_source: str = "han_cj_v41",
        question_task: str = "standard_select",
        question: str = "(가) 나라에 대한 설명으로 옳은 것은?",
    ) -> dict:
        choice_texts = [
            "영고라는 제천 행사를 열었다.",
            "신성 구역인 소도를 두었다.",
            "민며느리제가 있었다.",
            "책화라는 풍습이 있었다.",
            "많은 소국으로 이루어졌다.",
        ]
        return {
            "problem_id": problem_id,
            "data_source": data_source,
            "question_task": question_task,
            "material": "(가)는 사출도를 둔 연맹 왕국이다.",
            "question": question,
            "topic": "부여",
            "topic_type": "국가",
            "major_type": "역사 자료의 분석 및 해석",
            "minor_type": "자료 기반 시대·대상 추론",
            "difficulty_label": "쉬움",
            "answer_choice": choice_texts[0],
            "distractor_choices": choice_texts[1:],
            "choices": [
                {
                    "is_answer": index == 1,
                    "content": text,
                }
                for index, text in enumerate(choice_texts, start=1)
            ],
        }

    def make_fact(
        self,
        subject: str,
        predicate: str,
        object_value: str,
    ) -> dict:
        return {
            "subject": subject,
            "predicate": predicate,
            "object": object_value,
            "era": "초기 국가",
            "location": "",
        }

    def make_decision(self, task: dict) -> dict:
        answer_choice_id = task["answer_choice_id"]
        claims = []
        relations = []
        actual_subjects = ["부여", "삼한", "옥저", "동예", "삼한"]
        for choice in task["choices"]:
            is_answer = choice["is_answer_key"]
            contextual_validity = "DOES_NOT_MATCH_TARGET"
            if is_answer:
                contextual_validity = "MATCHES_TARGET"
            claims.append(
                {
                    "choice_id": choice["choice_id"],
                    "choice_index": choice["choice_index"],
                    "contextual_validity": contextual_validity,
                    "standalone_fact_status": "VALID_FACT",
                    "contextual_claim": self.make_fact(
                        "부여",
                        "보유",
                        choice["text"],
                    ),
                    "actual_fact": self.make_fact(
                        actual_subjects[choice["choice_index"] - 1],
                        "보유",
                        choice["text"],
                    ),
                    "explanation": "실제 풍습의 주체를 비교했다.",
                }
            )
            if not is_answer:
                relations.append(
                    {
                        "answer_choice_id": answer_choice_id,
                        "distractor_choice_id": choice["choice_id"],
                        "primary_relation_type": "TARGET_SWAP",
                        "secondary_relation_types": [],
                        "shared_dimensions": [
                            "PREDICATE",
                            "ERA",
                            "THEME",
                        ],
                        "changed_dimensions": ["TARGET", "OBJECT"],
                        "proximity": "NEAR",
                        "confidence": 0.95,
                        "explanation": "다른 초기 국가의 풍습이다.",
                    }
                )
        return {
            "choice_relation_task_id": task["choice_relation_task_id"],
            "problem_id": task["problem_id"],
            "decision_status": "PROPOSED",
            "review_model": self.policy["generator_model"]["model"],
            "prompt_version": self.policy["prompt_version"],
            "analysis_status": "ANALYZED",
            "question_target": {
                "name": "부여",
                "entity_type": "국가",
                "era": "초기 국가",
                "theme": "풍습",
                "inference_basis": "사출도",
            },
            "choice_claims": claims,
            "distractor_relations": relations,
            "confidence": 0.95,
            "reason": "부여와 다른 초기 국가의 풍습을 비교한 문항이다.",
        }

    def make_evaluation(self, evaluation_task: dict) -> dict:
        proposal = evaluation_task["generator_proposal"]
        return {
            "choice_relation_evaluation_id": evaluation_task[
                "choice_relation_evaluation_id"
            ],
            "choice_relation_task_id": evaluation_task[
                "choice_relation_task_id"
            ],
            "problem_id": evaluation_task["problem_id"],
            "review_model": self.policy["evaluator_model"]["model"],
            "prompt_version": self.policy["evaluator"]["prompt_version"],
            "input_quality_status": "CLEAN",
            "input_quality_issues": [],
            "target_status": "SUPPORTED",
            "target_reason": "문항의 대상과 시대를 확인했습니다.",
            "evidence_sources": [
                {
                    "url": "https://example.org/history-source",
                    "title": "검증용 역사 자료",
                }
            ],
            "choice_reviews": [
                {
                    "choice_id": claim["choice_id"],
                    "claim_status": "SUPPORTED",
                    "corrected_actual_fact": deepcopy(
                        claim["actual_fact"]
                    ),
                    "reason": "제안된 실제 사실과 일치합니다.",
                }
                for claim in proposal["choice_claims"]
            ],
            "relation_reviews": [
                {
                    "distractor_choice_id": relation[
                        "distractor_choice_id"
                    ],
                    "relation_status": "SUPPORTED",
                    "corrected_primary_relation_type": relation[
                        "primary_relation_type"
                    ],
                    "corrected_secondary_relation_types": deepcopy(
                        relation["secondary_relation_types"]
                    ),
                    "reason": "제안된 관계 분류와 일치합니다.",
                }
                for relation in proposal["distractor_relations"]
            ],
            "confidence": 0.95,
            "summary": "모든 사실과 관계가 독립 평가에서 확인되었습니다.",
        }

    def test_scope_includes_image_questions_and_excludes_non_standard(self):
        records = [
            self.make_problem(),
            self.make_problem(
                problem_id="ocr-1",
                data_source="han_cj_v41_image",
            ),
            self.make_problem(
                problem_id="negative-1",
                question_task="negative_select",
            ),
        ]

        result = self.build_tasks(records, self.policy)

        self.assertEqual(len(result["tasks"]), 2)
        self.assertEqual(len(result["source_choices"]), 10)
        self.assertEqual(
            result["summary"]["excluded_data_source_count"],
            0,
        )
        self.assertEqual(
            result["summary"]["excluded_question_task_count"],
            1,
        )

    def test_negative_wording_is_excluded_even_if_task_label_is_standard(self):
        records = [
            self.make_problem(
                question="(가) 국가의 문화유산으로 옳지 않은 것은?"
            )
        ]

        result = self.build_tasks(records, self.policy)

        self.assertFalse(result["tasks"])
        self.assertEqual(
            result["summary"]["excluded_negative_wording_count"],
            1,
        )
        self.assertEqual(
            result["exclusions"].iloc[0]["reason_code"],
            "NEGATIVE_WORDING_OUT_OF_SCOPE",
        )

    def test_explicit_problem_ids_select_only_requested_tasks(self):
        records = [
            self.make_problem(problem_id="problem-1"),
            self.make_problem(problem_id="problem-2"),
        ]

        result = self.build_tasks(
            records,
            self.policy,
            selected_problem_ids={"problem-2"},
        )

        self.assertEqual(
            [task["problem_id"] for task in result["tasks"]],
            ["problem-2"],
        )
        self.assertEqual(
            result["summary"]["excluded_problem_id_filter_count"],
            1,
        )

    def test_unknown_explicit_problem_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "현재 분석 범위"):
            self.build_tasks(
                [self.make_problem()],
                self.policy,
                selected_problem_ids={"unknown-problem"},
            )

    def test_valid_decision_creates_five_claims_and_four_relations(self):
        preparation = self.build_tasks(
            [self.make_problem()],
            self.policy,
        )
        task = preparation["tasks"][0]

        validated = self.validate_decisions(
            [self.make_decision(task)],
            [task],
            self.policy,
        )

        self.assertEqual(len(validated["choice_claims"]), 5)
        self.assertEqual(len(validated["distractor_relations"]), 4)
        self.assertTrue(validated["validation_errors"].empty)
        self.assertEqual(
            validated["decisions"].iloc[0]["verification_status"],
            "VERIFIED",
        )

    def test_generator_and_evaluator_models_are_separated(self):
        self.assertEqual(
            self.policy["generator_model"]["model"],
            "gpt-5.6-terra",
        )
        self.assertEqual(
            self.policy["evaluator_model"]["model"],
            "gpt-5.6-sol",
        )
        self.assertEqual(
            self.policy["evaluator_model"]["reasoning_effort"],
            "high",
        )

    def test_supported_independent_evaluation_is_final_verified(self):
        preparation = self.build_tasks(
            [self.make_problem()],
            self.policy,
        )
        task = preparation["tasks"][0]
        proposal = self.make_decision(task)
        evaluation_task = self.build_evaluation_tasks(
            [task],
            [proposal],
            self.policy,
        )[0]

        evaluated = self.validate_evaluations(
            [self.make_evaluation(evaluation_task)],
            [evaluation_task],
            self.policy,
        )

        self.assertEqual(
            evaluated["summary"].iloc[0][
                "final_verification_status"
            ],
            "FINAL_VERIFIED",
        )
        self.assertTrue(evaluated["validation_errors"].empty)

    def test_contradicted_relation_is_auto_corrected(self):
        preparation = self.build_tasks(
            [self.make_problem()],
            self.policy,
        )
        task = preparation["tasks"][0]
        evaluation_task = self.build_evaluation_tasks(
            [task],
            [self.make_decision(task)],
            self.policy,
        )[0]
        evaluation = self.make_evaluation(evaluation_task)
        evaluation["relation_reviews"][0]["relation_status"] = (
            "CONTRADICTED"
        )
        evaluation["relation_reviews"][0][
            "corrected_primary_relation_type"
        ] = "LOCATION_SWAP"

        evaluated = self.validate_evaluations(
            [evaluation],
            [evaluation_task],
            self.policy,
        )

        self.assertEqual(
            evaluated["summary"].iloc[0][
                "final_verification_status"
            ],
            "AUTO_CORRECTED",
        )
        self.assertIn(
            "RELATION_AUTO_CORRECTED",
            evaluated["summary"].iloc[0]["review_reason_codes_json"],
        )

    def test_unverifiable_relation_requires_manual_review(self):
        preparation = self.build_tasks(
            [self.make_problem()],
            self.policy,
        )
        task = preparation["tasks"][0]
        evaluation_task = self.build_evaluation_tasks(
            [task],
            [self.make_decision(task)],
            self.policy,
        )[0]
        evaluation = self.make_evaluation(evaluation_task)
        evaluation["relation_reviews"][0]["relation_status"] = (
            "UNVERIFIABLE"
        )

        evaluated = self.validate_evaluations(
            [evaluation],
            [evaluation_task],
            self.policy,
        )

        self.assertEqual(
            evaluated["summary"].iloc[0][
                "final_verification_status"
            ],
            "NEEDS_MANUAL_REVIEW",
        )
        self.assertIn(
            "RELATION_REVIEW_REQUIRED",
            evaluated["summary"].iloc[0]["review_reason_codes_json"],
        )

    def test_contradicted_claim_requires_manual_review(self):
        preparation = self.build_tasks(
            [self.make_problem()],
            self.policy,
        )
        task = preparation["tasks"][0]
        evaluation_task = self.build_evaluation_tasks(
            [task],
            [self.make_decision(task)],
            self.policy,
        )[0]
        evaluation = self.make_evaluation(evaluation_task)
        evaluation["choice_reviews"][0]["claim_status"] = "CONTRADICTED"

        evaluated = self.validate_evaluations(
            [evaluation],
            [evaluation_task],
            self.policy,
        )

        self.assertEqual(
            evaluated["summary"].iloc[0][
                "final_verification_status"
            ],
            "NEEDS_MANUAL_REVIEW",
        )
        self.assertIn(
            "CLAIM_REVIEW_REQUIRED",
            evaluated["summary"].iloc[0]["review_reason_codes_json"],
        )

    def test_missing_web_evidence_requires_manual_review(self):
        preparation = self.build_tasks(
            [self.make_problem()],
            self.policy,
        )
        task = preparation["tasks"][0]
        evaluation_task = self.build_evaluation_tasks(
            [task],
            [self.make_decision(task)],
            self.policy,
        )[0]
        evaluation = self.make_evaluation(evaluation_task)
        evaluation["evidence_sources"] = []

        evaluated = self.validate_evaluations(
            [evaluation],
            [evaluation_task],
            self.policy,
        )

        self.assertEqual(
            evaluated["summary"].iloc[0][
                "final_verification_status"
            ],
            "NEEDS_MANUAL_REVIEW",
        )
        self.assertIn(
            "WEB_EVIDENCE_REQUIRED",
            evaluated["summary"].iloc[0]["review_reason_codes_json"],
        )

    def test_clean_status_with_minor_issue_is_final_verified(self):
        preparation = self.build_tasks(
            [self.make_problem()],
            self.policy,
        )
        task = preparation["tasks"][0]
        evaluation_task = self.build_evaluation_tasks(
            [task],
            [self.make_decision(task)],
            self.policy,
        )[0]
        evaluation = self.make_evaluation(evaluation_task)
        evaluation["input_quality_issues"] = ["경미한 입력 문제"]

        evaluated = self.validate_evaluations(
            [evaluation],
            [evaluation_task],
            self.policy,
        )

        self.assertEqual(
            evaluated["summary"].iloc[0][
                "final_verification_status"
            ],
            "FINAL_VERIFIED",
        )
        self.assertTrue(evaluated["validation_errors"].empty)

    def test_missing_visual_context_does_not_block_final_verification(self):
        preparation = self.build_tasks(
            [self.make_problem()],
            self.policy,
        )
        task = preparation["tasks"][0]
        evaluation_task = self.build_evaluation_tasks(
            [task],
            [self.make_decision(task)],
            self.policy,
        )[0]
        evaluation = self.make_evaluation(evaluation_task)
        evaluation["input_quality_status"] = "MISSING_CONTEXT"
        evaluation["input_quality_issues"] = ["원본 도판 없음"]

        evaluated = self.validate_evaluations(
            [evaluation],
            [evaluation_task],
            self.policy,
        )

        self.assertEqual(
            evaluated["summary"].iloc[0][
                "final_verification_status"
            ],
            "FINAL_VERIFIED",
        )

    def test_semantically_blocking_ocr_requires_manual_review(self):
        preparation = self.build_tasks(
            [self.make_problem()],
            self.policy,
        )
        task = preparation["tasks"][0]
        evaluation_task = self.build_evaluation_tasks(
            [task],
            [self.make_decision(task)],
            self.policy,
        )[0]
        evaluation = self.make_evaluation(evaluation_task)
        evaluation["input_quality_status"] = "OCR_SUSPECTED"
        evaluation["input_quality_issues"] = ["핵심 연도를 복원할 수 없음"]

        evaluated = self.validate_evaluations(
            [evaluation],
            [evaluation_task],
            self.policy,
        )

        self.assertEqual(
            evaluated["summary"].iloc[0][
                "final_verification_status"
            ],
            "NEEDS_MANUAL_REVIEW",
        )

    def test_supported_fact_rephrasing_does_not_force_manual_review(self):
        preparation = self.build_tasks(
            [self.make_problem()],
            self.policy,
        )
        task = preparation["tasks"][0]
        evaluation_task = self.build_evaluation_tasks(
            [task],
            [self.make_decision(task)],
            self.policy,
        )[0]
        evaluation = self.make_evaluation(evaluation_task)
        evaluation["choice_reviews"][0]["corrected_actual_fact"][
            "object"
        ] = "의미가 같은 다른 표현"

        evaluated = self.validate_evaluations(
            [evaluation],
            [evaluation_task],
            self.policy,
        )

        self.assertEqual(
            evaluated["summary"].iloc[0][
                "final_verification_status"
            ],
            "FINAL_VERIFIED",
        )

    def test_evaluator_request_enables_web_search_and_records_sources(self):
        preparation = self.build_tasks(
            [self.make_problem()],
            self.policy,
        )
        task = preparation["tasks"][0]
        evaluation_task = self.build_evaluation_tasks(
            [task],
            [self.make_decision(task)],
            self.policy,
        )[0]
        raw_evaluation = self.make_evaluation(evaluation_task)
        raw_evaluation.pop("evidence_sources")
        captured_arguments = {}

        class FakeResponse:
            output_text = json.dumps(
                raw_evaluation,
                ensure_ascii=False,
            )
            output = [
                {
                    "type": "web_search_call",
                    "action": {
                        "type": "search",
                        "sources": [
                            {
                                "url": (
                                    "https://example.org/source"
                                    "?utm_source=openai"
                                ),
                                "title": "역사 자료",
                            }
                        ],
                    },
                }
            ]
            id = "response-1"
            usage = None

        class FakeResponses:
            def create(self, **arguments: object) -> FakeResponse:
                captured_arguments.update(arguments)
                return FakeResponse()

        class FakeClient:
            responses = FakeResponses()

        evaluation, _ = self.request_evaluation(
            FakeClient(),
            evaluation_task,
            "prompt",
            {},
            self.policy,
        )

        self.assertEqual(
            captured_arguments["tools"],
            [self.policy["evaluator"]["web_search"]["tool"]],
        )
        self.assertEqual(captured_arguments["max_tool_calls"], 5)
        self.assertEqual(
            captured_arguments["include"],
            ["web_search_call.action.sources"],
        )
        self.assertEqual(
            evaluation["evidence_sources"][0]["url"],
            "https://example.org/source",
        )

    def test_supported_evaluator_overrides_generator_claim_uncertainty(self):
        preparation = self.build_tasks(
            [self.make_problem()],
            self.policy,
        )
        task = preparation["tasks"][0]
        proposal = self.make_decision(task)
        proposal["choice_claims"][0][
            "standalone_fact_status"
        ] = "CONTEXT_DEPENDENT"
        evaluation_task = self.build_evaluation_tasks(
            [task],
            [proposal],
            self.policy,
        )[0]

        evaluated = self.validate_evaluations(
            [self.make_evaluation(evaluation_task)],
            [evaluation_task],
            self.policy,
        )

        self.assertEqual(
            evaluated["summary"].iloc[0][
                "final_verification_status"
            ],
            "FINAL_VERIFIED",
        )

    def test_changed_generator_proposal_creates_new_evaluation_id(self):
        preparation = self.build_tasks(
            [self.make_problem()],
            self.policy,
        )
        task = preparation["tasks"][0]
        first_proposal = self.make_decision(task)
        second_proposal = deepcopy(first_proposal)
        second_proposal["reason"] = "생성 제안을 수정했습니다."

        first_task = self.build_evaluation_tasks(
            [task],
            [first_proposal],
            self.policy,
        )[0]
        second_task = self.build_evaluation_tasks(
            [task],
            [second_proposal],
            self.policy,
        )[0]

        self.assertNotEqual(
            first_task["choice_relation_evaluation_id"],
            second_task["choice_relation_evaluation_id"],
        )

    def test_missing_distractor_relation_is_invalid(self):
        preparation = self.build_tasks(
            [self.make_problem()],
            self.policy,
        )
        task = preparation["tasks"][0]
        decision = self.make_decision(task)
        decision["distractor_relations"] = decision[
            "distractor_relations"
        ][:-1]

        validated = self.validate_decisions(
            [decision],
            [task],
            self.policy,
        )

        self.assertEqual(
            validated["decisions"].iloc[0]["verification_status"],
            "INVALID",
        )
        self.assertIn(
            "MISSING_DISTRACTOR_RELATION",
            set(validated["validation_errors"]["error_code"]),
        )

    def test_missing_question_reference_is_retained_with_integrity_signal(self):
        problem = self.make_problem(
            question="㉠, ㉡에 대한 설명으로 옳은 것은?"
        )
        preparation = self.build_tasks([problem], self.policy)

        self.assertEqual(len(preparation["tasks"]), 1)
        self.assertEqual(len(preparation["input_integrity_issues"]), 1)
        self.assertEqual(
            preparation["summary"]["excluded_missing_reference_count"],
            0,
        )
        self.assertTrue(preparation["exclusions"].empty)

    def test_missing_reference_does_not_block_generator_verification(self):
        problem = self.make_problem(
            question="㉠, ㉡에 대한 설명으로 옳은 것은?"
        )
        preparation = self.build_tasks([problem], self.policy)
        task = preparation["tasks"][0]
        validated = self.validate_decisions(
            [self.make_decision(task)],
            [task],
            self.policy,
        )

        self.assertEqual(
            validated["decisions"].iloc[0]["verification_status"],
            "VERIFIED",
        )

    def test_current_dataset_retains_missing_visual_context_tasks(self):
        problem_path = self.project_root / "ai" / "ml" / "ML_han_v1.json"
        records = self.load_problems(str(problem_path))

        preparation = self.build_tasks(records, self.policy)

        self.assertEqual(len(preparation["tasks"]), 1276)
        self.assertEqual(len(preparation["source_choices"]), 6380)
        self.assertEqual(
            preparation["summary"]["missing_reference_problem_count"],
            119,
        )
        self.assertEqual(
            preparation["summary"]["excluded_missing_reference_count"],
            0,
        )

    def test_runner_defaults_to_dry_run(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            input_path = temporary_path / "problems.json"
            input_path.write_text(
                json.dumps(
                    [self.make_problem()],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            output_path = temporary_path / "output"
            arguments = Namespace(
                config=str(self.config_path),
                input=str(input_path),
                output_dir=str(output_path),
                limit=0,
                problem_id=[],
                decisions="",
                execute=False,
                execute_limit=0,
                execute_all=False,
            )

            manifest = self.run_analysis(arguments)

            self.assertEqual(manifest["execution"]["mode"], "DRY_RUN")
            self.assertEqual(manifest["status"], "PREPARED")
            self.assertEqual(
                manifest["execution"]["pending_task_count"],
                1,
            )
            self.assertTrue(
                (output_path / self.policy["paths"]["tasks"]).is_file()
            )
            self.assertFalse(
                (output_path / self.policy["paths"]["decisions"]).exists()
            )

    def test_runner_reuses_separated_checkpoints_and_writes_final_outputs(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            input_path = temporary_path / "problems.json"
            input_path.write_text(
                json.dumps(
                    [self.make_problem()],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            output_path = temporary_path / "output"
            output_path.mkdir()
            task = self.build_tasks(
                [self.make_problem()],
                self.policy,
            )["tasks"][0]
            proposal = self.make_decision(task)
            evaluation_task = self.build_evaluation_tasks(
                [task],
                [proposal],
                self.policy,
            )[0]
            evaluation = self.make_evaluation(evaluation_task)
            evaluation["relation_reviews"][0]["relation_status"] = (
                "CONTRADICTED"
            )
            evaluation["relation_reviews"][0][
                "corrected_primary_relation_type"
            ] = "LOCATION_SWAP"

            generator_checkpoint = {
                "choice_relation_task_id": task[
                    "choice_relation_task_id"
                ],
                "problem_id": task["problem_id"],
                "review_model": self.policy["generator_model"]["model"],
                "prompt_version": self.policy["prompt_version"],
                "analysis_policy_version": self.policy["policy_version"],
                "decision": proposal,
            }
            evaluator_checkpoint = {
                "choice_relation_evaluation_id": evaluation_task[
                    "choice_relation_evaluation_id"
                ],
                "choice_relation_task_id": task[
                    "choice_relation_task_id"
                ],
                "problem_id": task["problem_id"],
                "proposal_digest": evaluation_task["proposal_digest"],
                "review_model": self.policy["evaluator_model"]["model"],
                "prompt_version": self.policy["evaluator"][
                    "prompt_version"
                ],
                "evaluator_policy_version": self.policy["evaluator"][
                    "policy_version"
                ],
                "evaluation": evaluation,
            }
            (
                output_path / self.policy["paths"]["checkpoint"]
            ).write_text(
                json.dumps(generator_checkpoint, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (
                output_path / self.policy["paths"]["evaluator_checkpoint"]
            ).write_text(
                json.dumps(evaluator_checkpoint, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            arguments = Namespace(
                config=str(self.config_path),
                input=str(input_path),
                output_dir=str(output_path),
                limit=0,
                problem_id=[],
                decisions="",
                execute=False,
                execute_generator=False,
                execute_evaluator=True,
                execute_limit=1,
                execute_all=False,
            )

            manifest = self.run_analysis(arguments)

            self.assertEqual(manifest["status"], "COMPLETED")
            self.assertEqual(
                manifest["execution"]["mode"],
                "CHECKPOINT_REUSE",
            )
            self.assertEqual(
                manifest["evaluator_execution"][
                    "reused_checkpoint_count"
                ],
                1,
            )
            self.assertEqual(
                manifest["final"]["final_accepted_decision_count"],
                1,
            )
            self.assertEqual(
                manifest["final"]["final_verified_decision_count"],
                0,
            )
            self.assertEqual(
                manifest["final"]["auto_corrected_decision_count"],
                1,
            )
            self.assertEqual(
                manifest["final"]["final_distractor_relation_count"],
                4,
            )
            final_relations_text = (
                output_path
                / self.policy["paths"]["final_distractor_relations"]
            ).read_text(encoding="utf-8-sig")
            self.assertIn("LOCATION_SWAP", final_relations_text)
            self.assertIn("EVALUATOR_CORRECTED", final_relations_text)
            self.assertTrue(
                (
                    output_path
                    / self.policy["paths"]["final_distractor_relations"]
                ).is_file()
            )

    def test_non_retryable_api_error_is_attempted_once_and_stays_pending(self):
        preparation = self.build_tasks(
            [self.make_problem()],
            self.policy,
        )
        task = preparation["tasks"][0]
        attempts = []

        def quota_failure(
            client: object,
            input_task: dict,
            prompt: str,
            schema: dict,
            policy: dict,
        ) -> tuple[dict, dict]:
            attempts.append(input_task["choice_relation_task_id"])
            raise RuntimeError("insufficient_quota")

        with tempfile.TemporaryDirectory() as temporary_directory:
            checkpoint_path = (
                Path(temporary_directory) / "checkpoint.jsonl"
            )
            execution = self.execute_tasks(
                [task],
                "prompt",
                {},
                str(checkpoint_path),
                self.policy,
                object(),
                requester=quota_failure,
            )

        self.assertEqual(len(attempts), 1)
        self.assertEqual(execution["failed_task_count"], 1)
        self.assertEqual(execution["completed_task_count"], 0)
        self.assertEqual(execution["pending_task_count"], 1)
        self.assertEqual(execution["failures"].iloc[0]["attempt_count"], 1)

    def test_seed_goldset_has_twenty_unique_pairs(self):
        goldset_path = (
            self.neo4j_root
            / "choice_relation"
            / "goldset"
            / "seed_expected_relations.csv"
        )

        goldset = self.load_relation_goldset(
            str(goldset_path),
            self.policy,
        )

        self.assertEqual(len(goldset), 20)
        self.assertEqual(goldset["problem_id"].nunique(), 5)
        self.assertTrue(
            (goldset.groupby("problem_id").size() == 4).all()
        )

    def test_perfect_seed_prediction_scores_one_but_is_not_official(self):
        goldset_path = (
            self.neo4j_root
            / "choice_relation"
            / "goldset"
            / "seed_expected_relations.csv"
        )
        goldset = self.load_relation_goldset(
            str(goldset_path),
            self.policy,
        )
        predictions = goldset.rename(
            columns={
                "gold_primary_relation_type": "primary_relation_type",
                "gold_secondary_relation_types_json": (
                    "secondary_relation_types_json"
                ),
            }
        )[
            [
                "problem_id",
                "answer_choice_id",
                "distractor_choice_id",
                "primary_relation_type",
                "secondary_relation_types_json",
            ]
        ]

        evaluation = self.evaluate_relations(
            predictions,
            goldset,
            self.policy,
        )

        self.assertEqual(evaluation["metrics"]["prediction_coverage"], 1.0)
        self.assertEqual(
            evaluation["metrics"]["primary_accuracy_overall"],
            1.0,
        )
        self.assertEqual(
            evaluation["metrics"]["relation_label_micro_f1"],
            1.0,
        )
        self.assertFalse(
            evaluation["metrics"]["official_evaluation_available"]
        )


if __name__ == "__main__":
    unittest.main()
