<script lang="ts">
	import type { RecipeSummary } from '$lib/services/api/recipes';
	import type { RecipeReadinessBadge } from '../recipeReadinessBadge';
	import { Badge, Button } from '$lib/components/ui';
	import LibraryEntryCard from '$lib/components/library/LibraryEntryCard.svelte';
	import { recipeCategoryIcon, recipeCategoryLabel } from './recipeSections';
	import { formatBytes } from '$lib/utils/format';

	let {
		recipe,
		readiness,
		dense = false,
		onOpen
	}: {
		recipe: RecipeSummary;
		readiness: RecipeReadinessBadge;
		dense?: boolean;
		onOpen: (id: string) => void;
	} = $props();

	const artifactSummary = $derived(
		recipe.artifact_count === 0
			? 'No downloads'
			: `${recipe.artifact_count} artifact${recipe.artifact_count === 1 ? '' : 's'}${
					recipe.total_download_bytes != null ? ` · ${formatBytes(recipe.total_download_bytes)}` : ''
				}`
	);

	function presetHref(presetId: string): string {
		return `/admin?tab=presets&id=${encodeURIComponent(presetId)}`;
	}

	function stop(event: MouseEvent) {
		event.stopPropagation();
		onOpen(recipe.id);
	}
	const linkedPresets = $derived(recipe.presets ?? []);
</script>

<LibraryEntryCard
	icon={recipeCategoryIcon(recipe.category)}
	name={recipe.name}
	{dense}
	description={recipe.summary}
	onOpen={() => onOpen(recipe.id)}
	ariaLabel={recipe.name}
>
	{#snippet topRight()}
		<Badge variant={readiness.variant} dot class="flex-shrink-0">{readiness.label}</Badge>
	{/snippet}
	{#snippet details()}
		{#if linkedPresets.length}
			<p class="flex min-w-0 flex-wrap items-baseline gap-x-1.5 text-xs text-fg-muted" data-recipe-sets-up>
				<span class="text-fg-subtle">Sets up:</span>
				{#each linkedPresets as linkedPreset, index (linkedPreset.id)}
					<a
						class="min-w-0 truncate text-fg hover:text-signal"
						href={presetHref(linkedPreset.id)}
						onclick={(event) => event.stopPropagation()}
					>{linkedPreset.name}</a>{#if index < linkedPresets.length - 1}<span class="text-fg-subtle">,</span>{/if}
				{/each}
			</p>
		{/if}
	{/snippet}
	{#snippet footer()}
		<Badge>{recipeCategoryLabel(recipe.category)}</Badge>
		<span class="font-mono text-xs tabular-nums text-fg-muted">{artifactSummary}</span>
		{#if recipe.last_completed_at}
			<Badge variant="success" dot>installed</Badge>
		{/if}
		<Button size="xs" variant={recipe.last_completed_at ? 'secondary' : 'primary'} class="ml-auto" onclick={stop}>
			{recipe.last_completed_at ? 'Open' : 'Install'}
		</Button>
	{/snippet}
</LibraryEntryCard>
