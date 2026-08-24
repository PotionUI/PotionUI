<script lang="ts">
	/**
	 * The composed admin generation detail page: header, stat tiles, the pipe
	 * timeline gantt, outputs, artifacts, prompt template, and a
	 * collapsed-by-default status log - the full-page replacement for what
	 * `GenerationPanelHistory.svelte` showed live in the user-facing history
	 * drawer. `report` is null for generations that predate run-report
	 * persistence; everything that depends on it is skipped, but the header,
	 * stat tiles, and outputs still render from `generation` alone.
	 */
	import type { AdminGenerationListItem, RunReport } from '$lib/services/admin-api';
	import { groupStatusHistory, groupByPipe, findRunningPipeKey, resolveRunEnd } from './runReport';
	import { Badge, EmptyState } from '$lib/components/ui';
	import GenerationDetailHeader from './GenerationDetailHeader.svelte';
	import GenerationStatTiles from './GenerationStatTiles.svelte';
	import GenerationPipeTimeline from './GenerationPipeTimeline.svelte';
	import GenerationOutputsGrid from './GenerationOutputsGrid.svelte';
	import GenerationArtifactsGrid from './GenerationArtifactsGrid.svelte';
	import GenerationPromptPanel from './GenerationPromptPanel.svelte';
	import GenerationStatusLog from './GenerationStatusLog.svelte';

	let {
		generation,
		report,
		username
	}: {
		generation: AdminGenerationListItem;
		report: RunReport | null;
		username: string;
	} = $props();

	let groupedEntries = $derived(report ? groupStatusHistory(report.status_history ?? []) : []);
	let byPipe = $derived(groupByPipe(groupedEntries));
	let hasTimelineData = $derived(byPipe.size > 0 || Object.keys(report?.pipe_timers ?? {}).length > 0);
	let failedPipeKey = $derived(
		report && generation.status === 'failed' ? findRunningPipeKey(report.pipe_timers ?? {}) : null
	);
	let runEnd = $derived(
		resolveRunEnd({
			completedAt: generation.completed_at ?? null,
			statusHistory: report?.status_history ?? [],
			fallback: generation.updated_at || generation.created_at
		})
	);
	let pluginOutputEntries = $derived(report ? Object.entries(report.plugin_outputs ?? {}) : []);
	let hasArtifacts = $derived((report?.artifacts?.length ?? 0) > 0);

	function pretty(value: unknown): string {
		if (value === null || value === undefined) return '';
		if (typeof value === 'string') return value;
		try {
			return JSON.stringify(value, null, 2);
		} catch {
			return String(value);
		}
	}
</script>

<div class="space-y-4">
	<GenerationDetailHeader {generation} {username} />
	<GenerationStatTiles {generation} pipeCount={byPipe.size} />

	{#if report && hasTimelineData}
		<GenerationPipeTimeline {report} {groupedEntries} runStart={generation.created_at} {runEnd} {failedPipeKey} />
	{/if}

	<GenerationOutputsGrid generationId={generation.id} files={generation.files ?? []} />

	{#if report}
		{#if hasArtifacts}
			<GenerationArtifactsGrid {byPipe} artifacts={report.artifacts} promptTemplate={report.prompt_template} />
		{/if}

		{#if report.prompt_template}
			<GenerationPromptPanel promptTemplate={report.prompt_template} />
		{/if}

		<GenerationStatusLog {byPipe} pipeTimers={report.pipe_timers ?? {}} />

		{#if pluginOutputEntries.length > 0}
			<div class="space-y-2">
				<h3 class="text-2xs font-mono uppercase tracking-[0.07em] text-fg-subtle px-1">Plugin output</h3>
				{#each pluginOutputEntries as [messageType, output] (messageType)}
					<div class="bg-surface-1 border border-line rounded-lg overflow-hidden">
						<div class="flex items-center gap-2 px-3 py-2">
							<Badge variant="neutral" size="sm" class="font-mono">{output.plugin_id}</Badge>
							<span class="text-xs font-mono text-fg-muted truncate">{messageType}</span>
						</div>
						<pre class="text-xs font-mono whitespace-pre-wrap overflow-x-auto overflow-y-auto max-h-48 bg-surface-2/40 border-t border-line p-2.5 text-fg-muted">{pretty(output.message)}</pre>
					</div>
				{/each}
			</div>
		{/if}

		{#if groupedEntries.length === 0 && !hasArtifacts && pluginOutputEntries.length === 0}
			<p class="text-xs text-fg-subtle px-1">This run report has no recorded status, artifact, or plugin output entries.</p>
		{/if}
	{:else}
		<EmptyState
			icon="document"
			title="No report recorded"
			description="This generation predates run-report persistence, so no status timeline, timers, or artifacts were captured — only the record above is on file."
			compact
		/>
	{/if}
</div>
