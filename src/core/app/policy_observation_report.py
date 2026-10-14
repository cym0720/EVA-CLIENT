"""MSE metrics and plots for runs driven by recorded policy observations."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from transport.dataset import DatasetTransport

logger = logging.getLogger(__name__)


class PolicyObservationTrace:
    """Collect policy predictions and sent commands against aligned LeRobot labels."""

    def __init__(
        self,
        source: DatasetTransport,
        output_dir: str | Path,
        publish_rate: int,
        gripper_open: float,
        gripper_close: float,
        inference_horizon: int = 1,
    ) -> None:
        self.source = source
        self.output_dir = Path(output_dir) / "policy_observation_reports"
        self.publish_rate = max(1, int(publish_rate))
        self.source_frames_per_action = max(1, int(round(source.fps / self.publish_rate)))
        self.inference_horizon = max(1, int(inference_horizon))
        self.gripper_open = float(gripper_open)
        self.gripper_close = float(gripper_close)
        self._labels = source.get_action_trajectory()
        self._predictions: dict[int, np.ndarray] = {}
        self._commands: dict[int, np.ndarray] = {}
        self._finalized = False

    def add_chunk(self, source_frame: int, actions: np.ndarray) -> None:
        """Store one predicted chunk, indexed on the 10Hz control timeline."""
        array = np.asarray(actions, dtype=np.float32)
        if array.ndim != 2 or array.shape[1] != self._labels.shape[1]:
            raise ValueError(
                f"Policy chunk must have shape [T,{self._labels.shape[1]}], got {array.shape}"
            )
        first_step = int(source_frame) // self.source_frames_per_action
        for offset, action in enumerate(array):
            self._predictions[first_step + offset] = action.copy()

    def record_command(self, action_step: int, action: np.ndarray) -> None:
        """Record the qpos target after EVA's per-step limiter and gripper overrides."""
        command = np.asarray(action, dtype=np.float32).reshape(-1)
        if command.shape != (self._labels.shape[1],):
            raise ValueError(
                f"Published command must have shape ({self._labels.shape[1]},), got {command.shape}"
            )
        self._commands[int(action_step)] = command.copy()

    def _contiguous_run_length(self) -> int:
        count = 0
        while count in self._commands and count in self._predictions:
            count += 1
        return count

    def save(self) -> dict[str, Any] | None:
        """Write a PNG plot and JSON metrics; return their paths and scores."""
        if self._finalized:
            return None
        self._finalized = True
        n_steps = self._contiguous_run_length()
        if n_steps == 0:
            logger.info("[POLICY_OBSERVATION_REPORT] no executed actions; skipping report")
            return None

        frame_step = self.source_frames_per_action
        label_indices = np.arange(n_steps, dtype=np.int64) * frame_step
        labels = self._labels[label_indices]
        predictions = np.stack([self._predictions[i] for i in range(n_steps)])
        commands = np.stack([self._commands[i] for i in range(n_steps)])
        state_indices = np.minimum(
            np.arange(n_steps + 1, dtype=np.int64) * frame_step,
            self.source.n_steps - 1,
        )
        states = np.stack([self.source.get_obs_state(int(i)) for i in state_indices])

        prediction_error = predictions - labels
        command_error = commands - labels
        feature_names = self._feature_names()
        metrics = {
            "dataset_dir": str(self.source._dataset_dir),
            "episode": self.source.episode_id,
            "task": self.source.current_task,
            "dataset_fps": self.source.fps,
            "control_rate_hz": self.publish_rate,
            "source_frames_per_action": frame_step,
            "inference_horizon": self.inference_horizon,
            "source_frames_per_inference": frame_step * self.inference_horizon,
            "executed_actions": n_steps,
            "source_action_indices": label_indices.tolist(),
            "prediction_mse": float(np.mean(np.square(prediction_error))),
            "command_mse": float(np.mean(np.square(command_error))),
            "prediction_mse_by_joint": {
                name: float(np.mean(np.square(prediction_error[:, i])))
                for i, name in enumerate(feature_names)
            },
            "command_mse_by_joint": {
                name: float(np.mean(np.square(command_error[:, i])))
                for i, name in enumerate(feature_names)
            },
        }

        self.output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = f"episode_{self.source.episode_id}_policy_mse_{stamp}"
        image_path = self.output_dir / f"{stem}.png"
        metrics_path = self.output_dir / f"{stem}.json"
        self._draw_plot(image_path, labels, predictions, states, label_indices, metrics)
        metrics["plot"] = str(image_path)
        metrics_path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False) + "\n")
        logger.info(
            "[POLICY_OBSERVATION_REPORT] actions=%d prediction_mse=%.6f command_mse=%.6f "
            "plot=%s metrics=%s",
            n_steps,
            metrics["prediction_mse"],
            metrics["command_mse"],
            image_path,
            metrics_path,
        )
        return {"plot": str(image_path), "metrics": str(metrics_path), **metrics}

    def _feature_names(self) -> list[str]:
        info_path = self.source._dataset_dir / "meta" / "info.json"
        try:
            info = json.loads(info_path.read_text())
            names = info.get("features", {}).get("observation.state", {}).get("names")
        except (OSError, json.JSONDecodeError, AttributeError):
            names = None
        if isinstance(names, list) and len(names) == self._labels.shape[1]:
            return [str(name) for name in names]
        return [f"joint_{i + 1}" for i in range(self._labels.shape[1])]

    def _display_values(self, values: np.ndarray, index: int) -> np.ndarray:
        out = np.asarray(values, dtype=np.float32).copy()
        # G2's right gripper is qpos dim 15; show its fraction closed like the sample.
        if index == 15 and abs(self.gripper_open - self.gripper_close) > 1e-8:
            out = (self.gripper_open - out) / (self.gripper_open - self.gripper_close)
            out = np.clip(out, 0.0, 1.0)
        return out

    def _draw_plot(
        self,
        image_path: Path,
        labels: np.ndarray,
        predictions: np.ndarray,
        states: np.ndarray,
        source_indices: np.ndarray,
        metrics: dict[str, Any],
    ) -> None:
        width, height = 1920, 1320
        image = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(image)
        font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
        font_bold_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
        font = (
            ImageFont.truetype(str(font_path), 18)
            if font_path.exists()
            else ImageFont.load_default()
        )
        font_bold = ImageFont.truetype(str(font_bold_path), 21) if font_bold_path.exists() else font
        title = (
            f"EVA episode {self.source.episode_id} | {len(labels)} executed actions | "
            f"infer every {self.inference_horizon} control steps "
            f"(source stride={self.source_frames_per_action * self.inference_horizon})"
        )
        draw.text((width // 2, 18), title, fill="#111111", font=font_bold, anchor="mt")
        draw.text(
            (width - 58, 18),
            f"MSE={metrics['prediction_mse']:.6f}",
            fill="#111111",
            font=font_bold,
            anchor="rt",
        )
        legend_y = 56
        legend = [
            ("#111111", "GT label"),
            ("#ff7f0e", "EVA ACT"),
            ("#7896df", "state (includes t=0)"),
        ]
        legend_x = width // 2 - 250
        for color, label in legend:
            draw.line((legend_x, legend_y + 10, legend_x + 30, legend_y + 10), fill=color, width=3)
            draw.text((legend_x + 38, legend_y), label, fill="#333333", font=font)
            legend_x += 210

        panel_w, panel_h = 895, 285
        lefts = (55, 990)
        top = 95
        right_dims = list(range(8, 16))
        x_values = source_indices.astype(np.float32)
        state_x = np.arange(len(states), dtype=np.float32) * self.source_frames_per_action
        max_x = max(float(state_x[-1]), 1.0)
        colors = {"gt": "#111111", "act": "#ff7f0e", "state": "#7896df"}

        def point(
            x: float, y: float, bounds: tuple[int, int, int, int], y_min: float, y_max: float
        ):
            x0, y0, x1, y1 = bounds
            px = x0 + x / max_x * (x1 - x0)
            py = y1 - (y - y_min) / (y_max - y_min) * (y1 - y0)
            return px, py

        def draw_series(
            xs: np.ndarray,
            ys: np.ndarray,
            color: str,
            line_width: int,
            bounds: tuple[int, int, int, int],
            y_min: float,
            y_max: float,
        ) -> None:
            if len(xs) < 2:
                return
            coords = [
                point(float(x), float(y), bounds, y_min, y_max)
                for x, y in zip(xs, ys, strict=True)
            ]
            draw.line(coords, fill=color, width=line_width, joint="curve")

        for panel_index, dim in enumerate(right_dims):
            col, row = panel_index % 2, panel_index // 2
            panel_x = lefts[col]
            panel_y = top + row * panel_h
            plot_bounds = (
                panel_x + 110,
                panel_y + 36,
                panel_x + panel_w - 12,
                panel_y + panel_h - 42,
            )
            gt = self._display_values(labels[:, dim], dim)
            act = self._display_values(predictions[:, dim], dim)
            state = self._display_values(states[:, dim], dim)
            joint_mse = float(np.mean(np.square(act - gt)))
            values = np.concatenate((gt, act, state))
            y_min = float(np.nanmin(values))
            y_max = float(np.nanmax(values))
            spread = y_max - y_min
            pad = max(spread * 0.08, 0.01)
            y_min -= pad
            y_max += pad
            if y_max - y_min < 1e-6:
                y_min -= 0.5
                y_max += 0.5

            draw.text(
                (panel_x + panel_w // 2, panel_y),
                self._joint_label(dim),
                fill="#111111",
                font=font_bold,
                anchor="mt",
            )
            x0, y0, x1, y1 = plot_bounds
            draw.rectangle(plot_bounds, outline="#555555", width=1)
            for tick in range(5):
                ratio = tick / 4.0
                y = y_min + ratio * (y_max - y_min)
                py = y1 - ratio * (y1 - y0)
                draw.line((x0, py, x1, py), fill="#e5e5e5", width=1)
                draw.text((x0 - 8, py), f"{y:.2f}", fill="#444444", font=font, anchor="rm")
            for tick in range(5):
                px = x0 + tick / 4.0 * (x1 - x0)
                draw.line((px, y1, px, y1 + 5), fill="#555555", width=1)
                if row == 3:
                    draw.text(
                        (px, y1 + 7),
                        f"{int(round(max_x * tick / 4))}",
                        fill="#444444",
                        font=font,
                        anchor="mt",
                    )

            draw_series(x_values, gt, colors["gt"], 3, plot_bounds, y_min, y_max)
            draw_series(x_values, act, colors["act"], 2, plot_bounds, y_min, y_max)
            draw_series(state_x, state, colors["state"], 2, plot_bounds, y_min, y_max)
            mse_text = f"MSE={joint_mse:.6f}"
            text_box = draw.textbbox((0, 0), mse_text, font=font_bold)
            text_width = text_box[2] - text_box[0]
            draw.rectangle((x1 - text_width - 14, y0 + 5, x1 - 3, y0 + 35), fill="white")
            draw.text((x1 - 8, y0 + 7), mse_text, fill="#111111", font=font_bold, anchor="rt")
            unit = "closed fraction" if dim == 15 else "rad"
            unit_layer = Image.new("RGBA", (220, 28), (255, 255, 255, 0))
            ImageDraw.Draw(unit_layer).text((110, 2), unit, fill="#333333", font=font, anchor="mt")
            unit_layer = unit_layer.rotate(90, expand=True)
            image.paste(unit_layer, (panel_x + 12, (y0 + y1 - unit_layer.height) // 2), unit_layer)
            if row == 3:
                draw.text(
                    ((x0 + x1) // 2, y1 + 28),
                    "source frame",
                    fill="#333333",
                    font=font,
                    anchor="mt",
                )
        image.save(image_path, format="PNG", optimize=True)

    @staticmethod
    def _joint_label(index: int) -> str:
        if 8 <= index <= 14:
            return f"right_joint_{index - 7}"
        if index == 15:
            return "right_gripper"
        return f"joint_{index + 1}"
