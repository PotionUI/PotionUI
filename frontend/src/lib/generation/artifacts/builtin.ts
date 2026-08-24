import { artifactRendererRegistry } from '$lib/registries/artifactRendererRegistry';
import SeedArtifact from '$lib/components/generation/artifacts/SeedArtifact.svelte';
import ModelsArtifact from '$lib/components/generation/artifacts/ModelsArtifact.svelte';
import CompareImagesArtifact from '$lib/components/generation/artifacts/CompareImagesArtifact.svelte';
import DiffTextArtifact from '$lib/components/generation/artifacts/DiffTextArtifact.svelte';
import ImageArtifact from '$lib/components/generation/artifacts/ImageArtifact.svelte';
import WorkflowArtifact from '$lib/components/generation/artifacts/WorkflowArtifact.svelte';
import RenderedPromptArtifact from '$lib/components/generation/artifacts/RenderedPromptArtifact.svelte';

artifactRendererRegistry.register('seed', { component: SeedArtifact });
artifactRendererRegistry.register('models', { component: ModelsArtifact });
artifactRendererRegistry.register('compare_images', { component: CompareImagesArtifact });
artifactRendererRegistry.register('diff_text', { component: DiffTextArtifact });
artifactRendererRegistry.register('image', { component: ImageArtifact });
artifactRendererRegistry.register('workflow', { component: WorkflowArtifact });
artifactRendererRegistry.register('rendered_prompt', { component: RenderedPromptArtifact });
