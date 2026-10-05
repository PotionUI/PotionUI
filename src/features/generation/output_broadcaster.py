import logging
from typing import Optional

from src.features.generation.failure import is_video_request, scope_error_payload, scope_status
from src.features.generation.handlers.error_handler import admin_error_fields
from src.features.generation.output_serializer import GenerationOutputSerializer
from src.features.generation.output_types import output_type_registry
from src.features.generation.repository import generation_repo
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
            await self._broadcast_status(generation_id, 'generation_complete', status_dict)
            return

        await self.broadcast_output(generation_id, output, status)

    async def broadcast_cancelled(self, generation_id: str) -> None:
        status = self.status_tracker.get(generation_id)
        if status:
            await self._broadcast_status(generation_id, 'generation_cancelled', status.model_dump())

    async def _broadcast_status(self, generation_id: str, message_type: str, status_dict: dict) -> None:
        await self.connection_hub.broadcast_to_generation(
            generation_id,
            {'type': message_type, 'data': scope_status(status_dict, False)},
            {'type': message_type, 'data': status_dict},
        )

    @staticmethod
    def _is_video(generation_id: str) -> bool:
        try:
            generation = generation_repo.get_by_id(generation_id)
        except Exception:
            return False
        if generation is None:
            return False
        return is_video_request(generation.form_data, generation.mode)

    async def broadcast_output(self, generation_id: str, output: GenerationOutput, status) -> None:
        if output_type_registry.is_server_only(output):
            return

        has_subscribers = bool(self.connection_hub.generation_connections.get(generation_id))

        try:
            serializer = GenerationOutputSerializer(
                generation_id=generation_id,
                preset_id=status.preset_id,
                grid_id=getattr(status, 'grid_id', None),
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
                message = scope_error_payload(message, False, self._is_video(generation_id))

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
                        'status': scope_status(status.model_dump(), False)
                    }
                },
                {
                    'type': 'generation_error',
                    'data': {
                        'generation_id': generation_id,
                        'message': "Output processing failed",
                        'status': status.model_dump()
                    }
                }
            )
