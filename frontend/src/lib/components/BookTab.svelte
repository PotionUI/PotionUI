<script lang="ts">
	import { createEventDispatcher } from 'svelte';

	export let id: string;
	export let name: string;
	export let isActive: boolean;
	export let canDelete: boolean = true;
	export let isGenerating: boolean = false;
	export let color: string | null = null;

	const dispatch = createEventDispatcher();

	let isEditing = false;
	let editingName = name;
	let inputElement: HTMLInputElement;

	// Color palette
	const paletteColors: Array<string | null> = [
		'#ef4444',
		'#f97316',
		'#eab308',
		'#22c55e',
		'#14b8a6',
		'#3b82f6',
		'#8b5cf6',
		'#ec4899',
		null
	];

	let showPalette = false;
	let paletteElement: HTMLDivElement;
	let paletteX = 0;
	let paletteY = 0;

	function handleRightClick(event: MouseEvent) {
		event.preventDefault();
		paletteX = event.clientX;
		paletteY = event.clientY;
		showPalette = !showPalette;
	}

	function selectColor(c: string | null) {
		dispatch('colorChange', { id, color: c });
		showPalette = false;
	}

	function handlePaletteClickOutside(event: MouseEvent) {
		if (showPalette && paletteElement && !paletteElement.contains(event.target as Node)) {
			showPalette = false;
		}
	}

	// Handle double-click to rename
	function handleDoubleClick() {
		if (!isActive) return; // Only allow renaming active tab
		startEditing();
	}

	function startEditing() {
		isEditing = true;
		editingName = name;
		setTimeout(() => {
			inputElement?.focus();
			inputElement?.select();
		}, 0);
	}

	function handleBlur() {
		if (isEditing) {
			finishEditing();
		}
	}

	function handleKeyDown(event: KeyboardEvent) {
		if (event.key === 'Enter') {
			finishEditing();
		} else if (event.key === 'Escape') {
			isEditing = false;
			editingName = name;
		}
	}

	function finishEditing() {
		isEditing = false;
		const trimmedName = editingName.trim();
		if (trimmedName && trimmedName !== name) {
			dispatch('rename', { id, name: trimmedName });
		} else {
			editingName = name;
		}
	}

	function handleClick() {
		if (!isEditing) {
			dispatch('select', { id });
		}
	}

	function handleDelete(event: Event) {
		event.stopPropagation();
		dispatch('delete', { id });
	}

	// Drag and drop handlers
	let isDragging = false;
	let dragOverPosition: 'none' | 'left' | 'right' = 'none';

	function handleDragStart(event: DragEvent) {
		if (isEditing) return;
		isDragging = true;
		event.dataTransfer!.effectAllowed = 'move';
		event.dataTransfer!.setData('text/plain', id);
		dispatch('dragstart', { id });
	}

	function handleDragEnd() {
		isDragging = false;
		dragOverPosition = 'none';
		dispatch('dragend', { id });
	}

	function handleDragOver(event: DragEvent) {
		event.preventDefault();
		if (isDragging) return; // Don't show drop indicator on self

		event.dataTransfer!.dropEffect = 'move';

		// Calculate which side of the tab the cursor is on
		const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
		const midpoint = rect.left + rect.width / 2;
		dragOverPosition = event.clientX < midpoint ? 'left' : 'right';
	}

	function handleDragLeave() {
		dragOverPosition = 'none';
	}

	function handleDrop(event: DragEvent) {
		event.preventDefault();
		const draggedId = event.dataTransfer!.getData('text/plain');

		if (draggedId !== id) {
			dispatch('drop', { draggedId, targetId: id, position: dragOverPosition });
		}

		dragOverPosition = 'none';
	}

	// Compute active border color: use tab color if set, else accent token
	$: activeBorderColor = isActive ? (color || 'rgb(var(--accent))') : 'transparent';
</script>

<svelte:window on:click={handlePaletteClickOutside} />

<div
	class="book-tab-wrapper"
	class:dragging={isDragging}
	class:drag-over-left={dragOverPosition === 'left'}
	class:drag-over-right={dragOverPosition === 'right'}
	draggable={!isEditing}
	on:dragstart={handleDragStart}
	on:dragend={handleDragEnd}
	on:dragover={handleDragOver}
	on:dragleave={handleDragLeave}
	on:drop={handleDrop}
>
	<button
		type="button"
		class="book-tab"
		class:active={isActive}
		class:generating={isGenerating}
		style="border-bottom-color: {activeBorderColor};"
		on:click={handleClick}
		on:dblclick={handleDoubleClick}
		on:contextmenu={handleRightClick}
	>
		<!-- Tab content -->
		<div class="tab-content">
			{#if color}
				<span class="color-dot" style="background-color: {color};"></span>
			{/if}
			{#if isEditing}
				<input
					bind:this={inputElement}
					bind:value={editingName}
					type="text"
					class="tab-input"
					on:blur={handleBlur}
					on:keydown={handleKeyDown}
					on:click|stopPropagation
				/>
			{:else}
				<span class="tab-name">{name}</span>
			{/if}
		</div>

		<!-- Close button -->
		{#if canDelete && !isEditing}
			<button
				type="button"
				class="close-button"
				on:click={handleDelete}
				title="Close tab"
			>
				<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
				</svg>
			</button>
		{/if}
	</button>

</div>

<!-- Color palette rendered as fixed overlay to escape overflow containers -->
{#if showPalette}
	<div class="color-palette-overlay" bind:this={paletteElement} style="left: {paletteX}px; top: {paletteY}px;" on:click|stopPropagation>
		{#each paletteColors as c}
			<button
				type="button"
				class="palette-swatch"
				class:active-swatch={color === c}
				style={c ? `background-color: ${c};` : ''}
				title={c ? c : 'No color'}
				on:click={() => selectColor(c)}
			>
				{#if c === null}
					<svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.5">
						<line x1="1" y1="1" x2="11" y2="11" />
						<line x1="11" y1="1" x2="1" y2="11" />
					</svg>
				{/if}
			</button>
		{/each}
	</div>
{/if}

<style>
	.book-tab-wrapper {
		position: relative;
		flex-shrink: 0;
		transition: transform 0.15s ease;
		margin-right: 0.25rem;
	}

	.book-tab-wrapper.dragging {
		opacity: 0.5;
		transform: scale(0.95);
	}

	/* Drop indicators — drag targets use signal ("where it will go") */
	.book-tab-wrapper.drag-over-left::before,
	.book-tab-wrapper.drag-over-right::after {
		content: '';
		position: absolute;
		top: 0;
		bottom: 0;
		width: 2px;
		background-color: rgb(var(--signal));
		z-index: 10;
	}

	.book-tab-wrapper.drag-over-left::before {
		left: -1px;
	}

	.book-tab-wrapper.drag-over-right::after {
		right: -1px;
	}

	.book-tab {
		position: relative;
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0.5rem 1rem;
		padding-bottom: 0.5rem;
		background: rgb(var(--fg) / 0.02);
		border: none;
		border-bottom: 2px solid transparent;
		cursor: pointer;
		font-size: 0.8125rem;
		line-height: 1.25rem;
		font-weight: 500;
		color: rgb(var(--fg-muted));
		transition: color 0.15s ease, background-color 0.15s ease, border-color 0.15s ease;
		border-radius: 0.375rem 0.375rem 0 0;
		white-space: nowrap;
		user-select: none;
		outline: none;
	}

	.book-tab:hover:not(.active) {
		color: rgb(var(--fg));
		background: rgb(var(--fg) / 0.05);
	}

	/* Active tab — flat raised surface + machined top edge; the underline carries the state */
	.book-tab.active {
		color: rgb(var(--fg));
		font-weight: 600;
		background: rgb(var(--surface-2));
		box-shadow: inset 0 1px 0 rgb(var(--fg) / 0.08);
	}

	/* Generating state - flat opacity pulse (no shimmer sweep) */
	.book-tab.generating {
		color: rgb(var(--fg-muted));
		animation: skeleton-pulse 1.2s ease-in-out infinite;
	}

	.book-tab.generating.active {
		color: rgb(var(--fg));
	}

	@keyframes skeleton-pulse {
		0%,
		100% {
			opacity: 0.55;
		}
		50% {
			opacity: 1;
		}
	}

	.tab-content {
		display: flex;
		align-items: center;
		gap: 0.375rem;
		flex: 1;
		min-width: 0;
	}

	.color-dot {
		display: inline-block;
		width: 7px;
		height: 7px;
		border-radius: 50%;
		flex-shrink: 0;
	}

	.tab-name {
		flex: 1;
		text-align: left;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.tab-input {
		width: 100%;
		min-width: 80px;
		padding: 0.125rem 0.25rem;
		background: rgb(var(--surface-2));
		border: 1px solid rgb(var(--accent));
		border-radius: 0.25rem;
		font-size: 0.8125rem;
		font-weight: 500;
		color: rgb(var(--fg));
		outline: none;
	}

	.close-button {
		display: flex;
		align-items: center;
		justify-content: center;
		padding: 0.125rem;
		background: transparent;
		border: none;
		color: rgb(var(--fg-subtle));
		cursor: pointer;
		border-radius: 0.25rem;
		transition: color 0.15s ease, background-color 0.15s ease, opacity 0.15s ease;
		opacity: 0.4;
	}

	.book-tab:hover .close-button {
		opacity: 1;
	}

	.close-button:hover {
		color: rgb(var(--danger));
		background: rgb(var(--danger) / 0.1);
	}

	/* Color palette - rendered as fixed overlay outside scoped tree */
	:global(.color-palette-overlay) {
		position: fixed;
		z-index: 9999;
		display: flex;
		gap: 4px;
		padding: 6px;
		background: rgb(var(--surface-1));
		border: 1px solid rgb(var(--line-strong));
		border-radius: 6px;
		box-shadow: var(--shadow-floating);
		transform: translate(-50%, -100%) translateY(-8px);
	}

	:global(.color-palette-overlay .palette-swatch) {
		width: 20px;
		height: 20px;
		border-radius: 50%;
		border: 2px solid transparent;
		cursor: pointer;
		padding: 0;
		display: flex;
		align-items: center;
		justify-content: center;
		flex-shrink: 0;
		transition: border-color 0.1s ease, transform 0.1s ease;
	}

	:global(.color-palette-overlay .palette-swatch:last-child) {
		background: rgb(var(--surface-3));
		border-color: rgb(var(--line-strong));
		color: rgb(var(--fg-subtle));
	}

	:global(.color-palette-overlay .palette-swatch:hover) {
		transform: scale(1.2);
		border-color: rgb(var(--accent));
	}

	:global(.color-palette-overlay .palette-swatch.active-swatch) {
		border-color: rgb(var(--accent));
	}
</style>
