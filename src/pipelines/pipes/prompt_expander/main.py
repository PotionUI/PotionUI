import asyncio
from typing import Dict, Any, List, Optional
from src.pipelines.outputs import DiffTextGenerationOutput, ProgressGenerationOutput
from src.pipelines.contracts import BasePipe, logger
from src.pipelines.contracts import (
    PipeInput,
    PipeOutput,
    IOType,
    PipeInputSpec,
    PipeOutputSpec,
    PipeConfigSpec,
)
from src.pipelines.pipes._shared.generation.prompt_diff import word_diff


class PromptExpanderPipe(BasePipe):
    name = "prompt_expander"
    description = "Expands prompts using configured LLM service"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        """Return default configuration for the pipe."""
        return {
            "llm_id": None,
            "command_id": None,
            "style_id": None,
            "prompt": "Expand this prompt with creative details",
            "p_prompt_input": "",
            "n_prompt_input": "",
            "p_prompt_output": "[[__expanded_p_prompt__]]",
            "n_prompt_output": "[[__expanded_n_prompt__]]",
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        """Return specification of configuration parameters this pipe accepts."""
        return [
            PipeConfigSpec("llm_id", str, None, "LLM configuration ID", required=False),
            PipeConfigSpec("command_id", str, None, "Predefined command ID", required=False),
            PipeConfigSpec("style_id", str, None, "Prompt style ID", required=False),
            PipeConfigSpec("prompt", str, "Expand this prompt", "Expansion instruction", required=False),
            PipeConfigSpec("p_prompt_input", str, "", "Positive prompt to expand", required=True),
            PipeConfigSpec("n_prompt_input", str, "", "Negative prompt to expand", required=False),
            PipeConfigSpec("p_prompt_output", str, "[[__expanded_p_prompt__]]", "Output template for positive", required=False),
            PipeConfigSpec("n_prompt_output", str, "[[__expanded_n_prompt__]]", "Output template for negative", required=False),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        """Prompt expander requires LLM service"""
        return [
            PipeInputSpec("LLM", IOType.SERVICE, True, "LLM service for prompt expansion", is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        """Prompt expander produces text outputs."""
        return [
            PipeOutputSpec("p_prompt", IOType.TEXT, "Expanded positive prompt", is_array=False),
            PipeOutputSpec("n_prompt", IOType.TEXT, "Expanded negative prompt", is_array=False),
        ]

    def _extract_preserved_text(self, text: str) -> tuple[str, str]:
        """Extract text within [[...]] markers and remove them from input.

        Args:
            text: Input text that may contain [[...]] markers

        Returns:
            Tuple of (preserved_prefix, cleaned_text)
            - preserved_prefix: All text from [[...]] concatenated with spaces
            - cleaned_text: Input text with [[...]] sections removed
        """
        import re

        if not text or not text.strip():
            return ('', '')

        # Find all [[...]] patterns
        pattern = r'\[\[([^\]]*)\]\]'
        matches = re.findall(pattern, text)

        # Concatenate all preserved text with spaces
        preserved_prefix = ' '.join(match.strip() for match in matches if match.strip())

        # Remove [[...]] sections from text
        cleaned_text = re.sub(pattern, '', text)

        # Clean up extra whitespace
        cleaned_text = ' '.join(cleaned_text.split())

        return (preserved_prefix, cleaned_text)

    async def _expand_prompt_async(self, llm_service, prompt: str, base_prompt: str) -> str:
        """Expand a prompt using LLM service

        Args:
            llm_service: The injected LLM service
            prompt: Expansion instruction
            base_prompt: The original prompt to expand

        Returns:
            Expanded prompt string
        """
        if not base_prompt or not base_prompt.strip():
            return ''

        # Extract preserved text from [[...]] markers
        preserved_prefix, cleaned_prompt = self._extract_preserved_text(base_prompt)

        if preserved_prefix:
            logger.debug(f"[PROMPT_EXPANDER] Extracted preserved prefix: '{preserved_prefix}'")
            logger.debug(f"[PROMPT_EXPANDER] Cleaned prompt for LLM: '{cleaned_prompt}'")

        # If only preserved text and no content to expand, return preserved text
        if not cleaned_prompt or not cleaned_prompt.strip():
            return preserved_prefix

        llm_id = self.config.get("llm_id")
        style_id = self.config.get("style_id")
        expansion_instruction = self.config.get("prompt", "Expand this prompt")

        if not llm_id:
            logger.warning("[PROMPT_EXPANDER] No llm_id configured, returning original prompt")
            return base_prompt

        # Combine instruction with cleaned prompt (without [[...]] sections)
        full_prompt = f"{expansion_instruction}: {cleaned_prompt}"

        try:
            response = await llm_service.generate_with_config_id(
                prompt=full_prompt,
                llm_id=llm_id,
                style_id=style_id
            )
            expanded_text = response.content.strip()

            # Prepend preserved prefix if it exists
            if preserved_prefix:
                result = f"{preserved_prefix} {expanded_text}"
                logger.debug(f"[PROMPT_EXPANDER] Final result with prefix: '{result}'")
                return result
            else:
                return expanded_text
        except Exception as e:
            logger.error(f"[PROMPT_EXPANDER] LLM expansion failed: {e}")
            return base_prompt

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        """Process prompts using LLM service

        Args:
            pipe_input: Input containing LLM service
            generation_outputs: Callback for progress updates

        Returns:
            PipeOutput with expanded prompts
        """
        generation_outputs(ProgressGenerationOutput(state="Expanding prompts with LLM"))

        # Get LLM service from injected services
        llm_service = pipe_input.input.get("LLM")
        if not llm_service:
            raise ValueError("LLM service not available")

        # Get prompts from config
        p_prompt_input = self.config.get("p_prompt_input", "")
        n_prompt_input = self.config.get("n_prompt_input", "")

        logger.debug(f"[PROMPT_EXPANDER] Input positive prompt: {p_prompt_input}")
        logger.debug(f"[PROMPT_EXPANDER] Input negative prompt: {n_prompt_input}")

        # Expand prompts using async LLM service
        # Use asyncio.run() to properly handle async in threaded context
        expanded_p_prompt = asyncio.run(
            self._expand_prompt_async(llm_service, "positive", p_prompt_input)
        )
        expanded_n_prompt = ""
        if n_prompt_input:
            expanded_n_prompt = asyncio.run(
                self._expand_prompt_async(llm_service, "negative", n_prompt_input)
            )

        logger.debug(f"[PROMPT_EXPANDER] Expanded positive: {expanded_p_prompt}")
        logger.debug(f"[PROMPT_EXPANDER] Expanded negative: {expanded_n_prompt}")

        # Generate diffs for UI
        p_diff = self._generate_diff(p_prompt_input, expanded_p_prompt)
        n_diff = self._generate_diff(n_prompt_input, expanded_n_prompt)

        generation_outputs(DiffTextGenerationOutput(index=0, name="Expanded Positive Prompt", diff=p_diff))
        if n_prompt_input:
            generation_outputs(DiffTextGenerationOutput(index=0, name="Expanded Negative Prompt", diff=n_diff))

        # Process output templates
        p_prompt_output = self.config.get("p_prompt_output", "[[__expanded_p_prompt__]]")
        n_prompt_output = self.config.get("n_prompt_output", "[[__expanded_n_prompt__]]")

        p_prompt_output = p_prompt_output.replace("[[__expanded_p_prompt__]]", expanded_p_prompt)
        n_prompt_output = n_prompt_output.replace("[[__expanded_n_prompt__]]", expanded_n_prompt)

        return PipeOutput(
            output={
                "p_prompt": p_prompt_output,
                "n_prompt": n_prompt_output,
            }
        )

    def _generate_diff(self, original: str, expanded: str) -> List[tuple]:
        """Generate diff between original and expanded prompts for UI display

        Args:
            original: Original prompt
            expanded: Expanded prompt

        Returns:
            List of (text, marker) tuples for diff display
        """
        return word_diff(original, expanded)
