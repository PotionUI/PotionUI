from typing import Dict, Any, List

from src.pipelines.outputs import SeedGenerationOutput
from src.pipelines.contracts import BasePipe, logger
from src.pipelines.contracts import (
    PipeInput,
    PipeOutput,
    IOType,
    PipeInputSpec,
    PipeOutputSpec,
    PipeConfigSpec,
)
from src.platform.util.latents import generate_seed


class SeedGeneratorPipe(BasePipe):
    name = "seed_generator"
    description = "Pipe that will generate a seed"

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        seed = int(self.config["seed"])
        quantity = int(self.config["quantity"])

        seeds = []
        for i in range(quantity):
            # If seed is -1, generate random seed for each image
            # If seed is provided, use seed + i for each image (so they're different but reproducible)
            generated_seed = generate_seed() if seed == -1 else (seed + i)
            logger.debug(f"[SEED GENERATOR][{i + 1}/{quantity}] Generated seed: {generated_seed}")

            generation_outputs(SeedGenerationOutput(index=i, seed=generated_seed))

            seeds.append(generated_seed)

        return PipeOutput(output={"seed": seeds})

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "seed": -1,
            "quantity": 1,
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        """Return specification of configuration parameters this pipe accepts"""
        return [
            PipeConfigSpec("seed", int, -1, "Random seed for generation (-1 for random)", required=False),
            PipeConfigSpec("quantity", int, 1, "Number of seeds to generate", required=False,
                          min_value=1, max_value=20)
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        """SeedGenerator has no inputs - it generates from configuration"""
        return []

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        """SeedGenerator produces seed values"""
        return [
            PipeOutputSpec("seed", IOType.SEED, "Generated random seeds for image generation", is_array=True),
        ]
