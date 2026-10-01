import logging
from typing import Optional

from src.features.generation.handlers.error_handler import admin_error_fields
from src.features.generation.output_serializer import GenerationOutputSerializer
from src.features.generation.output_types import output_type_registry
from src.features.generation.run_report_recorder import RunReportRecorder
from src.features.generation.status_tracker import GenerationStatusTracker
from src.pipelines.outputs import ErrorGenerationOutput, GenerationOutput
from src.platform.websocket.connection_hub import ConnectionHub


class GenerationOutputBroadcaster:
    def __init__(
        self,
        connection_hub: ConnectionHub,
        status_tracker: GenerationStatusTracker,
        run_report_recorder: RunReportRecorder,
    ):
        self.connection_hub = connection_hub
        self.status_tracker = status_tracker
        self.run_report_recorder = run_report_recorder

    async def handle_output(self, generation_id: str, output: Optional[GenerationOutput]) -> None:
        status = self.status_tracker.get(generation_id)
        if not status:
            return

        if output is None:
            status_dict = status.model_dump()
            try:
                self.run_report_recorder.flush(
                    generation_id,
                    terminal_status=status_dict.get('status'),
                    terminal_message=status_dict.get('message'),
                )
            except Exception:
                logging.exception(f"Failed to flush run report for {generation_id}")
            await self.connection_hub.broadcast_to_generation(
                generation_id,
                {'type': 'generation_complete', 'data': status_dict}
            )
            return

        await self.broadcast_output(generation_id, output, status)

    async def broadcast_cancelled(self, generation_id: str) -> None:
        status = self.status_tracker.get(generation_id)
        if status:
            await self.connection_hub.broadcast_to_generation(
                generation_id,
                {'type': 'generation_cancelled', 'data': status.model_dump()}
            )

    async def broadcast_output(self, generation_id: str, output: GenerationOutput, status) -> None:
        if output_type_registry.is_server_only(output):
            return

        has_subscribers = bool(self.connection_hub.generation_connections.get(generation_id))

        try:
            serializer = GenerationOutputSerializer(
                generation_id=generation_id,
                preset_id=status.preset_id,
            )

            message = serializer.serialize_output(output)

            try:
                self.run_report_recorder.record_output(generation_id, message)
            except Exception:
                logging.exception(f"Failed to record run report output for {generation_id}")

            if not has_subscribers:
                return

            privileged_message = None
            if isinstance(output, ErrorGenerationOutput):
                privileged_message = {**message, **admin_error_fields(output)}

            await self.connection_hub.broadcast_to_generation(generation_id, message, privileged_message)

        except Exception as e:
            logging.error(f"Failed to broadcast generation output: {str(e)}")

            await self.connection_hub.broadcast_to_generation(
                generation_id,
                {
                    'type': 'generation_error',
                    'data': {
                        'generation_id': generation_id,
                        'message': "Output processing failed",
                        'status': status.model_dump()
                    }
                }
            )
