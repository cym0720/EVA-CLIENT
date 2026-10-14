# White carton placement records to LeRobot for EVA

This converter handles EVA's raw AgiBot G2 record folders (with
`record/aligned_joints.h5` and timestamped `.h265` camera streams). It writes a
LeRobot v2.1 dataset that EVA's offline `DatasetTransport` and REPLAY tab can
read. The existing `agibot2lerobot` converter remains for the public
AgiBotWorld Beta HDF5 layout.

The replay dataset keeps the fields EVA consumes:

- `observation.state`: 24D G2 qpos, ordered as left arm + gripper, right arm +
  gripper, head, and waist.
- `action`: matching 24D commanded qpos.
- `observation.images.top_head`, `observation.images.hand_left`, and
  `observation.images.hand_right`: H.264 MP4 videos aligned to the HDF5 frame
  timestamps.
- `timestamp`, frame/episode/task indices, task text, and source episode
  metadata.

The task label is `Pick up the white carton and place it inside the large tan
carton on the left.` The source has no head action field, so the converter uses
the measured head position for that part of the 24D action. It trims leading
and trailing samples until all arm, gripper, and waist action fields are
available; the retained samples form one continuous interval in each episode.

The converter intentionally leaves out the extra raw streams EVA's G2
observation schema does not consume: head stereo/fisheye, depth, lidar, IMU,
base/chassis motion, VR buttons, and diagnostic topics. The source record is
not modified.

Run the default conversion:

```bash
bash any4lerobot/agibot2lerobot_eva/convert.sh
```

Override paths or task label with environment variables:

```bash
G2_RECORD_SRC_PATH=/data/collection/agibot_g2/white_carton_to_left_tan_carton_20261009/raw_records \
G2_LEROBOT_OUTPUT=/data/collection/agibot_g2/white_carton_to_left_tan_carton_20261009/lerobot_eva \
G2_LEROBOT_TASK="Pick up the white carton and place it inside the large tan carton on the left." \
  bash any4lerobot/agibot2lerobot_eva/convert.sh
```

`eva_replay.py` is an offline-only EVA config for checking the output at 30 Hz. It uses
`transport.type="dataset"`; it does not connect to a robot transport.

To replay episode 0 through EVA's headless SIM path, start EVA in a terminal
that keeps stdin interactive:

```bash
.venv/bin/eva --config any4lerobot/agibot2lerobot_eva/eva_replay.py --headless
```

From another shell, mount the episode through the control channel:

```bash
.venv/bin/python - <<'PY'
import json
import sys

sys.path.insert(0, "examples/control_channel")
from eva_ctl import send

host, port = "127.0.0.1", 15757
send(cmd="web:select_mode:sim", host=host, port=port)
payload = json.dumps({
    "dataset_dir": "/data/collection/agibot_g2/white_carton_to_left_tan_carton_20261009/lerobot_eva",
    "episode": 0,
})
send(cmd=f"web:load_replay_dataset:{payload}", host=host, port=port)
PY
```

Then enter `setup` and `run` at EVA's terminal prompt. The SIM replay uses
the offline dataset transport and does not command a physical robot.

## Replay on the G2 robot

`eva_replay_real.py` uses the G2 live ZMQ transport. Its setup skips the generic
home reset and interpolates from the live joint state to the selected episode's
first pose over 150 steps (about five seconds at 30 Hz). Start the hardware node
on the G2 host first:

```bash
source /home/agi/app/env.sh
export PYTHONPATH="$PWD/src:$PYTHONPATH"
examples/hardware/agibot_g2/.venv/bin/python examples/hardware/agibot_g2/node.py \
  --obs-endpoint tcp://127.0.0.1:5555 \
  --action-endpoint tcp://127.0.0.1:5556
```

In another terminal on that host, start EVA with the real replay config:

```bash
.venv/bin/eva --config any4lerobot/agibot2lerobot_eva/eva_replay_real.py --headless
```

Mount an episode with `web:load_replay_dataset` as above, then enter `setup`
and `run` at EVA's prompt. This sends recorded joint actions through the live
G2 node; run it only after the real robot connection and workspace are ready.
