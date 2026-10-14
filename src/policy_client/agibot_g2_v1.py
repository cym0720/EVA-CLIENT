"""EVA client for the stateful agibot_g2.v1 MessagePack WebSocket protocol."""

from __future__ import annotations

import itertools
import logging
from typing import Any

import numpy as np
from openpi_client import msgpack_numpy

from core.config import ConfigDict
from core.registry import POLICY_REGISTRY
from policy_client._ws import WebSocketPolicyClient
from policy_client.base import PolicyBuildContext, PolicyRequestError

logger = logging.getLogger(__name__)
_CAMERAS = ("top_head", "hand_left", "hand_right")
_STATE_DIM = 24


@POLICY_REGISTRY.register_client("agibot_g2_v1")
class AgibotG2V1PolicyClient(WebSocketPolicyClient):
    """Talk to the G2 rolling-cache inference server through EVA's policy runtime."""

    def __init__(
        self,
        host: str,
        port: int,
        client: tuple[Any, dict] | None = None,
    ) -> None:
        super().__init__(
            host,
            port,
            retry_until_connected=False,
            max_retries=0,
            server_label="agibot_g2.v1 policy server",
            retry_errors=(ConnectionRefusedError, OSError),
            client=client,
        )
        metadata = dict(self._server_metadata)
        metadata.setdefault("server_name", "agibot_g2.v1")
        metadata.setdefault("action_mode", "qpos")
        metadata.setdefault("chunk_size", int(metadata.get("action_chunk_size", 16)))
        metadata.setdefault("action_dim", _STATE_DIM)
        metadata["sequential_chunks"] = True
        self._server_metadata = metadata
        self._request_ids = itertools.count()

        if metadata.get("protocol") not in (None, "agibot_g2.v1"):
            raise PolicyRequestError(f"Unexpected policy protocol: {metadata.get('protocol')!r}")
        if int(metadata["action_dim"]) != _STATE_DIM:
            raise PolicyRequestError(
                f"Expected a {_STATE_DIM}D G2 policy, got {metadata['action_dim']}D"
            )

    @classmethod
    def from_config(cls, config: ConfigDict, ctx: PolicyBuildContext) -> AgibotG2V1PolicyClient:
        _ = ctx
        return cls(config.host, config.port)

    def _request_id(self, prefix: str) -> str:
        return f"eva-{prefix}-{next(self._request_ids)}"

    def _round_trip(self, request: dict) -> dict:
        try:
            self._ws.send(self._packer.pack(request))
            response = self._ws.recv()
            if isinstance(response, str):
                raise RuntimeError(response)
            decoded = msgpack_numpy.unpackb(response)
        except Exception as error:
            raise PolicyRequestError("agibot_g2.v1 WebSocket request failed") from error
        if not isinstance(decoded, dict) or decoded.get("ok") is not True:
            detail = decoded.get("error") if isinstance(decoded, dict) else decoded
            raise PolicyRequestError(f"agibot_g2.v1 server rejected request: {detail}")
        expected_id = request.get("request_id")
        response_id = decoded.get("request_id")
        if response_id is not None and expected_id is not None and response_id != expected_id:
            raise PolicyRequestError("agibot_g2.v1 response request_id did not match the request")
        return decoded

    def infer(
        self,
        observation: dict,
        prev_action: np.ndarray | None = None,
        rtc_params: dict | None = None,
    ) -> dict:
        _ = prev_action, rtc_params
        images = observation.get("images") or {}
        missing = [key for key in _CAMERAS if key not in images]
        if missing:
            raise PolicyRequestError(f"G2 observation is missing camera images: {missing}")
        rgb_images = {}
        for key in _CAMERAS:
            image = np.asarray(images[key])
            if image.dtype != np.uint8 or image.ndim != 3 or image.shape[-1] != 3:
                raise PolicyRequestError(
                    f"Camera {key} must be HWC RGB uint8; got {image.dtype} {image.shape}"
                )
            rgb_images[key] = np.ascontiguousarray(image)

        # 调用方已经从 transport 帧（或回放数据源）组装好 state。此 client 只负责
        # 校验并序列化；不会再读取 GDK，也不会换用其他 reader 的 state。
        state = np.asarray(observation.get("state"), dtype=np.float32).reshape(-1)
        if state.shape != (_STATE_DIM,) or not np.isfinite(state).all():
            raise PolicyRequestError(
                f"G2 state must be finite float32[{_STATE_DIM}], got {state.shape}"
            )

        request_id = self._request_id("infer")
        request = {
            "type": "infer",
            "request_id": request_id,
            "payload": {
                "images": rgb_images,
                "lang": str(observation.get("prompt", "")),
                "state": state,
            },
        }
        response = self._round_trip(request)
        if response.get("request_id") != request_id:
            raise PolicyRequestError("agibot_g2.v1 inference response omitted request_id")
        if response.get("type") != "inference_result":
            raise PolicyRequestError(
                f"Unexpected agibot_g2.v1 response type: {response.get('type')!r}"
            )
        try:
            actions = np.asarray(response["data"]["actions"], dtype=np.float32)
        except (KeyError, TypeError, ValueError) as error:
            raise PolicyRequestError("G2 response is missing a valid actions array") from error
        if actions.ndim != 3 or actions.shape[0] != 1 or actions.shape[2] != _STATE_DIM:
            raise PolicyRequestError(f"Expected G2 actions shaped [1,T,24], got {actions.shape}")
        actions = actions[0]
        if actions.shape[0] != int(self.metadata["chunk_size"]):
            raise PolicyRequestError(
                f"Expected {self.metadata['chunk_size']} actions, got {actions.shape[0]}"
            )
        if not np.isfinite(actions).all():
            raise PolicyRequestError("G2 response contains non-finite actions")
        return {"actions": actions}

    def reset(self) -> None:
        request_id = self._request_id("reset")
        self._round_trip({"type": "reset", "request_id": request_id})
