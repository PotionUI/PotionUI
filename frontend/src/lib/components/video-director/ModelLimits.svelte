<script lang="ts">
	import type { DirectorCapabilities, VideoDirectorValue } from '$lib/types/videoDirector';
	import { hasLengthProblems, modelFacts, modelFitNotices, snapShotLengths } from '$lib/utils/cloudDirector';
	import { normalizeDirectorValue, toModelessDirectorValue } from '$lib/utils/videoDirector';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Button from '$lib/components/ui/Button.svelte';

	let {
		value,
		capabilities,
		onChange
	}: {
		value: VideoDirectorValue | undefined;
		capabilities: DirectorCapabilities;
		onChange?: (next: VideoDirectorValue) => void;
	} = $props();

	let facts = $derived(modelFacts(capabilities));
	let doc = $derived(toModelessDirectorValue(normalizeDirectorValue(value, capabilities), capabilities));
	let notices = $derived(modelFitNotices(doc, capabilities));
	let canFixLengths = $derived(!!onChange && hasLengthProblems(doc, capabilities));
</script>

{#if capabilities.modelLabel}
	<div class="flex flex-col gap-2" data-model-limits>
		<div class="flex flex-wrap items-center gap-1.5">
			<span class="mr-1 text-xs font-semibold text-fg" data-model-name>{capabilities.modelLabel}</span>
			{#each facts as fact (fact)}
				<Badge variant="neutral"><span class="font-mono tabular-nums">{fact}</span></Badge>
			{/each}
		</div>
		{#if notices.length > 0}
			<Alert variant="warning" density="compact" icon title="Some of your film does not fit {capabilities.modelLabel}">
				{#snippet actions()}
					{#if canFixLengths}
						<Button variant="secondary" size="sm" onclick={() => onChange?.(snapShotLengths(doc, capabilities))}>
							Use lengths it renders
						</Button>
					{/if}
				{/snippet}
				<ul class="flex flex-col gap-1" data-model-notices>
					{#each notices as notice (notice)}
						<li>{notice}</li>
					{/each}
				</ul>
				<p class="mt-1.5">Change these, or pick another model, to generate.</p>
			</Alert>
		{/if}
	</div>
{/if}
