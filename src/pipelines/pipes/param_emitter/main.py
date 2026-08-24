from typing import Dict, Any, List
from collections import defaultdict

from src.pipelines.outputs import ParamGenerationOutput
from src.pipelines.contracts import BasePipe, logger
from src.pipelines.contracts import (
    PipeInput,
    PipeOutput,
    PipeInputSpec,
    PipeOutputSpec,
    PipeConfigSpec,
)


class ParamEmitterPipe(BasePipe):
    name = "param_emitter"
    description = "Pipe that emits generation parameters for tracking and storage"

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        """
        Process parameters and emit ParamGenerationOutput for each parameter type.

        Configuration format:
        {
            "parameters": [
                ["model", "path/to/model.safetensors"],
                ["steps", 30],  # scalar - same for all indexes
                ["cfg", 7.5]
            ],
            "quantity": 3,  # number of images/outputs per pass
            "passes": 1     # times the batch is saved; quantity x passes rows
        }

        Can also receive parameters from inputs (e.g., from from_iotype pipe)
        """
        parameters = self.config.get("parameters", [])
        quantity = int(self.config.get("quantity", 1))
        passes = max(1, int(self.config.get("passes", 1)))

        # Group parameters by name
        # Note: @loop directives are already expanded by the preset processor before reaching here
        # However, they may come back as nested lists that need flattening
        param_groups = defaultdict(list)

        # Flatten parameters list (handle nested lists from @loop expansion)
        flat_parameters = []
        for item in parameters:
            if isinstance(item, list) and len(item) > 0:
                # Check if this is a nested list from @loop
                if isinstance(item[0], list):
                    # Flatten: [[param1], [param2]] -> [param1, param2]
                    flat_parameters.extend(item)
                else:
                    # Regular parameter: [name, value]
                    flat_parameters.append(item)
            else:
                logger.warning(f"[PARAM EMITTER] Invalid parameter format: {item}, skipping")

        # Now process the flattened parameters
        for param in flat_parameters:
            if not isinstance(param, (list, tuple)) or len(param) != 2:
                logger.warning(f"[PARAM EMITTER] Invalid parameter format after flattening: {param}, skipping")
                continue

            param_name, param_value = param
            param_groups[param_name].append(param_value)

        # Add parameters from inputs (e.g., seed from from_iotype, prompts from prompt_expander)
        # Inputs have priority - if present, they override configuration parameters
        for input_name, input_value in pipe_input.input.items():
            if input_value is not None:
                # Map input names to parameter names
                param_name_map = {
                    'p_prompt': 'positive_prompt',
                    'n_prompt': 'negative_prompt'
                }
                param_name = param_name_map.get(input_name, input_name)

                # If this parameter was already added from config, replace it with input value
                if param_name in param_groups:
                    logger.debug(f"[PARAM EMITTER] Overriding parameter '{param_name}' from input: {input_value}")
                    param_groups[param_name] = [input_value]
                else:
                    param_groups[param_name].append(input_value)
                    logger.debug(f"[PARAM EMITTER] Added parameter from input: {param_name} = {input_value}")

        # Process each parameter group
        all_params = {}
        for param_name, values in param_groups.items():
            # Convert values to appropriate format
            processed_values = self._process_parameter_values(param_name, values, quantity, passes)

            # Emit ParamGenerationOutput
            generation_outputs(ParamGenerationOutput(
                name=param_name,
                values=processed_values
            ))

            all_params[param_name] = processed_values

            logger.debug(f"[PARAM EMITTER] Emitted parameter: {param_name} = {processed_values}")

        return PipeOutput(output={"parameters": all_params})

    def _process_parameter_values(
        self, param_name: str, values: List[Any], quantity: int, passes: int = 1
    ) -> List[Any]:
        """
        Process parameter values based on their format:
        - If single value and it's an array: use as-is (per-index values), tiled
          across passes when it only covers one pass
        - If single value and it's scalar: broadcast to every saved index
        - If multiple values: treat as multiple items for this parameter (e.g., multiple models)
        """
        if len(values) == 0:
            return []

        # If multiple values provided, return them all (e.g., multiple models)
        if len(values) > 1:
            return values

        # Single value case
        value = values[0]
        total = quantity * passes

        # If it's already a list/array, use it as-is (per-index values)
        if isinstance(value, (list, tuple)):
            value = list(value)
            if len(value) == total:
                return value
            if passes > 1 and len(value) == quantity:
                return value * passes
            logger.warning(
                f"[PARAM EMITTER] Parameter '{param_name}' array length ({len(value)}) "
                f"doesn't match the number of saved outputs ({total}), using as-is"
            )
            return value
        else:
            # Scalar value - broadcast to all indexes
            return [value] * total

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "parameters": [],
            "quantity": 1,
            "passes": 1,
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        """Return specification of configuration parameters this pipe accepts"""
        return [
            PipeConfigSpec("parameters", list, [], "List of [name, value] parameter pairs", required=True),
            PipeConfigSpec("quantity", int, 1, "Number of outputs being generated", required=False,
                          min_value=1, max_value=20),
            PipeConfigSpec("passes", int, 1,
                          "How many times the batch is saved to the gallery (a refinement pass that "
                          "saves alongside the base batch is a second pass). One parameter row is "
                          "written per saved index, i.e. quantity x passes rows.",
                          required=False, min_value=1, max_value=8),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        """ParamEmitter can accept dynamic inputs (e.g., seed from from_iotype)"""
        return [
            # Dynamic inputs - accepts any parameters passed from other pipes
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        """ParamEmitter produces parameters output"""
        return [
            # No IOType outputs - this pipe only emits generation outputs
        ]
