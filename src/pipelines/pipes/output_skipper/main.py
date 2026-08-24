from typing import Dict, Any, List, Union
from copy import deepcopy

from src.pipelines.outputs import ProgressGenerationOutput
from src.pipelines.contracts import BasePipe, logger
from src.pipelines.contracts import (
    PipeInput,
    PipeOutput,
    IOType,
    PipeInputSpec,
    PipeOutputSpec,
    PipeConfigSpec,
)
from src.pipelines.outputs import Icon, Progress


class OutputSkipperPipe(BasePipe):
    name = "output_skipper"
    description = "Filter and skip specific outputs from the previous pipe by type and index"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "rules": []  # List of filtering rules
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec(
                name="rules",
                param_type=list,
                default=[],
                description="List of filtering rules. Each rule should have: output_type, action ('skip'/'keep'), and indices or count",
                required=False
            ),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        # This pipe accepts any input dynamically, so we define the most common ones
        # but the actual processing will handle any input type
        return [
            PipeInputSpec("image", IOType.IMAGE, False, "Input images to filter", is_array=True),
            PipeInputSpec("video", IOType.VIDEO, False, "Input videos to filter", is_array=True),
            PipeInputSpec("mask", IOType.MASK, False, "Input masks to filter", is_array=True),
            PipeInputSpec("latent", IOType.LATENT, False, "Input latents to filter", is_array=True),
            PipeInputSpec("seed", IOType.SEED, False, "Input seeds to filter", is_array=True),
            PipeInputSpec("conditioning", IOType.CONDITIONING, False, "Input conditioning to filter", is_array=True),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        # This pipe outputs the same types as inputs, but filtered
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Filtered images", is_array=True),
            PipeOutputSpec("video", IOType.VIDEO, "Filtered videos", is_array=True),
            PipeOutputSpec("mask", IOType.MASK, "Filtered masks", is_array=True),
            PipeOutputSpec("latent", IOType.LATENT, "Filtered latents", is_array=True),
            PipeOutputSpec("seed", IOType.SEED, "Filtered seeds", is_array=True),
            PipeOutputSpec("conditioning", IOType.CONDITIONING, "Filtered conditioning", is_array=True),
        ]

    def parse_rule(self, rule: Dict[str, Any]) -> Dict[str, Any]:
        """Parse and validate a filtering rule"""
        if not isinstance(rule, dict):
            logger.warning(f"[OUTPUT_SKIPPER] Invalid rule format: {rule}")
            return None

        output_type = rule.get("output_type")
        action = rule.get("action")
        indices = rule.get("indices")
        count = rule.get("count")

        # Validate required fields
        if not output_type:
            logger.warning(f"[OUTPUT_SKIPPER] Rule missing 'output_type': {rule}")
            return None

        if action not in ["skip", "keep"]:
            logger.warning(f"[OUTPUT_SKIPPER] Invalid action '{action}'. Must be 'skip' or 'keep': {rule}")
            return None

        # Must have either indices or count
        if indices is None and count is None:
            logger.warning(f"[OUTPUT_SKIPPER] Rule must specify either 'indices' or 'count': {rule}")
            return None

        # Validate indices
        if indices is not None:
            if not isinstance(indices, list):
                logger.warning(f"[OUTPUT_SKIPPER] 'indices' must be a list: {rule}")
                return None
            # Ensure all indices are integers
            try:
                indices = [int(i) for i in indices]
            except (ValueError, TypeError):
                logger.warning(f"[OUTPUT_SKIPPER] All indices must be integers: {rule}")
                return None

        # Validate count
        if count is not None:
            try:
                count = int(count)
                if count < 0:
                    logger.warning(f"[OUTPUT_SKIPPER] 'count' must be non-negative: {rule}")
                    return None
            except (ValueError, TypeError):
                logger.warning(f"[OUTPUT_SKIPPER] 'count' must be an integer: {rule}")
                return None

        return {
            "output_type": output_type,
            "action": action,
            "indices": indices,
            "count": count
        }

    def apply_rule(self, data: List[Any], rule: Dict[str, Any]) -> List[Any]:
        """Apply a filtering rule to a list of data"""
        if not data or not isinstance(data, list):
            return data

        action = rule["action"]
        indices = rule["indices"]
        count = rule["count"]

        # Calculate target indices
        if indices is not None:
            target_indices = set(indices)
        elif count is not None:
            target_indices = set(range(min(count, len(data))))
        else:
            return data

        # Filter based on action
        filtered_data = []
        for i, item in enumerate(data):
            if action == "skip":
                if i not in target_indices:
                    filtered_data.append(item)
            else:  # keep
                if i in target_indices:
                    filtered_data.append(item)

        return filtered_data

    def find_rule_for_output_type(self, output_type: str, rules: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Find the first rule that matches the given output type"""
        for rule in rules:
            if rule and rule.get("output_type") == output_type:
                return rule
        return None

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        rules = self.config.get("rules", [])

        if not rules:
            logger.debug("[OUTPUT_SKIPPER] No filtering rules configured, passing through all outputs")
            generation_outputs(ProgressGenerationOutput(
                state="No filtering rules configured - passing through all outputs",
                icon=Icon("arrow-right"),
                progress=Progress(100, 100)
            ))
            return PipeOutput(output=pipe_input.input.copy())

        generation_outputs(ProgressGenerationOutput(
            state=f"Processing <<NUMBER:{len(rules)} filtering rules:filter>>",
            icon=Icon("filter"),
            progress=Progress(0, 100)
        ))

        # Parse and validate rules
        parsed_rules = []
        for i, rule in enumerate(rules):
            parsed_rule = self.parse_rule(rule)
            if parsed_rule:
                parsed_rules.append(parsed_rule)
            else:
                logger.warning(f"[OUTPUT_SKIPPER] Skipping invalid rule {i+1}: {rule}")

        if not parsed_rules:
            logger.warning("[OUTPUT_SKIPPER] No valid rules found, passing through all outputs")
            generation_outputs(ProgressGenerationOutput(
                state="No valid rules found - passing through all outputs",
                icon=Icon("arrow-right"),
                progress=Progress(100, 100)
            ))
            return PipeOutput(output=pipe_input.input.copy())

        # Process each output type
        filtered_output = {}
        total_items_before = 0
        total_items_after = 0

        for output_name, output_data in pipe_input.input.items():
            # Find rule for this output type
            rule = self.find_rule_for_output_type(output_name, parsed_rules)

            if rule is None:
                # No rule for this output type, pass through unchanged
                filtered_output[output_name] = output_data
                if isinstance(output_data, list):
                    total_items_before += len(output_data)
                    total_items_after += len(output_data)
                else:
                    total_items_before += 1
                    total_items_after += 1
                logger.debug(f"[OUTPUT_SKIPPER] No rule for '{output_name}', passing through")
            else:
                # Apply filtering rule
                original_count = len(output_data) if isinstance(output_data, list) else 1

                if isinstance(output_data, list):
                    filtered_data = self.apply_rule(output_data, rule)
                    filtered_output[output_name] = filtered_data
                    total_items_before += original_count
                    total_items_after += len(filtered_data)

                    logger.debug(f"[OUTPUT_SKIPPER] Applied {rule['action']} rule to '{output_name}': "
                              f"{original_count} → {len(filtered_data)} items")
                else:
                    # Single item, treat as list of one
                    single_item_list = [output_data]
                    filtered_list = self.apply_rule(single_item_list, rule)
                    filtered_output[output_name] = filtered_list[0] if filtered_list else None
                    total_items_before += 1
                    total_items_after += len(filtered_list)

                    logger.debug(f"[OUTPUT_SKIPPER] Applied {rule['action']} rule to single '{output_name}': "
                              f"{'kept' if filtered_list else 'skipped'}")

        generation_outputs(ProgressGenerationOutput(
            state=f"Filtering complete: <<NUMBER:{total_items_before}>> → <<NUMBER:{total_items_after}>> items",
            icon=Icon("check-circle"),
            progress=Progress(100, 100)
        ))

        # Log summary
        logger.debug(f"[OUTPUT_SKIPPER] Filtering complete: {total_items_before} → {total_items_after} total items")
        for rule in parsed_rules:
            logger.debug(f"[OUTPUT_SKIPPER] Rule: {rule['action']} {rule['output_type']} "
                       f"{'indices ' + str(rule['indices']) if rule['indices'] else 'count ' + str(rule['count'])}")

        return PipeOutput(output=filtered_output)