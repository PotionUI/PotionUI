<svelte:options immutable />

<script lang="ts">
	import { createEventDispatcher, onMount, onDestroy } from 'svelte';
	import type { Segment, ChipData } from '$lib/types/segments';
	import type { VariablesMap, VariableDef, VariableRoll } from '$lib/utils/variableDefs';
	import type { PromptResourceSpec } from '$lib/utils/promptResources';
	import type { PromptSyntaxSpec } from '$lib/utils/promptSyntax';
	import InlineChipEditor from './InlineChipEditor.svelte';
	import Tooltip from './Tooltip.svelte';
	import PromptSegmentActionMenu from './PromptSegmentActionMenu.svelte';
	import PromptSegmentDetailsModal from './PromptSegmentDetailsModal.svelte';
	import { computeFixedMenuPosition } from '$lib/utils/menuPosition';
	import { lastAppliedSegment } from '$lib/stores/lastAppliedSegment';
	import { countChoiceGroups } from '$lib/utils/choiceGroups';
	import { countVariableUsages } from '$lib/utils/promptVariables';
	import {
		DISABLED_SEGMENT_NOTE,
		UNNAMED_SEGMENT_PLACEHOLDER,
		formatSegmentIndex,
		segmentCharCount,
		segmentDisplayName
	} from '$lib/utils/segmentFooter';
	import { segmentMenuActions } from '$lib/utils/segmentActions';
	import { promptSegmentActionPins } from '$lib/stores/promptSegmentActionPins';

	promptSegmentActionPins.init();

	export let segment: Segment;
	export let index: number;
	export let total = 1;
	export let isNegative = false;
	export let compact = false;
	export let placeholder = 'Enter prompt content... (# for phrasebook)';
	export let variables: VariablesMap = {};
	export let variableRolls: Record<string, VariableRoll> = {};
	export let onVariableDefChange: ((name: string, def: VariableDef) => void) | undefined = undefined;
	export let onOpenVariableManager: (() => void) | undefined = undefined;
	export let activeTriggerWords: string[] = [];
	export let promptResources: PromptResourceSpec[] = [];
	export let resourceFieldValues: Record<string, unknown> = {};
	export let resourceFieldLabels: Record<string, string> = {};
	export let promptSyntax: PromptSyntaxSpec[] = [];

	const dispatch = createEventDispatcher();
	let isDragging = false;
	let dragOverPosition: 'none' | 'top' | 'bottom' = 'none';
	let dragEnabled = false;
	let menuOpen = false;
	let detailsModalOpen = false;
	let menuRoot: HTMLElement;
	let menuTriggerBtn: HTMLButtonElement;
	let menuStyle = '';
	let wrapperEl: HTMLDivElement;
	let cardEl: HTMLDivElement;
	let nameEl: HTMLButtonElement;
	let inlineEditor: InlineChipEditor;
	let scrolledNonce = -1;

	// The header sheds detail in stages as its own width shrinks: below
	// CHAR_COUNT_HIDE_WIDTH the char count drops out of the row (its value
	// still reachable — folded into the Details tooltip); below
	// CLUSTER_COLLAPSE_WIDTH the four content actions fold into the overflow
	// menu (leaving grip + more). An inline description becomes an info icon
	// either at that same collapse, or earlier still if the name (which never
	// shrinks — see .segment-name) is long enough that the description would be
	// left with an unreadable sliver of its own; HEADER_RESERVED_WIDTH is
	// everything else on the row (index, gaps, spacer, char count, the full
	// action cluster) at its most generous, so this stays a same-or-earlier
	// swap, never later than the width-only collapse. jsdom has no
	// ResizeObserver, so component tests always see containerWidth 0, i.e.
	// the widest, most-expanded shape — the one those tests assert against.
	// Separate from — and narrower than — the ported `@container (max-width:
	// 43rem)` rule that hides just the two `.optional` actions: this JS
	// collapse is the fallback for genuinely tight embeds (e.g. a squeezed
	// Video Director rail), folding the whole cluster into the overflow menu.
	const CHAR_COUNT_HIDE_WIDTH = 380;
	const CLUSTER_COLLAPSE_WIDTH = 340;
	const HEADER_RESERVED_WIDTH = 220;
	const DESCRIPTION_MIN_WIDTH = 100;
	let containerWidth = 0;
	let nameWidth = 0;
	let cardResizeObserver: ResizeObserver | undefined;

	onMount(() => {
		if (typeof ResizeObserver === 'undefined' || !cardEl) return;
		cardResizeObserver = new ResizeObserver((entries) => {
			for (const entry of entries) {
				const width = entry.contentRect.width;
				if (entry.target === cardEl) containerWidth = width;
				else if (entry.target === nameEl) nameWidth = width;
			}
		});
		cardResizeObserver.observe(cardEl);
		if (nameEl) cardResizeObserver.observe(nameEl);
	});

	onDestroy(() => cardResizeObserver?.disconnect());

	$: charCountHidden = containerWidth > 0 && containerWidth < CHAR_COUNT_HIDE_WIDTH;
	$: clusterCollapsed = containerWidth > 0 && containerWidth < CLUSTER_COLLAPSE_WIDTH;
	$: descriptionAsIcon =
		clusterCollapsed ||
		(containerWidth > 0 &&
			containerWidth - nameWidth - HEADER_RESERVED_WIDTH < DESCRIPTION_MIN_WIDTH);

	$: segmentDisabled = segment.enabled === false || !!segment.isDisabled;
	$: displayName = segmentDisplayName(segment);
	$: hasDescription = !!segment.description && segment.description.trim().length > 0;
	$: prefixText = segment.prefix || '';
	$: suffixText = segment.suffix || '';
	$: hasAffix = !!prefixText || !!suffixText;
	$: hasBody = !!segment.content && segment.content.trim().length > 0;
	$: menuActions = segmentMenuActions(segment, {
		index,
		total,
		segmentDisabled,
		hasPromptSyntax: promptSyntax.length > 0,
		hasPromptResources: promptResources.length > 0
	});
	$: pinnedIds = new Set($promptSegmentActionPins.ids);
	$: pinnedActions = menuActions.filter((action) => pinnedIds.has(action.id));
	$: charCount = segmentCharCount(segment);
	$: dynamicTokenCount =
		Object.keys(segment.chips || {}).length +
		countChoiceGroups(segment.content || '') +
		countVariableUsages(segment.content || '');
	$: dynamicTokenLabel = segmentDisabled
		? 'Excluded from prompt'
		: dynamicTokenCount === 0
			? 'No dynamic tokens'
			: `${dynamicTokenCount} dynamic token${dynamicTokenCount === 1 ? '' : 's'}`;
	$: segmentLabel = `${isNegative ? 'Negative segment' : 'Positive segment'} ${index + 1} of ${total}`;
	$: isLastApplied = $lastAppliedSegment?.segmentId === segment.id;
	$: handleAppliedChange($lastAppliedSegment);
	$: segColorStyle =
		!segmentDisabled && segment.color
			? `--seg-color: ${segment.color}; --segment-color: ${segment.color};`
			: undefined;

	function handleAppliedChange(applied: { segmentId: string; nonce: number } | null) {
		if (!applied || applied.segmentId !== segment.id) return;
		if (applied.nonce === scrolledNonce) return;
		scrolledNonce = applied.nonce;
		wrapperEl?.scrollIntoView({
			block: 'nearest',
			behavior: prefersReducedMotion() ? 'auto' : 'smooth'
		});
	}

	function prefersReducedMotion(): boolean {
		return (
			typeof window !== 'undefined' &&
			window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true
		);
	}

	function enableDrag() {
		dragEnabled = true;
	}

	function disableDrag() {
		dragEnabled = false;
	}

	function handleContentChange(
		e: CustomEvent<{ value: string; chips: Record<string, ChipData>; resources?: Segment['resources'] }>
	) {
		if (isLastApplied) lastAppliedSegment.clear(segment.id);
		dispatch('change', e.detail);
		dispatch('contentChange', e.detail.value);
		dispatch('chipsChange', e.detail.chips);
	}

	function handleDragStart(event: DragEvent) {
		isDragging = true;
		event.dataTransfer!.effectAllowed = 'move';
		event.dataTransfer!.setData('text/plain', segment.id);
		dispatch('dragstart', { id: segment.id });
	}

	function handleDragEnd() {
		isDragging = false;
		dragOverPosition = 'none';
		dragEnabled = false;
		dispatch('dragend', { id: segment.id });
	}

	function handleDragOver(event: DragEvent) {
		event.preventDefault();
		if (isDragging) return;

		event.dataTransfer!.dropEffect = 'move';
		const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
		dragOverPosition = event.clientY < rect.top + rect.height / 2 ? 'top' : 'bottom';
	}

	function handleDragLeave() {
		dragOverPosition = 'none';
	}

	function handleDrop(event: DragEvent) {
		event.preventDefault();
		const draggedId = event.dataTransfer!.getData('text/plain');
		if (draggedId !== segment.id) {
			dispatch('drop', { draggedId, targetId: segment.id, position: dragOverPosition });
		}
		dragOverPosition = 'none';
	}

	function handleWindowPointerDown(event: PointerEvent) {
		if (menuOpen && menuRoot && !menuRoot.contains(event.target as Node)) menuOpen = false;
	}

	function handleWindowKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape' && menuOpen) closeMenu();
	}

	function handleWindowScroll() {
		if (menuOpen) menuOpen = false;
	}

	function handleWindowResize() {
		if (menuOpen) menuOpen = false;
	}

	function computeMenuPosition() {
		if (!menuTriggerBtn) return;
		menuStyle = computeFixedMenuPosition(menuTriggerBtn, 540);
	}

	function toggleMenu() {
		if (menuOpen) {
			closeMenu(false);
			return;
		}
		computeMenuPosition();
		menuOpen = true;
	}

	function closeMenu(returnFocus = true) {
		menuOpen = false;
		if (returnFocus) menuTriggerBtn?.focus();
	}

	function openDetailsModal() {
		menuOpen = false;
		detailsModalOpen = true;
	}

	function closeDetailsModal() {
		detailsModalOpen = false;
	}

	function saveDetails(updates: {
		name?: string;
		color?: string;
		description?: string;
		prefix?: string;
		suffix?: string;
	}) {
		dispatch('metadataChange', updates);
	}

	function runAction(id: string) {
		if (id === 'editDetails') {
			openDetailsModal();
			return;
		}
		closeMenu(false);
		switch (id) {
			case 'insertPhrasebook':
				inlineEditor?.insertPhrasebookTrigger();
				break;
			case 'insertVariable':
				inlineEditor?.insertVariableTrigger();
				break;
			case 'insertChoice':
				inlineEditor?.insertChoiceGroup();
				break;
			case 'insertResource':
				inlineEditor?.insertResourceTrigger();
				break;
			case 'insertSyntax':
				inlineEditor?.insertSyntaxTrigger();
				break;
			default:
				dispatch(id);
		}
	}

	function togglePin(id: string) {
		promptSegmentActionPins.togglePin(id);
	}
</script>

<svelte:window
	on:pointerup={disableDrag}
	on:pointerdown={handleWindowPointerDown}
	on:keydown={handleWindowKeydown}
	on:scroll|capture={handleWindowScroll}
	on:resize={handleWindowResize}
/>

<div
	class="section-wrapper relative"
	class:compact
	class:menu-open={menuOpen}
	role="listitem"
	aria-label={segmentLabel}
	draggable={dragEnabled}
	bind:this={wrapperEl}
	on:dragstart={handleDragStart}
	on:dragend={handleDragEnd}
	on:dragover={handleDragOver}
	on:dragleave={handleDragLeave}
	on:drop={handleDrop}
>
	<div
			class="card segment"
			class:off={segmentDisabled}
			class:dragging={isDragging}
			class:drop-target={dragOverPosition !== 'none'}
			class:last-applied={isLastApplied}
			class:has-color={!segmentDisabled && !!segment.color}
			class:menu-open={menuOpen}
			data-drop-position={dragOverPosition === 'bottom' ? 'after' : undefined}
			style={segColorStyle}
			bind:this={cardEl}
		>
			<aside class="segment-rail">
				<button
					type="button"
					class="icon-btn grip"
					aria-label={`Drag ${segmentLabel.toLowerCase()} to reorder`}
					on:pointerdown={enableDrag}
				>
					<svg class="icon"><use href="#i-grip" /></svg>
				</button>
				<span class="index font-mono tabular-nums" aria-hidden="true">{formatSegmentIndex(index)}</span>
				<span class="type-dot" aria-hidden="true"></span>
			</aside>

			<header class="card-head segment-head">
				<div class="segment-identity">
					<button
						type="button"
						class="card-name segment-name"
						class:unnamed={!displayName}
						class:struck={segmentDisabled}
						aria-label={displayName ? `Rename ${segmentLabel.toLowerCase()}` : `Name ${segmentLabel.toLowerCase()}`}
						on:click={openDetailsModal}
						bind:this={nameEl}
					>
						{displayName || UNNAMED_SEGMENT_PLACEHOLDER}
					</button>

					{#if hasDescription}
						{#if descriptionAsIcon}
							<Tooltip text={segment.description ?? ''} position="top">
								<button
									type="button"
									class="icon-btn description-icon"
									aria-label={`Description: ${segment.description}`}
								>
									<svg class="icon"><use href="#i-info" /></svg>
								</button>
							</Tooltip>
						{:else}
							<span class="head-description segment-description" title={segment.description}>{segment.description}</span>
						{/if}
					{/if}
				</div>

				{#if segmentDisabled}
					<Tooltip text={DISABLED_SEGMENT_NOTE} position="top">
						<span class="state-chip off-chip state-badge off font-mono">Off</span>
					</Tooltip>
				{/if}

				{#if !charCountHidden}
					<Tooltip text={`${charCount} characters — ${dynamicTokenLabel}`} position="top">
						<span class="char-count reveal font-mono tabular-nums">{charCount}</span>
					</Tooltip>
				{/if}

				{#if !clusterCollapsed}
					{#each pinnedActions as action (action.id)}
						<Tooltip
							text={action.id === 'editDetails' && charCountHidden
								? `${action.label} — ${charCount} ch`
								: action.label}
							position="top"
							wrapperClass="head-action-btn"
						>
							<button
								type="button"
								class="icon-btn head-action"
								aria-label={action.label}
								disabled={action.disabled}
								on:click={() => runAction(action.id)}
							>
								{#if action.glyph}
									<span class="icon menu-glyph {action.glyphClass}">{action.glyph}</span>
								{:else}
									<svg class="icon"><use href={`#i-${action.icon}`} /></svg>
								{/if}
							</button>
						</Tooltip>
					{/each}
				{/if}

				<Tooltip text={total <= 1 ? 'Every prompt needs at least one segment' : 'Remove segment'} position="top">
					<button
						type="button"
						class="icon-btn head-action danger reveal"
						aria-label="Remove segment"
						disabled={total <= 1}
						on:click={() => dispatch('remove')}
					>
						<svg class="icon"><use href="#i-trash" /></svg>
					</button>
				</Tooltip>

				<div class="relative" bind:this={menuRoot}>
					<Tooltip text="More segment actions" position="top">
						<button
							type="button"
							class="icon-btn head-action reveal"
							class:active={menuOpen}
							aria-label={`More actions for ${segmentLabel.toLowerCase()}`}
							aria-haspopup="menu"
							aria-expanded={menuOpen}
							bind:this={menuTriggerBtn}
							on:click={toggleMenu}
						>
							<svg class="icon"><use href="#i-more" /></svg>
						</button>
					</Tooltip>

					{#if menuOpen}
						<PromptSegmentActionMenu
							{segment}
							{index}
							{total}
							{segmentDisabled}
							ariaLabel={`More actions for ${segmentLabel.toLowerCase()}`}
							style={menuStyle}
							{pinnedIds}
							hasPromptSyntax={promptSyntax.length > 0}
							hasPromptResources={promptResources.length > 0}
							on:run={(e) => runAction(e.detail)}
							on:togglePin={(e) => togglePin(e.detail)}
						/>
					{/if}
				</div>
			</header>

			<div
				class="card-content segment-content"
				class:with-affix={hasAffix}
				class:with-suffix={!!suffixText}
				class:has-body={hasBody}
			>
				{#if prefixText}
					<Tooltip text="Prefix, joined before your text. Edit it in Segment details." position="top" wrapperClass="segment-affix-slot">
						<button
							type="button"
							class="segment-affix font-mono"
							tabindex="-1"
							aria-label="Prefix {prefixText.trim()}"
							on:click={() => inlineEditor?.focusEditor()}
						>{prefixText.trim()}</button>
					</Tooltip>
				{/if}
				<InlineChipEditor
					bind:this={inlineEditor}
					value={segment.content}
					chips={segment.chips || {}}
					resources={segment.resources || {}}
					on:change={handleContentChange}
					{placeholder}
					disabled={false}
					segmentDisabled={segmentDisabled}
					borderless={true}
					density={compact ? 'compact' : 'default'}
					{variables}
					{variableRolls}
					{onVariableDefChange}
					{onOpenVariableManager}
					{activeTriggerWords}
					{promptResources}
					{resourceFieldValues}
					{resourceFieldLabels}
					{promptSyntax}
					variant="segment-composer"
				/>
				{#if suffixText}
					<Tooltip text="Suffix, joined after your text. Edit it in Segment details." position="top" wrapperClass="segment-affix-slot">
						<button
							type="button"
							class="segment-affix font-mono"
							tabindex="-1"
							aria-label="Suffix {suffixText.trim()}"
							on:click={() => inlineEditor?.focusEditor()}
						>{suffixText.trim()}</button>
					</Tooltip>
				{/if}
			</div>
		</div>
</div>

<PromptSegmentDetailsModal
	isOpen={detailsModalOpen}
	{segment}
	onClose={closeDetailsModal}
	onSave={saveDetails}
/>

<style>
	.section-wrapper.menu-open {
		z-index: 40;
	}

	/* Everything else the old card/header skin owned now comes from the
	   ported `.segment`/`.segment-head`/`.head-action`/… rules in
	   segment-composer.css (imported globally, scoped under
	   `.segment-composer` — see SegmentedPromptEditor.svelte) — this
	   component keeps the legacy `.card`/`.card-head`/`.card-name`/
	   `.icon-btn`/`.off-chip`/`.char-count` class names alongside the mock's
	   own, purely as the existing DOM contract (component tests, e2e specs)
	   nothing here restyles them a second time. */

	.card.last-applied {
		box-shadow: inset 0 0 0 1px rgb(var(--signal) / 0.5);
	}

	/* `:global()` only for the layout/hide hook — the class lands on Tooltip's
	   own wrapper div, which this component's scoping hash never reaches. A
	   defensive CSS-only fallback for browsers/tests with no ResizeObserver;
	   `clusterCollapsed` above removes these buttons from the DOM outright once
	   the observer is live. */
	.card :global(.head-action-btn) {
		display: inline-flex;
		align-items: center;
	}

	@container (max-width: 21.25rem) {
		.card :global(.head-action-btn) {
			display: none;
		}
	}

	.segment-content.with-affix {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-start;
		gap: 0 0.375rem;
	}

	.segment-content.with-affix > :global(.relative) {
		flex: 1 1 auto;
		min-width: 8rem;
		max-width: 100%;
	}

	.segment-content.with-suffix.has-body > :global(.relative) {
		flex: 0 1 auto;
		width: max-content;
	}

	.segment-content.with-affix > :global(.segment-affix-slot) {
		flex: 0 0 auto;
		padding: 0.4rem 0 0 0.75rem;
	}

	.segment-content.with-affix > :global(.segment-affix-slot:last-child) {
		padding-left: 0;
		padding-right: 0.75rem;
	}

	.compact .segment-content.with-affix > :global(.segment-affix-slot) {
		padding-left: 0.625rem;
	}

	.compact .segment-content.with-affix > :global(.segment-affix-slot:last-child) {
		padding-left: 0;
		padding-right: 0.625rem;
	}

	.segment-affix {
		display: inline-flex;
		align-items: center;
		max-width: 16rem;
		padding: 0.1rem 0.4rem;
		border: 1px solid rgb(var(--line-strong));
		border-radius: 4px;
		background: rgb(var(--surface-3) / 0.6);
		color: rgb(var(--fg-muted));
		font-size: 0.6875rem;
		line-height: 1.4;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		cursor: text;
		user-select: none;
	}

	.segment-affix:hover {
		color: rgb(var(--fg));
		border-color: rgb(var(--line-hover));
	}
</style>
