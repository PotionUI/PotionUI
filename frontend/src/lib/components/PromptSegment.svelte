<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import type { Segment, ChipData } from '$lib/types/segments';
	import type { VariablesMap, VariableDef, VariableRoll } from '$lib/utils/variableDefs';
	import InlineChipEditor from './InlineChipEditor.svelte';
	import Tooltip from './Tooltip.svelte';
	import Icon from './Icon.svelte';
	import PromptSegmentActionMenu from './PromptSegmentActionMenu.svelte';
	import PromptSegmentMetadataEditor from './PromptSegmentMetadataEditor.svelte';
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
	let metadataOpen = false;
	let menuRoot: HTMLElement;
	let menuTriggerBtn: HTMLButtonElement;
	let menuStyle = '';
	let wrapperEl: HTMLDivElement;
	let scrolledNonce = -1;

	$: isBreakSegment = segment.type === 'break';
	$: segmentDisabled = segment.enabled === false || !!segment.isDisabled;
	$: displayName = segmentDisplayName(segment);
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

	function toggleMetadataEditor() {
		menuOpen = false;
		metadataOpen = !metadataOpen;
	}

	function runFooterAction(id: string) {
		if (id === 'editDetails') {
			toggleMetadataEditor();
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
						on:editDetails={toggleMetadataEditor}
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

		{#if metadataOpen}
			<div class="break-metadata">
				<PromptSegmentMetadataEditor
					{segment}
					{compact}
					on:change={(e) => dispatch('metadataChange', e.detail)}
				/>
			</div>
		{/if}
	{:else}
		<div
			class="card"
			class:disabled={segmentDisabled}
			class:details-open={metadataOpen}
			class:last-applied={isLastApplied}
		>
			{#if !segmentDisabled}
				<div
					class="accent-bar"
					class:has-color={!!segment.color}
					style={segment.color ? `background-color: ${segment.color};` : undefined}
					aria-hidden="true"
				></div>
			{/if}

			<div class="card-head">
				<span class="index font-mono tabular-nums" aria-hidden="true">{formatSegmentIndex(index)}</span>

				<button
					type="button"
					class="card-name"
					class:unnamed={!displayName}
					class:struck={segmentDisabled}
					aria-label={displayName ? `Rename ${segmentLabel.toLowerCase()}` : `Name ${segmentLabel.toLowerCase()}`}
					on:click={toggleMetadataEditor}
				>
					{displayName || UNNAMED_SEGMENT_PLACEHOLDER}
				</button>

				{#if segmentDisabled}
					<span class="head-note">{DISABLED_SEGMENT_NOTE}</span>
				{:else if segment.template}
					<span class="head-note truncate">from template slot “{segment.template.slot}”</span>
				{/if}
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

			{#if metadataOpen}
				<div class="card-details">
					<PromptSegmentMetadataEditor
						{segment}
						{compact}
						on:change={(e) => dispatch('metadataChange', e.detail)}
					/>
				</div>
			{/if}

			<div class="card-footer">
				{#each footerActions as action (action.id)}
					<button
						type="button"
						class="footer-btn"
						class:enable={action.id === 'toggleDisabled' && segmentDisabled}
						class:active={action.id === 'editDetails' && metadataOpen}
						aria-pressed={action.id === 'editDetails' ? metadataOpen : undefined}
						on:click={() => runFooterAction(action.id)}
					>
						<Icon name={action.icon} className="h-3.5 w-3.5 flex-shrink-0" />
						<span class="footer-label">{action.label}</span>
					</button>
				{/each}

				<div class="footer-trailing">
					<span class="char-count font-mono tabular-nums">{charCount} chars</span>

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
								footerActionsShown={true}
								ariaLabel={`More actions for ${segmentLabel.toLowerCase()}`}
								style={menuStyle}
								on:moveUp={() => runMenuAction('moveUp')}
								on:moveDown={() => runMenuAction('moveDown')}
								on:editDetails={toggleMetadataEditor}
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
		</div>
	{/if}
</div>

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

	.card {
		overflow: hidden;
		border: 1px solid rgb(var(--line));
		border-radius: 0.375rem;
		background-color: rgb(var(--surface-1));
		transition: border-color 0.15s ease;
	}

	.card.details-open {
		border-color: rgb(var(--line-strong));
	}

	.card.last-applied {
		box-shadow: inset 0 0 0 1px rgb(var(--signal) / 0.5);
	}

	/* Disabled reads by absence — dashed edge, no accent bar, sunk to the page
	   tint — never as an error tone. */
	.card.disabled {
		border-style: dashed;
		background-color: rgb(var(--canvas));
	}

	.accent-bar {
		height: 2px;
		background-color: rgb(var(--line-strong));
	}

	.card-head {
		display: flex;
		align-items: baseline;
		gap: 0.5625rem;
		padding: 0.6875rem 0.875rem 0;
	}

	.section-wrapper.compact .card-head {
		padding: 0.5rem 0.625rem 0;
	}

	.index {
		flex-shrink: 0;
		font-size: 0.6875rem;
		color: rgb(var(--fg-subtle));
	}

	.card.disabled .index {
		color: rgb(var(--fg-disabled));
	}

	.card-name {
		min-width: 0;
		flex-shrink: 1;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 0.8125rem;
		font-weight: 600;
		color: rgb(var(--fg));
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

	.card-name:hover {
		color: rgb(var(--fg));
	}

	.head-note {
		min-width: 0;
		font-size: 0.6875rem;
		color: rgb(var(--fg-subtle));
	}

	.card-content {
		padding: 0 0.125rem 0.25rem;
	}

	/* The mock's content setting: 15/26, pretty-wrapped. Deliberately NOT
	   measure-capped — 66ch is a reading measure, and this element is the input
	   itself, so capping it would shrink the typing area and the click target
	   inside a wider card. The measure belongs on read-only text (the resolved
	   panel), where it does not fight the caret. */
	.card-content :global(.inline-chip-editor) {
		font-size: 0.9375rem;
		line-height: 1.7333;
		text-wrap: pretty;
	}

	.card.disabled .card-content :global(.inline-chip-editor) {
		color: rgb(var(--fg-disabled));
	}

	.card-details {
		padding: 0 0.875rem 0.75rem;
	}

	.section-wrapper.compact .card-details {
		padding: 0 0.625rem 0.625rem;
	}

	.card-footer {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.25rem;
		border-top: 1px solid rgb(var(--surface-2));
		padding: 0.375rem 0.625rem;
		background-color: rgb(var(--canvas));
	}

	/* The disabled card already sits on the page tint, so its footer takes no
	   second fill — the strip reads as part of the same sunk surface. */
	.card.disabled .card-footer {
		background-color: transparent;
	}

	.footer-trailing {
		display: flex;
		flex-shrink: 0;
		align-items: center;
		gap: 0.25rem;
		margin-left: auto;
	}

	.footer-btn {
		display: inline-flex;
		min-height: 1.625rem;
		flex-shrink: 0;
		align-items: center;
		gap: 0.375rem;
		border-radius: 0.25rem;
		padding: 0 0.5rem;
		font-size: 0.6875rem;
		color: rgb(var(--fg-muted));
		transition: color 0.15s, background-color 0.15s;
	}

	.card.disabled .footer-btn {
		color: rgb(var(--fg-subtle));
	}

	.footer-btn:hover,
	.footer-btn:focus-visible {
		color: rgb(var(--fg));
		background-color: rgb(var(--surface-2));
		outline: none;
	}

	.footer-btn.active {
		color: rgb(var(--fg));
		background-color: rgb(var(--surface-2));
	}

	/* Enable is state, not an action: signal tint, never a solid blue button. */
	.footer-btn.enable,
	.card.disabled .footer-btn.enable {
		color: rgb(var(--signal));
		background-color: rgb(var(--signal) / 0.17);
	}

	.footer-btn.enable:hover {
		background-color: rgb(var(--signal) / 0.18);
	}

	.char-count {
		margin-right: 0.25rem;
		font-size: 0.625rem;
		color: rgb(var(--fg-subtle));
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

	.break-metadata {
		padding: 0.5rem 0.25rem 0;
	}

	.icon-btn {
		display: inline-flex;
		min-width: 1.625rem;
		min-height: 1.625rem;
		flex-shrink: 0;
		align-items: center;
		justify-content: center;
		border-radius: 0.25rem;
		color: rgb(var(--fg-muted));
		transition: color 0.15s, background-color 0.15s;
	}

	.card.disabled .icon-btn {
		color: rgb(var(--fg-subtle));
	}

	.icon-btn:hover,
	.icon-btn.active {
		color: rgb(var(--fg));
		background-color: rgb(var(--surface-2));
	}

	/* Narrow columns (the generate sidebar) keep every footer action visible and
	   labelled by wrapping the strip; only below ~20rem do the labels go, and
	   the buttons stay reachable by their aria-label and tooltip. */
	@container (max-width: 20rem) {
		.footer-label {
			display: none;
		}

		.char-count {
			display: none;
		}
	}
</style>
