import asyncio
import pytest
from unittest.mock import Mock, patch

from src.features.generation.generation import (
    GenerationManager, deep_update, validate_pipe_configuration
)
from src.pipelines.outputs import (
    ErrorGenerationOutput,
    ProgressGenerationOutput,
    ImageGenerationOutput
)
from src.pipelines.contracts import IOType, PipeConfigSpec, PipeInputSpec, PipeOutputSpec
from src.pipelines.outputs import Icon
from src.pipelines.contracts import BasePipe, PipeInput, PipeOutput


class MockPipe(BasePipe):
    def __init__(self, config=None):
        super().__init__(config or {})
        self.process_call_count = 0

    @property
    def name(self) -> str:
        return "mock_pipe"

    @property
    def description(self) -> str:
        return "Mock pipe for testing"

    @classmethod
    def inputs(cls):
        return [
            PipeInputSpec(name="input_param", io_type=IOType.TEXT, required=True),
            PipeInputSpec(name="optional_param", io_type=IOType.INT, required=False)
        ]

    @classmethod
    def outputs(cls):
        return [
            PipeOutputSpec(name="output_param", io_type=IOType.TEXT)
        ]

    @classmethod
    def configuration(cls):
        return [
            PipeConfigSpec(
                name="param1",
                param_type=str,
                required=True,
                default="default_value"
            ),
            PipeConfigSpec(
                name="param2",
                param_type=int,
                required=False,
                min_value=1,
                max_value=100,
                default=50
            ),
            PipeConfigSpec(
                name="param3",
                param_type=str,
                required=False,
                choices=["option1", "option2", "option3"],
                default="option1"
            )
        ]

    @classmethod
    def get_default_config(cls):
        return {"param1": "default_value", "param2": 50}

    def process(self, pipe_input: PipeInput, generation_outputs: callable, is_cancelled=None):
        self.process_call_count += 1

        # Check cancellation if provided
        if is_cancelled and is_cancelled():
            return PipeOutput(output={"output_param": "cancelled"})

        # Simulate some processing
        generation_outputs(ProgressGenerationOutput(
            state="Processing mock pipe",
            icon=Icon("gear", "spin")
        ))

        # Return mock output
        return PipeOutput(output={"output_param": "mock_result"})


class TestDeepUpdate:
    def test_simple_update(self):
        original = {"a": 1, "b": 2}
        updates = {"b": 3, "c": 4}
        result = deep_update(original, updates)

        assert result == {"a": 1, "b": 3, "c": 4}
        assert result is original  # Should modify in place

    def test_nested_update(self):
        original = {"a": {"x": 1, "y": 2}, "b": 3}
        updates = {"a": {"y": 5, "z": 6}, "c": 7}
        result = deep_update(original, updates)

        expected = {"a": {"x": 1, "y": 5, "z": 6}, "b": 3, "c": 7}
        assert result == expected

    def test_deep_nested_update(self):
        original = {"level1": {"level2": {"level3": {"value": 1}}}}
        updates = {"level1": {"level2": {"level3": {"value": 2, "new": 3}}}}
        result = deep_update(original, updates)

        expected = {"level1": {"level2": {"level3": {"value": 2, "new": 3}}}}
        assert result == expected

    def test_overwrite_non_dict(self):
        original = {"a": {"x": 1}, "b": "string"}
        updates = {"a": "new_string", "b": {"y": 2}}
        result = deep_update(original, updates)

        expected = {"a": "new_string", "b": {"y": 2}}
        assert result == expected

    def test_empty_updates(self):
        original = {"a": 1, "b": 2}
        updates = {}
        result = deep_update(original, updates)

        assert result == {"a": 1, "b": 2}

    def test_empty_original(self):
        original = {}
        updates = {"a": 1, "b": {"x": 2}}
        result = deep_update(original, updates)

        assert result == {"a": 1, "b": {"x": 2}}


class TestValidatePipeConfiguration:
    def test_valid_configuration(self):
        config = {
            "param1": "test_value",
            "param2": 75,
            "param3": "option2"
        }
        result = validate_pipe_configuration(MockPipe, config)

        assert result["param1"] == "test_value"
        assert result["param2"] == 75
        assert result["param3"] == "option2"

    def test_blank_string_for_optional_numeric_uses_default(self):
        class OptionalIntPipe(MockPipe):
            @classmethod
            def configuration(cls):
                return [
                    PipeConfigSpec(
                        name="switch_step",
                        param_type=int,
                        required=False,
                        default=None,
                    )
                ]

        result = validate_pipe_configuration(OptionalIntPipe, {"switch_step": ""})
        assert result["switch_step"] is None

    def test_blank_string_for_optional_str_stays_blank(self):
        class OptionalStrPipe(MockPipe):
            @classmethod
            def configuration(cls):
                return [
                    PipeConfigSpec(
                        name="note",
                        param_type=str,
                        required=False,
                        default="x",
                    )
                ]

        result = validate_pipe_configuration(OptionalStrPipe, {"note": ""})
        assert result["note"] == ""

    def test_missing_required_with_default(self):
        config = {"param2": 30}  # Missing param1 but it has default
        result = validate_pipe_configuration(MockPipe, config)

        assert result["param1"] == "default_value"  # Should use default
        assert result["param2"] == 30

    def test_missing_required_without_default(self):
        # Create a pipe with required param without default
        class StrictPipe(MockPipe):
            @classmethod
            def configuration(cls):
                return [
                    PipeConfigSpec(
                        name="required_param",
                        param_type=str,
                        required=True,
                        default=None  # No default value
                    )
                ]

        config = {}
        with pytest.raises(ValueError, match="Required parameter 'required_param' is missing"):
            validate_pipe_configuration(StrictPipe, config)

    def test_type_conversion(self):
        config = {
            "param1": "test_value",
            "param2": "80",  # String that should convert to int
            "param3": "option1"
        }
        result = validate_pipe_configuration(MockPipe, config)

        assert result["param2"] == 80
        assert isinstance(result["param2"], int)

    def test_boolean_conversion(self):
        class BooleanPipe(MockPipe):
            @classmethod
            def configuration(cls):
                return [
                    PipeConfigSpec(
                        name="bool_param",
                        param_type=bool,
                        required=False,
                        default=False
                    )
                ]

        # Test string boolean conversion
        test_cases = [
            ("true", True),
            ("True", True),
            ("1", True),
            ("yes", True),
            ("on", True),
            ("false", False),
            ("False", False),
            ("0", False),
            ("no", False),
            ("off", False)
        ]

        for string_val, expected in test_cases:
            config = {"bool_param": string_val}
            result = validate_pipe_configuration(BooleanPipe, config)
            assert result["bool_param"] == expected

    def test_invalid_type_conversion(self):
        config = {
            "param1": "test_value",
            "param2": "not_a_number",  # Cannot convert to int
            "param3": "option1"
        }

        with pytest.raises(ValueError, match="must be of type int"):
            validate_pipe_configuration(MockPipe, config)

    def test_choices_validation_valid(self):
        config = {
            "param1": "test_value",
            "param2": 50,
            "param3": "option2"  # Valid choice
        }
        result = validate_pipe_configuration(MockPipe, config)
        assert result["param3"] == "option2"

    def test_choices_validation_invalid(self):
        config = {
            "param1": "test_value",
            "param2": 50,
            "param3": "invalid_option"  # Invalid choice
        }

        with pytest.raises(ValueError, match="must be one of"):
            validate_pipe_configuration(MockPipe, config)

    def test_range_validation_valid(self):
        config = {
            "param1": "test_value",
            "param2": 75,  # Within range 1-100
            "param3": "option1"
        }
        result = validate_pipe_configuration(MockPipe, config)
        assert result["param2"] == 75

    def test_range_validation_too_low(self):
        config = {
            "param1": "test_value",
            "param2": 0,  # Below minimum of 1
            "param3": "option1"
        }

        with pytest.raises(ValueError, match="must be >= 1"):
            validate_pipe_configuration(MockPipe, config)

    def test_range_validation_too_high(self):
        config = {
            "param1": "test_value",
            "param2": 150,  # Above maximum of 100
            "param3": "option1"
        }

        with pytest.raises(ValueError, match="must be <= 100"):
            validate_pipe_configuration(MockPipe, config)

    def test_unknown_parameters_preserved(self):
        """Unknown parameters (like backend_config injected by backends) should be preserved."""
        config = {
            "param1": "test_value",
            "param2": 50,
            "param3": "option1",
            "unknown_param": "value",  # Unknown parameter
            "backend_config": {"host": "192.168.1.1", "port": 8188}  # Injected by backend
        }

        with patch('src.features.generation.generation.logger') as mock_logger:
            result = validate_pipe_configuration(MockPipe, config)
            # Unknown parameters should be preserved, not stripped
            assert "unknown_param" in result
            assert result["unknown_param"] == "value"
            assert "backend_config" in result
            assert result["backend_config"]["host"] == "192.168.1.1"
            # Should log debug for preserved parameters
            assert mock_logger.debug.call_count >= 2  # At least 2 unknown params logged

    def test_pipe_without_configuration_method(self):
        class SimplePipe(MockPipe):
            @classmethod
            def configuration(cls):
                raise AttributeError("No configuration method")

        config = {"any_param": "value"}

        with patch('src.features.generation.generation.logger') as mock_logger:
            result = validate_pipe_configuration(SimplePipe, config)
            # Should return original config and log warning
            assert result == config
            mock_logger.warning.assert_called_once()

    def test_validate_config_default_is_a_noop(self):
        """BasePipe.validate_config defaults to a no-op -- MockPipe doesn't
        override it, so per-parameter validation is unaffected."""
        config = {"param1": "test_value", "param2": 75, "param3": "option2"}
        result = validate_pipe_configuration(MockPipe, config)
        assert result["param2"] == 75

    def test_validate_config_hook_raises_value_error_is_propagated(self):
        """A pipe's cross-field validate_config can reject an otherwise
        individually-valid combination of parameters -- the
        ValueError surfaces exactly like any per-parameter check above."""
        class CrossFieldPipe(MockPipe):
            @classmethod
            def validate_config(cls, config):
                if config.get("param2") == 42 and config.get("param3") == "option3":
                    raise ValueError("param2=42 with param3='option3' is a degenerate combination")

        config = {"param1": "test_value", "param2": 42, "param3": "option3"}
        with pytest.raises(ValueError, match="degenerate combination"):
            validate_pipe_configuration(CrossFieldPipe, config)

    def test_validate_config_hook_allows_non_degenerate_combination(self):
        class CrossFieldPipe(MockPipe):
            @classmethod
            def validate_config(cls, config):
                if config.get("param2") == 42 and config.get("param3") == "option3":
                    raise ValueError("param2=42 with param3='option3' is a degenerate combination")

        config = {"param1": "test_value", "param2": 42, "param3": "option2"}
        result = validate_pipe_configuration(CrossFieldPipe, config)
        assert result["param2"] == 42
        assert result["param3"] == "option2"

    def test_validate_config_hook_unexpected_exception_is_logged_not_raised(self):
        """A bug in the hook itself (not a config problem) shouldn't turn a
        valid configuration into a hard failure -- it's logged and swallowed."""
        class BuggyHookPipe(MockPipe):
            @classmethod
            def validate_config(cls, config):
                raise RuntimeError("bug in the hook, not the config")

        config = {"param1": "test_value", "param2": 50, "param3": "option1"}
        with patch('src.features.generation.generation.logger') as mock_logger:
            result = validate_pipe_configuration(BuggyHookPipe, config)
            assert result["param2"] == 50
            mock_logger.warning.assert_called_once()


class TestGenerationManager:
    @pytest.fixture(autouse=True)
    def setup_manager(self, mock_settings_manager):
        """Set up GenerationManager with mocked dependencies"""
        self.mock_gpu = Mock()
        self.mock_model_manager = Mock()
        self.mock_pipe_catalog = Mock()
        self.mock_settings_manager = mock_settings_manager
        self.mock_system_monitor = Mock()
        self.mock_memory_manager = Mock()
        self.mock_llm_service = Mock()
        self.mock_models = Mock()

        self.manager = GenerationManager(
            gpu=self.mock_gpu,
            model_manager=self.mock_model_manager,
            pipe_catalog=self.mock_pipe_catalog,
            settings_manager=self.mock_settings_manager,
            system_monitor=self.mock_system_monitor,
            memory_manager=self.mock_memory_manager,
            llm_service=self.mock_llm_service,
            models=self.mock_models,
        )

    def test_cancel_is_a_no_op_when_nothing_is_running(self):
        assert self.manager._cancelled is False

        assert self.manager.cancel("test_gen_id") is False

        assert self.manager._cancelled is False

    def test_cancel_ignores_an_id_that_is_not_the_running_generation(self):
        """
        The isolation guarantee: one tab cancelling its own generation must not
        abort a different generation that happens to share this manager.
        """
        self.manager._running_generation_id = "generation_a"

        assert self.manager.cancel("generation_b") is False
        assert self.manager._cancelled is False

        assert self.manager.cancel("generation_a") is True
        assert self.manager._cancelled is True

    def test_running_generation_id_is_exposed_while_running(self):
        assert self.manager.running_generation_id is None
        self.manager._running_generation_id = "generation_a"
        assert self.manager.running_generation_id == "generation_a"

    def test_generate_refuses_to_run_two_generations_concurrently(self):
        """The scheduler owns the one-at-a-time slot; re-entering must be loud, not silent."""
        self.manager._running_generation_id = "generation_a"

        with pytest.raises(RuntimeError, match="already running generation_a"):
            self.manager.generate([], Mock(), "generation_b")

        # The in-flight run must be left untouched by the rejected caller.
        assert self.manager._running_generation_id == "generation_a"
        assert self.manager._cancelled is False

    def test_validate_pipeline_success(self):
        # Setup mock pipe classes
        # First pipe - no inputs required, produces outputs
        pipe1_class = Mock()
        pipe1_class.inputs.return_value = []  # No required inputs
        # Create output specs with explicit attributes
        output1_spec = Mock(name="output1", io_type=IOType.TEXT, is_array=False)
        output1_spec.name = "output1"
        output1_spec.io_type = IOType.TEXT
        output1_spec.is_array = False

        output2_spec = Mock(name="output2", io_type=IOType.INT, is_array=False)
        output2_spec.name = "output2"
        output2_spec.io_type = IOType.INT
        output2_spec.is_array = False

        pipe1_class.outputs.return_value = [output1_spec, output2_spec]

        # Second pipe - requires input from first pipe
        pipe2_class = Mock()
        # Create input spec with explicit attributes
        input1_spec = Mock(name="input1", required=True, io_type=IOType.TEXT, is_array=False)
        input1_spec.name = "input1"
        input1_spec.required = True
        input1_spec.io_type = IOType.TEXT
        input1_spec.is_array = False

        pipe2_class.inputs.return_value = [input1_spec]
        pipe2_class.outputs.return_value = []

        def get_pipe_side_effect(name):
            if name == "pipe1":
                return pipe1_class
            elif name == "pipe2":
                return pipe2_class

        self.mock_pipe_catalog.get_pipe.side_effect = get_pipe_side_effect

        pipes = [
            {
                "name": "pipe1",
                "enabled": True,
                "input": [],  # No inputs required for first pipe
                "config": {}
            },
            {
                "name": "pipe2",
                "enabled": True,
                "input": [["input1", "pipe1", "output1"]],  # Valid input from pipe1
                "config": {}
            }
        ]

        # Should not raise exception
        self.manager.validate_pipeline(pipes)

    def test_validate_pipeline_missing_input(self):
        # Setup mock pipe classes
        mock_pipe_class = Mock()
        # Create mock input spec with string attributes
        input_spec = Mock(name="required_input", required=True, io_type=IOType.TEXT, is_array=False)
        # Ensure the mock's name attribute returns the actual string
        input_spec.name = "required_input"
        input_spec.io_type = IOType.TEXT
        mock_pipe_class.inputs.return_value = [input_spec]
        mock_pipe_class.outputs.return_value = []

        self.mock_pipe_catalog.get_pipe.return_value = mock_pipe_class

        pipes = [
            {
                "name": "pipe1",
                "enabled": True,
                "input": [],  # Missing required input
                "config": {}
            }
        ]

        with pytest.raises(ValueError, match="requires input.*TEXT.*but it's not provided in the pipeline"):
            self.manager.validate_pipeline(pipes)

    def test_validate_pipeline_type_mismatch(self):
        # Setup provider pipe
        provider_pipe_class = Mock()
        provider_pipe_class.inputs.return_value = []
        # Create output spec with explicit attributes
        output_spec = Mock(name="output1", io_type=IOType.INT, is_array=False)
        output_spec.name = "output1"
        output_spec.io_type = IOType.INT
        output_spec.is_array = False
        provider_pipe_class.outputs.return_value = [output_spec]

        # Setup consumer pipe
        consumer_pipe_class = Mock()
        # Create input spec with explicit attributes
        input_spec = Mock(name="input1", required=True, io_type=IOType.TEXT, is_array=False)
        input_spec.name = "input1"
        input_spec.required = True
        input_spec.io_type = IOType.TEXT
        input_spec.is_array = False
        consumer_pipe_class.inputs.return_value = [input_spec]
        consumer_pipe_class.outputs.return_value = []

        def get_pipe_side_effect(name):
            if name == "provider":
                return provider_pipe_class
            elif name == "consumer":
                return consumer_pipe_class

        self.mock_pipe_catalog.get_pipe.side_effect = get_pipe_side_effect

        pipes = [
            {
                "name": "provider",
                "enabled": True,
                "input": [],
                "config": {}
            },
            {
                "name": "consumer",
                "enabled": True,
                "input": [["input1", "provider", "output1"]],  # Type mismatch
                "config": {}
            }
        ]

        # The actual error message uses uppercase IOType.value strings
        with pytest.raises(ValueError, match="expects TEXT.*produces INT"):
            self.manager.validate_pipeline(pipes)

    def test_hijack_pipe_generation_output(self):
        mock_output = ProgressGenerationOutput(state="Test state")
        mock_pipe = Mock()
        mock_pipe.name = "generator"
        mock_pipe.config = {}
        mock_pipe.display_title = None
        mock_callback = Mock()
        generation_id = "test_gen_id"
        pipe_id = 2

        self.manager.hijack_pipe_generation_output(
            mock_callback, mock_output, mock_pipe, generation_id, pipe_id
        )

        # Check that pipe tracking info was set
        assert mock_output.pipe_id == pipe_id
        assert mock_output.pipe_name == "generator"
        # The status-line title is the human phase name, not the raw pipe id -
        # the raw id stays available on pipe_name (asserted above) for debugging.
        assert mock_output.title == "<<PIPE:Generating>>"

        # Check that callback was called (application layer will handle output processing)
        mock_callback.assert_called_once_with(mock_output)

    def test_hijack_pipe_generation_output_falls_back_to_cleaned_name_for_unknown_family(self):
        mock_output = ProgressGenerationOutput(state="Test state")
        mock_pipe = Mock()
        mock_pipe.name = "third_party_step"
        mock_pipe.config = {}
        mock_pipe.display_title = None

        self.manager.hijack_pipe_generation_output(
            Mock(), mock_output, mock_pipe, "test_gen_id", 0
        )

        assert mock_output.title == "<<PIPE:Third party step>>"

    def test_hijack_pipe_generation_output_uses_pipe_display_title(self):
        mock_output = ProgressGenerationOutput(state="Test state")
        mock_pipe = Mock()
        mock_pipe.name = "detailer"
        mock_pipe.config = {}
        mock_pipe.display_title = "Refining faces and hands"

        self.manager.hijack_pipe_generation_output(
            Mock(), mock_output, mock_pipe, "test_gen_id", 0
        )

        assert mock_output.title == "<<PIPE:Refining faces and hands>>"

    def test_hijack_pipe_generation_output_config_override_wins_over_pipe_display_title(self):
        mock_output = ProgressGenerationOutput(state="Test state")
        mock_pipe = Mock()
        mock_pipe.name = "generator"
        mock_pipe.config = {"display_title": "Custom step name"}
        mock_pipe.display_title = None

        self.manager.hijack_pipe_generation_output(
            Mock(), mock_output, mock_pipe, "test_gen_id", 0
        )

        assert mock_output.title == "<<PIPE:Custom step name>>"

    def test_generate_success(self):
        # Setup mock pipe
        mock_pipe_instance = MockPipe()
        mock_pipe_class = Mock(return_value=mock_pipe_instance)
        mock_pipe_class.get_default_config.return_value = {"param1": "default"}
        mock_pipe_class.inputs = MockPipe.inputs  # Use actual inputs() method
        mock_pipe_class.name = MockPipe.name.fget(mock_pipe_instance)  # Use actual name

        self.mock_pipe_catalog.get_pipe.return_value = mock_pipe_class

        pipes = [
            {
                "name": "test_pipe",
                "enabled": True,
                "input": [],
                "cache": [],
                "config": {"param1": "custom_value"}
            }
        ]

        mock_callback = Mock()

        with patch.object(self.manager, 'validate_pipeline'), \
             patch.object(self.manager, 'hijack_pipe_generation_output') as mock_hijack, \
             patch('src.features.generation.generation.validate_pipe_configuration') as mock_validate:

            mock_validate.return_value = {"param1": "custom_value"}

            self.manager.generate(pipes, mock_callback, "test_gen_id")

            # Verify pipe was instantiated with correct config
            mock_pipe_class.assert_called_once_with({"param1": "custom_value"})

            # Verify pipe process was called
            assert mock_pipe_instance.process_call_count == 1

            # Verify completion progress was sent
            assert mock_callback.call_count >= 2  # At least progress + timer outputs

    def test_generate_with_cancellation(self):
        # Setup mock pipe that will cancel during processing
        mock_pipe_instance = MockPipe()

        # Track if pipe was instantiated
        instantiation_count = 0
        def create_pipe(*args, **kwargs):
            nonlocal instantiation_count
            instantiation_count += 1
            return mock_pipe_instance

        mock_pipe_class = Mock(side_effect=create_pipe)
        mock_pipe_class.get_default_config.return_value = {}
        mock_pipe_class.inputs = MockPipe.inputs  # Use actual inputs() method
        mock_pipe_class.name = MockPipe.name.fget(mock_pipe_instance)  # Use actual name

        self.mock_pipe_catalog.get_pipe.return_value = mock_pipe_class

        pipes = [
            {
                "name": "test_pipe",
                "enabled": True,
                "input": [],
                "cache": [],
                "config": {}
            }
        ]

        mock_callback = Mock()

        # Since generate() resets _cancelled to False at line 338, we need to test
        # cancellation during generation, not before
        original_process = mock_pipe_instance.process
        def process_with_cancel(*args, **kwargs):
            # Cancel during pipe processing
            self.manager.cancel("test_gen_id")
            return original_process(*args, **kwargs)

        mock_pipe_instance.process = process_with_cancel

        with patch.object(self.manager, 'validate_pipeline'), \
             patch('src.features.generation.generation.validate_pipe_configuration') as mock_validate:

            mock_validate.return_value = {}

            self.manager.generate(pipes, mock_callback, "test_gen_id")

            # The pipe will be instantiated and process will be called
            assert instantiation_count == 1
            assert mock_pipe_instance.process_call_count == 1

            # After cancellation, no completion message should be sent
            # Check that we got a cancellation message after the pipe ran
            cancellation_found = False
            for call in mock_callback.call_args_list:
                if len(call[0]) > 0 and isinstance(call[0][0], ProgressGenerationOutput):
                    if "cancelled" in call[0][0].state.lower():
                        cancellation_found = True
                        break

            assert cancellation_found, "Cancellation message should be sent"

    def test_generate_handles_sampling_cancelled_from_pipe(self):
        """A pipe raising SamplingCancelled (the native sampler's cancel
        signal) must be treated like the between-pipe cancellation check: no
        re-raise, no ErrorGenerationOutput, and the same cancelled-shape
        progress output."""
        from src.platform.runtime.native.errors import SamplingCancelled

        mock_pipe_instance = MockPipe()

        def raise_cancelled(*args, **kwargs):
            raise SamplingCancelled(step_index=3)

        mock_pipe_instance.process = raise_cancelled

        mock_pipe_class = Mock(return_value=mock_pipe_instance)
        mock_pipe_class.get_default_config.return_value = {}
        mock_pipe_class.inputs = MockPipe.inputs  # Use actual inputs() method
        mock_pipe_class.name = MockPipe.name.fget(mock_pipe_instance)  # Use actual name

        self.mock_pipe_catalog.get_pipe.return_value = mock_pipe_class

        pipes = [
            {
                "name": "test_pipe",
                "enabled": True,
                "input": [],
                "cache": [],
                "config": {}
            }
        ]

        mock_callback = Mock()

        with patch.object(self.manager, 'validate_pipeline'), \
             patch('src.features.generation.generation.validate_pipe_configuration') as mock_validate:

            mock_validate.return_value = {}

            # Must not raise - SamplingCancelled is the graceful cancel outcome.
            self.manager.generate(pipes, mock_callback, "test_gen_id")

        cancellation_found = False
        error_found = False
        for call in mock_callback.call_args_list:
            output = call[0][0]
            if isinstance(output, ProgressGenerationOutput) and "cancelled" in output.state.lower():
                cancellation_found = True
            if isinstance(output, ErrorGenerationOutput):
                error_found = True

        assert cancellation_found, "Cancellation message should be sent"
        assert not error_found, "SamplingCancelled must not be reported as a generation error"

    def test_generate_with_pipe_inputs(self):
        # Setup first pipe that produces output
        first_pipe = MockPipe()

        # Setup second pipe that consumes first pipe's output
        second_pipe = MockPipe()

        def get_pipe_side_effect(name):
            if name == "first_pipe":
                mock_class = Mock(return_value=first_pipe, get_default_config=Mock(return_value={}))
                mock_class.inputs = MockPipe.inputs  # Use actual inputs() method
                mock_class.name = MockPipe.name.fget(first_pipe)  # Use actual name
                return mock_class
            elif name == "second_pipe":
                mock_class = Mock(return_value=second_pipe, get_default_config=Mock(return_value={}))
                mock_class.inputs = MockPipe.inputs  # Use actual inputs() method
                mock_class.name = MockPipe.name.fget(second_pipe)  # Use actual name
                return mock_class

        self.mock_pipe_catalog.get_pipe.side_effect = get_pipe_side_effect

        pipes = [
            {
                "name": "first_pipe",
                "enabled": True,
                "input": [],
                "cache": [],
                "config": {}
            },
            {
                "name": "second_pipe",
                "enabled": True,
                "input": [["input_param", "first_pipe", "output_param"]],
                "cache": [],
                "config": {}
            }
        ]

        mock_callback = Mock()

        with patch.object(self.manager, 'validate_pipeline'), \
             patch.object(self.manager, 'hijack_pipe_generation_output'), \
             patch('src.features.generation.generation.validate_pipe_configuration') as mock_validate:

            mock_validate.return_value = {}

            self.manager.generate(pipes, mock_callback, "test_gen_id")

            # Verify both pipes were processed
            assert first_pipe.process_call_count == 1
            assert second_pipe.process_call_count == 1

    def test_generate_exception_handling(self):
        # Setup mock pipe that raises exception
        mock_pipe_instance = Mock()
        mock_pipe_instance.process.side_effect = RuntimeError("Pipe error")
        mock_pipe_class = Mock(return_value=mock_pipe_instance)
        mock_pipe_class.get_default_config.return_value = {}
        mock_pipe_class.inputs = Mock(return_value=[])  # no SERVICE inputs to inject

        self.mock_pipe_catalog.get_pipe.return_value = mock_pipe_class

        pipes = [
            {
                "name": "error_pipe",
                "enabled": True,
                "input": [],
                "cache": [],
                "config": {}
            }
        ]

        mock_callback = Mock()

        with patch.object(self.manager, 'validate_pipeline'), \
             patch('src.features.generation.generation.validate_pipe_configuration') as mock_validate, \
             patch('src.features.generation.generation.logger') as mock_logger:

            mock_validate.return_value = {}

            # Exceptions now propagate (so the backend wrapper can transition
            # the tracked status to FAILED) after emitting an
            # ErrorGenerationOutput and logging.
            with pytest.raises(RuntimeError, match="Pipe error"):
                self.manager.generate(pipes, mock_callback, "test_gen_id")

            mock_logger.error.assert_called()
            # Both the error summary and the full traceback are logged at
            # error level; check across all calls.
            error_messages = [c[0][0] for c in mock_logger.error.call_args_list]
            assert any("Generation error" in m for m in error_messages)
            assert any("Traceback" in m for m in error_messages)

            error_outputs = [
                call.args[0] for call in mock_callback.call_args_list
                if isinstance(call.args[0], ErrorGenerationOutput)
            ]
            assert len(error_outputs) == 1
            assert error_outputs[0].error == "Something went wrong during generation."
            assert "Pipe error" in error_outputs[0].detail

            # _cancelled must still be reset in the finally block even on error
            assert self.manager._cancelled is False

    def test_profile_flushes_with_has_profile_true_when_pipe_raises(self, tmp_path, monkeypatch):
        """A failed generation must still leave behind a readable resource
        profile: ``profiler.stop()`` runs in ``generate()``'s ``finally`` block
        (src/features/generation/generation.py), so a crash mid-pipe still
        flushes/closes the writer and the ``generation.end`` mark lands."""
        from src.platform.observability.profiling.profiler import (
            GenerationProfiler, reset_enabled_cache,
        )
        from src.platform.observability.profiling import report as profile_report
        from src.features.generation import profile_paths

        monkeypatch.setenv("POTIONUI_PROFILE", "1")
        reset_enabled_cache()

        self.mock_settings_manager.get_file_storage_directory.return_value = str(tmp_path)
        fresh_profiler = GenerationProfiler()

        mock_pipe_instance = Mock()
        mock_pipe_instance.process.side_effect = RuntimeError("Pipe error")
        mock_pipe_class = Mock(return_value=mock_pipe_instance)
        mock_pipe_class.get_default_config.return_value = {}
        mock_pipe_class.inputs = Mock(return_value=[])

        self.mock_pipe_catalog.get_pipe.return_value = mock_pipe_class

        pipes = [
            {
                "name": "error_pipe",
                "enabled": True,
                "input": [],
                "cache": [],
                "config": {},
            }
        ]

        generation_id = "gen-crash-1"

        try:
            with patch.object(self.manager, 'validate_pipeline'), \
                 patch('src.features.generation.generation.validate_pipe_configuration', return_value={}), \
                 patch('src.features.generation.generation.get_profiler', return_value=fresh_profiler):
                with pytest.raises(RuntimeError, match="Pipe error"):
                    self.manager.generate(pipes, Mock(), generation_id)

            # stop() must have run: the writer is flushed and closed even
            # though generate() propagated the exception.
            assert fresh_profiler._fh is None

            jsonl_path = profile_paths.profile_jsonl_path(tmp_path, generation_id)
            assert jsonl_path is not None and jsonl_path.is_file()
            assert profile_paths.has_profile(tmp_path, generation_id) is True

            rows = profile_report.load_rows(jsonl_path)
            events = {r.get("event") for r in rows if r.get("kind") == "event"}
            assert "generation.start" in events
            assert "generation.end" in events
        finally:
            monkeypatch.delenv("POTIONUI_PROFILE", raising=False)
            reset_enabled_cache()

    def test_generate_injects_models_service(self):
        # The old pipe-level `cache:` mechanism (GenerationManager.cache) has
        # been replaced by the MODELS service (ModelLifecycleManager),
        # injected like GPU/SYSTEM/MEMORY/LLM for pipes declaring a MODELS
        # SERVICE input.
        captured_inputs = []

        class ModelsAwarePipe(MockPipe):
            @classmethod
            def inputs(cls):
                return [
                    PipeInputSpec(name="MODELS", io_type=IOType.SERVICE, required=False),
                ]

            def process(self, pipe_input: PipeInput, generation_outputs: callable, is_cancelled=None):
                captured_inputs.append(pipe_input.input.copy())
                return PipeOutput(output={})

        mock_pipe_instance = ModelsAwarePipe()
        mock_pipe_class = Mock(return_value=mock_pipe_instance)
        mock_pipe_class.get_default_config.return_value = {}
        mock_pipe_class.inputs = ModelsAwarePipe.inputs
        mock_pipe_class.name = "test_pipe"

        self.mock_pipe_catalog.get_pipe.return_value = mock_pipe_class

        pipes = [
            {
                "name": "test_pipe",
                "enabled": True,
                "input": [],
                "cache": [],
                "config": {}
            }
        ]

        mock_callback = Mock()

        with patch.object(self.manager, 'validate_pipeline'), \
             patch.object(self.manager, 'hijack_pipe_generation_output'), \
             patch('src.features.generation.generation.validate_pipe_configuration') as mock_validate:

            mock_validate.return_value = {}

            self.manager.generate(pipes, mock_callback, "test_gen_id")

            assert len(captured_inputs) == 1
            assert captured_inputs[0]["MODELS"] is self.mock_models

    def test_generate_disabled_pipes_skipped(self):
        # Setup mock pipes
        enabled_pipe = MockPipe()
        disabled_pipe = MockPipe()

        def get_pipe_side_effect(name):
            if name == "enabled_pipe":
                mock_class = Mock(return_value=enabled_pipe, get_default_config=Mock(return_value={}))
                mock_class.inputs = MockPipe.inputs  # Use actual inputs() method
                mock_class.name = MockPipe.name.fget(enabled_pipe)  # Use actual name
                return mock_class
            elif name == "disabled_pipe":
                mock_class = Mock(return_value=disabled_pipe, get_default_config=Mock(return_value={}))
                mock_class.inputs = MockPipe.inputs  # Use actual inputs() method
                mock_class.name = MockPipe.name.fget(disabled_pipe)  # Use actual name
                return mock_class

        self.mock_pipe_catalog.get_pipe.side_effect = get_pipe_side_effect

        pipes = [
            {
                "name": "enabled_pipe",
                "enabled": True,
                "input": [],
                "cache": [],
                "config": {}
            },
            {
                "name": "disabled_pipe",
                "enabled": False,  # Disabled
                "input": [],
                "cache": [],
                "config": {}
            }
        ]

        mock_callback = Mock()

        with patch.object(self.manager, 'validate_pipeline'), \
             patch.object(self.manager, 'hijack_pipe_generation_output'), \
             patch('src.features.generation.generation.validate_pipe_configuration') as mock_validate:

            mock_validate.return_value = {}

            self.manager.generate(pipes, mock_callback, "test_gen_id")

            # Verify only enabled pipe was processed
            assert enabled_pipe.process_call_count == 1
            assert disabled_pipe.process_call_count == 0


class TestResourceStatsCapture:
    """`GenerationManager.pop_resource_stats()`: the
    always-on, profiling-independent cold/warm signal, sourced from the REAL
    `ModelLifecycleManager`'s generation lease (not a bare Mock -- a Mock's
    `.get()`/comparison auto-vivification would mask a real wiring bug here).
    """

    @pytest.fixture(autouse=True)
    def setup_manager(self, mock_settings_manager, tmp_path):
        from src.platform.runtime.model_lifecycle.manager import ModelLifecycleManager

        mock_settings_manager.get_file_storage_directory.return_value = str(tmp_path)
        fake_gpu = Mock()
        fake_gpu.get_vram_budget.return_value = 10.0
        self.models = ModelLifecycleManager(gpu_manager=fake_gpu, settings_manager=None)
        self.manager = GenerationManager(
            gpu=Mock(), model_manager=Mock(), pipe_catalog=Mock(),
            settings_manager=mock_settings_manager, system_monitor=Mock(),
            memory_manager=Mock(), llm_service=Mock(), models=self.models,
        )

    def _run_with_pipe(self, generation_id, during_process):
        """Runs one generation whose single pipe calls `during_process(self.models)`."""
        models_ref = self.models

        class _DuringProcessPipe(MockPipe):
            def process(pipe_self, pipe_input, generation_outputs, is_cancelled=None):
                during_process(models_ref)
                return super().process(pipe_input, generation_outputs, is_cancelled)

        mock_pipe_instance = _DuringProcessPipe()
        mock_pipe_class = Mock(return_value=mock_pipe_instance)
        mock_pipe_class.get_default_config.return_value = {"param1": "default"}
        mock_pipe_class.inputs = MockPipe.inputs
        mock_pipe_class.name = MockPipe.name.fget(mock_pipe_instance)
        self.manager.pipe_catalog.get_pipe.return_value = mock_pipe_class

        pipes = [{"name": "test_pipe", "enabled": True, "input": [], "cache": [], "config": {}}]
        with patch.object(self.manager, 'validate_pipeline'), \
             patch.object(self.manager, 'hijack_pipe_generation_output'), \
             patch('src.features.generation.generation.validate_pipe_configuration', return_value={}):
            self.manager.generate(pipes, Mock(), generation_id)

    def test_cold_start_recorded_on_cache_miss(self):
        self._run_with_pipe("gen-cold", lambda models: models.acquire("dit", "fp", lambda: object()))

        stats = self.manager.pop_resource_stats("gen-cold")

        assert stats["cold_start"] is True
        assert stats["model_load_ms"] is not None
        assert stats["model_load_ms"] >= 0

    def test_warm_start_recorded_on_cache_hit(self):
        self.models.acquire("dit", "fp", lambda: object())  # pre-warm outside any lease

        self._run_with_pipe("gen-warm", lambda models: models.acquire("dit", "fp", lambda: object()))

        stats = self.manager.pop_resource_stats("gen-warm")
        assert stats["cold_start"] is False

    def test_no_model_acquire_leaves_cold_start_unknown(self):
        """A pipeline that never touches the model cache (e.g. a pure
        postprocess pipe) must not be misreported as "warm" -- unknown stays
        unknown."""
        self._run_with_pipe("gen-none", lambda models: None)

        stats = self.manager.pop_resource_stats("gen-none")
        assert stats["cold_start"] is None
        assert stats["model_load_ms"] is None

    def test_pop_resource_stats_is_read_once(self):
        self._run_with_pipe("gen-once", lambda models: None)

        assert self.manager.pop_resource_stats("gen-once") is not None
        assert self.manager.pop_resource_stats("gen-once") is None

    def test_unknown_generation_id_returns_none(self):
        assert self.manager.pop_resource_stats("never-ran") is None

    def test_resource_stats_always_carry_ram_reading(self):
        """`peak_ram_mb` comes from `psutil` directly (no CUDA/model-lease
        dependency) so it's populated even on a pipeline that never touches
        the model cache."""
        self._run_with_pipe("gen-ram", lambda models: None)

        stats = self.manager.pop_resource_stats("gen-ram")
        assert stats["peak_ram_mb"] is not None
        assert stats["peak_ram_mb"] > 0
