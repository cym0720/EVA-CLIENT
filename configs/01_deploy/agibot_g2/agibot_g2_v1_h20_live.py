"""Live G2 observations with the agibot_g2.v1 policy served through the H20 tunnel."""

_base_ = ["agibot_g2_v1.py"]

policy = dict(
    host="127.0.0.1",
    port=11094,
)

inference_cfg = dict(max_run_frames=1000)

# The H20 server advances a sequential cache per 16-action response. Async modes
# request the next chunk only after the current 16 actions are consumed. ACT's
# temporal ensembling needs overlapping predictions, so on this sequential
# server it will run safely but have no overlapping chunks to ensemble.
inference_strategies = dict(
    _delete_=True,
    sync=dict(
        type="BaseInferStrategy",
        args=dict(execute_horizon=16),
    ),
    **{
        "async": dict(
            type="AsyncLinearOverlapInferStrategy",
            args=dict(
                execute_horizon=16,
                latency_k=0,
                inference_rate=3.0,
            ),
        ),
        "naive_async": dict(
            type="NaiveAsyncInferStrategy",
            args=dict(
                execute_horizon=16,
                latency_k=0,
                inference_rate=3.0,
            ),
        ),
        "act_ensemble": dict(
            type="ActEnsembleInferStrategy",
            args=dict(
                execute_horizon=16,
                exp_weight_m=0.01,
                inference_rate=3.0,
            ),
        ),
    },
)
