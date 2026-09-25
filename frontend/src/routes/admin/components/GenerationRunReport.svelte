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
	import { browser } from '$app/environment';
	import type { AdminGenerationListItem, RunReport } from '$lib/services/admin-api';
	import { groupStatusHistory, groupByPipe, findRunningPipeKey, resolveRunEnd } from './runReport';
	import { buildTocSections, pickActiveSectionId } from './generations/runReportToc';
	import { timeAgo, parseServerDate } from '$lib/utils/relativeTime';
	import { formatDurationMs } from '$lib/components/generation-panel/barState';
	import { Alert, Badge, EmptyState } from '$lib/components/ui';
	import { DetailHeader, DetailBody, DetailLayout, DetailSection, KVGrid, KVItem, DETAIL_INSET_CLASS } from '$lib/components/detail';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import GenerationRoutingPanel from './GenerationRoutingPanel.svelte';
	import GenerationPipeTimeline from './GenerationPipeTimeline.svelte';
	import GenerationOutputsGrid from './GenerationOutputsGrid.svelte';
	import GenerationArtifactsGrid from './GenerationArtifactsGrid.svelte';
	import GenerationPromptPanel from './GenerationPromptPanel.svelte';
	import GenerationStatusLog from './GenerationStatusLog.svelte';
	import RunReportToc from './generations/RunReportToc.svelte';

	let {
		generation,
		report,
		username,
		backLabel,
		onBack
	}: {
		generation: AdminGenerationListItem;
		report: RunReport | null;
		username: string;
		backLabel?: string;
		onBack?: () => void;
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
		const completed = parseServerDate(generation.completed_at)?.getTime();
		const created = parseServerDate(generation.created_at)?.getTime();
		const ms = completed != null && created != null ? completed - created : NaN;
		return Number.isFinite(ms) && ms >= 0 ? ms : null;
	});
	let durationLabel = $derived.by(() => {
		if (durationMs !== null) return formatDurationMs(durationMs);
		return generation.status === 'running' ? 'running' : '—';
	});
	let seedLabel = $derived(generation.seed != null && generation.seed !== -1 ? String(generation.seed) : 'random');
	let fileCount = $derived(generation.files?.length ?? 0);

	let tocSections = $derived(
		buildTocSections({
			hasRouting: !!generation.routing,
			hasTimeline: !!report && hasTimelineData,
			hasArtifacts,
			hasPrompt: !!report?.prompt_template,
			hasStatusLog: !!report,
			hasPluginOutput: pluginOutputEntries.length > 0
		})
	);
	let visibleSectionIds = $state<Set<string>>(new Set());
	let activeSectionId = $state('overview');

	$effect(() => {
		activeSectionId = pickActiveSectionId(tocSections.map((s) => s.id), visibleSectionIds, activeSectionId);
	});

	function observeSection(node: HTMLElement, id: string) {
		if (!browser || typeof IntersectionObserver === 'undefined') return {};
		const observer = new IntersectionObserver(
			(entries) => {
				const next = new Set(visibleSectionIds);
				for (const entry of entries) {
					if (entry.isIntersecting) next.add(id);
					else next.delete(id);
				}
				visibleSectionIds = next;
			},
			{ rootMargin: '-10% 0px -70% 0px', threshold: 0 }
		);
		observer.observe(node);
		return { destroy: () => observer.disconnect() };
	}

	function scrollToSection(id: string) {
		document.getElementById(id)?.scrollIntoView({ block: 'start', behavior: 'smooth' });
	}

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
		const date = parseServerDate(iso);
		if (!date) return '—';
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
	<DetailHeader {title} icon="generation" {backLabel} {onBack}>
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
			<Tooltip text={absolute(generation.created_at)}><span class="tabular-nums">{timeAgo(generation.created_at)}</span></Tooltip>
			<span class="text-fg-disabled">·</span>
			<Tooltip text={generation.id}><span class="truncate">{generation.id}</span></Tooltip>
		{/snippet}
	</DetailHeader>

	<DetailBody>
		<DetailLayout>
			{#snippet main()}
				{#if generation.status === 'failed' && generation.error_message}
					<Alert variant="danger" icon="warning" title="Generation failed">
						<p class="text-xs leading-relaxed">{generation.error_message}</p>
					</Alert>
				{/if}

				<div id="overview" use:observeSection={'overview'}>
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
				</div>

				{#if generation.routing}
					<div id="routing" use:observeSection={'routing'}>
						<GenerationRoutingPanel routing={generation.routing} />
					</div>
				{/if}

				{#if report && hasTimelineData}
					<div id="timeline" use:observeSection={'timeline'}>
						<GenerationPipeTimeline {report} {groupedEntries} runStart={generation.created_at} {runEnd} {failedPipeKey} />
					</div>
				{/if}

				<div id="outputs" use:observeSection={'outputs'}>
					<GenerationOutputsGrid generationId={generation.id} files={generation.files ?? []} />
				</div>

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
						<div id="artifacts" use:observeSection={'artifacts'}>
							<GenerationArtifactsGrid {byPipe} artifacts={report.artifacts} promptTemplate={report.prompt_template} />
						</div>
					{/if}

					{#if report.prompt_template}
						<div id="prompt" use:observeSection={'prompt'}>
							<GenerationPromptPanel promptTemplate={report.prompt_template} />
						</div>
					{/if}

					<div id="status-log" use:observeSection={'status-log'}>
						<GenerationStatusLog {byPipe} pipeTimers={report.pipe_timers ?? {}} />
					</div>

					{#if pluginOutputEntries.length > 0}
						<div id="plugin-output" use:observeSection={'plugin-output'}>
							<DetailSection label="Plugin output">
								<div class="space-y-3">
									{#each pluginOutputEntries as [messageType, output] (messageType)}
										<div class={DETAIL_INSET_CLASS}>
											<div class="flex items-center gap-2 px-3 py-2">
												<Badge variant="neutral" size="sm" class="font-mono">{output.plugin_id}</Badge>
												<span class="text-xs font-mono text-fg-muted truncate">{messageType}</span>
											</div>
											{#if output.omitted}
												<p class="text-xs border-t border-line px-3 py-2.5 text-fg-subtle">
													Payload not recorded (<span class="font-mono tabular-nums">{output.omitted.bytes}</span> bytes,
													{output.omitted.reason.replace(/_/g, ' ')})
												</p>
											{:else}
												<pre class="text-xs font-mono whitespace-pre-wrap overflow-x-auto overflow-y-auto max-h-48 border-t border-line px-3 py-2.5 text-fg-muted">{pretty(output.message)}</pre>
											{/if}
										</div>
									{/each}
								</div>
							</DetailSection>
						</div>
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
			{/snippet}
			{#snippet aside()}
				<RunReportToc sections={tocSections} activeId={activeSectionId} onSelect={scrollToSection} />
				<DetailSection label="Details">
					<KVGrid>
						<KVItem label="User">{username}</KVItem>
						<KVItem label="Rating">
							<span class="flex items-center gap-1.5">
								{#if generation.is_favorite}
									<Icon name="star" className="w-3.5 h-3.5 text-warning" strokeWidth={2.5} />
								{/if}
								<span class="font-mono tabular-nums">{generation.rating > 0 ? generation.rating : '—'}</span>
							</span>
						</KVItem>
						<KVItem label="Files" mono>{fileCount}</KVItem>
					</KVGrid>
				</DetailSection>
			{/snippet}
		</DetailLayout>
	</DetailBody>
</div>
