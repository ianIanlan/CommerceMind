from evaluation.task_evaluator import CommerceTaskEvaluator


def test_task_level_process_assertions_pass():
    results = CommerceTaskEvaluator().run()
    assert results
    assert all(item.passed for item in results)
