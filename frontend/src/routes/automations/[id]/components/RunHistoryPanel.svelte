<script lang="ts">
	import { Spinner, EmptyState } from '$lib/components/ui';
	import { DetailLayout, DetailSection, DETAIL_INSET_CLASS } from '$lib/components/detail';
	import RunStatusBadge from '$lib/components/automation/RunStatusBadge.svelte';
	import { automationRuns } from '$lib/stores/automationRuns';
	import { timeAgo } from '$lib/utils/relativeTime';

	let { automationId }: { automationId: string } = $props();

	let runs = $derived($automationRuns.runs);
	let loading = $derived($automationRuns.runsLoading);
	let inspectedRun = $derived($automationRuns.inspectedRun);

	// Collapsed by default to preserve density; expanded ids reset when the
	// inspected run changes (stale expansion state from a prior run is noise).
	let expandedNodeIds = $state<Set<string>>(new Set());
	let expandedEventPayload = $state(false);

	$effect(() => {
		inspectedRun;
		expandedNodeIds = new Set();
		expandedEventPayload = false;
	});

	function toggleNode(nodeRowId: string) {
		const next = new Set(expandedNodeIds);
		if (next.has(nodeRowId)) next.delete(nodeRowId);
		else next.add(nodeRowId);
		expandedNodeIds = next;
	}

	function prettyJson(value: unknown): string {
		if (value === undefined || value === null) return '—';
		try {
			return JSON.stringify(value, null, 2);
		} catch {
			return String(value);
		}
	}

	function handleInspect(runId: string) {
		automationRuns.inspectRun(automationId, runId);
	}
</script>

<DetailLayout>
	{#snippet main()}
		<DetailSection label="Runs" padded={loading || runs.length === 0}>
			{#if loading}
				<div class="flex items-center justify-center py-10">
					<Spinner />
				</div>
			{:else if runs.length === 0}
				<EmptyState compact icon="clock" title="No runs yet" />
			{:else}
				<ul class="divide-y divide-line">
					{#each runs as run (run.id)}
						<li>
							<button
								type="button"
								class="w-full text-left px-4 sm:px-5 py-3 hover:bg-surface-2 transition-colors {inspectedRun?.id ===
								run.id
									? 'bg-surface-2'
									: ''}"
								onclick={() => handleInspect(run.id)}
							>
								<div class="flex items-center justify-between gap-2">
									<div class="flex items-center gap-2">
										<RunStatusBadge status={run.status} />
										{#if run.trigger_type}
											<span class="text-xs text-fg-muted">{run.trigger_type}</span>
										{/if}
									</div>
									<div class="flex items-center gap-3 flex-shrink-0">
										{#if run.duration_ms !== undefined && run.duration_ms !== null}
											<span class="font-mono text-xs tabular-nums text-fg-subtle">
												{(run.duration_ms / 1000).toFixed(1)}s
											</span>
										{/if}
										<span class="font-mono text-xs tabular-nums text-fg-subtle">
											{timeAgo(run.started_at)}
										</span>
									</div>
								</div>
							</button>
						</li>
					{/each}
				</ul>
			{/if}
		</DetailSection>
	{/snippet}

	{#snippet aside()}
		<DetailSection label="Run detail">
			{#if !inspectedRun}
				<p class="text-xs text-fg-subtle">Select a run to see its node-by-node detail.</p>
			{:else}
				<div class="space-y-1.5">
					{#if inspectedRun.event_payload !== undefined && inspectedRun.event_payload !== null}
						<div>
							<button
								type="button"
								class="w-full flex items-center justify-between gap-2 text-2xs font-mono font-semibold uppercase tracking-wide text-fg-subtle py-1 hover:text-fg-muted transition-colors"
								onclick={() => (expandedEventPayload = !expandedEventPayload)}
							>
								<span>Event payload</span>
								<span class="text-fg-subtle">{expandedEventPayload ? '−' : '+'}</span>
							</button>
							{#if expandedEventPayload}
								<pre
									class="font-mono text-2xs {DETAIL_INSET_CLASS} p-2 overflow-x-auto whitespace-pre-wrap break-words text-fg-muted">{prettyJson(
										inspectedRun.event_payload
									)}</pre>
							{/if}
						</div>
					{/if}

					<p class="text-2xs font-mono font-semibold uppercase tracking-wide text-fg-subtle mb-1">
						Node statuses
					</p>
					{#each inspectedRun.nodes as node (node.id)}
						<div class="text-xs">
							<button
								type="button"
								class="w-full flex items-center justify-between gap-2 py-0.5 hover:bg-surface-2 rounded transition-colors"
								onclick={() => toggleNode(node.id)}
							>
								<span class="flex items-center gap-1.5 min-w-0">
									<span class="text-fg-subtle text-2xs">{expandedNodeIds.has(node.id) ? '−' : '+'}</span>
									<span class="font-mono text-fg-muted truncate">{node.node_id}</span>
								</span>
								<span class="flex items-center gap-1.5 flex-shrink-0">
									{#if node.duration_ms !== undefined && node.duration_ms !== null}
										<span class="font-mono tabular-nums text-2xs text-fg-subtle"
											>{(node.duration_ms / 1000).toFixed(1)}s</span
										>
									{/if}
									<RunStatusBadge status={node.status} dot={false} />
								</span>
							</button>

							{#if expandedNodeIds.has(node.id)}
								<div class="pl-4 pb-2 space-y-1.5">
									<div>
										<p class="text-2xs text-fg-subtle mb-0.5">Input</p>
										<pre
											class="font-mono text-2xs {DETAIL_INSET_CLASS} p-2 overflow-x-auto whitespace-pre-wrap break-words text-fg-muted">{prettyJson(
												node.input
											)}</pre>
									</div>
									<div>
										<p class="text-2xs text-fg-subtle mb-0.5">Output</p>
										<pre
											class="font-mono text-2xs {DETAIL_INSET_CLASS} p-2 overflow-x-auto whitespace-pre-wrap break-words text-fg-muted">{prettyJson(
												node.output
											)}</pre>
									</div>
									{#if node.error}
										<div>
											<p class="text-2xs text-fg-subtle mb-0.5">Error</p>
											<pre
												class="font-mono text-2xs {DETAIL_INSET_CLASS} p-2 overflow-x-auto whitespace-pre-wrap break-words text-danger">{node.error}</pre>
										</div>
									{/if}
								</div>
							{/if}
						</div>
					{/each}
				</div>
			{/if}
		</DetailSection>
	{/snippet}
</DetailLayout>
