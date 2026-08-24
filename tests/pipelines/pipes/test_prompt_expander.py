import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from src.pipelines.pipes.prompt_expander.main import PromptExpanderPipe
from src.pipelines.contracts import PipeInput, PipeOutput, IOType


class TestPromptExpanderPipe:

    def test_init(self):
        """Test pipe initialization"""
        config = PromptExpanderPipe.get_default_config()
        pipe = PromptExpanderPipe(config)

        assert pipe.name == "prompt_expander"
        assert pipe.description == "Expands prompts using configured LLM service"

    def test_get_default_config(self):
        """Test default configuration"""
        config = PromptExpanderPipe.get_default_config()

        assert config["llm_id"] is None
        assert config["command_id"] is None
        assert config["style_id"] is None
        assert config["prompt"] == "Expand this prompt with creative details"
        assert config["p_prompt_input"] == ""
        assert config["n_prompt_input"] == ""
        assert config["p_prompt_output"] == "[[__expanded_p_prompt__]]"
        assert config["n_prompt_output"] == "[[__expanded_n_prompt__]]"

    def test_configuration_specs(self):
        """Test configuration specifications"""
        specs = PromptExpanderPipe.configuration()

        spec_names = [spec.name for spec in specs]
        assert "llm_id" in spec_names
        assert "command_id" in spec_names
        assert "style_id" in spec_names
        assert "prompt" in spec_names
        assert "p_prompt_input" in spec_names
        assert "n_prompt_input" in spec_names
        assert "p_prompt_output" in spec_names
        assert "n_prompt_output" in spec_names

        # Check that llm_id is required
        llm_spec = next(s for s in specs if s.name == "llm_id")
        assert llm_spec.param_type == str

    def test_inputs_outputs(self):
        """Test input and output specifications"""
        inputs = PromptExpanderPipe.inputs()
        outputs = PromptExpanderPipe.outputs()

        # Should require LLM service
        assert len(inputs) == 1
        assert inputs[0].name == "LLM"
        assert inputs[0].io_type == IOType.SERVICE
        assert inputs[0].required is True

        # Two text outputs
        assert len(outputs) == 2
        assert outputs[0].name == "p_prompt"
        assert outputs[0].io_type == IOType.TEXT
        assert outputs[1].name == "n_prompt"
        assert outputs[1].io_type == IOType.TEXT

    @pytest.mark.asyncio
    async def test_expand_prompt_async_empty(self):
        """Test expansion with empty prompt"""
        config = PromptExpanderPipe.get_default_config()
        config["llm_id"] = "test_llm"
        pipe = PromptExpanderPipe(config)

        mock_llm_service = AsyncMock()
        result = await pipe._expand_prompt_async(mock_llm_service, "Test instruction", "")

        assert result == ""
        mock_llm_service.generate_with_config_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_expand_prompt_async_whitespace_only(self):
        """Test expansion with whitespace-only prompt"""
        config = PromptExpanderPipe.get_default_config()
        config["llm_id"] = "test_llm"
        pipe = PromptExpanderPipe(config)

        mock_llm_service = AsyncMock()
        result = await pipe._expand_prompt_async(mock_llm_service, "Test instruction", "   ")

        assert result == ""
        mock_llm_service.generate_with_config_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_expand_prompt_async_no_llm_id(self):
        """Test expansion without llm_id configured"""
        config = PromptExpanderPipe.get_default_config()
        # llm_id is None
        pipe = PromptExpanderPipe(config)

        mock_llm_service = AsyncMock()
        result = await pipe._expand_prompt_async(mock_llm_service, "Test instruction", "a cat")

        assert result == "a cat"  # Should return original prompt
        mock_llm_service.generate_with_config_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_expand_prompt_async_success(self):
        """Test successful prompt expansion"""
        config = PromptExpanderPipe.get_default_config()
        config["llm_id"] = "test_llm"
        config["style_id"] = "creative"
        config["prompt"] = "Make it detailed"
        pipe = PromptExpanderPipe(config)

        mock_response = Mock()
        mock_response.content = "  a beautiful cat with detailed fur  "

        mock_llm_service = AsyncMock()
        mock_llm_service.generate_with_config_id.return_value = mock_response

        result = await pipe._expand_prompt_async(mock_llm_service, "Make it detailed", "a cat")

        assert result == "a beautiful cat with detailed fur"
        mock_llm_service.generate_with_config_id.assert_called_once_with(
            prompt="Make it detailed: a cat",
            llm_id="test_llm",
            style_id="creative"
        )

    @pytest.mark.asyncio
    async def test_expand_prompt_async_llm_error(self):
        """Test expansion when LLM service fails"""
        config = PromptExpanderPipe.get_default_config()
        config["llm_id"] = "test_llm"
        pipe = PromptExpanderPipe(config)

        mock_llm_service = AsyncMock()
        mock_llm_service.generate_with_config_id.side_effect = Exception("LLM service error")

        result = await pipe._expand_prompt_async(mock_llm_service, "Expand", "a cat")

        assert result == "a cat"  # Should return original prompt on error

    def test_process_no_llm_service(self):
        """Test process fails without LLM service"""
        config = PromptExpanderPipe.get_default_config()
        pipe = PromptExpanderPipe(config)

        pipe_input = PipeInput(input={})
        generation_outputs = Mock()

        with pytest.raises(ValueError) as exc_info:
            pipe.process(pipe_input, generation_outputs)

        assert "LLM service not available" in str(exc_info.value)

    def test_process_positive_only(self):
        """Test processing with only positive prompt"""
        config = PromptExpanderPipe.get_default_config()
        config["llm_id"] = "test_llm"
        config["p_prompt_input"] = "a cat"
        config["p_prompt_output"] = "Photo of [[__expanded_p_prompt__]]"
        config["n_prompt_input"] = ""
        pipe = PromptExpanderPipe(config)

        mock_response = Mock()
        mock_response.content = "a beautiful cat"

        mock_llm_service = AsyncMock()
        mock_llm_service.generate_with_config_id.return_value = mock_response

        pipe_input = PipeInput(input={"LLM": mock_llm_service})
        generation_outputs = Mock()

        with patch('asyncio.run') as mock_run:
            mock_run.return_value = "a beautiful cat"

            result = pipe.process(pipe_input, generation_outputs)

        assert isinstance(result, PipeOutput)
        assert result.output["p_prompt"] == "Photo of a beautiful cat"
        assert result.output["n_prompt"] == ""

    def test_process_both_prompts(self):
        """Test processing with both positive and negative prompts"""
        config = PromptExpanderPipe.get_default_config()
        config["llm_id"] = "test_llm"
        config["p_prompt_input"] = "a cat"
        config["n_prompt_input"] = "ugly"
        config["p_prompt_output"] = "[[__expanded_p_prompt__]]"
        config["n_prompt_output"] = "[[__expanded_n_prompt__]]"
        pipe = PromptExpanderPipe(config)

        mock_llm_service = AsyncMock()

        pipe_input = PipeInput(input={"LLM": mock_llm_service})
        generation_outputs = Mock()

        with patch('asyncio.run') as mock_run:
            # Return different values for positive and negative
            mock_run.side_effect = [
                "a beautiful cat with detailed fur",
                "ugly, distorted, blurry"
            ]

            result = pipe.process(pipe_input, generation_outputs)

        assert isinstance(result, PipeOutput)
        assert result.output["p_prompt"] == "a beautiful cat with detailed fur"
        assert result.output["n_prompt"] == "ugly, distorted, blurry"

    def test_process_template_replacement(self):
        """Test that template placeholders are replaced correctly"""
        config = PromptExpanderPipe.get_default_config()
        config["llm_id"] = "test_llm"
        config["p_prompt_input"] = "a cat"
        config["p_prompt_output"] = "Start: [[__expanded_p_prompt__]], End"
        config["n_prompt_input"] = "ugly"
        config["n_prompt_output"] = "Negative: [[__expanded_n_prompt__]]"
        pipe = PromptExpanderPipe(config)

        mock_llm_service = AsyncMock()

        pipe_input = PipeInput(input={"LLM": mock_llm_service})
        generation_outputs = Mock()

        with patch('asyncio.run') as mock_run:
            mock_run.side_effect = [
                "expanded positive",
                "expanded negative"
            ]

            result = pipe.process(pipe_input, generation_outputs)

        assert result.output["p_prompt"] == "Start: expanded positive, End"
        assert result.output["n_prompt"] == "Negative: expanded negative"

    def test_process_generates_diffs(self):
        """Test that diff outputs are generated"""
        config = PromptExpanderPipe.get_default_config()
        config["llm_id"] = "test_llm"
        config["p_prompt_input"] = "a cat"
        config["n_prompt_input"] = "ugly"
        pipe = PromptExpanderPipe(config)

        mock_llm_service = AsyncMock()

        pipe_input = PipeInput(input={"LLM": mock_llm_service})
        generation_outputs = Mock()

        with patch('asyncio.run') as mock_run:
            mock_run.side_effect = [
                "a beautiful cat",
                "ugly, blurry"
            ]

            pipe.process(pipe_input, generation_outputs)

        # Should have called generation_outputs with ProgressGenerationOutput and DiffTextGenerationOutput
        assert generation_outputs.call_count >= 3  # Progress + 2 diffs

    def test_generate_diff_basic(self):
        """Test basic diff generation"""
        config = PromptExpanderPipe.get_default_config()
        pipe = PromptExpanderPipe(config)

        original = "a cat"
        expanded = "a beautiful cat"

        diff = pipe._generate_diff(original, expanded)

        assert isinstance(diff, list)
        assert len(diff) > 0
        # Should have tuples of (text, marker)
        for item in diff:
            assert isinstance(item, tuple)
            assert len(item) == 2

    def test_generate_diff_identical(self):
        """Test diff generation with identical strings"""
        config = PromptExpanderPipe.get_default_config()
        pipe = PromptExpanderPipe(config)

        text = "a cat"
        diff = pipe._generate_diff(text, text)

        assert isinstance(diff, list)
        # All items should have no marker (None) since they're unchanged
        for item in diff:
            assert item[1] is None or item[1] == " "

    def test_generate_diff_completely_different(self):
        """Test diff generation with completely different strings"""
        config = PromptExpanderPipe.get_default_config()
        pipe = PromptExpanderPipe(config)

        original = "abc"
        expanded = "xyz"

        diff = pipe._generate_diff(original, expanded)

        assert isinstance(diff, list)
        assert len(diff) > 0
        # Should have markers indicating changes
        has_additions = any(item[1] == "+" for item in diff)
        has_deletions = any(item[1] == "-" for item in diff)
        assert has_additions or has_deletions

    def test_generate_diff_with_spaces(self):
        """Test diff generation handles spaces correctly"""
        config = PromptExpanderPipe.get_default_config()
        pipe = PromptExpanderPipe(config)

        original = "a cat"
        expanded = "a beautiful cat"

        diff = pipe._generate_diff(original, expanded)

        # Should contain space characters as separate tokens
        space_items = [item for item in diff if item[0].isspace()]
        assert len(space_items) > 0

    def test_process_skip_negative_when_empty(self):
        """Test that negative prompt expansion is skipped when input is empty"""
        config = PromptExpanderPipe.get_default_config()
        config["llm_id"] = "test_llm"
        config["p_prompt_input"] = "a cat"
        config["n_prompt_input"] = ""  # Empty negative
        pipe = PromptExpanderPipe(config)

        mock_llm_service = AsyncMock()

        pipe_input = PipeInput(input={"LLM": mock_llm_service})
        generation_outputs = Mock()

        with patch('asyncio.run') as mock_run:
            # Only one call for positive prompt
            mock_run.side_effect = ["a beautiful cat"]

            result = pipe.process(pipe_input, generation_outputs)

        # Should only expand positive prompt
        assert mock_run.call_count == 1
        assert result.output["n_prompt"] == ""

    def test_extract_preserved_text_single_section(self):
        """Test extracting a single [[...]] section"""
        config = PromptExpanderPipe.get_default_config()
        pipe = PromptExpanderPipe(config)

        text = "[[masterpiece]] a beautiful sunset"
        preserved, cleaned = pipe._extract_preserved_text(text)

        assert preserved == "masterpiece"
        assert cleaned == "a beautiful sunset"

    def test_extract_preserved_text_multiple_sections(self):
        """Test extracting multiple [[...]] sections"""
        config = PromptExpanderPipe.get_default_config()
        pipe = PromptExpanderPipe(config)

        text = "[[masterpiece]] a cat [[best quality]]"
        preserved, cleaned = pipe._extract_preserved_text(text)

        assert preserved == "masterpiece best quality"
        assert cleaned == "a cat"

    def test_extract_preserved_text_no_sections(self):
        """Test text with no [[...]] sections"""
        config = PromptExpanderPipe.get_default_config()
        pipe = PromptExpanderPipe(config)

        text = "a beautiful sunset"
        preserved, cleaned = pipe._extract_preserved_text(text)

        assert preserved == ""
        assert cleaned == "a beautiful sunset"

    def test_extract_preserved_text_empty_brackets(self):
        """Test text with empty [[]] brackets"""
        config = PromptExpanderPipe.get_default_config()
        pipe = PromptExpanderPipe(config)

        text = "[[]] a sunset"
        preserved, cleaned = pipe._extract_preserved_text(text)

        assert preserved == ""
        assert cleaned == "a sunset"

    def test_extract_preserved_text_whitespace_handling(self):
        """Test whitespace handling in extraction"""
        config = PromptExpanderPipe.get_default_config()
        pipe = PromptExpanderPipe(config)

        text = "[[  masterpiece  ]] some   text  [[  quality  ]]"
        preserved, cleaned = pipe._extract_preserved_text(text)

        assert preserved == "masterpiece quality"
        assert cleaned == "some text"

    def test_extract_preserved_text_empty_input(self):
        """Test empty input"""
        config = PromptExpanderPipe.get_default_config()
        pipe = PromptExpanderPipe(config)

        preserved, cleaned = pipe._extract_preserved_text("")

        assert preserved == ""
        assert cleaned == ""

    @pytest.mark.asyncio
    async def test_expand_prompt_with_preserved_prefix(self):
        """Test that preserved prefix is prepended to LLM response"""
        config = PromptExpanderPipe.get_default_config()
        config["llm_id"] = "test_llm"
        pipe = PromptExpanderPipe(config)

        mock_response = Mock()
        mock_response.content = "expanded beautiful sunset"

        mock_llm_service = AsyncMock()
        mock_llm_service.generate_with_config_id.return_value = mock_response

        result = await pipe._expand_prompt_async(
            mock_llm_service,
            "Expand",
            "[[masterpiece, best quality]] a sunset"
        )

        # Should have sent only "a sunset" to LLM
        mock_llm_service.generate_with_config_id.assert_called_once()
        call_args = mock_llm_service.generate_with_config_id.call_args
        assert "a sunset" in call_args.kwargs["prompt"]
        assert "masterpiece" not in call_args.kwargs["prompt"]

        # Result should have preserved text prepended
        assert result == "masterpiece, best quality expanded beautiful sunset"

    @pytest.mark.asyncio
    async def test_expand_prompt_only_preserved_text(self):
        """Test when input contains only [[...]] and no other text"""
        config = PromptExpanderPipe.get_default_config()
        config["llm_id"] = "test_llm"
        pipe = PromptExpanderPipe(config)

        mock_llm_service = AsyncMock()

        result = await pipe._expand_prompt_async(
            mock_llm_service,
            "Expand",
            "[[masterpiece]]"
        )

        # Should not call LLM if there's nothing to expand
        mock_llm_service.generate_with_config_id.assert_not_called()
        # Should return just the preserved text
        assert result == "masterpiece"

    def test_process_with_preserved_prefix(self):
        """Test full process with preserved prefix in positive prompt"""
        config = PromptExpanderPipe.get_default_config()
        config["llm_id"] = "test_llm"
        config["p_prompt_input"] = "[[masterpiece, best quality]] a cat"
        config["n_prompt_input"] = ""
        pipe = PromptExpanderPipe(config)

        mock_llm_service = AsyncMock()
        pipe_input = PipeInput(input={"LLM": mock_llm_service})
        generation_outputs = Mock()

        with patch('asyncio.run') as mock_run:
            mock_run.return_value = "masterpiece, best quality a beautiful detailed cat"

            result = pipe.process(pipe_input, generation_outputs)

        assert isinstance(result, PipeOutput)
        # The preserved prefix should be in the final output
        assert "masterpiece, best quality" in result.output["p_prompt"]
