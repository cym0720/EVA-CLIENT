"""Offline EVA replay config for the converted AgiBot G2 record dataset."""

_base_ = ["../../configs/00_base/defaults.py"]

robot = dict(
    type="agibot_g2",
    gripper_threshold=-0.4,
    gripper_open=0.0,
    gripper_close=-0.785,
)

transport = dict(
    type="dataset",
    dataset_dir="/data/collection/agibot_g2/white_carton_to_left_tan_carton_20261009/lerobot_eva",
    episode_id=0,
    image_height=400,
    image_width=640,
    resize_pad=False,
    image_layout="hwc",
    dataset_keys=dict(
        state_key="observation.state",
        action_key="action",
        video_keys=dict(
            top_head="observation.images.top_head",
            hand_left="observation.images.hand_left",
            hand_right="observation.images.hand_right",
        ),
    ),
)

policy = dict(type="replay")
console = dict(initial_tab="replay")
control_channel = dict(enabled=True, host="127.0.0.1", port=15757)
inference_cfg = dict(publish_rate=30, setup_warmup_chunks=0)
