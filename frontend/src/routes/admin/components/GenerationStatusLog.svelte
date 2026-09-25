<script lang="ts">
	import { processTemplateString, removePipeFromMessage } from '$lib/utils/templateProcessor';
	import { Badge, EmptyState } from '$lib/components/ui';
	import { DetailSection } from '$lib/components/detail';
	import Icon from '$lib/components/Icon.svelte';
	import type { RunReportPipeTimer } from '$lib/services/admin-api';
	import { formatPipeTiming, type GroupedStatusEntry } from './runReport';

	let {
		byPipe,
		pipeTimers
	}: {
		byPipe: Map<string, GroupedStatusEntry[]>;
		pipeTimers: Record<string, RunReportPipeTimer>;
	} = $props();

	let expanded = $state(false);
	let entryCount = $derived([...byPipe.values()].reduce((sum, items) => sum + items.length, 0));
</script>

<DetailSection label="Status log" collapsible bind:open={expanded} padded={false}>
	{#snippet headerExtra()}
		<span class="font-mono text-2xs tabular-nums text-fg-subtle">
			{entryCount === 0 ? 'no entries' : `${entryCount} ${entryCount === 1 ? 'entry' : 'entries'}`}
		</span>
	{/snippet}

	<div class="px-4 sm:px-5 py-4 space-y-2">
		{#if entryCount === 0}
			<EmptyState icon="document" title="No status entries" description="No status entries were recorded for this run." compact />
		{:else}
			{#each [...byPipe.entries()] as [pipeKey, items] (pipeKey)}
				{@const pipeLabel = items[0]?.pipeLabel ?? pipeKey}
				{@const executionTime = formatPipeTiming(pipeTimers[pipeKey])}

				{#each items as item, i (i)}
					<div class="grid grid-cols-12 gap-3 items-center bg-surface-2/40 border border-line rounded p-2.5">
						<div class="col-span-3 sm:col-span-2 flex items-center gap-1.5 min-w-0">
							<Icon name="generation" className="w-3 h-3 text-fg-subtle flex-shrink-0" />
							<span class="text-xs font-mono text-fg-muted truncate">{pipeLabel}</span>
						</div>
						<div class="col-span-7 sm:col-span-8 min-w-0">
							<div class="text-xs font-medium text-fg">{@html processTemplateString(item.step)}</div>
							{#if item.message && removePipeFromMessage(item.message)}
								<div class="text-xs text-fg-muted mt-0.5">{@html processTemplateString(removePipeFromMessage(item.message))}</div>
							{/if}
						</div>
						<div class="col-span-2 flex items-center justify-end gap-2">
							{#if item.type === 'progress_group'}
								<span class="text-2xs font-mono tabular-nums text-fg-muted">{Math.round(item.startProgress)}% → {Math.round(item.endProgress)}%</span>
							{:else if item.startProgress > 0}
								<span class="text-2xs font-mono tabular-nums text-fg-muted">{Math.round(item.startProgress)}%</span>
							{:else}
								<Badge variant="success" size="sm">Done</Badge>
							{/if}
						</div>
					</div>
				{/each}

				{#if executionTime !== '-'}
					<div class="flex items-center justify-center py-1">
						<span class="font-mono text-2xs tabular-nums text-fg-subtle">{pipeLabel} · {executionTime}</span>
					</div>
				{/if}
			{/each}
		{/if}
	</div>
</DetailSection>
