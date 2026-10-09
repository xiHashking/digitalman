import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from knowledge_base import KnowledgeDocument, KnowledgeMatch, KnowledgeSearchResult
from knowledge_qa_agent import KnowledgeQaAnswer, KnowledgeQaEvidence
from knowledge_qa_agent import KnowledgeQaError
from llm import (
    AI_LITERACY_PRODUCT_POLICY,
    SpeechSegmenter,
    _build_system_prompt,
    _direct_guard_answer,
    _llm_model,
    _normalize_asr_query,
    _requires_ai_literacy_policy_reasoning,
    llm_response,
)


class SpeechSegmenterTestCase(unittest.TestCase):
    def test_environment_model_overrides_configured_default(self):
        opt = SimpleNamespace(llm_provider="dashscope", llm_model="qwen-turbo")

        with patch.dict("os.environ", {"LLM_MODEL": "deepseek-v4-flash"}):
            self.assertEqual(_llm_model(opt), "deepseek-v4-flash")

    def test_ai_literacy_policy_is_injected_for_matching_product(self):
        result = KnowledgeSearchResult(
            query="AI素养评价是什么？",
            matches=(
                KnowledgeMatch(
                    title="AI素养评价产品介绍",
                    text="产品资料",
                    score=10,
                    coverage=1,
                    matched_terms=("素养评价",),
                ),
            ),
        )

        prompt = _build_system_prompt("AI素养评价是什么？", result, 6000)

        self.assertIn(AI_LITERACY_PRODUCT_POLICY, prompt)
        self.assertIn("AI不能自动决定学生优劣", prompt)
        self.assertIn("需要进一步确认", prompt)
        self.assertNotIn("现有资料没有明确说明", prompt)

    def test_ai_literacy_policy_is_not_injected_for_other_products(self):
        result = KnowledgeSearchResult(query="招生系统有什么功能？", matches=())

        prompt = _build_system_prompt("招生系统有什么功能？", result, 6000)

        self.assertNotIn(AI_LITERACY_PRODUCT_POLICY, prompt)

    def test_fallback_prompt_forbids_unavailable_sales_actions(self):
        result = KnowledgeSearchResult(query="可以试点吗？", matches=())
        strategy = KnowledgeDocument(
            scope="数字员工策略",
            title="数字员工策略/main.md",
            text="需要时转人工。",
        )

        prompt = _build_system_prompt(
            "可以试点吗？",
            result,
            6000,
            strategy_documents=(strategy,),
        )

        self.assertIn("没有转人工、联系同事或发送文件的能力", prompt)
        self.assertIn("不得声称已经同步", prompt)

    def test_policy_guard_recognizes_ai_boundary_and_commitment_questions(self):
        ai_result = KnowledgeSearchResult(
            query="",
            matches=(
                KnowledgeMatch(
                    title="AI素养评价产品QA/AI素养评价产品QA",
                    text="",
                    score=10,
                    coverage=1,
                    matched_terms=(),
                    kind="qa",
                ),
            ),
        )

        self.assertTrue(
            _requires_ai_literacy_policy_reasoning("AI可以自动给学生打分吗？", ai_result)
        )
        self.assertTrue(
            _requires_ai_literacy_policy_reasoning("能完全替代老师录入吗？", ai_result)
        )
        for question in (
            "AI自己给学生打分并发布可以吗？",
            "AI能判断学生是不是故意的吗？",
            "能判断孩子是故意犯错的吗？",
            "能根据一个分数判断哪个学生更优秀吗？",
            "完全不需要老师录入和确认吗？",
            "可以不经过老师审核直接给家长看吗？",
            "学校管理决定可以交给AI吗？",
            "这个系统是不是专门考孩子的？",
        ):
            self.assertTrue(
                _requires_ai_literacy_policy_reasoning(question, ai_result),
                question,
            )
        self.assertTrue(
            _requires_ai_literacy_policy_reasoning("能保证一个月上线吗？", ai_result)
        )

    def test_policy_guard_prevents_wrong_qa_and_llm_from_answering(self):
        result = KnowledgeSearchResult(
            query="AI可以自动给学生打分吗？",
            matches=(
                KnowledgeMatch(
                    title="AI素养评价产品QA/AI素养评价产品QA",
                    text="错误候选答案",
                    score=50,
                    coverage=0.5,
                    matched_terms=("学生",),
                    kind="qa",
                    standard_question="系统能否进行家校互动？",
                    standard_answer="错误候选答案",
                ),
            ),
        )
        create = Mock(return_value=[])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        spoken = []
        avatar = SimpleNamespace(
            opt=SimpleNamespace(),
            put_msg_txt=lambda text, data: spoken.append(text),
        )

        with (
            patch("llm.search_knowledge", return_value=result),
            patch("llm._llm_client", return_value=client),
            patch("llm._llm_model", return_value="test-model"),
        ):
            llm_response("AI可以自动给学生打分吗？", avatar)

        create.assert_not_called()
        self.assertNotIn("错误候选答案", "".join(spoken))
        self.assertIn("不能", "".join(spoken))

    def test_company_unknown_facts_use_single_turn_strategy_and_stay_uncertain(self):
        spoken = []
        avatar = SimpleNamespace(
            opt=SimpleNamespace(),
            put_msg_txt=lambda text, data: spoken.append(text),
        )
        result = KnowledgeSearchResult(
            query="目前已经服务了多少学生？",
            matches=(),
        )
        answer = KnowledgeQaAnswer(
            answer="现有资料没有明确说明服务数量，需要进一步确认。",
            evidence=(),
            gaps=("服务学生数量",),
        )

        with (
            patch("llm.search_knowledge", return_value=result) as search,
            patch("llm._llm_client", return_value=Mock()) as client_factory,
            patch("llm._llm_model", return_value="test-model"),
            patch("llm.answer_knowledge_question", return_value=answer) as agent,
        ):
            llm_response("目前已经服务了多少学生？", avatar)

        search.assert_called_once_with("目前已经服务了多少学生？")
        client_factory.assert_called_once()
        agent.assert_called_once()
        self.assertIn("需要进一步确认", "".join(spoken))

    def test_unverified_custom_development_timeline_is_not_promised(self):
        spoken = []
        avatar = SimpleNamespace(
            opt=SimpleNamespace(),
            put_msg_txt=lambda text, data: spoken.append(text),
        )

        with (
            patch("llm.search_knowledge") as search,
            patch("llm._llm_client") as client_factory,
        ):
            llm_response("能不能一个月之内完成个性化开发？", avatar)

        search.assert_not_called()
        client_factory.assert_not_called()
        speech = "".join(spoken)
        self.assertIn("需要团队", speech)
        self.assertIn("不能直接承诺", speech)

    def test_pdf_request_does_not_claim_file_was_sent(self):
        spoken = []
        avatar = SimpleNamespace(
            opt=SimpleNamespace(),
            put_msg_txt=lambda text, data: spoken.append(text),
        )

        with (
            patch("llm.search_knowledge") as search,
            patch("llm._llm_client") as client_factory,
        ):
            llm_response("能发一份产品介绍PDF吗？", avatar)

        search.assert_not_called()
        client_factory.assert_not_called()
        speech = "".join(spoken)
        self.assertIn("不能直接发送PDF", speech)
        self.assertIn("需要在平板应用中单独配置", speech)

    def test_personal_app_operation_does_not_invent_page_steps(self):
        answer = _direct_guard_answer("个人版小程序在哪里查看成长报告？")

        self.assertIn("不能提供可靠的页面步骤", answer)
        self.assertIn("产品支持人员确认", answer)

    def test_voice_chat_request_matches_tablet_capability(self):
        self.assertEqual(
            _direct_guard_answer("咱可以发语音聊吗？"),
            "可以，您直接说就行，我会用语音回答。",
        )

    def test_personal_indicator_question_does_not_inherit_school_capability(self):
        answer = _direct_guard_answer("个人版能自定义评价指标吗？")

        self.assertIn("学校版支持自定义评价指标", answer)
        self.assertIn("个人版能否修改没有明确说明", answer)

    def test_school_price_calculates_the_requested_student_count(self):
        answer = _direct_guard_answer("4000名学生使用大概要多少钱？")

        self.assertIn("25元", answer)
        self.assertIn("4000名学生约10万元每年", answer)
        self.assertIn("学校统一采购", answer)

    def test_report_generation_uses_process_evidence_and_teacher_review(self):
        answer = _direct_guard_answer("学生成长报告是怎么生成的？")

        self.assertIn("平时的过程性记录", answer)
        self.assertIn("AI辅助", answer)
        self.assertIn("教师审核", answer)

    def test_incoherent_asr_is_not_repeated(self):
        self.assertEqual(
            _direct_guard_answer("你看在小红书上实页个分是这个呀"),
            "抱歉，我没听清，请再说一遍。",
        )
        self.assertEqual(
            _direct_guard_answer("的话的问题"),
            "抱歉，我没听清，请再说一遍。",
        )
        for fragment in (
            "产品没有问",
            "那就因为他",
            "没有是",
            "今天手感特很",
            "就比如先应加评论这种",
        ):
            self.assertEqual(
                _direct_guard_answer(fragment),
                "抱歉，我没听清，请再说一遍。",
                fragment,
            )

    def test_common_asr_product_name_is_normalized(self):
        self.assertEqual(
            _normalize_asr_query("介绍一下什么是二零四用评价"),
            "介绍一下什么是AI素养评价",
        )
        self.assertEqual(
            _normalize_asr_query("介绍一下 a i 素养评价"),
            "介绍一下 AI 素养评价",
        )

    def test_emits_first_complete_sentence_while_stream_is_open(self):
        segmenter = SpeechSegmenter()
        self.assertEqual(segmenter.feed('没问题，我给您简单介绍一下'), [])
        self.assertEqual(
            segmenter.feed('咱们的AI招生就业系统。后面还有内容'),
            ['没问题，我给您简单介绍一下咱们的AI招生就业系统。'],
        )
        self.assertEqual(segmenter.flush(), '后面还有内容')

    def test_combines_short_comma_clauses_to_reduce_tts_requests(self):
        segmenter = SpeechSegmenter()
        segments = segmenter.feed('它可以线上答疑，也可以线下展示，还能减轻老师的工作压力。')
        self.assertEqual(segments, ['它可以线上答疑，也可以线下展示，还能减轻老师的工作压力。'])

    def test_hard_limit_prevents_unbounded_first_segment_latency(self):
        segmenter = SpeechSegmenter(max_chars=20)
        segments = segmenter.feed('这是一段一直没有任何标点并且会持续很长时间的模型输出内容')
        self.assertTrue(segments)
        self.assertLessEqual(len(segments[0]), 20)

    def test_qa_template_is_grounding_for_knowledge_agent(self):
        avatar = SimpleNamespace(
            opt=SimpleNamespace(),
            put_msg_txt=lambda text, data: spoken.append(text),
        )
        spoken = []
        result = KnowledgeSearchResult(
            query="测试",
            matches=(
                KnowledgeMatch(
                    title="标准问答",
                    text="**可以**。\n- 第一项\n- 第二项",
                    score=100,
                    coverage=1,
                    matched_terms=("测试",),
                    kind="qa",
                    standard_question="可以吗？",
                    standard_answer="**可以**。\n- 第一项\n- 第二项",
                ),
            ),
            scope="AI素养评价产品QA",
            documents=(
                KnowledgeDocument(
                    scope="AI素养评价产品QA",
                    title="标准问答",
                    text="**可以**。\n- 第一项\n- 第二项",
                ),
            ),
        )
        grounded_answer = KnowledgeQaAnswer(
            answer="**可以**。第一项和第二项都支持。",
            evidence=(KnowledgeQaEvidence("标准问答", "可以"),),
            gaps=(),
        )

        with (
            patch("llm.search_knowledge", return_value=result),
            patch("llm._llm_client") as client_factory,
            patch(
                "llm.answer_knowledge_question",
                return_value=grounded_answer,
            ) as knowledge_agent,
        ):
            llm_response("测试", avatar)

        client_factory.assert_called_once()
        knowledge_agent.assert_called_once()
        speech = "".join(spoken)
        self.assertNotIn("*", speech)
        self.assertIn("可以", speech)
        self.assertIn("第一项", speech)
        self.assertIn("第二项", speech)

    def test_failed_knowledge_agent_uses_targeted_llm_instead_of_raw_qa(self):
        avatar = SimpleNamespace(
            opt=SimpleNamespace(),
            put_msg_txt=lambda text, data: spoken.append(text),
        )
        spoken = []
        result = KnowledgeSearchResult(
            query="学生个人成长报告支持导出吗？",
            matches=(
                KnowledgeMatch(
                    title="标准问答",
                    text="学生报告支持导出。其他报告内容很多。",
                    score=100,
                    coverage=1,
                    matched_terms=("导出",),
                    kind="qa",
                    standard_question="系统会生成哪些报告？",
                    standard_answer="学生报告支持导出。其他报告内容很多。",
                ),
            ),
        )
        chunks = [
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="支持导出。"))]
            )
        ]
        create = Mock(return_value=chunks)
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        with (
            patch("llm.search_knowledge", return_value=result),
            patch("llm._llm_client", return_value=client),
            patch("llm._llm_model", return_value="test-model"),
            patch(
                "llm.answer_knowledge_question",
                side_effect=KnowledgeQaError("invalid"),
            ),
        ):
            llm_response("学生个人成长报告支持导出吗？", avatar)

        self.assertEqual("".join(spoken), "支持导出。")
        self.assertEqual(create.call_count, 1)
        self.assertEqual(create.call_args.kwargs["temperature"], 0.1)


if __name__ == '__main__':
    unittest.main()
