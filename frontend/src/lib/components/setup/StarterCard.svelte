<script lang="ts">
	import type { SetupMachine, SetupRecipe } from '$lib/services/api/setup';
	import { Badge, ChoiceCard } from '$lib/components/ui';
	import { formatBytes } from '$lib/utils/format';
	import { starterFit, starterModelName } from '$lib/utils/setupGuide';

	let {
		recipe,
		machine,
		checked,
		tabbable,
		recommended = false,
		onselect
	}: {
		recipe: SetupRecipe;
		machine: SetupMachine | null;
		checked: boolean;
		tabbable: boolean;
		recommended?: boolean;
		onselect: () => void;
	} = $props();

	const fit = $derived(starterFit(recipe, machine));
	const fitClass = { good: 'text-success', low: 'text-warning', dim: 'text-fg-muted' };
</script>

<ChoiceCard
	{checked}
	{tabbable}
	title={recipe.blurb ?? recipe.summary}
	badge={recommended ? 'Recommended for your card' : undefined}
	{onselect}
>
	<span class="font-mono text-sm text-fg-subtle" data-spec>
		{starterModelName(recipe)}
		<span aria-hidden="true">&middot;</span>
		<span class="tabular-nums" data-spec-download>
			{recipe.total_download_bytes != null ? formatBytes(recipe.total_download_bytes) : 'Size not stated'}
		</span>
		download
	</span>
	<span class="block {fitClass[fit.tone]}" data-spec-fit>{fit.text}</span>
	{#if recipe.last_completed_at}
		<span class="mt-1"><Badge size="sm" variant="success">Installed</Badge></span>
	{/if}
</ChoiceCard>
