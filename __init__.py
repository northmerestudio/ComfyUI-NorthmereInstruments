"""ComfyUI entrypoint for Northmere Instruments."""
from __future__ import annotations

WEB_DIRECTORY = "./web"


async def comfy_entrypoint():
    """Register native ComfyUI V3 nodes without legacy V1 mappings."""
    from comfy_api.latest import ComfyExtension, io
    from .northmere_instruments.nodes import LoRAEnvelopeNode

    class NorthmereInstrumentsExtension(ComfyExtension):
        async def get_node_list(self) -> list[type[io.ComfyNode]]:
            return [LoRAEnvelopeNode]

    return NorthmereInstrumentsExtension()
