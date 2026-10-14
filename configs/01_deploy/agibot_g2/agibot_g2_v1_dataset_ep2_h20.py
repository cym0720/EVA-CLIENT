"""G2 dataset-observation deployment through the H20 policy server tunnel."""

_base_ = ["agibot_g2_v1_dataset_ep2.py"]

policy = dict(
    host="127.0.0.1",
    port=11094,
)

inference_cfg = dict(
    observation_dataset=dict(
        dataset_dir="/data/cym_ws/EVA-CLIENT/work_dirs/lerobot/1012_20261012195201",
        episode_id=0,
    ),
)
