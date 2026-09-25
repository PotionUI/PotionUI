<script lang="ts">
	import type { RecipeRun } from '$lib/services/api/recipes';
	import { runStatusLabel } from '$lib/utils/setupRunDisplay';
	import { runDuration, runStartedLabel } from '../recipeRunHistory';
	import { runStatusTone } from './recipeRunStatusTone';
	import { Button } from '$lib/components/ui';
	import { DataTable, StatusCell, type DataTableColumn } from '$lib/components/table';

	let { runs, limit = 5 }: { runs: RecipeRun[]; limit?: number } = $props();

	let expanded = $state(false);

	const visibleRuns = $derived(expanded ? runs : runs.slice(0, limit));
	const hiddenCount = $derived(Math.max(0, runs.length - limit));

	const columns: DataTableColumn<RecipeRun>[] = $derived([
		{ key: 'status', label: 'Status', width: '10rem', cell: statusCell },
		{ key: 'started', label: 'Started', width: 'minmax(10rem, 1fr)', mono: true, accessor: (run) => runStartedLabel(run) },
		{ key: 'mode', label: 'Mode', width: '7rem', mono: true, priority: 1, accessor: (run) => run.mode },
		{ key: 'duration', label: 'Duration', width: '7rem', mono: true, align: 'right', accessor: (run) => runDuration(run) ?? '—' }
	]);
</script>

{#snippet statusCell(run: RecipeRun)}
	<StatusCell tone={runStatusTone(run.status)} label={runStatusLabel(run.status)} />
{/snippet}

{#if runs.length === 0}
	<p class="text-sm text-fg-muted">This recipe hasn't been run yet.</p>
{:else}
	<div data-recipe-runs>
		<DataTable {columns} rows={visibleRuns} getRowId={(run) => run.id} />
	</div>
	{#if !expanded && hiddenCount > 0}
		<Button variant="ghost" size="sm" class="mt-2" onclick={() => (expanded = true)}>
			Show all {runs.length} runs
		</Button>
	{/if}
{/if}
