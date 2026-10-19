from scripts.run_embedding_cost_router import calibrate, score


def test_calibration_chooses_coverage_without_using_validation_rows():
    calibration = [
        {"expected": "logistics", "predicted": "logistics", "confidence": 0.90, "margin": 0.20},
        {"expected": "refund", "predicted": "refund", "confidence": 0.80, "margin": 0.10},
        {"expected": "refund", "predicted": "logistics", "confidence": 0.70, "margin": 0.03},
    ]
    threshold, margin, metrics = calibrate(calibration, minimum_precision=1.0)

    assert metrics.correct == metrics.routed
    assert threshold >= 0.60
    assert margin >= 0.02


def test_router_reports_coverage_as_estimated_llm_reduction():
    rows = [
        {"expected": "logistics", "predicted": "logistics", "confidence": 0.90, "margin": 0.20},
        {"expected": "refund", "predicted": "logistics", "confidence": 0.60, "margin": 0.01},
    ]
    metrics = score(rows, threshold=0.8, margin=0.1)

    assert metrics.routed == 1
    assert metrics.precision == 1.0
    assert metrics.coverage == metrics.estimated_llm_call_reduction == 0.5
