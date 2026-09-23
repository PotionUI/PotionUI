<script lang="ts">
	import type { RecipeSummary } from '$lib/services/api/recipes';
	import type { RecipeReadinessBadge } from '../recipeReadinessBadge';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Badge } from '$lib/components/ui';
	import { formatBytes } from '$lib/utils/format';
	import { recipeCategoryLabel } from './recipeSections';

	let {
		recipe,
		readiness,
		selected,
		onSelect
	}: {
		recipe: RecipeSummary;
		readiness: RecipeReadinessBadge;
		selected: boolean;
		onSelect: () => void;
	} = $props();
</script>

<button
	type="button"
	role="option"
	aria-selected={selected}
	class="flex w-full items-start gap-4 rounded-lg border px-4 py-3 text-left transition-colors {selected
		? 'border-signal bg-signal/[0.06]'
		: 'border-line bg-surface-1 hover:border-line-hover'}"
	onclick={onSelect}
>
	<div class="min-w-0 flex-1">
		<Tooltip text={recipe.name} wrapperClass="flex w-full min-w-0">
			<p class="truncate text-sm font-medium text-fg">{recipe.name}</p>
		</Tooltip>
		{#if recipe.summary}
			<Tooltip text={recipe.summary} wrapperClass="flex w-full min-w-0">
				<p class="mt-0.5 line-clamp-2 text-xs text-fg-muted">{recipe.summary}</p>
			</Tooltip>
		{/if}
		<div class="mt-2 flex flex-wrap items-center gap-1.5">
			<Badge size="sm" variant="neutral">{recipeCategoryLabel(recipe.category)}</Badge>
			<Badge size="sm" variant="info" class="font-mono">{recipe.engine}</Badge>
			<Badge size="sm" class="font-mono">
				{recipe.source}{recipe.plugin_id ? `: ${recipe.plugin_id}` : ''}
			</Badge>
			<span class="font-mono text-xs tabular-nums text-fg-subtle">
				{recipe.step_count} step{recipe.step_count === 1 ? '' : 's'}
			</span>
		</div>
	</div>

	<div class="flex flex-shrink-0 flex-col items-end gap-1.5">
		<Badge variant={readiness.variant} size="sm">{readiness.label}</Badge>
		{#if recipe.last_completed_at}
			<Badge variant="success" size="sm" dot>installed</Badge>
		{/if}
		{#if recipe.total_download_bytes != null}
			<span class="font-mono text-xs tabular-nums text-fg-subtle">
				~{formatBytes(recipe.total_download_bytes)}
			</span>
		{/if}
	</div>
</button>
