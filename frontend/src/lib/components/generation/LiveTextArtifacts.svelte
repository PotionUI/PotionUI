<script lang="ts">
	import type { Tab, PipeArtifact } from '$lib/types/tabs';
	import { tabsStore } from '$lib/stores/tabs';
	import { formValidationStore } from '$lib/stores/formValidation';
	import { toasts } from '$lib/stores/toast';
	import { applyArtifactValues, type TextArtifactData } from '$lib/generation/artifacts/textArtifact';
	import TextArtifact from './artifacts/TextArtifact.svelte';

	let { tab }: { tab: Tab } = $props();

	let textArtifacts = $derived(
		(tab.generation.artifacts ?? []).filter(
			(artifact: PipeArtifact) => artifact.artifact_type === 'text' && artifact.artifact_data
		) as Array<PipeArtifact & { artifact_data: TextArtifactData }>
	);

	function apply(values: Record<string, unknown>) {
		const current = $tabsStore.tabs.find((t) => t.id === tab.id) ?? tab;
		tabsStore.updateTab(tab.id, { formData: applyArtifactValues(current.formData, values) });
		for (const field of Object.keys(values)) formValidationStore.clearField(tab.id, field);
		toasts.success('Applied to the form');
	}
</script>

{#if textArtifacts.length > 0}
	<div class="flex flex-col gap-2" data-live-text-artifacts>
		{#each textArtifacts as artifact, index (index)}
			<div class="rounded-lg bg-surface-2 p-3">
				<TextArtifact {artifact} onApply={apply} />
			</div>
		{/each}
	</div>
{/if}
