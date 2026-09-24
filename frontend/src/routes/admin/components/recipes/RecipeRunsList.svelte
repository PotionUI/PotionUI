<script lang="ts">
	import type { RecipeRun } from '$lib/services/api/recipes';
	import { runBadgeVariant, runStatusLabel } from '$lib/utils/setupRunDisplay';
	import { runDuration, runStartedLabel } from '../recipeRunHistory';
	import { Badge, Button } from '$lib/components/ui';

	let { runs, limit = 5 }: { runs: RecipeRun[]; limit?: number } = $props();

	let expanded = $state(false);

	const visibleRuns = $derived(expanded ? runs : runs.slice(0, limit));
	const hiddenCount = $derived(Math.max(0, runs.length - limit));
</script>

{#if runs.length === 0}
	<p class="text-sm text-fg-muted">This recipe hasn't been run yet.</p>
{:else}
	<ul class="space-y-1" data-recipe-runs>
		{#each visibleRuns as run (run.id)}
			<li class="flex items-center justify-between gap-3 rounded border border-line bg-surface-1 px-3 py-2">
				<div class="flex items-center gap-2 min-w-0">
					<Badge variant={runBadgeVariant(run.status)} size="sm">{runStatusLabel(run.status)}</Badge>
					<span class="font-mono text-xs tabular-nums text-fg-muted truncate">{runStartedLabel(run)}</span>
					<Badge variant="neutral" size="sm">{run.mode}</Badge>
				</div>
				<span class="font-mono text-xs tabular-nums text-fg-subtle shrink-0">{runDuration(run) ?? '—'}</span>
			</li>
		{/each}
	</ul>
	{#if !expanded && hiddenCount > 0}
		<Button variant="ghost" size="sm" class="mt-2" onclick={() => (expanded = true)}>
			Show all {runs.length} runs
		</Button>
	{/if}
{/if}
