<script lang="ts">
	/**
	 * Admin usage-statistics dashboard. All endpoints are scoped by the same
	 * single date-range row; every chart carries a table-view toggle via
	 * ChartCard (the accessibility twin required by the palette).
	 */
	import { onMount } from 'svelte';
	import { api } from '$lib/services/api/index';
	import type {
		StatsOverview,
		StatsTimeseries,
		StatsDurations,
		StatsStorage,
		StatsBreakdown,
		StatsDimension,
		DurationBucket,
		PresetTimingItem,
		PresetResourcesItem
	} from '$lib/services/api/stats';
	import { Button, Input, Spinner, EmptyState, Alert } from '$lib/components/ui';
	import AdminTabShell from './AdminTabShell.svelte';
	import AdminFilterBar from './AdminFilterBar.svelte';
	import {
		StatTile,
		ChartCard,
		TimeSeriesChart,
		RankBarChart,
		HistogramChart,
		RowLimitSelect
	} from '$lib/components/charts';
	import { formatBytes, formatDuration, formatCount } from '$lib/utils/format';
	import { statsRowLimits, type StatsSection } from '$lib/stores/statsRowLimits';

	/** MB values (peak_vram_mb, peak_ram_mb, ...) reuse the byte formatter. */
	function formatMb(mb: number | null): string {
		return mb === null ? '—' : formatBytes(mb * 1024 * 1024);
	}

	let dateFrom = $state('');
	let dateTo = $state('');

	let loading = $state(true);
	let error = $state<string | null>(null);

	let overview = $state<StatsOverview | null>(null);
	let timeseries = $state<StatsTimeseries | null>(null);
	let durations = $state<StatsDurations | null>(null);
	let storage = $state<StatsStorage | null>(null);
	let presets = $state<StatsBreakdown | null>(null);
	let models = $state<StatsBreakdown | null>(null);
	let samplers = $state<StatsBreakdown | null>(null);
	let schedulers = $state<StatsBreakdown | null>(null);
	let resolutions = $state<StatsBreakdown | null>(null);
	let steps = $state<StatsBreakdown | null>(null);
	let cfgs = $state<StatsBreakdown | null>(null);
	let denoises = $state<StatsBreakdown | null>(null);
	// Durable, generation-independent stats: keep reporting after the underlying
	// generation is deleted.
	let presetTiming = $state<PresetTimingItem[] | null>(null);
	let presetResources = $state<PresetResourcesItem[] | null>(null);

	function currentRange() {
		return {
			from: dateFrom || undefined,
			to: dateTo || undefined
		};
	}

	async function load() {
		loading = true;
		error = null;
		const range = currentRange();
		const limits = $statsRowLimits;
		try {
			const [
				overviewRes,
				timeseriesRes,
				durationsRes,
				storageRes,
				presetsRes,
				modelsRes,
				samplersRes,
				schedulersRes,
				resolutionsRes,
				stepsRes,
				cfgsRes,
				denoisesRes,
				presetTimingRes,
				presetResourcesRes
			] = await Promise.all([
				api.getStatsOverview(range),
				api.getStatsTimeseries('count', 'day', range),
				api.getStatsDurations(range),
				api.getStatsStorage(range, 'day', limits.storage),
				api.getStatsBreakdown('preset', limits.presets, range),
				api.getStatsBreakdown('model', limits.models, range),
				api.getStatsBreakdown('sampler', limits.samplers, range),
				api.getStatsBreakdown('scheduler', limits.schedulers, range),
				api.getStatsBreakdown('resolution', limits.resolutions, range),
				api.getStatsBreakdown('steps', limits.steps, range),
				api.getStatsBreakdown('cfg', limits.cfgs, range),
				api.getStatsBreakdown('denoise', limits.denoises, range),
				api.getStatsPresetTiming(limits.presetTiming),
				api.getStatsPresetResources(limits.presetResources)
			]);

			overview = overviewRes.success ? overviewRes.data ?? null : null;
			timeseries = timeseriesRes.success ? timeseriesRes.data ?? null : null;
			durations = durationsRes.success ? durationsRes.data ?? null : null;
			storage = storageRes.success ? storageRes.data ?? null : null;
			presets = presetsRes.success ? presetsRes.data ?? null : null;
			models = modelsRes.success ? modelsRes.data ?? null : null;
			samplers = samplersRes.success ? samplersRes.data ?? null : null;
			schedulers = schedulersRes.success ? schedulersRes.data ?? null : null;
			resolutions = resolutionsRes.success ? resolutionsRes.data ?? null : null;
			steps = stepsRes.success ? stepsRes.data ?? null : null;
			cfgs = cfgsRes.success ? cfgsRes.data ?? null : null;
			denoises = denoisesRes.success ? denoisesRes.data ?? null : null;
			presetTiming = presetTimingRes.success ? presetTimingRes.data?.items ?? null : null;
			presetResources = presetResourcesRes.success ? presetResourcesRes.data?.items ?? null : null;

			if (!overviewRes.success) {
				error = overviewRes.message || 'Failed to load statistics';
			}
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to load statistics';
		} finally {
			loading = false;
		}
	}

	// --- per-section row-limit changes: refetch only the affected section,
	// not the whole page (a per-section control, not a global reload trigger). ---

	async function reloadBreakdown(dimension: StatsDimension, section: StatsSection, limit: number) {
		const res = await api.getStatsBreakdown(dimension, limit, currentRange());
		const data = res.success ? (res.data ?? null) : null;
		if (section === 'presets') presets = data;
		else if (section === 'models') models = data;
		else if (section === 'samplers') samplers = data;
		else if (section === 'schedulers') schedulers = data;
		else if (section === 'resolutions') resolutions = data;
		else if (section === 'steps') steps = data;
		else if (section === 'cfgs') cfgs = data;
		else if (section === 'denoises') denoises = data;
	}

	async function reloadStorage(limit: number) {
		const res = await api.getStatsStorage(currentRange(), 'day', limit);
		storage = res.success ? (res.data ?? null) : null;
	}

	async function reloadPresetTiming(limit: number) {
		const res = await api.getStatsPresetTiming(limit);
		presetTiming = res.success ? (res.data?.items ?? null) : null;
	}

	async function reloadPresetResources(limit: number) {
		const res = await api.getStatsPresetResources(limit);
		presetResources = res.success ? (res.data?.items ?? null) : null;
	}

	onMount(load);

	function durationBucketLabel(b: DurationBucket): string {
		return b.upper_s === null ? `${b.lower_s}s+` : `${b.lower_s}-${b.upper_s}s`;
	}

	let durationHistogramData = $derived(
		(durations?.buckets ?? []).map((b) => ({ label: durationBucketLabel(b), count: b.count }))
	);

	function bucketLabelForMs(ms: number | null): string | null {
		if (ms === null || !durations) return null;
		const s = ms / 1000;
		const match = durations.buckets.find((b) =>
			b.upper_s === null ? s >= b.lower_s : s >= b.lower_s && s < b.upper_s
		);
		return match ? durationBucketLabel(match) : null;
	}

	let durationMarkers = $derived(
		[
			{ label: 'p50', at: bucketLabelForMs(durations?.p50_ms ?? null) },
			{ label: 'p95', at: bucketLabelForMs(durations?.p95_ms ?? null) }
		].filter((m): m is { label: string; at: string } => m.at !== null)
	);

	// Generations with no recorded duration are excluded from every bin rather than counted as
	// zero (pre-migration rows, and any row whose completion time could not be trusted). Saying
	// so is the difference between "nothing took 0s" and "we don't know".
	let durationSubtitle = $derived(
		[
			`p50 ${durations?.p50_ms != null ? formatDuration(durations.p50_ms) : '—'}`,
			`p95 ${durations?.p95_ms != null ? formatDuration(durations.p95_ms) : '—'}`,
			...(durations?.unknown ? [`${formatCount(durations.unknown)} with unknown duration`] : [])
		].join(' · ')
	);

	let storageMaxTotal = $derived(
		Math.max(
			1,
			...((storage?.over_time ?? []).map((d) => d.image_bytes + d.video_bytes) ?? [0])
		)
	);
</script>

<div class="space-y-4">
	<AdminTabShell title="Stats" icon="gauge" />

	<!-- Date-range labels aren't paired via `for`/`id` — AdminFilterBar renders
	     `filters` twice (inline at lg+, again inside the below-lg popover), and
	     duplicate ids would break label association whenever the popover is
	     open on a narrow screen. -->
	{#snippet dateRangeFilters()}
		<div class="flex items-end gap-2 flex-wrap">
			<div>
				<span class="block text-xs font-medium text-fg-muted mb-1">From</span>
				<Input type="date" bind:value={dateFrom} class="w-40" aria-label="From date" />
			</div>
			<div>
				<span class="block text-xs font-medium text-fg-muted mb-1">To</span>
				<Input type="date" bind:value={dateTo} class="w-40" aria-label="To date" />
			</div>
			<Button variant="primary" size="sm" onclick={load} disabled={loading}>Apply</Button>
		</div>
	{/snippet}
	{#snippet dateRangeTrailing()}
		{#if loading}<Spinner size="sm" />{/if}
	{/snippet}

	<AdminFilterBar
		filters={dateRangeFilters}
		trailing={dateRangeTrailing}
		activeCount={Number(!!dateFrom) + Number(!!dateTo)}
		onClear={() => {
			dateFrom = '';
			dateTo = '';
			load();
		}}
	/>

	{#if error}
		<Alert variant="danger" density="compact" live="polite">{error}</Alert>
	{/if}

	{#if overview}
		<!-- KPI row -->
		<div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
			<StatTile label="Generations" value={formatCount(overview.total_generations)} hero />
			<StatTile
				label="Completed"
				value={formatCount(overview.completed)}
				hint="{overview.total_generations
					? Math.round((overview.completed / overview.total_generations) * 100)
					: 0}% of total"
			/>
			<StatTile
				label="Failed"
				value={formatCount(overview.failed)}
				hint="{overview.total_generations
					? Math.round((overview.failed / overview.total_generations) * 100)
					: 0}% of total"
			/>
			<StatTile label="Distinct models" value={formatCount(overview.distinct_models)} />
			<StatTile
				label="Outputs"
				value={formatCount(overview.total_outputs)}
				hint={formatBytes(overview.total_bytes)}
			/>
			<StatTile
				label="Median duration"
				value={overview.median_duration_ms !== null
					? formatDuration(overview.median_duration_ms)
					: '—'}
				hint={overview.p95_duration_ms !== null
					? `p95 ${formatDuration(overview.p95_duration_ms)}`
					: undefined}
			/>
		</div>
	{/if}

	<div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
		{#if timeseries}
			<ChartCard
				title="Generations over time"
				subtitle="Daily count"
				tableData={timeseries.points}
				tableColumns={[
					{ key: 'bucket', label: 'Date' },
					{ key: 'value', label: 'Count', align: 'right', format: (v) => formatCount(Number(v)) }
				]}
			>
				<TimeSeriesChart
					data={timeseries.points}
					label="Generations"
					valueFormat={(n) => formatCount(Math.round(n))}
				/>
			</ChartCard>
		{/if}

		{#if durations}
			<ChartCard
				title="Duration distribution"
				subtitle={durationSubtitle}
				tableData={durationHistogramData}
				tableColumns={[
					{ key: 'label', label: 'Bucket' },
					{ key: 'count', label: 'Count', align: 'right', format: (v) => formatCount(Number(v)) }
				]}
			>
				<HistogramChart
					data={durationHistogramData}
					markers={durationMarkers}
					valueFormat={(n) => formatCount(Math.round(n))}
				/>
			</ChartCard>
		{/if}

		{#if presets}
			<ChartCard
				title="Top presets"
				subtitle="By generation count"
				tableData={presets.items}
				tableColumns={[
					{ key: 'label', label: 'Preset' },
					{ key: 'count', label: 'Count', align: 'right', format: (v) => formatCount(Number(v)) }
				]}
			>
				{#snippet headerExtra()}
					<RowLimitSelect
						section="presets"
						value={$statsRowLimits.presets}
						onchange={(limit) => reloadBreakdown('preset', 'presets', limit)}
					/>
				{/snippet}
				<RankBarChart
					data={presets.items}
					colorBy="single"
					valueFormat={(n) => formatCount(Math.round(n))}
				/>
			</ChartCard>
		{/if}

		{#if models}
			<ChartCard
				title="Top models"
				subtitle="Colored by model type"
				tableData={models.items}
				tableColumns={[
					{ key: 'label', label: 'Model' },
					{ key: 'group', label: 'Type' },
					{ key: 'count', label: 'Count', align: 'right', format: (v) => formatCount(Number(v)) }
				]}
			>
				{#snippet headerExtra()}
					<RowLimitSelect
						section="models"
						value={$statsRowLimits.models}
						onchange={(limit) => reloadBreakdown('model', 'models', limit)}
					/>
				{/snippet}
				<RankBarChart
					data={models.items}
					colorBy="group"
					valueFormat={(n) => formatCount(Math.round(n))}
				/>
			</ChartCard>
		{/if}

		{#if storage}
			<ChartCard
				title="Storage over time"
				subtitle="Image vs. video bytes — the list the row limit controls"
				tableData={storage.over_time}
				tableColumns={[
					{ key: 'bucket', label: 'Date' },
					{
						key: 'image_bytes',
						label: 'Image',
						align: 'right',
						format: (v) => formatBytes(Number(v))
					},
					{
						key: 'video_bytes',
						label: 'Video',
						align: 'right',
						format: (v) => formatBytes(Number(v))
					}
				]}
			>
				{#snippet headerExtra()}
					<RowLimitSelect
						section="storage"
						value={$statsRowLimits.storage}
						onchange={(limit) => reloadStorage(limit)}
					/>
				{/snippet}
				<div class="flex flex-col gap-1">
					<div class="mb-2 flex items-center gap-3 text-xs text-fg-muted">
						<span class="flex items-center gap-1.5">
							<span class="inline-block h-2 w-2 rounded-full" style="background: rgb(var(--viz-1));"
							></span>
							Image
						</span>
						<span class="flex items-center gap-1.5">
							<span class="inline-block h-2 w-2 rounded-full" style="background: rgb(var(--viz-2));"
							></span>
							Video
						</span>
					</div>
					{#each storage.over_time as bucket (bucket.bucket)}
						{@const total = bucket.image_bytes + bucket.video_bytes}
						<div class="flex items-center gap-2">
							<div class="w-20 shrink-0 truncate text-xs text-fg-muted">{bucket.bucket}</div>
							<div class="relative h-4 flex-1 min-w-0 flex overflow-hidden rounded">
								<div
									style="width: {(bucket.image_bytes / storageMaxTotal) * 100}%; background: rgb(var(--viz-1));"
								></div>
								<div
									style="width: {(bucket.video_bytes / storageMaxTotal) * 100}%; background: rgb(var(--viz-2));"
								></div>
							</div>
							<div class="w-16 shrink-0 text-right font-mono text-xs tabular-nums text-fg">
								{formatBytes(total)}
							</div>
						</div>
					{/each}
				</div>
			</ChartCard>
		{/if}

		{#if resolutions}
			<ChartCard
				title="Top resolutions"
				subtitle="By generation count"
				tableData={resolutions.items}
				tableColumns={[
					{ key: 'label', label: 'Resolution' },
					{ key: 'count', label: 'Count', align: 'right', format: (v) => formatCount(Number(v)) }
				]}
			>
				{#snippet headerExtra()}
					<RowLimitSelect
						section="resolutions"
						value={$statsRowLimits.resolutions}
						onchange={(limit) => reloadBreakdown('resolution', 'resolutions', limit)}
					/>
				{/snippet}
				<RankBarChart
					data={resolutions.items}
					colorBy="single"
					valueFormat={(n) => formatCount(Math.round(n))}
				/>
			</ChartCard>
		{/if}
	</div>

	<h3 class="text-sm font-semibold text-fg mt-2">Generation parameters</h3>
	<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
		{#if samplers}
			<ChartCard
				title="Samplers"
				tableData={samplers.items}
				tableColumns={[
					{ key: 'label', label: 'Sampler' },
					{ key: 'count', label: 'Count', align: 'right', format: (v) => formatCount(Number(v)) }
				]}
			>
				{#snippet headerExtra()}
					<RowLimitSelect
						section="samplers"
						value={$statsRowLimits.samplers}
						onchange={(limit) => reloadBreakdown('sampler', 'samplers', limit)}
					/>
				{/snippet}
				<RankBarChart data={samplers.items} colorBy="single" />
			</ChartCard>
		{/if}
		{#if schedulers}
			<ChartCard
				title="Schedulers"
				tableData={schedulers.items}
				tableColumns={[
					{ key: 'label', label: 'Scheduler' },
					{ key: 'count', label: 'Count', align: 'right', format: (v) => formatCount(Number(v)) }
				]}
			>
				{#snippet headerExtra()}
					<RowLimitSelect
						section="schedulers"
						value={$statsRowLimits.schedulers}
						onchange={(limit) => reloadBreakdown('scheduler', 'schedulers', limit)}
					/>
				{/snippet}
				<RankBarChart data={schedulers.items} colorBy="single" />
			</ChartCard>
		{/if}
		{#if steps}
			<ChartCard
				title="Steps"
				tableData={steps.items}
				tableColumns={[
					{ key: 'label', label: 'Steps' },
					{ key: 'count', label: 'Count', align: 'right', format: (v) => formatCount(Number(v)) }
				]}
			>
				{#snippet headerExtra()}
					<RowLimitSelect
						section="steps"
						value={$statsRowLimits.steps}
						onchange={(limit) => reloadBreakdown('steps', 'steps', limit)}
					/>
				{/snippet}
				<RankBarChart data={steps.items} colorBy="single" />
			</ChartCard>
		{/if}
		{#if cfgs}
			<ChartCard
				title="CFG scale"
				tableData={cfgs.items}
				tableColumns={[
					{ key: 'label', label: 'CFG' },
					{ key: 'count', label: 'Count', align: 'right', format: (v) => formatCount(Number(v)) }
				]}
			>
				{#snippet headerExtra()}
					<RowLimitSelect
						section="cfgs"
						value={$statsRowLimits.cfgs}
						onchange={(limit) => reloadBreakdown('cfg', 'cfgs', limit)}
					/>
				{/snippet}
				<RankBarChart data={cfgs.items} colorBy="single" />
			</ChartCard>
		{/if}
		{#if denoises}
			<ChartCard
				title="Denoise"
				tableData={denoises.items}
				tableColumns={[
					{ key: 'label', label: 'Denoise' },
					{ key: 'count', label: 'Count', align: 'right', format: (v) => formatCount(Number(v)) }
				]}
			>
				{#snippet headerExtra()}
					<RowLimitSelect
						section="denoises"
						value={$statsRowLimits.denoises}
						onchange={(limit) => reloadBreakdown('denoise', 'denoises', limit)}
					/>
				{/snippet}
				<RankBarChart data={denoises.items} colorBy="single" />
			</ChartCard>
		{/if}
	</div>

	<h3 class="text-sm font-semibold text-fg mt-2">Per-preset performance (durable)</h3>
	<div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
		<div class="rounded-lg border border-line bg-surface-1 shadow-raised p-4">
			<div class="flex items-start justify-between gap-2 mb-3">
				<div class="min-w-0">
					<h4 class="text-sm font-semibold text-fg truncate">Cold vs. warm start</h4>
					<p class="mt-0.5 text-xs text-fg-muted truncate">
						Cold = at least one model had to load from disk. Warm = every model came from cache.
					</p>
				</div>
				<RowLimitSelect
					section="presetTiming"
					value={$statsRowLimits.presetTiming}
					onchange={(limit) => reloadPresetTiming(limit)}
				/>
			</div>
			{#if presetTiming && presetTiming.length > 0}
				<div class="overflow-x-auto">
					<table class="w-full text-xs">
						<thead>
							<tr class="border-b border-line">
								<th class="py-1.5 px-2 text-left font-medium text-fg-subtle">Preset</th>
								<th class="py-1.5 px-2 text-right font-medium text-fg-subtle">Runs</th>
								<th class="py-1.5 px-2 text-right font-medium text-fg-subtle">Cold</th>
								<th class="py-1.5 px-2 text-right font-medium text-fg-subtle">Warm</th>
								<th class="py-1.5 px-2 text-right font-medium text-fg-subtle">Avg cold</th>
								<th class="py-1.5 px-2 text-right font-medium text-fg-subtle">Avg warm</th>
							</tr>
						</thead>
						<tbody>
							{#each presetTiming as item (item.preset_id)}
								<tr class="border-b border-line/50 last:border-0">
									<td class="py-1.5 px-2 text-fg truncate max-w-40" title={item.preset_id}
										>{item.preset_name}</td
									>
									<td class="py-1.5 px-2 text-right font-mono tabular-nums text-fg"
										>{formatCount(item.total_runs)}</td
									>
									<td class="py-1.5 px-2 text-right font-mono tabular-nums text-fg"
										>{formatCount(item.cold_runs)}</td
									>
									<td class="py-1.5 px-2 text-right font-mono tabular-nums text-fg"
										>{formatCount(item.warm_runs)}</td
									>
									<td class="py-1.5 px-2 text-right font-mono tabular-nums text-fg-muted">
										{item.avg_cold_duration_ms !== null
											? formatDuration(item.avg_cold_duration_ms)
											: '—'}
									</td>
									<td class="py-1.5 px-2 text-right font-mono tabular-nums text-fg-muted">
										{item.avg_warm_duration_ms !== null
											? formatDuration(item.avg_warm_duration_ms)
											: '—'}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{:else}
				<EmptyState
					icon="hourglass"
					title="No stats yet"
					description="This fills in as generations complete."
					compact
				/>
			{/if}
		</div>

		<div class="rounded-lg border border-line bg-surface-1 shadow-raised p-4">
			<div class="flex items-start justify-between gap-2 mb-3">
				<div class="min-w-0">
					<h4 class="text-sm font-semibold text-fg truncate">Resources per preset</h4>
					<p class="mt-0.5 text-xs text-fg-muted truncate">Peak and average VRAM/RAM/CPU.</p>
				</div>
				<RowLimitSelect
					section="presetResources"
					value={$statsRowLimits.presetResources}
					onchange={(limit) => reloadPresetResources(limit)}
				/>
			</div>
			{#if presetResources && presetResources.length > 0}
				<div class="overflow-x-auto">
					<table class="w-full text-xs">
						<thead>
							<tr class="border-b border-line">
								<th class="py-1.5 px-2 text-left font-medium text-fg-subtle">Preset</th>
								<th class="py-1.5 px-2 text-right font-medium text-fg-subtle">Peak VRAM</th>
								<th class="py-1.5 px-2 text-right font-medium text-fg-subtle">Avg VRAM</th>
								<th class="py-1.5 px-2 text-right font-medium text-fg-subtle">Peak RAM</th>
								<th class="py-1.5 px-2 text-right font-medium text-fg-subtle">Avg CPU</th>
							</tr>
						</thead>
						<tbody>
							{#each presetResources as item (item.preset_id)}
								<tr class="border-b border-line/50 last:border-0">
									<td class="py-1.5 px-2 text-fg truncate max-w-40" title={item.preset_id}
										>{item.preset_name}</td
									>
									<td class="py-1.5 px-2 text-right font-mono tabular-nums text-fg"
										>{formatMb(item.peak_vram_mb)}</td
									>
									<td class="py-1.5 px-2 text-right font-mono tabular-nums text-fg-muted"
										>{formatMb(item.avg_vram_mb)}</td
									>
									<td class="py-1.5 px-2 text-right font-mono tabular-nums text-fg"
										>{formatMb(item.peak_ram_mb)}</td
									>
									<td class="py-1.5 px-2 text-right font-mono tabular-nums text-fg-muted">
										{item.avg_cpu_percent !== null ? `${Math.round(item.avg_cpu_percent)}%` : '—'}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{:else}
				<EmptyState
					icon="cpu"
					title="No stats yet"
					description="This fills in as generations complete."
					compact
				/>
			{/if}
		</div>
	</div>
</div>
