<script lang="ts">
	import { createEventDispatcher, onMount, onDestroy } from 'svelte';
	import type { Segment, ChipData } from '$lib/types/segments';
	import type { VariablesMap, VariableDef, VariableRoll } from '$lib/utils/variableDefs';
	import InlineChipEditor from './InlineChipEditor.svelte';
	import Tooltip from './Tooltip.svelte';
	import Icon from './Icon.svelte';
	import PromptSegmentActionMenu from './PromptSegmentActionMenu.svelte';
	import PromptSegmentDetailsModal from './PromptSegmentDetailsModal.svelte';
	import { computeFixedMenuPosition } from '$lib/utils/menuPosition';
	import { lastAppliedSegment } from '$lib/stores/lastAppliedSegment';
	import {
		DISABLED_SEGMENT_NOTE,
		UNNAMED_SEGMENT_PLACEHOLDER,
		formatSegmentIndex,
		segmentCharCount,
		segmentDisplayName,
		segmentFooterActions
	} from '$lib/utils/segmentFooter';

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
	let scrolledNonce = -1;

	// The header sheds detail in stages as its own width shrinks: below
	// CHAR_COUNT_HIDE_WIDTH the char count drops out of the row (its value
	// still reachable — folded into the Details tooltip); below
	// CLUSTER_COLLAPSE_WIDTH the four content actions fold into the overflow
	// menu (leaving grip + more). An inline description becomes an info icon
	// either at that same collapse, or earlier still if the name (which never
	// shrinks — see .card-name) is long enough that the description would be
	// left with an unreadable sliver of its own; HEADER_RESERVED_WIDTH is
	// everything else on the row (index, gaps, spacer, char count, the full
	// action cluster) at its most generous, so this stays a same-or-earlier
	// swap, never later than the width-only collapse. jsdom has no
	// ResizeObserver, so component tests always see containerWidth 0, i.e.
	// the widest, most-expanded shape — the one those tests assert against.
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

	$: isBreakSegment = segment.type === 'break';
	$: segmentDisabled = segment.enabled === false || !!segment.isDisabled;
	$: displayName = segmentDisplayName(segment);
	$: hasDescription = !!segment.description && segment.description.trim().length > 0;
	$: footerActions = segmentFooterActions(segment);
	$: charCount = segmentCharCount(segment);
	$: segmentLabel = isBreakSegment
		? `Break ${index + 1} of ${total}`
		: `${isNegative ? 'Negative segment' : 'Positive segment'} ${index + 1} of ${total}`;
	$: isLastApplied = $lastAppliedSegment?.segmentId === segment.id;
	$: handleAppliedChange($lastAppliedSegment);

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

	function handleContentChange(e: CustomEvent<{ value: string; chips: Record<string, ChipData> }>) {
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
		if (event.key === 'Escape') menuOpen = false;
	}

	function handleWindowScroll() {
		if (menuOpen) menuOpen = false;
	}

	function handleWindowResize() {
		if (menuOpen) menuOpen = false;
	}

	function computeMenuPosition() {
		if (!menuTriggerBtn) return;
		menuStyle = computeFixedMenuPosition(menuTriggerBtn);
	}

	function toggleMenu() {
		if (!menuOpen) computeMenuPosition();
		menuOpen = !menuOpen;
	}

	function runMenuAction(eventName: string, detail?: unknown) {
		menuOpen = false;
		dispatch(eventName, detail);
	}

	function openDetailsModal() {
		menuOpen = false;
		detailsModalOpen = true;
	}

	function closeDetailsModal() {
		detailsModalOpen = false;
	}

	function saveDetails(updates: { name?: string; color?: string; description?: string }) {
		dispatch('metadataChange', updates);
	}

	function runFooterAction(id: string) {
		if (id === 'editDetails') {
			openDetailsModal();
			return;
		}
		dispatch(id);
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
	class:dragging={isDragging}
	class:drag-over-top={dragOverPosition === 'top'}
	class:drag-over-bottom={dragOverPosition === 'bottom'}
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
	{#if isBreakSegment}
		<div class="break-row">
			<span class="index font-mono tabular-nums" aria-hidden="true">{formatSegmentIndex(index)}</span>
			<span class="break-rule" aria-hidden="true"></span>
			<span class="break-pill font-mono">BREAK</span>
			<span class="break-rule" aria-hidden="true"></span>

			<Tooltip text={total <= 1 ? 'Every prompt needs at least one segment' : 'Remove segment'} position="top">
				<button
					type="button"
					class="icon-btn delete"
					aria-label="Remove segment"
					disabled={total <= 1}
					on:click={() => dispatch('remove')}
				>
					<Icon name="trash" className="h-3.5 w-3.5" />
				</button>
			</Tooltip>

			<Tooltip text="Drag to reorder" position="top">
				<button
					type="button"
					class="icon-btn cursor-grab active:cursor-grabbing"
					aria-label={`Drag ${segmentLabel.toLowerCase()} to reorder`}
					on:pointerdown={enableDrag}
				>
					<Icon name="grip" className="h-3.5 w-3.5" />
				</button>
			</Tooltip>

			<div class="relative" bind:this={menuRoot}>
				<Tooltip text="Segment actions" position="top">
					<button
						type="button"
						class="icon-btn"
						class:active={menuOpen}
						aria-label={`Actions for ${segmentLabel.toLowerCase()}`}
						aria-haspopup="menu"
						aria-expanded={menuOpen}
						bind:this={menuTriggerBtn}
						on:click={toggleMenu}
					>
						<Icon name="more" className="h-3.5 w-3.5" />
					</button>
				</Tooltip>
				{#if menuOpen}
					<PromptSegmentActionMenu
						{index}
						{total}
						isBreakSegment={true}
						{segmentDisabled}
						ariaLabel={`Actions for ${segmentLabel.toLowerCase()}`}
						style={menuStyle}
						on:moveUp={() => runMenuAction('moveUp')}
						on:moveDown={() => runMenuAction('moveDown')}
						on:editDetails={openDetailsModal}
						on:saveAsSegment={() => runMenuAction('saveAsSegment')}
						on:replaceFromSaved={() => runMenuAction('replaceFromSaved')}
						on:toggleBreak={() => runMenuAction('toggleBreak')}
						on:duplicate={() => runMenuAction('duplicate')}
						on:toggleDisabled={() => runMenuAction('toggleDisabled')}
						on:remove={() => runMenuAction('remove')}
					/>
				{/if}
			</div>
		</div>
	{:else}
		<div
			class="card"
			class:disabled={segmentDisabled}
			class:last-applied={isLastApplied}
			class:has-color={!segmentDisabled && !!segment.color}
			style={!segmentDisabled && segment.color ? `--seg-color: ${segment.color};` : undefined}
			bind:this={cardEl}
		>
			<div class="card-head">
				<span class="index font-mono tabular-nums" aria-hidden="true">{formatSegmentIndex(index)}</span>

				<button
					type="button"
					class="card-name"
					class:unnamed={!displayName}
					class:struck={segmentDisabled}
					aria-label={displayName ? `Rename ${segmentLabel.toLowerCase()}` : `Name ${segmentLabel.toLowerCase()}`}
					on:click={openDetailsModal}
					bind:this={nameEl}
				>
					{displayName || UNNAMED_SEGMENT_PLACEHOLDER}
				</button>

				{#if segmentDisabled}
					<Tooltip text={DISABLED_SEGMENT_NOTE} position="top">
						<span class="state-chip off-chip font-mono">off</span>
					</Tooltip>
				{/if}

				{#if hasDescription}
					{#if descriptionAsIcon}
						<Tooltip text={segment.description ?? ''} position="top">
							<button
								type="button"
								class="icon-btn description-icon"
								aria-label={`Description: ${segment.description}`}
							>
								<Icon name="info" className="h-3.5 w-3.5" />
							</button>
						</Tooltip>
					{:else}
						<span class="head-description" title={segment.description}>{segment.description}</span>
					{/if}
				{/if}

				<span class="head-spacer" aria-hidden="true"></span>

				{#if !charCountHidden}
					<Tooltip text={`${charCount} characters`} position="top">
						<span class="char-count font-mono tabular-nums">{charCount}</span>
					</Tooltip>
				{/if}

				<div class="head-actions">
					{#if !clusterCollapsed}
						{#each footerActions as action (action.id)}
							<Tooltip
								text={action.id === 'editDetails' && charCountHidden
									? `${action.label} — ${charCount} ch`
									: action.label}
								position="top"
								wrapperClass="head-action-btn"
							>
								<button
									type="button"
									class="icon-btn"
									aria-label={action.label}
									on:click={() => runFooterAction(action.id)}
								>
									<Icon name={action.icon} className="h-3.5 w-3.5" />
								</button>
							</Tooltip>
						{/each}
					{/if}

					<Tooltip text={total <= 1 ? 'Every prompt needs at least one segment' : 'Remove segment'} position="top">
						<button
							type="button"
							class="icon-btn delete"
							aria-label="Remove segment"
							disabled={total <= 1}
							on:click={() => dispatch('remove')}
						>
							<Icon name="trash" className="h-3.5 w-3.5" />
						</button>
					</Tooltip>

					<Tooltip text="Drag to reorder" position="top">
						<button
							type="button"
							class="icon-btn cursor-grab active:cursor-grabbing"
							aria-label={`Drag ${segmentLabel.toLowerCase()} to reorder`}
							on:pointerdown={enableDrag}
						>
							<Icon name="grip" className="h-3.5 w-3.5" />
						</button>
					</Tooltip>

					<div class="relative" bind:this={menuRoot}>
						<Tooltip text="More segment actions" position="top">
							<button
								type="button"
								class="icon-btn"
								class:active={menuOpen}
								aria-label={`More actions for ${segmentLabel.toLowerCase()}`}
								aria-haspopup="menu"
								aria-expanded={menuOpen}
								bind:this={menuTriggerBtn}
								on:click={toggleMenu}
							>
								<Icon name="more" className="h-3.5 w-3.5" />
							</button>
						</Tooltip>

						{#if menuOpen}
							<PromptSegmentActionMenu
								{index}
								{total}
								isBreakSegment={false}
								{segmentDisabled}
								footerActionsShown={!clusterCollapsed}
								ariaLabel={`More actions for ${segmentLabel.toLowerCase()}`}
								style={menuStyle}
								on:moveUp={() => runMenuAction('moveUp')}
								on:moveDown={() => runMenuAction('moveDown')}
								on:editDetails={openDetailsModal}
								on:saveAsSegment={() => runMenuAction('saveAsSegment')}
								on:replaceFromSaved={() => runMenuAction('replaceFromSaved')}
								on:toggleBreak={() => runMenuAction('toggleBreak')}
								on:duplicate={() => runMenuAction('duplicate')}
								on:toggleDisabled={() => runMenuAction('toggleDisabled')}
								on:remove={() => runMenuAction('remove')}
							/>
						{/if}
					</div>
				</div>
			</div>

			<div class="card-content">
				<InlineChipEditor
					value={segment.content}
					chips={segment.chips || {}}
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
				/>
			</div>
		</div>
	{/if}
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

	.section-wrapper.dragging {
		opacity: 0.5;
	}

	.section-wrapper.drag-over-top::before,
	.section-wrapper.drag-over-bottom::after {
		content: '';
		position: absolute;
		left: 0;
		right: 0;
		height: 2px;
		background-color: rgb(var(--signal));
		z-index: 10;
	}

	.section-wrapper.drag-over-top::before {
		top: -1px;
	}

	.section-wrapper.drag-over-bottom::after {
		bottom: -1px;
	}

	/* No overflow:hidden — the rail below is a real border, so it already
	   follows the card's own radius with no clipping needed, and a header
	   icon's tooltip is never clipped by the card either. */
	.card {
		border: 1px solid rgb(var(--line));
		border-left: 3px solid rgb(var(--line-strong));
		border-radius: 0.375rem;
		background-color: rgb(var(--surface-1));
		transition: border-color 0.15s ease;
	}

	/* Colour reads as a permanent 3px rail on the leading edge, not a top bar
	   — the header sits directly beside it instead of under a strip. */
	.card.has-color {
		border-left-color: var(--seg-color);
	}

	.card.last-applied {
		box-shadow: inset 0 0 0 1px rgb(var(--signal) / 0.5);
	}

	/* Disabled reads by the Off chip and dimmed content, never as an error
	   tone or a sunken/dashed frame — the card keeps its normal shape. */

	/* Deliberately thin padding — the 28px icon buttons set the row's real
	   height, so the row itself adds as little on top of that as possible,
	   keeping this a quiet meta strip rather than a second content band. */
	.card-head {
		display: flex;
		align-items: center;
		gap: 0.4375rem;
		padding: 0.25rem 0.5rem 0.25rem 0.625rem;
	}

	.section-wrapper.compact .card-head {
		padding: 0.1875rem 0.4375rem 0.1875rem 0.5625rem;
	}

	.index {
		flex-shrink: 0;
		font-size: 0.625rem;
		color: rgb(var(--fg-subtle));
	}

	.card.disabled .index {
		color: rgb(var(--fg-disabled));
	}

	/* flex-shrink: 0 rather than a merely-larger shrink factor on the
	   description — flexbox always splits a deficit proportionally to
	   shrink-factor × basis, so any ratio still lets a long description eat
	   into the name once the numbers are big enough. Zero removes the name
	   from that math entirely: the description gives up its own room first,
	   down to the icon fallback once the header collapses, well before a
	   normal name would ever need to. The ellipsis stays as a safety net for
	   a name that's pathologically long even on its own. */
	/* Quiet on purpose: the header is metadata, the content below is the
	   prompt itself, and the content is the only fg-strength text on the
	   card — the name only lifts to fg on hover/focus, a hint rather than a
	   standing emphasis. */
	.card-name {
		min-width: 0;
		flex-shrink: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		max-width: 60%;
		font-size: 0.75rem;
		font-weight: 500;
		color: rgb(var(--fg-muted));
		text-align: left;
	}

	.card-name.unnamed {
		font-style: italic;
		font-weight: 400;
		color: rgb(var(--fg-subtle));
		cursor: text;
	}

	.card-name.struck {
		color: rgb(var(--fg-subtle));
		text-decoration: line-through;
	}

	.card:hover .card-name,
	.card:focus-within .card-name {
		color: rgb(var(--fg));
	}

	.state-chip {
		flex-shrink: 0;
		border-radius: 0.25rem;
		padding: 0.125rem 0.3125rem;
		font-size: 0.5625rem;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: rgb(var(--fg-subtle));
	}

	.state-chip.off-chip {
		background-color: rgb(var(--surface-2));
	}

	/* Shrinks before the name ever does. Flexbox distributes a shrink deficit
	   proportional to shrink-factor × basis, so a merely *larger* factor than
	   the name's still lets a short description eat into a long name; this
	   factor is deliberately lopsided so the split is "practically all
	   description" for any normal name/description pair, with the name's own
	   flex-shrink: 1 staying only as a last-resort safety valve against a
	   pathologically long name with no description to make room. */
	.head-description {
		min-width: 0;
		flex-shrink: 20;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 0.75rem;
		color: rgb(var(--fg-subtle));
	}

	/* Sits after the chip/description: absorbs any leftover width so the
	   actions cluster hugs the right edge, while the name above only ever
	   takes the room its own text needs instead of stretching to fill it. */
	.head-spacer {
		flex: 1 1 auto;
		min-width: 0.25rem;
	}

	.char-count {
		flex-shrink: 0;
		font-size: 0.625rem;
		color: rgb(var(--fg-subtle));
	}

	.head-actions {
		display: flex;
		flex-shrink: 0;
		align-items: center;
		gap: 0.125rem;
	}

	/* `:global()` only for the layout/hide hook — the class lands on Tooltip's
	   own wrapper div, which this component's scoping hash never reaches. The
	   resting/hover colors live on `.icon-btn` itself below, which IS this
	   component's own markup (slotted into Tooltip, but still scoped to it),
	   so no :global() is needed there. */
	.card :global(.head-action-btn) {
		display: inline-flex;
		align-items: center;
	}

	.card-content {
		padding: 0 0 0.25rem;
	}

	/* The mock's content setting: 15/1.6, pretty-wrapped. Deliberately NOT
	   measure-capped — 66ch is a reading measure, and this element is the input
	   itself, so capping it would shrink the typing area and the click target
	   inside a wider card. The measure belongs on read-only text (the resolved
	   panel), where it does not fight the caret. */
	.card-content :global(.inline-chip-editor) {
		font-size: 0.9375rem;
		line-height: 1.6;
		text-wrap: pretty;
	}

	.card.disabled .card-content :global(.inline-chip-editor) {
		color: rgb(var(--fg-disabled));
	}

	.break-row {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		padding: 0.125rem 0.25rem;
	}

	.break-rule {
		flex: 1 1 auto;
		min-width: 0.75rem;
		height: 0;
		border-top: 1px solid rgb(var(--line-strong));
	}

	.break-pill {
		flex-shrink: 0;
		border-radius: 0.25rem;
		padding: 0.1875rem 0.5625rem;
		font-size: 0.6875rem;
		font-weight: 600;
		letter-spacing: 0.1em;
		color: rgb(var(--fg-muted));
		background-color: rgb(var(--surface-1));
		box-shadow: inset 0 0 0 1px rgb(var(--line-strong));
	}

	/* Rest at fg-subtle so a row of resting cards stays calm; the whole
	   cluster brightens once its card is hovered or focused-within, and the
	   hovered/active button itself goes further to full fg. */
	.icon-btn {
		display: inline-flex;
		min-width: 1.75rem;
		min-height: 1.75rem;
		flex-shrink: 0;
		align-items: center;
		justify-content: center;
		border-radius: 0.25rem;
		color: rgb(var(--fg-subtle));
		transition: color 0.15s, background-color 0.15s;
	}

	.card:hover .icon-btn,
	.card:focus-within .icon-btn {
		color: rgb(var(--fg-muted));
	}

	.card.disabled .icon-btn {
		color: rgb(var(--fg-disabled));
	}

	.icon-btn:hover,
	.icon-btn:focus-visible,
	.icon-btn.active {
		color: rgb(var(--fg));
		background-color: rgb(var(--surface-2));
	}

	.icon-btn:disabled {
		color: rgb(var(--fg-disabled));
		cursor: not-allowed;
	}

	/* Danger reads on hover only, as a colour change on the same neutral
	   button — never a solid/red-filled button, which would read as a
	   standing warning rather than an ordinary reachable action. */
	.icon-btn.delete:hover:not(:disabled),
	.icon-btn.delete:focus-visible:not(:disabled) {
		color: rgb(var(--danger));
	}

	/* Below ~21.25rem (340px) the cluster has no room for six icon buttons
	   beside a readable name, so it collapses to grip + more; the script's own
	   ResizeObserver crosses the same threshold to fold those four actions
	   back into the overflow menu, so they stay reachable either way. */
	@container (max-width: 21.25rem) {
		.card :global(.head-action-btn) {
			display: none;
		}
	}
</style>
