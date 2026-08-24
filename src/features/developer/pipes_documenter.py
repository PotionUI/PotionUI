"""Pipes documentation generator."""
from typing import Dict, Any, List
import logging

logger = logging.getLogger(__name__)


class PipesDocumenter:
    """Generates documentation for all available pipes."""

    def __init__(self, pipe_catalog):
        """Initialize with pipe registry.

        Args:
            pipe_catalog: PipeCatalog instance for accessing pipes
        """
        self.pipe_catalog = pipe_catalog

    def _document_pipe(self, pipe_name: str, pipe_class) -> Dict[str, Any]:
        """Document a single pipe.

        Args:
            pipe_name: Name of the pipe
            pipe_class: Pipe class to document

        Returns:
            Dict with pipe documentation
        """
        pipe_info = {
            'name': pipe_name,
            'description': pipe_class.description if hasattr(pipe_class, 'description') else pipe_class.__doc__ or "No description available",
            'status': self.pipe_catalog.get_pipe_status(pipe_name).value,
            # Non-null means no Install action can help this pipe - the reader
            # renders these commands instead of offering one.
            'manual_install': (
                pipe_class.manual_install_instructions()
                if hasattr(pipe_class, 'manual_install_instructions') else None
            ),
            'default_config': pipe_class.get_default_config(),
        }

        # Get inputs specification
        if hasattr(pipe_class, 'inputs'):
            pipe_info['inputs'] = [
                {
                    'name': spec.name,
                    'io_type': spec.io_type.value if hasattr(spec.io_type, 'value') else str(spec.io_type),
                    'required': spec.required,
                    'description': spec.description,
                    'is_array': spec.is_array
                }
                for spec in pipe_class.inputs()
            ]
        else:
            pipe_info['inputs'] = []

        # Get outputs specification
        if hasattr(pipe_class, 'outputs'):
            pipe_info['outputs'] = [
                {
                    'name': spec.name,
                    'io_type': spec.io_type.value if hasattr(spec.io_type, 'value') else str(spec.io_type),
                    'description': spec.description,
                    'is_array': spec.is_array
                }
                for spec in pipe_class.outputs()
            ]
        else:
            pipe_info['outputs'] = []

        # Get configuration specification
        if hasattr(pipe_class, 'configuration'):
            pipe_info['configuration'] = [
                {
                    'name': config.name,
                    'param_type': config.param_type.__name__,
                    'default': config.default,
                    'description': config.description,
                    'required': config.required,
                    'choices': config.choices,
                    'min_value': config.min_value,
                    'max_value': config.max_value
                }
                for config in pipe_class.configuration()
            ]
        else:
            pipe_info['configuration'] = []

        # Get requirements
        if hasattr(pipe_class, 'get_requirements'):
            pipe_info['requirements'] = pipe_class.get_requirements()
        else:
            pipe_info['requirements'] = {}

        return pipe_info

    def generate_documentation(self) -> Dict[str, Any]:
        """Generate documentation for all available pipes.

        Returns:
            Dict with 'pipes' list and 'total' count

        Raises:
            ValueError: If pipe discovery fails
        """
        # Discover pipes if not already done
        if not self.pipe_catalog.pipes:
            self.pipe_catalog.discover_pipes()

        pipes_docs = []
        for pipe_name, pipe_class in self.pipe_catalog.pipes.items():
            try:
                pipe_info = self._document_pipe(pipe_name, pipe_class)
                pipes_docs.append(pipe_info)
            except Exception as e:
                logger.warning(f"Error documenting pipe {pipe_name}: {e}")
                # If a pipe fails to document, include basic info with error
                pipes_docs.append({
                    'name': pipe_name,
                    'description': f"Error documenting pipe: {str(e)}",
                    'status': 'error',
                    'inputs': [],
                    'outputs': [],
                    'configuration': [],
                    'requirements': {}
                })

        return {
            'pipes': pipes_docs,
            'total': len(pipes_docs)
        }
