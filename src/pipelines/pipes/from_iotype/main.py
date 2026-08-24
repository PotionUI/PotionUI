from typing import Dict, Any, List

from src.pipelines.contracts import BasePipe, logger
from src.pipelines.contracts import (
    PipeInput,
    PipeOutput,
    IOType,
    PipeInputSpec,
    PipeOutputSpec,
    PipeConfigSpec,
)


class FromIOTypePipe(BasePipe):
    name = "from_iotype"
    description = "Converts IOType values to regular output values for use in other pipes"

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        """
        Extract IOType values from inputs and convert to regular output.

        Configuration:
        {
            "mappings": [
                {"input": "seed", "output": "seed_values"},
                {"input": "model", "output": "model_info"}
            ]
        }

        Or simplified:
        {
            "from": "seed"  # Same input and output name
        }
        """
        mappings = self.config.get("mappings", [])
        from_input = self.config.get("from")

        output = {}

        # Simple mode: single input with same output name
        if from_input:
            if from_input in pipe_input.input:
                value = pipe_input.input[from_input]
                output[from_input] = value
                logger.debug(f"[FROM_IOTYPE] Converted {from_input}: {value}")
            else:
                logger.warning(f"[FROM_IOTYPE] Input '{from_input}' not found in pipe input")

        # Advanced mode: multiple mappings
        for mapping in mappings:
            input_name = mapping.get("input")
            output_name = mapping.get("output", input_name)

            if not input_name:
                logger.warning(f"[FROM_IOTYPE] Invalid mapping (missing input): {mapping}")
                continue

            if input_name in pipe_input.input:
                value = pipe_input.input[input_name]
                output[output_name] = value
                logger.debug(f"[FROM_IOTYPE] Converted {input_name} -> {output_name}: {value}")
            else:
                logger.warning(f"[FROM_IOTYPE] Input '{input_name}' not found in pipe input")

        return PipeOutput(output=output)

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "from": None,
            "mappings": [],
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        """Return specification of configuration parameters this pipe accepts"""
        return [
            PipeConfigSpec("from", str, None, "Single input to convert (simple mode)", required=False),
            PipeConfigSpec("mappings", list, [], "List of input->output mappings", required=False),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        """FromIOType accepts any IOType inputs dynamically"""
        return [
            # Dynamic inputs - will accept whatever is connected
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        """FromIOType produces dynamic outputs based on configuration"""
        return [
            # Dynamic outputs - will produce whatever is configured
        ]
