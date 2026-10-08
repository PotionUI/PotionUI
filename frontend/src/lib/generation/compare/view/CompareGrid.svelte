<script lang="ts">
	import { Badge, Button } from '$lib/components/ui';
	import type { ActiveGrid } from '../compareStore.svelte';
	import CompareCell from './CompareCell.svelte';
	import VideoTransport from './VideoTransport.svelte';
	import { resolveCellFiles } from './cellFiles';
	import {
		axisValueLabel,
		computeCellSize,
		createCellTimer,
		gridDimensions,
		gridProgress,
		gridTemplateColumns,
		gridTitle,
		queueOrdinals,
		retryableCount,
		CELL_GAP,
		SCROLLER_PADDING
	} from './gridModel';
	import { createVideoGroup } from './videoSync';

	let {
		grid,
		selectedIndex = null,
		overview = false,
		durations = $bindable(new Map<string, number>()),
		onOpenCell,
		onCancelAll,
		onRetryFailed
	}: {
		grid: ActiveGrid;
		selectedIndex?: number | null;
		overview?: boolean;
		durations?: Map<string, number>;
		onOpenCell?: (index: number) => void;
		onCancelAll?: () => void;
		onRetryFailed?: () => void;
	} = $props();

	const timer = createCellTimer();
	const group = createVideoGroup();

	let contentBox = $state<DOMRectReadOnly | undefined>();
	let cornerHeight = $state(0);
	let imageAspect = $state(1);
	let aspectLocked = $state(false);
	let videoUrls = $state<Record<string, string>>({});
	let resolving = new Set<string>();

	let progress = $derived(gridProgress(grid));
	let failed = $derived(retryableCount(grid));
	let ordinals = $derived(queueOrdinals(grid));
	let isVideoGrid = $derived(grid.cells.some((cell) => cell.mediaType === 'video'));
	let aspect = $derived(isVideoGrid && !aspectLocked ? 16 / 9 : imageAspect);
	let sizing = $derived(
		computeCellSize(
			contentBox?.width ?? 0,
			grid.cols,
			overview
				? { height: contentBox?.height ?? 0, rows: grid.rows, aspect, header: Math.max(0, cornerHeight - SCROLLER_PADDING) }
				: undefined
		)
	);
	let hasPlayable = $derived(
		isVideoGrid && grid.cells.some((cell) => cell.status === 'completed' && videoUrls[cell.generationId ?? ''])
	);
	let allDone = $derived(!progress.active && progress.done === progress.total);

	$effect(() => {
		durations = timer.observe(grid.cells);
	});

	$effect(() => {
		if (!isVideoGrid) return;
		const pending = grid.cells.filter(
			(cell) =>
				cell.status === 'completed' &&
				cell.mediaType === 'video' &&
				cell.generationId &&
				!videoUrls[cell.generationId] &&
				!resolving.has(cell.generationId)
		);
		if (pending.length === 0) return;
		for (const cell of pending) resolving.add(cell.generationId as string);
		void resolveCellFiles({ cells: pending }, 'video').then((files) => {
			const next = { ...videoUrls };
			for (const [id, file] of Object.entries(files)) next[id] = file.url;
			videoUrls = next;
			for (const cell of pending) resolving.delete(cell.generationId as string);
		});
	});

	function lockAspect(next: number) {
		if (aspectLocked || !Number.isFinite(next) || next <= 0) return;
		aspectLocked = true;
		imageAspect = next;
	}
</script>

<section
	class="flex min-h-0 w-full min-w-0 max-w-full flex-col rounded-lg border border-line bg-surface-1"
	class:h-full={overview}
	aria-label="Compare grid"
	data-testid="compare-grid"
	data-grid-cols={grid.cols}
	data-grid-rows={grid.rows}
>
	<header class="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line px-4 py-3" data-testid="compare-header">
		<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">Compare</span>
		<span class="text-sm font-medium text-fg">{gridTitle(grid.config)}</span>
		{#if overview}
			<Badge variant="neutral" size="sm" class="font-mono tabular-nums">{gridDimensions(grid)}</Badge>
		{/if}
		<div
			class="flex h-1.5 min-w-24 max-w-64 flex-1 gap-px"
			role="progressbar"
			aria-label="Grid progress"
			aria-valuemin="0"
			aria-valuemax={progress.total}
			aria-valuenow={progress.done}
			data-testid="compare-progress"
		>
			{#each progress.segments as state, i (i)}
				<span
					data-segment={state}
					class="h-full min-w-px flex-1 {state === 'done'
						? 'bg-signal-solid'
						: state === 'running'
							? 'bg-signal'
							: state === 'failed'
								? 'bg-danger-solid'
								: state === 'queued'
									? 'bg-line-hover'
									: 'bg-line'}"
				></span>
			{/each}
		</div>
		<span class="font-mono text-sm tabular-nums text-fg-muted" data-testid="compare-count">
			{progress.done}/{progress.total}
		</span>
		{#if failed > 0}
			<span data-testid="compare-failed">
				<Badge variant="danger" size="sm" class="font-mono tabular-nums">{failed} failed</Badge>
			</span>
		{/if}
		{#if allDone}
			<Badge variant="success" size="sm" class="uppercase tracking-wide">All done</Badge>
		{/if}
		<div class="ml-auto flex items-center gap-2">
			{#if failed > 0 && !progress.active && onRetryFailed}
				<Button variant="secondary" size="xs" icon="refresh" onclick={() => onRetryFailed?.()}>Retry failed</Button>
			{/if}
			{#if progress.active && onCancelAll}
				<Button variant="ghost" size="xs" icon="close" class="text-danger" onclick={() => onCancelAll?.()}>Cancel all</Button>
			{/if}
		</div>
	</header>

	<div
		bind:contentRect={contentBox}
		class="min-h-0 min-w-0 max-w-full flex-1 overflow-auto bg-canvas p-3"
		class:max-h-[70vh]={!overview}
		data-testid="compare-scroller"
		data-scrolls={sizing.scrolls}
	>
		<div
			class="mx-auto grid w-max"
			style="grid-template-columns: {gridTemplateColumns(grid.cols, sizing.size)}; gap: {CELL_GAP}px"
		>
			<div
				bind:clientHeight={cornerHeight}
				class="sticky left-0 -top-3 z-20 -mt-3 flex flex-col justify-end bg-canvas pb-1 pt-3 font-mono text-2xs uppercase leading-5 tracking-[0.07em] text-fg-subtle"
			>
				<span><span class="text-signal">X</span> {grid.config.x?.label ?? ''}</span>
				{#if grid.config.y}
					<span><span class="text-signal">Y</span> {grid.config.y.label}</span>
				{/if}
			</div>
			{#each { length: grid.cols } as _, x (x)}
				<div
					class="sticky -top-3 z-10 -mt-3 flex items-end justify-center truncate bg-canvas pb-1 pt-3 text-center font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted"
					data-testid="compare-x-label"
				>
					{axisValueLabel(grid.config.x, x)}
				</div>
			{/each}

			{#each { length: grid.rows } as _, y (y)}
				<div
					class="sticky left-0 z-10 flex items-center justify-end bg-canvas pr-2 text-right font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted"
					data-testid="compare-y-label"
				>
					{grid.config.y ? axisValueLabel(grid.config.y, y) : ''}
				</div>
				{#each { length: grid.cols } as _, x (x)}
					{@const index = y * grid.cols + x}
					{@const cell = grid.cells[index]}
					<CompareCell
						{cell}
						{index}
						{aspect}
						{group}
						ordinal={ordinals.get(index) ?? null}
						seconds={cell.elapsedSeconds ?? (cell.generationId ? (durations.get(cell.generationId) ?? null) : null)}
						selected={selectedIndex === index}
						videoUrl={cell.generationId ? (videoUrls[cell.generationId] ?? null) : null}
						onOpen={onOpenCell}
						onRetry={() => onRetryFailed?.()}
						onAspect={lockAspect}
					/>
				{/each}
			{/each}
		</div>
	</div>

	{#if hasPlayable}
		<div class="border-t border-line p-3">
			<VideoTransport {group} />
		</div>
	{/if}
</section>
