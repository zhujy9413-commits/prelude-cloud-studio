"""Protect comparison validity and the prepare-only budget boundary."""
import copy
import unittest

import generation_spec
import quality_plan


class QualityPlanChecks(unittest.TestCase):
    def test_proposed_pair_has_equal_parameters_but_different_motion_prompts(self):
        plan=quality_plan.prepare('  原始长剧情\n五官不能凭空长出  ')
        a,b=plan['cases']
        self.assertEqual(a['request_body']['parameters'],b['request_body']['parameters'])
        self.assertEqual(a['request_body']['model'],'wan2.6-t2v')
        self.assertEqual(a['request_body']['parameters'],{'duration':15,'prompt_extend':True,'shot_type':'single','watermark':False,'size':'1280*720','seed':20261029})
        self.assertNotEqual(a['snapshot']['submitted_prompt'],b['snapshot']['submitted_prompt'])
        for case in plan['cases']:
            self.assertIsNone(case['snapshot']['reference'])
            self.assertLessEqual(len(case['snapshot']['submitted_prompt']),1200)
            self.assertEqual(case['request_sha256'],generation_spec.digest(generation_spec.encoded(case['request_body'])))
        # Rendering is safe to repeat and has no stateful submission side effects.
        before=copy.deepcopy(plan)
        quality_plan.render(plan)
        self.assertEqual(plan,before)

    def test_budget_and_history_are_not_represented_as_approved_or_generated(self):
        raw='  原文 <script>must remain text</script>\n  '
        plan=quality_plan.prepare(raw)
        self.assertEqual(plan['source']['original_prompt'],raw)
        self.assertEqual(plan['source']['historical_parameter_state'],'legacy_unrecorded')
        self.assertEqual(plan['source']['original_prompt_sha256'],generation_spec.digest(raw.encode()))
        self.assertEqual(plan['budget']['list_cost_cents'],1800)
        self.assertEqual(plan['budget']['actual_cash_spend_cents'],0)
        self.assertEqual(plan['status'],'PREPARED')
        self.assertEqual(plan['new_generation_calls'],0)
        rendered=quality_plan.render(plan)
        self.assertIn('¥18.00',rendered)
        self.assertNotIn('<script>',rendered)
        self.assertNotIn('<form',rendered)
        self.assertNotIn('<button',rendered)


if __name__=='__main__':unittest.main()
