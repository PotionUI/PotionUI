<script lang="ts">
	import type { GenerationTab, PipeArtifact } from '$lib/types/tabs';
	import { Card } from '$lib/components/ui';
	import type { ModelFileData } from '$lib/generation/artifacts/modelFile';
	import ModelFileArtifact from './artifacts/ModelFileArtifact.svelte';

	let { tab }: { tab: GenerationTab } = $props();

	let modelFiles = $derived(
		(tab.generation.artifacts ?? []).filter(
			(artifact: PipeArtifact) => artifact.artifact_type === 'model_file' && artifact.artifact_data
		) as Array<PipeArtifact & { artifact_data: ModelFileData }>
	);
</script>

{#if modelFiles.length > 0}
	<div class="flex flex-col gap-2" data-live-model-file-artifacts>
		{#each modelFiles as artifact, index (index)}
			<Card padding="sm">
				<ModelFileArtifact {artifact} />
			</Card>
		{/each}
	</div>
{/if}
