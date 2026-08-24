<script lang="ts">
	// The gantt the drawer never had room for: one row per pipe, bars
	// positioned/scaled on the run's own time axis. Layout math lives in
	// `buildPipeTimeline` (runReport.ts) so it's unit-testable without
	// mounting; this component only lays the numbers out as CSS percentages.
	import type { RunReport } from '$lib/services/admin-api';
	import { buildPipeTimeline, type GroupedStatusEntry } from './runReport';
	import { formatDurationMs } from '$lib/components/generation-panel/barState';

	let {
		report,
		groupedEntries,
		runStart,
		runEnd,
		failedPipeKey = null
	}: {
		report: RunReport;
		groupedEntries: GroupedStatusEntry[];
		runStart: string;
		runEnd: string;
		failedPipeKey?: string | null;
	} = $props();

	let timeline = $derived(buildPipeTimeline(report.pipe_timers ?? {}, groupedEntries, runStart, runEnd, { failedPipeKey }));
	let runStartMs = $derived(new Date(runStart).getTime());

	function absolute(atMs: number): string {
		const date = new Date(atMs);
		if (Number.isNaN(date.getTime())) return '-';
		return date.toLocaleTimeString(undefined, { hour12: false });
	}
</script>

<div class="bg-surface-1 border border-line rounded-lg p-4 sm:p-5">
	<div class="flex items-center justify-between mb-4">
		<h3 class="text-sm font-medium text-fg">Pipe timeline</h3>
		<span class="font-mono text-2xs tabular-nums text-fg-subtle uppercase tracking-[0.07em]">
			{formatDurationMs(timeline.spanMs)} total
		</span>
	</div>

	<div class="grid" style="grid-template-columns: minmax(96px, 12ch) 1fr;">
		{#each timeline.bars as bar (bar.pipeKey)}
			<div class="flex items-center pr-3 py-1.5 text-xs font-mono text-fg-muted truncate" title={bar.pipeLabel}>
				{bar.pipeLabel}
			</div>
			<div class="relative h-7 py-1.5 border-l border-line">
				<div class="relative h-full rounded bg-surface-2/60">
					{#each timeline.axisTicks as tick (tick.pct)}
						<div class="absolute inset-y-0 w-px bg-viz-grid" style="left: {tick.pct}%;"></div>
					{/each}

					{#if bar.widthPct > 0}
						<div
							class="group absolute top-1/2 -translate-y-1/2 h-1.5 rounded-full transition-[filter] duration-100 {bar.failed
								? 'bg-danger'
								: 'bg-signal'} {bar.running ? 'timeline-bar--running' : ''}"
							style="left: {bar.startPct}%; width: {bar.widthPct}%;"
						>
							<div
								class="pointer-events-none absolute bottom-full left-0 mb-1.5 hidden group-hover:flex group-focus-within:flex flex-col gap-0.5 whitespace-nowrap bg-surface-3 border border-line-strong rounded px-2 py-1 shadow-floating z-10"
							>
								<span class="text-2xs font-mono text-fg">{bar.pipeLabel}</span>
								<span class="text-2xs font-mono tabular-nums text-fg-muted">
									{absolute(runStartMs + (bar.startPct / 100) * timeline.spanMs)}{bar.durationMs !== null
										? ` · ${formatDurationMs(bar.durationMs)}`
										: bar.running
											? ' · running'
											: ''}
								</span>
							</div>
							<button
								type="button"
								class="absolute inset-0 w-full h-full cursor-default"
								aria-label="{bar.pipeLabel} — {bar.durationMs !== null ? formatDurationMs(bar.durationMs) : bar.running ? 'still running' : 'unknown duration'}"
							></button>
						</div>
						{#if bar.durationMs !== null}
							<span
								class="absolute top-1/2 -translate-y-1/2 text-2xs font-mono tabular-nums text-fg-subtle whitespace-nowrap"
								style="left: calc({bar.startPct + bar.widthPct}% + 6px);"
							>
								{formatDurationMs(bar.durationMs)}
							</span>
						{/if}
					{/if}

					{#each bar.ticks as tick, i (i)}
						<div class="absolute top-0 bottom-0 w-px bg-fg-subtle/50" style="left: {tick.pct}%;"></div>
					{/each}
				</div>
			</div>
		{/each}

		<div></div>
		<div class="relative h-4 mt-0.5 border-l border-line">
			{#each timeline.axisTicks as tick (tick.pct)}
				<span
					class="absolute top-0 text-2xs font-mono tabular-nums text-fg-subtle"
					style="left: {tick.pct}%; transform: translateX({tick.pct === 0 ? '0' : tick.pct === 100 ? '-100%' : '-50%'});"
				>
					{tick.label}
				</span>
			{/each}
		</div>
	</div>
</div>

<style>
	@media (prefers-reduced-motion: no-preference) {
		:global(.timeline-bar--running) {
			animation: timeline-pulse 1.6s ease-in-out infinite;
		}
	}
	@keyframes timeline-pulse {
		0%,
		100% {
			opacity: 1;
		}
		50% {
			opacity: 0.55;
		}
	}
</style>
