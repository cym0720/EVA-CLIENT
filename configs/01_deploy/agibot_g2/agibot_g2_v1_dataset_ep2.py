"""G2 deployment using recorded images and live robot joint state for inference."""

_base_ = ["agibot_g2_v1.py"]

inference_cfg = dict(
    black_policy_images=False,
    observation_dataset=dict(
        dataset_dir=(
            "/data/cym_ws/EVA-CLIENT/work_dirs/lerobot/1012_20261012195201"
            # "lerobot_eva"
        ),
        episode_id=0,
        use_live_state=True,  # Read G2 feedback immediately before each inference.
    ),
)

inference_strategies = dict(
    _delete_=True,
    sync=dict(
        type="BaseInferStrategy",
        args=dict(execute_horizon=16),
    ),
)
