<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { storage } from '$lib/utils/storage';

	export let leftWidth: number = 380;
	export let minWidth: number = 280;
	export let maxWidth: number = 500;
	export let storageKey: string = 'master-detail-width';

	let isResizing = false;
	let containerRef: HTMLElement;

	// Load saved width from localStorage
	onMount(() => {
		const saved = storage.get(storageKey);
		if (saved) {
			const parsed = parseInt(saved, 10);
			if (!isNaN(parsed) && parsed >= minWidth && parsed <= maxWidth) {
				leftWidth = parsed;
			}
		}
	});

	function startResize(event: MouseEvent) {
		isResizing = true;
		document.addEventListener('mousemove', handleResize);
		document.addEventListener('mouseup', stopResize);
		document.body.style.cursor = 'col-resize';
		document.body.style.userSelect = 'none';
	}

	function handleResize(event: MouseEvent) {
		if (!isResizing || !containerRef) return;

		const containerRect = containerRef.getBoundingClientRect();
		const newWidth = event.clientX - containerRect.left;
		const clampedWidth = Math.min(maxWidth, Math.max(minWidth, newWidth));

		leftWidth = clampedWidth;

		// Save to localStorage
		storage.set(storageKey, String(clampedWidth));
	}

	function stopResize() {
		isResizing = false;
		document.removeEventListener('mousemove', handleResize);
		document.removeEventListener('mouseup', stopResize);
		document.body.style.cursor = '';
		document.body.style.userSelect = '';
	}

	onDestroy(() => {
		if (isResizing) {
			document.removeEventListener('mousemove', handleResize);
			document.removeEventListener('mouseup', stopResize);
		}
	});

	function handleKeyDown(event: KeyboardEvent) {
		if (event.key === 'ArrowLeft') {
			leftWidth = Math.max(minWidth, leftWidth - 10);
			storage.set(storageKey, String(leftWidth));
		} else if (event.key === 'ArrowRight') {
			leftWidth = Math.min(maxWidth, leftWidth + 10);
			storage.set(storageKey, String(leftWidth));
		}
	}
</script>

<div class="master-detail flex h-full overflow-hidden" bind:this={containerRef}>
	<!-- Left Pane (List) -->
	<div
		class="list-pane flex-shrink-0 border-r border-line overflow-hidden flex flex-col bg-surface-1/30"
		style="width: {leftWidth}px"
	>
		<slot name="list" />
	</div>

	<!-- Resize Handle -->
	<button
		type="button"
		class="resize-handle flex-shrink-0 w-1 bg-line hover:bg-line-hover cursor-col-resize transition-colors relative group"
		on:mousedown={startResize}
		on:keydown={handleKeyDown}
		role="separator"
		aria-orientation="vertical"
		aria-valuenow={leftWidth}
		aria-valuemin={minWidth}
		aria-valuemax={maxWidth}
		aria-label="Resize panel"
		tabindex="0"
	>
		<span class="absolute inset-y-0 -left-1 -right-1 group-hover:bg-line-hover/20"></span>
	</button>

	<!-- Right Pane (Detail) -->
	<div class="detail-pane flex-1 min-w-0 overflow-hidden flex flex-col bg-surface-2">
		<slot name="detail" />
	</div>
</div>

<style>
	.resize-handle {
		touch-action: none;
	}

	.resize-handle:hover,
	.resize-handle:active {
		background-color: rgb(var(--line-hover));
	}

	.resize-handle:focus {
		outline: none;
		background-color: rgb(var(--signal));
	}

	@media (max-width: 767px) {
		.master-detail {
			flex-direction: column;
		}

		.list-pane {
			width: 100% !important;
			height: min(42%, 22rem);
			border-right: 0;
			border-bottom: 1px solid rgb(var(--line));
		}

		.resize-handle {
			display: none;
		}

		.detail-pane {
			min-height: 0;
		}
	}
</style>
