from dotenv import load_dotenv

load_dotenv()

from src.platform.runtime.cuda_env import apply_cuda_env_defaults

apply_cuda_env_defaults()

from src.bootstrap.app import create_app
from src.bootstrap.container import build_container
from src.platform.observability.logger import configure_logging

from tests.e2e.harness.stub_engine import control_path_from_env, engine_factory

configure_logging()

app = create_app(build_container(generation_engine_factory=engine_factory(control_path_from_env())))
