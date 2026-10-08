<script lang="ts">
	import GenerationDetailsModal from '$lib/components/modals/GenerationDetailsModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Badge, Button, Kbd, Spinner } from '$lib/components/ui';
	import { api } from '$lib/services/api/index';
	import type { GenerationHistoryItem } from '$lib/types/history';
	import type { ActiveGrid, GridCell } from '../compareStore.svelte';
	import GridDetailsCard from './GridDetailsCard.svelte';
	import GridMiniMap from './GridMiniMap.svelte';
	import {
		axisFieldLabel,
		axisTag,
		cellFormPatch,
		cellFormPatchSummary,
		cellStatusLabel,
		formatSeconds,
		gridDimensions,
		cellProgressLabel,
		gridProgress,
		moveCell,
		retryableCount,
		stepCell,
		type MoveDirection
	} from './gridModel';

	let {
		grid,
		index,
		seconds = null,
		onIndex,
		onClose,
		onUse,
		onExport,
		onRetryFailed,
		onReuse
	}: {
		grid: ActiveGrid;
		index: number;
		seconds?: number | null;
		onIndex: (index: number) => void;
		onClose: () => void;
		onUse?: (index: number) => void;
		onExport?: () => void;
		onRetryFailed?: () => void;
		onReuse?: (generation: GenerationHistoryItem) => void;
	} = $props();

	let plain = $state(false);
	let detail = $state<GenerationHistoryItem | null>(null);

	let cell = $derived(grid.cells[index] as GridCell);
	let shownSeconds = $derived(cell.elapsedSeconds ?? seconds);
	let progress = $derived(gridProgress(grid));
	let failed = $derived(retryableCount(grid));
	let preview = $derived(cellFormPatch(grid, cell));
	let usable = $derived(Object.keys(preview.patch).length > 0 || preview.prompt !== null);
	let isImageGrid = $derived(grid.cells.every((candidate) => candidate.mediaType !== 'video'));
	let statusSummary = $derived(
		progress.done === progress.total ? 'All done' : `${progress.done} / ${progress.total} done`
	);

	$effect(() => {
		const id = cell.generationId;
		const status = cell.status;
		if (!id || status === 'empty' || status === 'queued' || status === 'deleted') {
			detail = null;
			return;
		}
		let stale = false;
		api
			.getGenerationById(id, true, true)
			.then((response) => {
				if (!stale && response.success && response.data) detail = response.data as GenerationHistoryItem;
			})
			.catch(() => undefined);
		return () => {
			stale = true;
		};
	});

	function syntheticStatus(status: GridCell['status']): GenerationHistoryItem['status'] {
		if (status === 'queued') return 'pending';
		if (status === 'running') return 'running';
		if (status === 'completed') return 'completed';
		if (status === 'failed') return 'failed';
		return 'cancelled';
	}

	function synthetic(source: GridCell, position: number): GenerationHistoryItem {
		const now = new Date().toISOString();
		return {
			id: source.generationId ?? `cell-${position + 1}`,
			status: syntheticStatus(source.status),
			progress: 0,
			created_at: now,
			updated_at: now,
			form_data: {},
			files: [],
			rating: 0,
			is_favorite: false,
			segments: [],
			error_message: source.error ?? undefined
		};
	}

	let generation = $derived(
		detail && detail.id === cell.generationId ? detail : synthetic(cell, index)
	);
	let axisTags = $derived.by(() => {
		const tags: Record<string, string> = {};
		for (const field of Object.keys(cell.axisValues)) {
			const tag = axisTag(field, grid.config);
			if (tag) tags[field] = tag;
		}
		return tags;
	});

	function navigate(direction: -1 | 1): boolean {
		const next = stepCell(grid, index, direction);
		if (next === index) return false;
		onIndex(next);
		return true;
	}

	const KEY_DIRECTIONS: Record<string, MoveDirection> = {
		ArrowLeft: 'left',
		ArrowRight: 'right',
		ArrowUp: 'up',
		ArrowDown: 'down'
	};

	function handleArrow(key: string): boolean {
		if (plain) return false;
		const direction = KEY_DIRECTIONS[key];
		if (!direction) return false;
		const next = moveCell(grid, index, direction);
		if (next !== index) onIndex(next);
		return true;
	}

	function reuse(event: CustomEvent<GenerationHistoryItem>) {
		onReuse?.(event.detail);
	}
</script>

<GenerationDetailsModal
	isOpen={true}
	{generation}
	title={plain ? 'Generation Details' : 'Compare grid'}
	headerIconName={plain ? 'image' : 'grid'}
	{axisTags}
	axisValues={cell.axisValues}
	onNavigate={(direction) => navigate(direction)}
	hasPrevious={index > 0}
	hasNext={index < grid.cells.length - 1}
	position={{ index: index + 1, total: grid.cells.length }}
	onArrowKey={handleArrow}
	{onClose}
	on:reuse={reuse}
>
	<svelte:fragment slot="stageEmpty">
		<div
			class="absolute inset-0 flex flex-col items-center justify-center gap-3 p-6 text-center"
			data-testid="cell-viewer-empty"
		>
			{#if cell.status === 'running'}
				<Spinner size="lg" />
				<span class="font-mono text-sm tabular-nums text-fg">
					{cell.progress ? cellProgressLabel(cell.progress) : 'Running'}
				</span>
			{:else if cell.status === 'queued'}
				<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">Queued</span>
			{:else if cell.status === 'failed'}
				<span class="font-mono text-2xs uppercase tracking-[0.07em] text-danger">Failed</span>
				{#if cell.error}
					<p class="max-w-md text-sm text-fg-muted">{cell.error}</p>
				{/if}
				<Button variant="secondary" size="sm" icon="refresh" onclick={() => onRetryFailed?.()}>Retry</Button>
			{:else if cell.status === 'deleted'}
				<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">Deleted</span>
				<Button variant="secondary" size="sm" icon="refresh" onclick={() => onRetryFailed?.()}>Retry</Button>
			{:else}
				<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">
					{cellStatusLabel(cell.status)}
				</span>
			{/if}
		</div>
	</svelte:fragment>

	<svelte:fragment slot="afterInformation">
		{#if plain}
			<GridDetailsCard {grid} {index} onOpenGrid={() => (plain = false)} openLabel="Back to compare view" />
		{:else}
			<div class="overflow-hidden rounded-lg bg-surface-2" data-testid="cell-viewer-card">
				<div class="flex items-center justify-between border-b border-line px-3 py-2.5">
					<div class="flex items-center gap-2">
						<Icon name="grid" className="h-4 w-4 text-fg-muted" />
						<h3 class="text-sm font-semibold text-fg">This cell</h3>
					</div>
					<Badge
						variant={cell.status === 'completed'
							? 'success'
							: cell.status === 'failed'
								? 'danger'
								: cell.status === 'running'
									? 'signal'
									: 'neutral'}
						size="sm"
						class="uppercase tracking-wide"
					>
						{cellStatusLabel(cell.status)}
					</Badge>
				</div>
				<div class="divide-y divide-line">
					{#each Object.entries(cell.axisValues) as [field, value] (field)}
						<div class="flex items-center justify-between px-3 py-2">
							<span class="font-mono text-2xs uppercase tracking-wider text-fg-disabled">{axisFieldLabel(field)}</span>
							<span class="font-mono text-xs text-fg" class:tabular-nums={field === 'seed'}>{value}</span>
						</div>
					{/each}
					{#if cell.seed !== null && !('seed' in cell.axisValues)}
						<div class="flex items-center justify-between px-3 py-2">
							<span class="font-mono text-2xs uppercase tracking-wider text-fg-disabled">Seed</span>
							<span class="font-mono text-xs tabular-nums text-fg">
								{cell.seed}
								<span class="text-fg-subtle">{grid.config.lockSeed ? 'locked' : 'random'}</span>
							</span>
						</div>
					{/if}
					{#if shownSeconds !== null}
						<div class="flex items-center justify-between px-3 py-2">
							<span class="font-mono text-2xs uppercase tracking-wider text-fg-disabled">Time</span>
							<span class="font-mono text-xs tabular-nums text-fg">{formatSeconds(shownSeconds)}</span>
						</div>
					{/if}
				</div>
			</div>

			<div class="space-y-1.5">
				<Button
					variant="primary"
					size="md"
					icon="sliders"
					class="w-full"
					onclick={() => onUse?.(index)}
					disabled={!usable}
				>
					Use these settings
				</Button>
				{#if usable}
					<p class="text-xs text-fg-muted" data-testid="use-settings-note">
						Puts {cellFormPatchSummary(preview, cell.axisValues).join(' and ')} into the form.
					</p>
				{/if}
			</div>

			<div class="rounded-lg bg-surface-2 p-3">
				<div class="mb-2 flex items-center justify-between">
					<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">Grid</span>
					<span class="font-mono text-2xs tabular-nums text-fg-subtle">
						{gridDimensions(grid)} · {statusSummary}{failed > 0 ? ` · ${failed} failed` : ''}
					</span>
				</div>
				<GridMiniMap {grid} selectedIndex={index} onSelect={onIndex} />
				<p class="mt-2 flex items-center gap-1.5 text-xs text-fg-subtle">
					<Kbd keys="←" /><Kbd keys="→" /><Kbd keys="↑" /><Kbd keys="↓" />
					<span>move across the grid</span>
				</p>
			</div>

			<div class="grid grid-cols-2 gap-2">
				<Button variant="secondary" size="sm" icon="download" disabled={!isImageGrid} onclick={() => onExport?.()}>
					Export stitched
				</Button>
				<Button variant="secondary" size="sm" icon="refresh" disabled={failed === 0} onclick={() => onRetryFailed?.()}>
					Retry failed{failed > 0 ? ` ${failed}` : ''}
				</Button>
			</div>
			<button
				type="button"
				class="flex items-center gap-1.5 text-sm text-fg-muted hover:text-fg"
				onclick={() => (plain = true)}
			>
				<Icon name="information-circle" className="h-4 w-4" />
				Open generation details
			</button>
		{/if}
	</svelte:fragment>
</GenerationDetailsModal>
