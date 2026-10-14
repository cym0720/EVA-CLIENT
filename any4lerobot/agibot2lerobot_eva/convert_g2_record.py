#!/usr/bin/env python3
"""Convert raw EVA/AgiBot G2 DLB record folders to LeRobot v2.1 for EVA replay."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq


CAMERAS = (
    ("head_color", "top_head"),
    ("hand_left_color", "hand_left"),
    ("hand_right_color", "hand_right"),
)
JOINT_NAMES = (
    "idx21_arm_l_joint1",
    "idx22_arm_l_joint2",
    "idx23_arm_l_joint3",
    "idx24_arm_l_joint4",
    "idx25_arm_l_joint5",
    "idx26_arm_l_joint6",
    "idx27_arm_l_joint7",
    "idx31_gripper_l_inner_joint1",
    "idx61_arm_r_joint1",
    "idx62_arm_r_joint2",
    "idx63_arm_r_joint3",
    "idx64_arm_r_joint4",
    "idx65_arm_r_joint5",
    "idx66_arm_r_joint6",
    "idx67_arm_r_joint7",
    "idx71_gripper_r_inner_joint1",
    "idx11_head_joint1",
    "idx12_head_joint2",
    "idx13_head_joint3",
    "idx01_body_joint1",
    "idx02_body_joint2",
    "idx03_body_joint3",
    "idx04_body_joint4",
    "idx05_body_joint5",
)


@dataclass
class Episode:
    source_dir: Path
    meta: dict[str, Any]
    state: np.ndarray
    action: np.ndarray
    timestamps: np.ndarray
    camera_indices: dict[str, tuple[int, int]]
    source_frame_start: int
    source_frame_end: int
    source_frame_count: int
    head_action_source: str

    @property
    def task(self) -> str:
        return str(self.meta.get("text") or "").strip()


def _vector_series(h5: h5py.File, frame_keys: list[str], field: str, dim: int) -> np.ndarray:
    rows = []
    for key in frame_keys:
        if field not in h5[key]:
            raise ValueError(f"{h5.filename}: missing {key}/{field}")
        row = np.asarray(h5[key][field], dtype=np.float32).reshape(-1)
        if row.size != dim:
            raise ValueError(
                f"{h5.filename}: {key}/{field} has {row.size} values; expected {dim}"
            )
        rows.append(row)
    return np.stack(rows)


def _compose_qpos(
    h5: h5py.File, frame_keys: list[str], prefix: str
) -> np.ndarray:
    joint = _vector_series(h5, frame_keys, f"{prefix}/joint/position", 14)
    left_gripper = _vector_series(
        h5, frame_keys, f"{prefix}/left_effector/position", 1
    )
    right_gripper = _vector_series(
        h5, frame_keys, f"{prefix}/right_effector/position", 1
    )
    head_rows = []
    for key in frame_keys:
        head_field = f"{prefix}/head/position"
        if head_field not in h5[key] and prefix == "action":
            # G2 recordings do not always emit head targets. Keep the uncommanded
            # head at its recorded joint position so EVA still receives a 24D qpos.
            head_field = "state/head/position"
        if head_field not in h5[key]:
            raise ValueError(f"{h5.filename}: missing {key}/{head_field}")
        row = np.asarray(h5[key][head_field], dtype=np.float32).reshape(-1)
        if row.size != 3:
            raise ValueError(
                f"{h5.filename}: {key}/{head_field} has {row.size} values; expected 3"
            )
        head_rows.append(row)
    head = np.stack(head_rows)
    waist = _vector_series(h5, frame_keys, f"{prefix}/waist/position", 5)
    return np.concatenate(
        (
            joint[:, :7],
            left_gripper,
            joint[:, 7:],
            right_gripper,
            head,
            waist,
        ),
        axis=1,
    )


def _camera_frame_range(
    h5: h5py.File, frame_keys: list[str], record_dir: Path, camera_name: str
) -> tuple[int, int]:
    timestamp_key = f"timestamp/camera/{camera_name}"
    target_times = [
        int(np.asarray(h5[key][timestamp_key]).reshape(-1)[0]) for key in frame_keys
    ]
    timestamp_file = record_dir / "camera" / camera_name / f"{camera_name}.txt"
    if not timestamp_file.is_file():
        raise FileNotFoundError(timestamp_file)

    video_times: list[int] = []
    for line_number, line in enumerate(timestamp_file.read_text().splitlines(), 1):
        parts = line.split()
        if not parts:
            continue
        try:
            video_times.append(int(parts[0]))
        except ValueError as error:
            raise ValueError(
                f"{timestamp_file}:{line_number}: invalid camera timestamp"
            ) from error

    index_by_timestamp = {timestamp: index for index, timestamp in enumerate(video_times)}
    try:
        frame_indices = [index_by_timestamp[timestamp] for timestamp in target_times]
    except KeyError as error:
        raise ValueError(
            f"{record_dir.name}/{camera_name}: HDF5 camera timestamp {error.args[0]} "
            "is absent from the camera .txt index"
        ) from error

    start = frame_indices[0]
    expected = list(range(start, start + len(frame_keys)))
    if frame_indices != expected:
        raise ValueError(
            f"{record_dir.name}/{camera_name}: matched video frames are not contiguous; "
            "cannot trim safely"
        )
    return start, start + len(frame_keys) - 1


def load_episode(record_dir: Path) -> Episode:
    meta_path = record_dir / "meta_info.json"
    h5_path = record_dir / "record" / "aligned_joints.h5"
    if not meta_path.is_file() or not h5_path.is_file():
        raise FileNotFoundError(
            f"{record_dir}: expected meta_info.json and record/aligned_joints.h5"
        )
    meta = json.loads(meta_path.read_text())

    with h5py.File(h5_path, "r") as h5:
        frame_keys = sorted(
            (key for key in h5.keys() if key.isdigit()), key=lambda value: int(value)
        )
        if len(frame_keys) < 2:
            raise ValueError(f"{h5_path}: expected at least two aligned frames")

        source_frame_count = len(frame_keys)
        required_action_fields = (
            "action/joint/position",
            "action/left_effector/position",
            "action/right_effector/position",
            "action/waist/position",
        )
        valid_action_rows = [
            index
            for index, key in enumerate(frame_keys)
            if all(field in h5[key] for field in required_action_fields)
        ]
        if not valid_action_rows:
            raise ValueError(f"{h5_path}: no frames have a complete G2 action")
        first_action_row, last_action_row = valid_action_rows[0], valid_action_rows[-1]
        expected_action_rows = list(range(first_action_row, last_action_row + 1))
        if valid_action_rows != expected_action_rows:
            raise ValueError(
                f"{h5_path}: complete action rows contain internal gaps; refusing to bridge them"
            )
        frame_keys = frame_keys[first_action_row : last_action_row + 1]
        source_frame_start = int(frame_keys[0])
        source_frame_end = int(frame_keys[-1])

        has_head_action = ["action/head/position" in h5[key] for key in frame_keys]
        if all(has_head_action):
            head_action_source = "action/head/position"
        elif any(has_head_action):
            head_action_source = "action/head/position with state/head/position fallback"
        else:
            head_action_source = "state/head/position (source has no head action)"

        state = _compose_qpos(h5, frame_keys, "state")
        action = _compose_qpos(h5, frame_keys, "action")
        timestamps = np.asarray(
            [int(h5[key]["main_timestamp"][()]) for key in frame_keys], dtype=np.int64
        )
        camera_indices = {
            camera_name: _camera_frame_range(
                h5, frame_keys, record_dir, camera_name
            )
            for camera_name, _ in CAMERAS
        }

    if state.shape[1] != 24 or action.shape[1] != 24:
        raise ValueError(f"{record_dir}: EVA G2 state/action must each be 24D")
    if not np.isfinite(state).all() or not np.isfinite(action).all():
        raise ValueError(f"{record_dir}: state/action contain NaN or infinity")
    if not np.all(np.diff(timestamps) > 0):
        raise ValueError(f"{record_dir}: main_timestamp is not strictly increasing")

    return Episode(
        record_dir,
        meta,
        state,
        action,
        timestamps,
        camera_indices,
        source_frame_start,
        source_frame_end,
        source_frame_count,
        head_action_source,
    )


def _probe_video(path: Path) -> tuple[int, int]:
    result = subprocess.run(
        [
            "ffprobe",
            "-probesize",
            "1048576",
            "-analyzeduration",
            "100000",
            "-v",
            "error",
            "-f",
            "hevc",
            "-i",
            str(path),
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    streams = json.loads(result.stdout).get("streams", [])
    if not streams:
        raise ValueError(f"ffprobe found no video stream in {path}")
    return int(streams[0]["width"]), int(streams[0]["height"])


def _encode_video(
    source: Path,
    destination: Path,
    start_frame: int,
    end_frame: int,
    fps: int,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame_count = end_frame - start_frame + 1
    frame_select = f"select='between(n\\,{start_frame}\\,{end_frame})'"
    filters = f"{frame_select},setpts=N/({fps}*TB)"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-threads",
            "1",
            "-f",
            "hevc",
            "-r",
            str(fps),
            "-i",
            str(source),
            "-vf",
            filters,
            "-frames:v",
            str(frame_count),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "21",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(fps),
            "-movflags",
            "+faststart",
            str(destination),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if not destination.is_file() or destination.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg produced no video at {destination}")

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,nb_frames,width,height",
            "-of",
            "json",
            str(destination),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    streams = json.loads(probe.stdout).get("streams", [])
    if not streams or streams[0].get("codec_name") != "h264":
        raise RuntimeError(f"Expected an H.264 MP4 video at {destination}")
    if int(streams[0].get("nb_frames", -1)) != frame_count:
        raise RuntimeError(
            f"{destination}: wrote {streams[0].get('nb_frames')} frames, "
            f"expected {frame_count}"
        )


def _stats(values: np.ndarray) -> dict[str, list[float] | list[int]]:
    return {
        "min": np.min(values, axis=0).astype(float).tolist(),
        "max": np.max(values, axis=0).astype(float).tolist(),
        "mean": np.mean(values, axis=0).astype(float).tolist(),
        "std": np.std(values, axis=0).astype(float).tolist(),
        "count": [int(values.shape[0])],
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def _parquet_array(values: np.ndarray, width: int, arrow_type: pa.DataType) -> pa.Array:
    fixed_size_type = pa.list_(arrow_type, list_size=width)
    return pa.array(values.tolist(), type=fixed_size_type)


def convert(src_path: Path, output_path: Path, task: str, fps: int) -> None:
    src_path = src_path.expanduser().resolve()
    output_path = output_path.expanduser().resolve()
    if not src_path.is_dir():
        raise FileNotFoundError(f"Source directory does not exist: {src_path}")
    if output_path.exists():
        raise FileExistsError(
            f"Output already exists: {output_path}; move it aside or choose another path"
        )
    partial_path = output_path.with_name(output_path.name + ".partial")
    if partial_path.exists():
        raise FileExistsError(
            f"Partial output already exists: {partial_path}; inspect it before retrying"
        )

    record_dirs = sorted(
        path
        for path in src_path.iterdir()
        if path.is_dir()
        and (path / "meta_info.json").is_file()
        and (path / "record" / "aligned_joints.h5").is_file()
    )
    if not record_dirs:
        raise ValueError(
            f"No G2 record folders found under {src_path}; expected */record/aligned_joints.h5"
        )
    missing = [
        str(path)
        for path in src_path.iterdir()
        if path.is_dir()
        and (path / "meta_info.json").is_file()
        and not (path / "record" / "aligned_joints.h5").is_file()
    ]
    if missing:
        raise ValueError("Some records lack aligned_joints.h5:\n" + "\n".join(missing))

    if shutil.which("ffmpeg") is None:
        raise FileNotFoundError("ffmpeg not found")
    if shutil.which("ffprobe") is None:
        raise FileNotFoundError("ffprobe not found")
    episodes = []
    for index, record_dir in enumerate(record_dirs, 1):
        episode = load_episode(record_dir)
        episodes.append(episode)
        print(
            f"[inspect {index}/{len(record_dirs)}] {record_dir.name}: "
            f"{len(episode.timestamps)} action frames "
            f"(source frames {episode.source_frame_start}..{episode.source_frame_end}/"
            f"{episode.source_frame_count})",
            flush=True,
        )
    task_texts = [episode.task or task for episode in episodes]
    unique_tasks = list(dict.fromkeys(task_texts))
    task_indices = {value: index for index, value in enumerate(unique_tasks)}

    source_dimensions: dict[str, tuple[int, int]] = {}
    for camera_name, _ in CAMERAS:
        camera_path = (
            episodes[0].source_dir
            / "camera"
            / camera_name
            / f"{camera_name}.h265"
        )
        source_dimensions[camera_name] = _probe_video(camera_path)

    for episode in episodes:
        for camera_name, _ in CAMERAS:
            video_path = (
                episode.source_dir / "camera" / camera_name / f"{camera_name}.h265"
            )
            if not video_path.is_file():
                raise FileNotFoundError(video_path)

    partial_path.mkdir(parents=True)
    (partial_path / "meta").mkdir()
    total_frames = sum(len(episode.timestamps) for episode in episodes)
    all_states = np.concatenate([episode.state for episode in episodes], axis=0)
    all_actions = np.concatenate([episode.action for episode in episodes], axis=0)
    video_features: dict[str, dict[str, Any]] = {}
    for camera_name, eva_camera in CAMERAS:
        width, height = source_dimensions[camera_name]
        video_features[f"observation.images.{eva_camera}"] = {
            "dtype": "video",
            "shape": [height, width, 3],
            "names": ["height", "width", "channels"],
            "info": {
                "video.height": height,
                "video.width": width,
                "video.codec": "h264",
                "video.pix_fmt": "yuv420p",
                "video.is_depth_map": False,
                "video.fps": fps,
                "video.channels": 3,
                "has_audio": False,
            },
        }

    features = {
        **video_features,
        "observation.state": {
            "dtype": "float32",
            "shape": [24],
            "names": list(JOINT_NAMES),
        },
        "action": {
            "dtype": "float32",
            "shape": [24],
            "names": list(JOINT_NAMES),
        },
        "timestamp": {"dtype": "float32", "shape": [1], "names": None},
        "frame_index": {"dtype": "int64", "shape": [1], "names": None},
        "episode_index": {"dtype": "int64", "shape": [1], "names": None},
        "index": {"dtype": "int64", "shape": [1], "names": None},
        "task_index": {"dtype": "int64", "shape": [1], "names": None},
    }

    info = {
        "codebase_version": "v2.1",
        "robot_type": "agibot_g2",
        "total_episodes": len(episodes),
        "total_frames": total_frames,
        "total_tasks": len(unique_tasks),
        "total_videos": len(episodes) * len(CAMERAS),
        "total_chunks": 1,
        "chunks_size": 1000,
        "fps": fps,
        "splits": {"train": f"0:{len(episodes)}"},
        "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
        "video_path": "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4",
        "features": features,
        "source_format": "AgiBot G2 DLB record",
    }
    _write_json(partial_path / "meta" / "info.json", info)
    with (partial_path / "meta" / "tasks.jsonl").open("w") as tasks_file:
        for task_index, task_text in enumerate(unique_tasks):
            tasks_file.write(
                json.dumps(
                    {"task_index": task_index, "task": task_text}, ensure_ascii=False
                )
                + "\n"
            )

    episodes_meta = []
    episode_stats = []
    global_index = 0
    for episode_index, episode in enumerate(episodes):
        source_name = episode.source_dir.name
        task_text = task_texts[episode_index]
        n_steps = len(episode.timestamps)
        episode_file_stem = f"episode_{episode_index:06d}"
        chunk = 0
        chunk_path = partial_path / "data" / f"chunk-{chunk:03d}"
        chunk_path.mkdir(parents=True, exist_ok=True)
        parquet_path = chunk_path / f"{episode_file_stem}.parquet"
        relative_video_paths: dict[str, str] = {}

        def encode_camera(camera: tuple[str, str]) -> tuple[str, str]:
            camera_name, eva_camera = camera
            video_feature = f"observation.images.{eva_camera}"
            relative_path = (
                Path("videos")
                / f"chunk-{chunk:03d}"
                / video_feature
                / f"{episode_file_stem}.mp4"
            )
            raw_video = (
                episode.source_dir / "camera" / camera_name / f"{camera_name}.h265"
            )
            start, end = episode.camera_indices[camera_name]
            _encode_video(raw_video, partial_path / relative_path, start, end, fps)
            return video_feature, relative_path.as_posix()

        # Three independent H.265 streams per episode; encode them concurrently so
        # the conversion uses the available CPU cores instead of serializing cameras.
        with ThreadPoolExecutor(max_workers=len(CAMERAS)) as executor:
            for video_feature, relative_path in executor.map(encode_camera, CAMERAS):
                relative_video_paths[video_feature] = relative_path

        timestamps = (episode.timestamps - episode.timestamps[0]).astype(np.float64) / 1e9
        table = pa.table(
            {
                "observation.state": _parquet_array(episode.state, 24, pa.float32()),
                "action": _parquet_array(episode.action, 24, pa.float32()),
                "timestamp": pa.array(timestamps.astype(np.float32)),
                "frame_index": pa.array(np.arange(n_steps, dtype=np.int64)),
                "episode_index": pa.array(
                    np.full(n_steps, episode_index, dtype=np.int64)
                ),
                "index": pa.array(
                    np.arange(global_index, global_index + n_steps, dtype=np.int64)
                ),
                "task_index": pa.array(
                    np.full(n_steps, task_indices[task_text], dtype=np.int64)
                ),
            }
        )
        pq.write_table(table, parquet_path, compression="snappy")

        episode_source_meta = {
            "episode_index": episode_index,
            "file_stem": episode_file_stem,
            "source_episode": source_name,
            "source_episode_id": episode.meta.get("episode_id"),
            "task_id": episode.meta.get("task_id"),
            "task_mode": episode.meta.get("task_mode"),
            "robot_type": episode.meta.get("robot_type"),
            "create_time": episode.meta.get("create_time"),
            "source_text": episode.meta.get("text", ""),
            "duration": episode.meta.get("duration"),
            "source_frame_start": episode.source_frame_start,
            "source_frame_end": episode.source_frame_end,
            "source_frame_count": episode.source_frame_count,
            "head_action_source": episode.head_action_source,
            "camera_video_paths": relative_video_paths,
        }
        episodes_meta.append(
            {
                **episode_source_meta,
                "tasks": [task_text],
                "length": n_steps,
            }
        )
        episode_stats.append(
            {
                "episode_index": episode_index,
                "stats": {
                    "observation.state": _stats(episode.state),
                    "action": _stats(episode.action),
                },
            }
        )
        global_index += n_steps
        print(
            f"[{episode_index + 1}/{len(episodes)}] {source_name}: "
            f"{n_steps} frames, task_id={episode.meta.get('task_id')}",
            flush=True,
        )

    with (partial_path / "meta" / "episodes.jsonl").open("w") as episodes_file:
        for row in episodes_meta:
            episodes_file.write(json.dumps(row, ensure_ascii=False) + "\n")
    with (partial_path / "meta" / "episodes_stats.jsonl").open("w") as stats_file:
        for row in episode_stats:
            stats_file.write(json.dumps(row, ensure_ascii=False) + "\n")
    _write_json(
        partial_path / "meta" / "stats.json",
        {
            "observation.state": _stats(all_states),
            "action": _stats(all_actions),
        },
    )
    _write_json(
        partial_path / "meta" / "source.json",
        {
            "source_root": str(src_path),
            "source_format": "AgiBot G2 DLB record",
            "task_fallback": task,
            "retained_for_eva": [
                "24D observation.state qpos",
                "24D action qpos",
                "top_head / hand_left / hand_right RGB video",
                "timestamps and task/source episode metadata",
            "frames in the continuous range with complete joint, gripper, and waist actions",
            "head action from recorded state when the source has no head command",
            ],
            "excluded_from_eva_dataset": [
                "head stereo and fisheye streams",
                "depth stream",
                "lidar / IMU / TF and diagnostic topics",
                "base/chassis velocity and pose",
                "VR button and teleoperation diagnostics",
            ],
        },
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path.rename(output_path)
    print(
        f"LeRobot v2.1 dataset ready: {output_path} "
        f"({len(episodes)} episodes, {total_frames} frames, 3 cameras)",
        flush=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src-path", type=Path, required=True)
    parser.add_argument("--output-path", type=Path, required=True)
    parser.add_argument(
        "--task",
        default="warmup",
        help="Fallback task label when meta_info.json has no text (default: warmup)",
    )
    parser.add_argument("--fps", type=int, default=30)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.fps < 1:
        raise ValueError("--fps must be positive")
    convert(args.src_path, args.output_path, args.task, args.fps)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise
