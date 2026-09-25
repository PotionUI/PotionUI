<script module lang="ts">
	export interface PromptPickerTreeItem {
		id: string;
		name: string;
		description?: string;
		icon?: string;
		count?: number;
	}
</script>

<script lang="ts">
	import BaseModal from './modals/BaseModal.svelte';
	import ConfirmFooter from './modals/ConfirmFooter.svelte';
	import { createConfirmSettlementGate, getConfirmKeyboardAction, settleIfEligible } from './modals/confirmKeyboard';
	import Icon from './Icon.svelte';
	import Button from './ui/Button.svelte';
	import SegmentedControl from './ui/SegmentedControl.svelte';
	import Kbd from './ui/Kbd.svelte';
	import Tooltip from './Tooltip.svelte';
	import IconButton from './ui/IconButton.svelte';
	import HighlightedText from './HighlightedText.svelte';
	import { compileTextMatcher, textMatches, type TextMatchMode } from '$lib/utils/textMatch';
	import type { AutocompleteValue } from './AutocompleteDropdown.svelte';

	const TRIGGER_COLOR: Record<string, string> = {
		'#': 'rgb(var(--signal))',
		$: 'rgb(var(--ai-1))',
		'@': 'rgb(var(--success))',
		'/': 'rgb(var(--warning))'
	};

	const PGRID_COLUMNS = 4;

	let {
		triggerChar,
		title,
		initialQuery = '',
		contextBefore = '',
		contextMarker,
		contextAfter = '',
		layout = 'list',
		categories = [],
		activeCategoryId = null,
		values = [],
		getImageUrl,
		insertHint = 'Insert',
		initialSelectedId = null,
		footerLeft = 'none',
		initialShuffle = false,
		canShuffle = false,
		onSelectCategory,
		onInsertValue,
		onRemove,
		onExclude,
		onClose
	}: {
		triggerChar: '#' | '$' | '@' | '/';
		title: string;
		initialQuery?: string;
		contextBefore?: string;
		contextMarker: string;
		contextAfter?: string;
		layout?: 'list' | 'grid';
		categories?: PromptPickerTreeItem[];
		activeCategoryId?: string | null;
		values?: AutocompleteValue[];
		getImageUrl?: (fileId: string) => string;
		insertHint?: string;
		initialSelectedId?: string | null;
		footerLeft?: 'none' | 'phrasebook' | 'resource';
		initialShuffle?: boolean;
		canShuffle?: boolean;
		onSelectCategory?: (category: PromptPickerTreeItem) => void;
		onInsertValue: (value: AutocompleteValue, extra?: { shuffle: boolean }) => void;
		onRemove?: () => void;
		onExclude?: () => void;
		onClose: () => void;
	} = $props();

	let query = $state(initialQuery);
	let matchMode = $state<TextMatchMode>('contains');
	let selectedId = $state<string | null>(initialSelectedId);
	let pendingShuffle = $state(initialShuffle);
	let previewOpen = $state(false);
	let previewIndex = $state(0);
	let previewPaneRef = $state<HTMLDivElement | null>(null);
	let lastFocusedBeforePreview: HTMLElement | null = null;
	const settlementGate = createConfirmSettlementGate();

	let compiledSearch = $derived(compileTextMatcher(query, matchMode));
	let searchMatcher = $derived(compiledSearch.matcher);

	let filteredCategories = $derived.by(() => {
		if (categories.length <= 1) return categories;
		return categories.filter((c) => textMatches(searchMatcher, c.name, c.description));
	});

	let filteredValues = $derived(
		values.filter((v) => textMatches(searchMatcher, v.label, v.value, v.description))
	);

	function toggleMatchMode() {
		matchMode = matchMode === 'regex' ? 'contains' : 'regex';
	}

	$effect(() => {
		if (filteredValues.some((v) => v.id === selectedId)) return;
		if (initialSelectedId && filteredValues.some((v) => v.id === initialSelectedId)) {
			selectedId = initialSelectedId;
			return;
		}
		selectedId = filteredValues[0]?.id ?? null;
	});

	let showPreviewGrid = $derived(triggerChar === '#' && layout === 'list' && filteredValues.some((v) => v.preview_file_id));

	$effect(() => {
		if (previewOpen) previewPaneRef?.focus();
	});

	function openPreviewAt(index: number) {
		lastFocusedBeforePreview = typeof document !== 'undefined' ? (document.activeElement as HTMLElement | null) : null;
		previewIndex = index;
		previewOpen = true;
	}

	function closePreview() {
		previewOpen = false;
		lastFocusedBeforePreview?.focus();
	}

	function previewPrev() {
		if (previewIndex > 0) previewIndex -= 1;
	}

	function previewNext() {
		if (previewIndex < filteredValues.length - 1) previewIndex += 1;
	}

	function useThisValue() {
		const picked = filteredValues[previewIndex];
		if (picked) selectedId = picked.id;
		closePreview();
	}

	function moveGridSelection(currentIndex: number, delta: number) {
		const nextIndex = currentIndex + delta;
		if (nextIndex < 0 || nextIndex >= filteredValues.length) return;
		selectedId = filteredValues[nextIndex].id;
	}

	function handleConfirm() {
		settleIfEligible(settlementGate, !!selectedId, commitSelection);
	}

	function handleCancel() {
		settlementGate.settle(onClose);
	}

	function handleWindowKeydown(e: KeyboardEvent) {
		if (previewOpen) {
			if (e.key === 'ArrowLeft') {
				e.preventDefault();
				previewPrev();
			} else if (e.key === 'ArrowRight') {
				e.preventDefault();
				previewNext();
			} else if (e.key === 'Enter') {
				e.preventDefault();
				useThisValue();
			} else if (e.key === 'Escape') {
				e.preventDefault();
				e.stopPropagation();
				closePreview();
			}
			return;
		}

		const { action, suppress } = getConfirmKeyboardAction(e);
		if (action === 'cancel') {
			handleCancel();
		} else if (action === 'confirm') {
			handleConfirm();
		} else if (e.key === 'Enter' && !e.repeat && (e.target as HTMLElement | null)?.tagName === 'INPUT') {
			e.preventDefault();
			handleConfirm();
			return;
		}
		if (suppress) e.preventDefault();
		if (action !== null) return;

		if (!showPreviewGrid) return;
		const activeTag = (e.target as HTMLElement | null)?.tagName;
		if (activeTag === 'INPUT' || activeTag === 'TEXTAREA') return;

		const currentIndex = filteredValues.findIndex((v) => v.id === selectedId);
		if (currentIndex === -1) return;

		if (e.key === ' ') {
			e.preventDefault();
			openPreviewAt(currentIndex);
		} else if (e.key === 'ArrowRight') {
			e.preventDefault();
			moveGridSelection(currentIndex, 1);
		} else if (e.key === 'ArrowLeft') {
			e.preventDefault();
			moveGridSelection(currentIndex, -1);
		} else if (e.key === 'ArrowDown') {
			e.preventDefault();
			moveGridSelection(currentIndex, PGRID_COLUMNS);
		} else if (e.key === 'ArrowUp') {
			e.preventDefault();
			moveGridSelection(currentIndex, -PGRID_COLUMNS);
		}
	}

	function rowLabel(value: AutocompleteValue): string {
		return triggerChar === '@' ? `<${value.label}>` : value.label;
	}

	function scrollCurrentIntoView(node: HTMLElement, isCurrent: boolean) {
		if (isCurrent && typeof node.scrollIntoView === 'function') node.scrollIntoView({ block: 'nearest' });
		return {};
	}

	function shuffleNow() {
		const candidates = values.filter((v) => v.id !== selectedId);
		if (!candidates.length) return;
		selectedId = candidates[Math.floor(Math.random() * candidates.length)].id;
	}

	function commitSelection() {
		const picked = values.find((v) => v.id === selectedId);
		if (!picked) return;
		onInsertValue(picked, footerLeft === 'phrasebook' ? { shuffle: pendingShuffle } : undefined);
		onClose();
	}

	function pickValue(value: AutocompleteValue) {
		selectedId = value.id;
		commitSelection();
	}

	function handleExcludeClick() {
		onExclude?.();
		onClose();
	}

	function handleRemoveClick() {
		onRemove?.();
		onClose();
	}
</script>

<svelte:window on:keydown|capture={handleWindowKeydown} />

<BaseModal
	isOpen={true}
	closeable={!previewOpen}
	handleEscapeKey={false}
	sizeClass="w-full md:w-[64rem] md:max-w-[calc(100vw-2rem)]"
	on:close={handleCancel}
>
	<svelte:fragment slot="headerIcon">
		<span class="picker-chip" style="--trig: {TRIGGER_COLOR[triggerChar]}">{triggerChar}</span>
	</svelte:fragment>
	<svelte:fragment slot="header">
		<strong class="picker-modal-title">{title}</strong>
		<div class="picker-modal-search" class:invalid={!!compiledSearch.error}>
			<Icon name="search" className="icon" />
			<input
				bind:value={query}
				aria-label="Search {title}"
				aria-invalid={compiledSearch.error ? 'true' : undefined}
				placeholder={matchMode === 'regex' ? 'Regular expression…' : undefined}
			/>
			<Tooltip text="Regular expression" position="bottom">
				<IconButton
					icon="regex"
					label="Regular expression"
					size="xs"
					active={matchMode === 'regex'}
					ariaPressed={matchMode === 'regex'}
					onclick={toggleMatchMode}
				/>
			</Tooltip>
		</div>
	</svelte:fragment>

	{#if compiledSearch.error}
		<div class="picker-search-error" role="alert">{compiledSearch.error}</div>
	{/if}

	<div class="picker-context">
		{#if contextBefore}…{contextBefore}&nbsp;{/if}<span class="k" style="--trig: {TRIGGER_COLOR[triggerChar]}">{contextMarker}<span class="care"></span></span>&nbsp;{contextAfter}{#if contextAfter}…{/if}
	</div>

	<div class="picker-modal-body">
		{#if previewOpen}
			{@const current = filteredValues[previewIndex]}
			<div class="picker-preview-pane" role="dialog" aria-label="Preview {current?.label ?? ''}" tabindex="-1" bind:this={previewPaneRef}>
				<div class="picker-preview-head">
					<Tooltip text="Back to the grid">
						<Button variant="ghost" size="sm" icon="arrow-left" onclick={closePreview}>Back</Button>
					</Tooltip>
					<span class="picker-preview-pos">{previewIndex + 1} / {filteredValues.length}</span>
				</div>
				<div class="picker-preview-body">
					<Tooltip text="Previous (←)">
						<button
							type="button"
							class="picker-preview-nav"
							disabled={previewIndex === 0}
							onclick={previewPrev}
							aria-label="Previous value"
						>
							<Icon name="arrow-left" className="icon" />
						</button>
					</Tooltip>
					<div class="picker-preview-image">
						{#if current?.preview_file_id && getImageUrl}
							<img src={getImageUrl(current.preview_file_id)} alt={current.label} />
						{:else}
							<span class="picker-preview-glyph">{triggerChar}</span>
						{/if}
					</div>
					<Tooltip text="Next (→)">
						<button
							type="button"
							class="picker-preview-nav"
							disabled={previewIndex === filteredValues.length - 1}
							onclick={previewNext}
							aria-label="Next value"
						>
							<Icon name="arrow-right" className="icon" />
						</button>
					</Tooltip>
				</div>
				<div class="picker-preview-foot">
					<div class="picker-preview-copy">
						<strong class="picker-preview-value"><HighlightedText text={current?.value ?? ''} matcher={searchMatcher} /></strong>
						{#if current?.label && current.label !== current.value}<p class="picker-value-secondary"><span class="picker-value-secondary-prefix">Title:</span> <HighlightedText text={current.label} matcher={searchMatcher} /></p>{/if}
					</div>
					<Button variant="primary" onclick={useThisValue}>Use this value</Button>
				</div>
			</div>
		{:else}
			{#if categories.length > 0}
				<div class="picker-tree">
					{#each filteredCategories as category (category.id)}
						{@const isActive = category.id === activeCategoryId}
						<button
							type="button"
							class="picker-tree-row"
							class:on={isActive}
							use:scrollCurrentIntoView={isActive}
							onclick={() => onSelectCategory?.(category)}
						>
							<Icon name={category.icon ?? 'folder'} className="icon" />
							<HighlightedText text={category.name} matcher={searchMatcher} />
							{#if category.count !== undefined}<span class="n">{category.count}</span>{/if}
						</button>
					{/each}
				</div>
			{/if}

			{#if showPreviewGrid}
				<div class="picker-gridwrap" class:full={categories.length === 0}>
					<div class="picker-pgrid">
						{#each filteredValues as value, index (value.id)}
							{@const isCurrent = value.id === initialSelectedId}
							{@const isSelected = value.id === selectedId}
							<div class="picker-pgitem-wrap">
								<button
									type="button"
									class="picker-pgitem"
									class:sel={isSelected}
									use:scrollCurrentIntoView={isCurrent}
									onclick={() => (selectedId = value.id)}
									ondblclick={() => pickValue(value)}
								>
									<span class="picker-pgthumb">
										{#if value.preview_file_id && getImageUrl}
											<img src={getImageUrl(value.preview_file_id)} alt={value.label} loading="lazy" />
										{:else}
											<span class="picker-pgglyph">{triggerChar}</span>
										{/if}
									</span>
									<span class="picker-pgcopy">
										<strong class="picker-value-primary"><HighlightedText text={value.value} matcher={searchMatcher} /></strong>
										{#if value.label && value.label !== value.value}<span class="picker-value-secondary"><span class="picker-value-secondary-prefix">Title:</span> <HighlightedText text={value.label} matcher={searchMatcher} /></span>{/if}
									</span>
									{#if isCurrent}
										<span class="picker-vbadge" class:signal={isSelected} class:neutral={!isSelected}>Current</span>
									{/if}
								</button>
								<Tooltip text="Preview (Space)">
									<button
										type="button"
										class="picker-pgpreview-btn"
										aria-label="Preview {value.label}"
										onclick={(e) => {
											e.stopPropagation();
											openPreviewAt(index);
										}}
									>
										<Icon name="eyes" className="icon" />
									</button>
								</Tooltip>
							</div>
						{/each}
					</div>
				</div>
			{:else if layout === 'grid'}
				<div class="picker-gridwrap" class:full={categories.length === 0}>
					<div class="picker-grid">
						{#each filteredValues as value (value.id)}
							{@const isCurrent = value.id === initialSelectedId}
							{@const isSelected = value.id === selectedId}
							<button
								type="button"
								class="picker-gitem"
								class:sel={isSelected}
								use:scrollCurrentIntoView={isCurrent}
								onclick={() => (selectedId = value.id)}
								ondblclick={() => pickValue(value)}
							>
								<span class="picker-gthumb">
									{#if value.preview_file_id && getImageUrl}
										<img src={getImageUrl(value.preview_file_id)} alt={value.label} loading="lazy" />
									{:else}
										<Icon name={value.kind === 'video' ? 'video' : value.kind === 'audio' ? 'audio' : 'image'} className="icon" />
									{/if}
								</span>
								<span class="picker-gcheck"><Icon name="check" className="icon" /></span>
								<span class="picker-gcopy">
									<strong><HighlightedText text={rowLabel(value)} matcher={searchMatcher} /></strong>
									{#if value.value !== value.label}<span><HighlightedText text={value.value} matcher={searchMatcher} /></span>{/if}
								</span>
							</button>
						{/each}
					</div>
				</div>
			{:else}
				<div class="picker-values" class:full={categories.length === 0}>
					{#each filteredValues as value (value.id)}
						{@const isCurrent = value.id === initialSelectedId}
						{@const isSelected = value.id === selectedId}
						<button
							type="button"
							class="picker-vrow"
							class:sel={isSelected}
							use:scrollCurrentIntoView={isCurrent}
							onclick={() => (selectedId = value.id)}
							ondblclick={() => pickValue(value)}
						>
							<span class="picker-vthumb">
								{#if value.preview_file_id && getImageUrl}
									<img src={getImageUrl(value.preview_file_id)} alt={value.label} loading="lazy" />
								{:else}
									{triggerChar}
								{/if}
							</span>
							<span class="picker-vcopy">
								{#if triggerChar === '#'}
									<strong class="picker-value-primary"><HighlightedText text={value.value} matcher={searchMatcher} /></strong>
									{#if value.label && value.label !== value.value}<span class="picker-value-secondary"><span class="picker-value-secondary-prefix">Title:</span> <HighlightedText text={value.label} matcher={searchMatcher} /></span>{/if}
								{:else}
									<strong class="picker-label-primary"><HighlightedText text={rowLabel(value)} matcher={searchMatcher} /></strong>
									{#if value.description || value.value !== value.label}<span class="picker-label-secondary"><HighlightedText text={value.description ?? value.value} matcher={searchMatcher} /></span>{/if}
								{/if}
							</span>
							{#if isCurrent}
								<span class="picker-vbadge" class:signal={isSelected} class:neutral={!isSelected}>Current</span>
							{:else}
								<span class="picker-vcheck"><Icon name="check" className="icon" /></span>
							{/if}
						</button>
					{/each}
				</div>
			{/if}
		{/if}
	</div>

	<svelte:fragment slot="footer">
		<ConfirmFooter
			confirmLabel={insertHint}
			confirmDisabled={!selectedId}
			onCancel={handleCancel}
			onConfirm={handleConfirm}
		>
			{#snippet leftActions()}
				{#if footerLeft === 'none' && showPreviewGrid}
					<span class="picker-kbd-hint"><Kbd keys="Space" /> preview</span>
				{/if}
				{#if footerLeft === 'phrasebook'}
					{#if showPreviewGrid}
						<span class="picker-kbd-hint"><Kbd keys="Space" /> preview</span>
						<span class="fsep"></span>
					{/if}
					<SegmentedControl
						items={[
							{ id: 'fixed', label: 'Fixed value' },
							{ id: 'shuffle', label: 'Auto-shuffle' }
						]}
						selected={pendingShuffle ? 'shuffle' : 'fixed'}
						onSelect={(id) => {
							if (id === 'shuffle' && !canShuffle) return;
							pendingShuffle = id === 'shuffle';
						}}
					/>
					<span class="fsep"></span>
					<Tooltip text="Pick a different value now">
						<Button variant="secondary" size="sm" icon="shuffle" disabled={!canShuffle} onclick={shuffleNow}>
							Shuffle now
						</Button>
					</Tooltip>
					{#if canShuffle}
						<Tooltip text="This value won't be picked by Auto-shuffle again">
							<Button variant="ghost" size="sm" icon="eye-off" onclick={handleExcludeClick}>
								Exclude from shuffles
							</Button>
						</Tooltip>
					{/if}
					<span class="fsep"></span>
					<Tooltip text="Remove this chip from the prompt">
						<Button variant="ghost" size="sm" class="text-danger hover:bg-danger/10" icon="trash" onclick={handleRemoveClick}>
							Remove chip
						</Button>
					</Tooltip>
				{:else if footerLeft === 'resource'}
					<Tooltip text="Remove this reference from the prompt">
						<Button variant="ghost" size="sm" class="text-danger hover:bg-danger/10" icon="trash" onclick={handleRemoveClick}>
							Remove reference
						</Button>
					</Tooltip>
				{/if}
			{/snippet}
		</ConfirmFooter>
	</svelte:fragment>
</BaseModal>

<style>
	.picker-chip {
		width: 26px;
		height: 26px;
		display: grid;
		place-items: center;
		flex: 0 0 auto;
		color: var(--trig);
		background: rgb(var(--surface-2));
		border: 1px solid rgb(var(--line));
		border-radius: 5px;
		font: 600 13px 'IBM Plex Mono', monospace;
	}

	.picker-modal-title {
		font-size: 14px;
		font-weight: 600;
		white-space: nowrap;
	}

	.picker-modal-search {
		display: flex;
		align-items: center;
		gap: 7px;
		height: 32px;
		margin-left: auto;
		padding: 0 10px;
		width: 13rem;
		color: rgb(var(--fg));
		background: rgb(var(--surface-2));
		border: 1px solid rgb(var(--line-strong));
		border-radius: 5px;
		font-size: 13px;
	}

	.picker-modal-search.invalid {
		border-color: rgb(var(--danger));
	}

	.picker-search-error {
		padding: 6px 14px;
		font-size: 12px;
		color: rgb(var(--danger));
		border-bottom: 1px solid rgb(var(--line));
	}

	.picker-modal-search :global(.icon) {
		width: 13px;
		height: 13px;
		color: rgb(var(--fg-subtle));
		flex: none;
	}

	.picker-modal-search input {
		width: 100%;
		color: inherit;
		background: transparent;
		border: 0;
		font: inherit;
	}

	.picker-modal-search input:focus {
		outline: 0;
	}

	.picker-context {
		display: flex;
		align-items: center;
		gap: 4px;
		min-height: 38px;
		padding: 0 14px;
		background: rgb(var(--surface-2) / 0.5);
		border-bottom: 1px solid rgb(var(--line));
		font-size: 13px;
		color: rgb(var(--fg-subtle));
		overflow: hidden;
		white-space: nowrap;
		text-overflow: ellipsis;
	}

	.picker-context .k {
		color: var(--trig);
		font-weight: 600;
	}

	.picker-context .care {
		display: inline-block;
		width: 2px;
		height: 1em;
		background: var(--trig);
		vertical-align: -2px;
		margin: 0 1px;
	}

	.picker-modal-body {
		flex: 1;
		min-height: 22rem;
		display: flex;
	}

	.picker-tree {
		width: 13rem;
		flex: 0 0 auto;
		padding: 8px;
		border-right: 1px solid rgb(var(--line));
		overflow-y: auto;
	}

	.picker-tree-row {
		display: flex;
		align-items: center;
		gap: 8px;
		width: 100%;
		height: 30px;
		padding: 0 8px;
		border-radius: 4px;
		color: rgb(var(--fg-muted));
		font-size: 13px;
		text-align: left;
	}

	.picker-tree-row:hover {
		background: rgb(var(--surface-2));
		color: rgb(var(--fg));
	}

	.picker-tree-row.on {
		background: rgb(var(--signal) / 0.1);
		color: rgb(var(--fg));
		box-shadow: inset 2px 0 0 rgb(var(--signal));
	}

	.picker-tree-row.on .n {
		color: rgb(var(--signal));
	}

	.picker-tree-row .n {
		margin-left: auto;
		font-family: 'IBM Plex Mono', monospace;
		font-size: 11px;
		color: rgb(var(--fg-subtle));
	}

	.picker-tree-row :global(.icon) {
		width: 13px;
		height: 13px;
		color: rgb(var(--fg-subtle));
		flex: none;
	}

	.picker-values {
		flex: 1;
		min-width: 0;
		padding: 8px;
		overflow-y: auto;
	}

	.picker-values.full {
		width: 100%;
	}

	.picker-vrow {
		display: flex;
		align-items: center;
		gap: 11px;
		width: 100%;
		min-height: 54px;
		padding: 7px 8px;
		border-radius: 6px;
		color: rgb(var(--fg-muted));
		text-align: left;
	}

	.picker-vrow:hover {
		background: rgb(var(--surface-2));
		color: rgb(var(--fg));
	}

	.picker-vrow.sel {
		background: rgb(var(--signal) / 0.1);
		color: rgb(var(--fg));
		box-shadow: inset 0 0 0 1px rgb(var(--signal) / 0.35);
	}

	.picker-vthumb {
		width: 40px;
		height: 40px;
		flex: 0 0 auto;
		display: grid;
		place-items: center;
		overflow: hidden;
		color: var(--trig);
		background: rgb(var(--surface-2));
		border: 1px solid rgb(var(--line));
		border-radius: 5px;
		font: 600 14px 'IBM Plex Mono', monospace;
	}

	.picker-vthumb img {
		width: 100%;
		height: 100%;
		object-fit: cover;
	}

	.picker-vthumb :global(.icon) {
		width: 15px;
		height: 15px;
	}

	.picker-vcopy {
		min-width: 0;
		flex: 1;
	}

	.picker-label-primary {
		display: block;
		font-size: 14px;
		font-weight: 600;
		font-family: 'IBM Plex Mono', monospace;
	}

	.picker-label-secondary {
		display: block;
		margin-top: 3px;
		font-size: 12px;
		color: rgb(var(--fg-subtle));
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.picker-value-primary {
		display: -webkit-box;
		-webkit-box-orient: vertical;
		-webkit-line-clamp: 3;
		line-clamp: 3;
		font-size: 14px;
		font-weight: 500;
		color: rgb(var(--fg));
		overflow: hidden;
	}

	.picker-value-secondary {
		display: block;
		margin-top: 3px;
		font-size: 12px;
		color: rgb(var(--fg-muted));
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.picker-value-secondary-prefix {
		color: rgb(var(--fg-subtle));
	}

	.picker-vcheck {
		width: 18px;
		height: 18px;
		flex: 0 0 auto;
		display: grid;
		place-items: center;
		color: transparent;
		border: 1px solid rgb(var(--line-strong));
		border-radius: 50%;
	}

	.picker-vrow.sel .picker-vcheck {
		color: rgb(var(--accent-contrast));
		background: rgb(var(--signal));
		border-color: rgb(var(--signal));
	}

	.picker-vcheck :global(.icon) {
		width: 10px;
		height: 10px;
	}

	.picker-vbadge {
		flex: 0 0 auto;
		padding: 2px 7px;
		border-radius: 4px;
		font-family: 'IBM Plex Mono', monospace;
		font-size: 12px;
		font-weight: 500;
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}

	.picker-vbadge.neutral {
		color: rgb(var(--fg-subtle));
		background: rgb(var(--surface-2));
		border: 1px solid rgb(var(--line));
	}

	.picker-vbadge.signal {
		color: rgb(var(--signal));
		background: rgb(var(--signal) / 0.12);
		border: 1px solid rgb(var(--signal) / 0.32);
	}

	.picker-gridwrap {
		flex: 1;
		min-width: 0;
		padding: 8px;
		overflow-y: auto;
	}

	.picker-gridwrap.full {
		width: 100%;
	}

	.picker-grid {
		display: grid;
		grid-template-columns: repeat(4, 1fr);
		gap: 10px;
		padding: 4px;
	}

	.picker-gitem {
		position: relative;
		display: flex;
		flex-direction: column;
		border-radius: 6px;
		overflow: hidden;
		border: 1px solid rgb(var(--line));
		background: rgb(var(--surface-2));
		text-align: left;
	}

	.picker-gitem:hover {
		border-color: rgb(var(--line-strong));
	}

	.picker-gitem.sel {
		box-shadow: 0 0 0 2px rgb(var(--signal));
		border-color: transparent;
	}

	.picker-gthumb {
		height: 72px;
		display: grid;
		place-items: center;
		background: linear-gradient(
			135deg,
			rgb(var(--surface-3)) 0%,
			rgb(var(--surface-2)) 55%,
			rgb(var(--surface-3)) 100%
		);
		overflow: hidden;
	}

	.picker-gthumb img {
		width: 100%;
		height: 100%;
		object-fit: cover;
	}

	.picker-gthumb :global(.icon) {
		width: 16px;
		height: 16px;
		color: rgb(var(--fg-muted));
	}

	.picker-gcheck {
		position: absolute;
		top: 6px;
		right: 6px;
		width: 18px;
		height: 18px;
		display: grid;
		place-items: center;
		color: transparent;
		border: 1px solid rgb(var(--line-hover));
		border-radius: 50%;
		background: rgb(var(--surface-1) / 0.7);
	}

	.picker-gitem.sel .picker-gcheck {
		color: rgb(var(--accent-contrast));
		background: rgb(var(--signal));
		border-color: rgb(var(--signal));
	}

	.picker-gcheck :global(.icon) {
		width: 10px;
		height: 10px;
	}

	.picker-gcopy {
		padding: 7px 8px 9px;
	}

	.picker-gcopy strong {
		display: block;
		font-size: 13px;
		font-weight: 600;
		font-family: 'IBM Plex Mono', monospace;
	}

	.picker-gcopy span {
		display: block;
		margin-top: 2px;
		font-size: 12px;
		color: rgb(var(--fg-subtle));
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.fsep {
		width: 1px;
		height: 20px;
		background: rgb(var(--line-strong));
		flex-shrink: 0;
	}

	.picker-kbd-hint {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		font-size: 12px;
		color: rgb(var(--fg-subtle));
		white-space: nowrap;
	}

	.picker-pgrid {
		display: grid;
		grid-template-columns: repeat(4, 1fr);
		gap: 12px;
		padding: 4px;
	}

	.picker-pgitem-wrap {
		position: relative;
	}

	.picker-pgitem {
		position: relative;
		display: flex;
		flex-direction: column;
		width: 100%;
		border-radius: 8px;
		overflow: hidden;
		border: 1px solid rgb(var(--line));
		background: rgb(var(--surface-2));
		text-align: left;
	}

	.picker-pgitem:hover {
		border-color: rgb(var(--line-strong));
	}

	.picker-pgitem.sel {
		box-shadow: 0 0 0 2px rgb(var(--signal));
		border-color: transparent;
	}

	.picker-pgthumb {
		height: 9rem;
		width: 100%;
		display: grid;
		place-items: center;
		background: linear-gradient(
			135deg,
			rgb(var(--surface-3)) 0%,
			rgb(var(--surface-2)) 55%,
			rgb(var(--surface-3)) 100%
		);
		overflow: hidden;
	}

	.picker-pgthumb img {
		width: 100%;
		height: 100%;
		object-fit: cover;
	}

	.picker-pgglyph {
		color: var(--trig);
		font: 600 20px 'IBM Plex Mono', monospace;
	}

	.picker-pgcopy {
		padding: 8px 9px 10px;
	}

	.picker-pgpreview-btn {
		position: absolute;
		top: 8px;
		right: 8px;
		width: 26px;
		height: 26px;
		display: grid;
		place-items: center;
		color: rgb(var(--fg));
		background: rgb(var(--surface-1) / 0.75);
		border: 1px solid rgb(var(--line-strong));
		border-radius: 5px;
	}

	.picker-pgpreview-btn:hover {
		background: rgb(var(--surface-1));
	}

	.picker-pgpreview-btn :global(.icon) {
		width: 14px;
		height: 14px;
	}

	.picker-preview-pane {
		flex: 1;
		min-width: 0;
		display: flex;
		flex-direction: column;
		width: 100%;
	}

	.picker-preview-pane:focus {
		outline: none;
	}

	.picker-preview-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 10px;
		padding: 8px 10px;
		border-bottom: 1px solid rgb(var(--line));
	}

	.picker-preview-pos {
		font-family: 'IBM Plex Mono', monospace;
		font-size: 12px;
		color: rgb(var(--fg-subtle));
	}

	.picker-preview-body {
		flex: 1;
		min-height: 0;
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 12px;
	}

	.picker-preview-nav {
		flex: 0 0 auto;
		width: 34px;
		height: 34px;
		display: grid;
		place-items: center;
		color: rgb(var(--fg-muted));
		background: rgb(var(--surface-2));
		border: 1px solid rgb(var(--line));
		border-radius: 6px;
	}

	.picker-preview-nav:hover:not(:disabled) {
		color: rgb(var(--fg));
		background: rgb(var(--surface-3));
	}

	.picker-preview-nav:disabled {
		opacity: 0.35;
	}

	.picker-preview-nav :global(.icon) {
		width: 15px;
		height: 15px;
	}

	.picker-preview-image {
		flex: 1;
		min-width: 0;
		height: 100%;
		min-height: 16rem;
		display: grid;
		place-items: center;
		background: rgb(var(--surface-2));
		border-radius: 8px;
		overflow: hidden;
	}

	.picker-preview-image img {
		max-width: 100%;
		max-height: 100%;
		object-fit: contain;
	}

	.picker-preview-glyph {
		color: var(--trig);
		font: 600 32px 'IBM Plex Mono', monospace;
	}

	.picker-preview-foot {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		padding: 10px 14px;
		border-top: 1px solid rgb(var(--line));
	}

	.picker-preview-copy {
		min-width: 0;
	}

	.picker-preview-value {
		display: block;
		font-size: 14px;
		font-weight: 500;
		color: rgb(var(--fg));
		white-space: normal;
	}
</style>
