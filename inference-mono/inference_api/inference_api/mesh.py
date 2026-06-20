from __future__ import annotations

import httpx

from inference_api.config import settings
from inference_api.errors import service_unavailable
from inference_api.schemas import InferenceRequest


class MeshInferenceResult:
    def __init__(self, output: str, output_tokens: int | None = None) -> None:
        self.output = output
        self.output_tokens = output_tokens


class MeshInferenceClient:
    async def run(self, payload: InferenceRequest) -> MeshInferenceResult:
        if settings.mesh_router_url is None:
            output = f"Echo: {payload.prompt[: min(len(payload.prompt), payload.max_tokens)]}"
            return MeshInferenceResult(output=output)

        try:
            async with httpx.AsyncClient(timeout=settings.mesh_request_timeout_seconds) as client:
                response = await client.post(
                    f"{settings.mesh_router_url}/inference",
                    json=payload.model_dump(),
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise service_unavailable("mesh_unavailable", "Peer mesh inference router unavailable") from exc

        data = response.json()
        output = data.get("output")
        if not isinstance(output, str):
            raise service_unavailable("mesh_invalid_response", "Peer mesh inference router returned invalid output")

        usage = data.get("usage") if isinstance(data, dict) else None
        output_tokens = None
        if isinstance(usage, dict) and isinstance(usage.get("output_tokens"), int):
            output_tokens = usage["output_tokens"]
        return MeshInferenceResult(output=output, output_tokens=output_tokens)
