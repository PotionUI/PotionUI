<script lang="ts">
	/**
	 * The composed admin generation detail: the detail-family header and body
	 * (`$lib/components/detail`), then one DetailSection per concern - overview,
	 * routing, the pipe timeline gantt, outputs, artifacts, prompt template, a
	 * collapsed-by-default status log and plugin output. `report` is null for
	 * generations that predate run-report persistence; everything that depends
	 * on it is skipped, but the header, overview and outputs still render from
	 * `generation` alone.
	 */
	import type { AdminGenerationListItem, RunReport } from '$lib/services/admin-api';
	import { groupStatusHistory, groupByPipe, findRunningPipeKey, resolveRunEnd } from './runReport';
	import { timeAgo } from '$lib/utils/relativeTime';
	import { formatDurationMs } from '$lib/components/generation-panel/barState';
	import { Alert, Badge, EmptyState } from '$lib/components/ui';
	import { DetailHeader, DetailBody, DetailSection, KVGrid, KVItem } from '$lib/components/detail';
	import Icon from '$lib/components/Icon.svelte';
	import GenerationRoutingPanel from './GenerationRoutingPanel.svelte';
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

	const STATUS_VARIANT: Record<string, 'neutral' | 'success' | 'warning' | 'danger' | 'info'> = {
		completed: 'success',
		running: 'info',
		pending: 'neutral',
		failed: 'danger',
		cancelled: 'warning'
	};

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

	let title = $derived(generation.preset_name || generation.mode || 'Untitled generation');
	let durationMs = $derived.by(() => {
		if (!generation.completed_at) return null;
		const ms = new Date(generation.completed_at).getTime() - new Date(generation.created_at).getTime();
		return Number.isFinite(ms) && ms >= 0 ? ms : null;
	});
	let durationLabel = $derived.by(() => {
		if (durationMs !== null) return formatDurationMs(durationMs);
		return generation.status === 'running' ? 'running' : '—';
	});
	let seedLabel = $derived(generation.seed != null && generation.seed !== -1 ? String(generation.seed) : 'random');
	let fileCount = $derived(generation.files?.length ?? 0);

	// What the recorder refused to keep. Reports written before these counters
	// existed simply have none, and the summary stays hidden.
	let notRecorded = $derived(
		(
			[
				['artifacts', (report as Record<string, number> | null)?.artifacts_dropped],
				['artifact payloads', (report as Record<string, number> | null)?.artifacts_omitted],
				['plugin output types', (report as Record<string, number> | null)?.plugin_output_types_dropped],
				['plugin output payloads', (report as Record<string, number> | null)?.plugin_outputs_omitted],
				['status entries', (report as Record<string, number> | null)?.status_history_dropped],
				['pipe timers', (report as Record<string, number> | null)?.pipe_timers_dropped]
			] as [string, number | undefined][]
		)
			.filter(([, count]) => typeof count === 'number' && count > 0)
			.map(([label, count]) => `${count} ${label}`)
	);
	let textsTruncated = $derived((report as Record<string, number> | null)?.texts_truncated ?? 0);

	function absolute(iso: string | undefined): string {
		if (!iso) return '—';
		const date = new Date(iso);
		if (Number.isNaN(date.getTime())) return '—';
		return date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'medium' });
	}

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

<div class="h-full min-h-0 flex flex-col">
	<DetailHeader {title} icon="generation">
		{#snippet chips()}
			<Badge variant={STATUS_VARIANT[generation.status] ?? 'neutral'} size="sm" dot class="uppercase">
				{generation.status}
			</Badge>
			{#if generation.mode && generation.preset_name}
				<Badge variant="neutral" size="sm" class="font-mono">{generation.mode}</Badge>
			{/if}
		{/snippet}
		{#snippet subtitle()}
			<span>{username}</span>
			<span class="text-fg-disabled">·</span>
			<span class="tabular-nums" title={absolute(generation.created_at)}>{timeAgo(generation.created_at)}</span>
			<span class="text-fg-disabled">·</span>
			<span class="truncate" title={generation.id}>{generation.id}</span>
		{/snippet}
	</DetailHeader>

	<DetailBody fullWidth>
		<div class="space-y-5">
			{#if generation.status === 'failed' && generation.error_message}
				<Alert variant="danger" icon="warning" title="Generation failed">
					<p class="text-xs leading-relaxed">{generation.error_message}</p>
				</Alert>
			{/if}

			<DetailSection label="Overview">
				<KVGrid>
					<KVItem label="User">{username}</KVItem>
					<KVItem label="Duration" mono>{durationLabel}</KVItem>
					<KVItem label="Created" mono>{absolute(generation.created_at)}</KVItem>
					<KVItem label="Completed" mono>
						{#if generation.completed_at}
							{absolute(generation.completed_at)}
						{:else}
							<span class="text-fg-subtle">{generation.status === 'running' ? 'in progress' : 'never completed'}</span>
						{/if}
					</KVItem>
					<KVItem label="Seed" mono>{seedLabel}</KVItem>
					<KVItem label="Rating">
						<span class="flex items-center gap-1.5">
							{#if generation.is_favorite}
								<Icon name="star" className="w-3.5 h-3.5 text-warning" strokeWidth={2.5} />
							{/if}
							<span class="font-mono tabular-nums">{generation.rating > 0 ? generation.rating : '—'}</span>
						</span>
					</KVItem>
					<KVItem label="Files" mono>{fileCount}</KVItem>
					<KVItem label="Pipes" mono>{byPipe.size || '—'}</KVItem>
					<KVItem label="Generation id" mono full>
						<span class="break-all">{generation.id}</span>
					</KVItem>
				</KVGrid>
			</DetailSection>

			{#if generation.routing}
				<GenerationRoutingPanel routing={generation.routing} />
			{/if}

			{#if report && hasTimelineData}
				<GenerationPipeTimeline {report} {groupedEntries} runStart={generation.created_at} {runEnd} {failedPipeKey} />
			{/if}

			<GenerationOutputsGrid generationId={generation.id} files={generation.files ?? []} />

			{#if report}
				{#if notRecorded.length > 0 || textsTruncated > 0}
					<Alert variant="neutral" icon="info" density="compact">
						{#if notRecorded.length > 0}
							<p class="text-xs">Not recorded, over the report's size limits: {notRecorded.join(', ')}.</p>
						{/if}
						{#if textsTruncated > 0}
							<p class="text-xs">
								Text shortened on <span class="font-mono tabular-nums">{textsTruncated}</span>
								{textsTruncated === 1 ? 'entry' : 'entries'}, including any terminal status message.
							</p>
						{/if}
					</Alert>
				{/if}

				{#if hasArtifacts}
					<GenerationArtifactsGrid {byPipe} artifacts={report.artifacts} promptTemplate={report.prompt_template} />
				{/if}

				{#if report.prompt_template}
					<GenerationPromptPanel promptTemplate={report.prompt_template} />
				{/if}

				<GenerationStatusLog {byPipe} pipeTimers={report.pipe_timers ?? {}} />

				{#if pluginOutputEntries.length > 0}
					<DetailSection label="Plugin output">
						<div class="space-y-3">
							{#each pluginOutputEntries as [messageType, output] (messageType)}
								<div class="border border-line rounded-lg overflow-hidden">
									<div class="flex items-center gap-2 px-3 py-2 bg-surface-1">
										<Badge variant="neutral" size="sm" class="font-mono">{output.plugin_id}</Badge>
										<span class="text-xs font-mono text-fg-muted truncate">{messageType}</span>
									</div>
									{#if output.omitted}
										<p class="text-xs bg-surface-2/60 border-t border-line px-3 py-2.5 text-fg-subtle">
											Payload not recorded (<span class="font-mono tabular-nums">{output.omitted.bytes}</span> bytes,
											{output.omitted.reason.replace(/_/g, ' ')})
										</p>
									{:else}
										<pre class="text-xs font-mono whitespace-pre-wrap overflow-x-auto overflow-y-auto max-h-48 bg-surface-2/60 border-t border-line px-3 py-2.5 text-fg-muted">{pretty(output.message)}</pre>
									{/if}
								</div>
							{/each}
						</div>
					</DetailSection>
				{/if}

				{#if groupedEntries.length === 0 && !hasArtifacts && pluginOutputEntries.length === 0}
					<p class="text-xs text-fg-subtle px-1">This run report has no recorded status, artifact, or plugin output entries.</p>
				{/if}
			{:else}
				<DetailSection label="Run report">
					<EmptyState
						icon="document"
						title="No report recorded"
						description="This generation predates run-report persistence, so no status timeline, timers, or artifacts were captured — only the record above is on file."
						compact
					/>
				</DetailSection>
			{/if}
		</div>
	</DetailBody>
</div>
