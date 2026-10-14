"""AgiBot G2: shared deploy settings for openpi (eef / qpos)."""

_base_ = ["../../00_base/defaults.py"]

console = dict(initial_tab="debug")

robot = dict(
    type="agibot_g2",
    # Live pose captured from the connected G2 on 2026-10-10. This shared base is
    # inherited by the G2 deploy and collection profiles, so every console page uses
    # the same current pose for its initial/home defaults.
    initial_qpos=[
        0.038729120045900345,
        -0.0034921031910926104,
        -0.17084015905857086,
        -2.2650630474090576,
        -0.3486744463443756,
        0.25968414545059204,
        -0.3717955946922302,
        -0.7850000262260437,
        -1.83231782913208,
        -1.301379919052124,
        1.5835881233215332,
        -1.7765344381332397,
        -1.4549758434295654,
        -0.11678411811590195,
        -0.4070385694503784,
        -0.7835782766342163,
        0.0023006072733551264,
        0.0,
        0.31518319249153137,
        -0.05135408416390419,
        0.053572412580251694,
        0.41591405868530273,
        0.0,
        0.0,
    ],
    gripper_threshold=-0.4,
    gripper_open=0.0,
    gripper_close=-0.785,
)

transport = dict(
    type="zmq",
    # The G2 node publishes all three cameras and all joints in each snapshot.
    conflate_observations=True,
)

policy = dict(
    type="openpi_rtc",
    backend_options=dict(latency_k=4),
)

inference_cfg = dict(
    debug_tasks=[
        "Put the pen into the pen holder.",
        "Close the laptop.",
        "Put the tissue into the trash bin.",
        "Place the mouse on the right side of the laptop.",
        "Straighten the colored pencil box.",
    ],
)

inference_strategies = {
    "async": dict(
        args=dict(
            latency_k=4,
        ),
    ),
    "rtc": dict(
        args=dict(
            latency_k=4,
        ),
    ),
}
