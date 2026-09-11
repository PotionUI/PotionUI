<script lang="ts">
	import { api } from '$lib/services/api';
	import type { PresetStyle } from '$lib/types/api';
	import { categoryOptions, filterStyles } from '$lib/prompt/styleFilter';
	import { storage } from '$lib/utils/storage';
	import BaseModal from './modals/BaseModal.svelte';
	import ConfirmFooter from './modals/ConfirmFooter.svelte';
	import { createConfirmSettlementGate, getConfirmKeyboardAction } from './modals/confirmKeyboard';
	import Icon from './Icon.svelte';
	import { Input, SegmentedControl } from '$lib/components/ui';

	type PickerSize = 'small' | 'big';

	const SIZE_STORAGE_KEY = 'potionui-styles-picker-size';

	let {
		isOpen = false,
		presetId,
		styles = [],
		/** The style id currently applied to the prompt, when it belongs to `presetId` — the
		 *  caller derives this from the tagged segments via `appliedStyleTag` in styleSegments.ts. */
		appliedStyleId = null,
		onClose,
		onApply,
		onClear
	}: {
		isOpen?: boolean;
		presetId: string;
		styles?: PresetStyle[];
		appliedStyleId?: string | null;
		onClose: () => void;
		onApply: (style: PresetStyle) => void;
		onClear: () => void;
	} = $props();

	function loadStoredSize(): PickerSize {
		try {
			return storage.get(SIZE_STORAGE_KEY) === 'big' ? 'big' : 'small';
		} catch {
			return 'small';
		}
	}

	function persistSize(next: PickerSize) {
		try {
			storage.set(SIZE_STORAGE_KEY, next);
		} catch {
			// Best-effort — a blocked/private store just won't remember the choice.
		}
	}

	let selectedId = $state<string | null>(null);
	let categoryFilter = $state('all');
	let textFilter = $state('');
	let size = $state<PickerSize>(loadStoredSize());
	let gridEl: HTMLDivElement | undefined = $state();
	let gridWidth = $state(0);
	let innerWidth = $state(1024);
	let wasOpen = false;

	const settlementGate = createConfirmSettlementGate();

	$effect(() => {
		if (isOpen && !wasOpen) {
			selectedId = appliedStyleId;
			categoryFilter = 'all';
			textFilter = '';
			settlementGate.reset();
		}
		wasOpen = isOpen;
	});

	let categories = $derived(categoryOptions(styles));
	let visibleStyles = $derived(filterStyles(styles, categoryFilter, textFilter));
	let selectedStyle = $derived(styles.find((s) => s.id === selectedId) ?? null);
	let appliedStyle = $derived(styles.find((s) => s.id === appliedStyleId) ?? null);
	let isRemoving = $derived(!!selectedStyle && selectedStyle.id === appliedStyleId);
	let confirmLabel = $derived(isRemoving ? 'Remove style' : 'Apply style');
	let summary = $derived(buildSummary(selectedStyle, appliedStyle, isRemoving));

	// Mirrors the small-size `grid-cols-*` breakpoints below, so arrow-key
	// navigation moves one visual row at a time instead of jumping the flat
	// list. Big mode's `auto-fill` columns have no fixed breakpoint ladder, so
	// its count comes from the grid's own measured width instead.
	let smallColumns = $derived(innerWidth >= 1024 ? 5 : innerWidth >= 768 ? 4 : innerWidth >= 640 ? 3 : 2);
	let bigColumns = $derived(gridWidth > 0 ? Math.max(1, Math.floor(gridWidth / (320 + 16))) : 1);
	let columns = $derived(size === 'big' ? bigColumns : smallColumns);

	function segmentParts(style: PresetStyle): string[] {
		return style.negative ? ['start', 'end', 'negative'] : ['start', 'end'];
	}

	function buildSummary(selected: PresetStyle | null, applied: PresetStyle | null, removing: boolean): string {
		if (!selected) return '';
		if (removing) {
			const parts = segmentParts(selected);
			return `Removes ${parts.length} segments · ${parts.join(', ')}`;
		}
		if (applied && applied.id !== selected.id) {
			return `Replaces "${applied.name}"`;
		}
		const parts = segmentParts(selected);
		return `Adds ${parts.length} segments · ${parts.join(', ')}`;
	}

	function tileId(style: PresetStyle): string {
		return `style-tile-${style.id}`;
	}

	function previewSrc(style: PresetStyle): string | null {
		if (!style.preview) return null;
		// Small renders at the existing thumbnail size; Big shows the
		// pre-rendered preview at its own native resolution (no `size=`) rather
		// than requesting the `medium`/`large` render tiers, which upscale past
		// that native size on every preset — see the picker rework's report.
		return size === 'small' ? api.getPresetAssetURL(presetId, style.preview, 'small') : api.getPresetAssetURL(presetId, style.preview);
	}

	function selectTile(style: PresetStyle) {
		selectedId = style.id;
	}

	// The actual commit (apply/clear + close) — always routed through the
	// settlement gate so a keyboard Enter racing a mouse click can't fire it
	// twice for the same tile.
	function commitStyle(style: PresetStyle) {
		if (style.id === appliedStyleId) onClear();
		else onApply(style);
		onClose();
	}

	function commitTile(style: PresetStyle) {
		selectedId = style.id;
		settlementGate.settle(() => commitStyle(style));
	}

	function commitSelected() {
		const style = selectedStyle;
		if (!style) return; // ineligible — leaves the gate unsettled for a later, real attempt
		settlementGate.settle(() => commitStyle(style));
	}

	function setSize(id: string) {
		size = id === 'big' ? 'big' : 'small';
		persistSize(size);
	}

	function handleCancel() {
		settlementGate.settle(onClose);
	}

	function handleKeydown(event: KeyboardEvent) {
		if (!isOpen) return;
		const { action, suppress } = getConfirmKeyboardAction(event);
		if (action === 'cancel') handleCancel();
		else if (action === 'confirm') commitSelected();
		if (suppress) event.preventDefault();
	}

	function handleGridKeydown(event: KeyboardEvent) {
		const target = event.target as HTMLElement;
		const currentIndex = visibleStyles.findIndex((style) => tileId(style) === target.id);
		if (currentIndex === -1) return;

		if (event.key === 'Enter') {
			if (event.repeat) return;
			event.preventDefault();
			commitTile(visibleStyles[currentIndex]);
			return;
		}

		if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight' && event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;

		event.preventDefault();
		let nextIndex = currentIndex;
		if (event.key === 'ArrowLeft') nextIndex = currentIndex - 1;
		else if (event.key === 'ArrowRight') nextIndex = currentIndex + 1;
		else if (event.key === 'ArrowUp') nextIndex = currentIndex - columns;
		else if (event.key === 'ArrowDown') nextIndex = currentIndex + columns;

		if (nextIndex < 0 || nextIndex >= visibleStyles.length) return;
		const next = visibleStyles[nextIndex];
		selectedId = next.id;
		gridEl?.querySelector<HTMLButtonElement>(`#${CSS.escape(tileId(next))}`)?.focus();
	}
</script>

<svelte:window bind:innerWidth on:keydown|capture={handleKeydown} />

<BaseModal
	{isOpen}
	title="Styles"
	subtitle="Wrap the prompt with a curated style"
	sizeClass="md:max-w-6xl md:w-full md:max-h-[90vh]"
	handleEscapeKey={false}
	on:close={handleCancel}
>
	<svelte:fragment slot="headerIcon"><Icon name="sparkles" className="h-5 w-5 text-fg-muted" /></svelte:fragment>

	<div class="flex h-full flex-col">
		<div class="sticky top-0 z-10 flex flex-wrap items-center gap-3 border-b border-line bg-surface-1 px-4 py-3 sm:px-6">
			<div class="flex flex-1 flex-wrap items-center gap-1.5" role="group" aria-label="Filter by category">
				{#each categories as cat (cat.id)}
					<button
						type="button"
						class="inline-flex items-center gap-1.5 rounded border px-2.5 py-1 font-mono text-xs uppercase tracking-wide transition-colors {categoryFilter ===
						cat.id
							? 'border-signal/40 bg-signal/10 text-signal'
							: 'border-line text-fg-muted hover:bg-surface-2 hover:text-fg'}"
						aria-pressed={categoryFilter === cat.id}
						onclick={() => (categoryFilter = cat.id)}
					>
						{cat.label}
						<span class="text-2xs tabular-nums opacity-70">{cat.count}</span>
					</button>
				{/each}
			</div>

			<div class="flex items-center gap-3">
				<Input bind:value={textFilter} placeholder="Filter styles…" class="w-48" aria-label="Filter styles" />
				<SegmentedControl
					items={[
						{ id: 'small', label: 'Small' },
						{ id: 'big', label: 'Big' }
					]}
					selected={size}
					onSelect={setSize}
					ariaLabel="Tile size"
				/>
			</div>
		</div>

		<div class="p-4 sm:p-6">
			{#if styles.length === 0}
				<div class="rounded-lg border border-dashed border-line p-8 text-center text-sm text-fg-muted">
					This preset has no styles yet.
				</div>
			{:else if visibleStyles.length === 0}
				<div class="rounded-lg border border-dashed border-line p-8 text-center text-sm text-fg-muted">
					No styles match your filters.
				</div>
			{:else}
				<div
					class={size === 'big'
						? 'grid grid-cols-[repeat(auto-fill,minmax(320px,1fr))] gap-4'
						: 'grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3'}
					role="toolbar"
					aria-label="Styles"
					aria-orientation="horizontal"
					tabindex="-1"
					bind:this={gridEl}
					bind:clientWidth={gridWidth}
					onkeydown={handleGridKeydown}
				>
					{#each visibleStyles as style (style.id)}
						{@const selected = style.id === selectedId}
						{@const applied = style.id === appliedStyleId}
						{@const preview = previewSrc(style)}
						<button
							type="button"
							id={tileId(style)}
							class="group flex flex-col gap-1 rounded-lg text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
							title={size === 'small' ? style.description || style.name : undefined}
							aria-pressed={selected}
							onclick={() => selectTile(style)}
							ondblclick={() => commitTile(style)}
						>
							<div
								class="relative aspect-square w-full overflow-hidden rounded-lg border bg-surface-2 {selected
									? 'border-signal ring-2 ring-signal/30'
									: 'border-line group-hover:border-line-hover'}"
							>
								{#if preview}
									<img src={preview} alt={style.name} class="h-full w-full object-cover" loading="lazy" />
								{:else}
									<div class="flex h-full w-full items-center justify-center text-2xl font-semibold text-fg-subtle">
										{style.name.charAt(0).toUpperCase()}
									</div>
								{/if}
								{#if applied}
									<div class="absolute right-1.5 top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-signal text-white">
										<Icon name="check" className="h-3 w-3" />
									</div>
								{/if}
							</div>
							<span class="truncate text-xs font-medium {selected ? 'text-signal' : 'text-fg'}">{style.name}</span>
							<span class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">{style.category || 'General'}</span>
							{#if size === 'big' && style.description}
								<p class="line-clamp-2 text-xs text-fg-muted">{style.description}</p>
							{/if}
						</button>
					{/each}
				</div>
			{/if}
		</div>
	</div>

	<svelte:fragment slot="footer">
		<ConfirmFooter
			{confirmLabel}
			confirmDisabled={!selectedStyle}
			{summary}
			onCancel={handleCancel}
			onConfirm={commitSelected}
		/>
	</svelte:fragment>
</BaseModal>
