<script module lang="ts">
	// Generic item type - any object with at least an id
	export interface FuzzyFindItem {
		id: string;
		label: string;
		description?: string;
		imageUrl?: string;
		/** SVG path `d` data, rendered as a small icon tile when no imageUrl is set. */
		icon?: string;
		/** Short tag rendered next to the label, e.g. to separate item sources in a merged list. */
		badge?: { text: string; variant?: 'neutral' | 'success' | 'warning' | 'danger' | 'info' | 'signal' };
		[key: string]: any;
	}

	// Modal size options
	type ModalSize = 'sm' | 'md' | 'lg';
</script>

<script lang="ts">
	import { createEventDispatcher, onMount, onDestroy } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Badge, Kbd } from '$lib/components/ui';

	export let isOpen: boolean = false;
	export let title: string = 'Select an option';
	export let subtitle: string = '';
	export let items: FuzzyFindItem[] = [];
	export let selectedId: string | null = null;
	export let placeholder: string = 'Search...';
	export let emptyMessage: string = 'No items found';
	export let showDescriptions: boolean = true;
	export let showImages: boolean = true;
	export let size: ModalSize = 'sm';

	const dispatch = createEventDispatcher<{
		select: FuzzyFindItem;
		close: void;
	}>();

	let searchQuery = '';
	let searchInput: HTMLInputElement;
	let selectedIndex = 0;
	let listContainer: HTMLDivElement;
	let showPreview = false;

	// Size configurations
	const sizeConfig = {
		sm: {
			modalWidth: 'max-w-xs',
			listHeight: 'max-h-48',
			imageSize: 'w-6 h-6',
			itemPadding: 'px-3 py-1.5',
			itemGap: 'gap-2',
			previewSize: 'w-48 h-48'
		},
		md: {
			modalWidth: 'max-w-lg',
			listHeight: 'max-h-[50vh]',
			imageSize: 'w-16 h-16',
			itemPadding: 'px-4 py-3',
			itemGap: 'gap-4',
			previewSize: 'w-64 h-64'
		},
		lg: {
			modalWidth: 'max-w-2xl',
			listHeight: 'max-h-[500px]',
			imageSize: 'w-24 h-24',
			itemPadding: 'px-5 py-4',
			itemGap: 'gap-5',
			previewSize: 'w-80 h-80'
		}
	};

	$: config = sizeConfig[size];

	// Get currently selected item for preview
	$: currentItem = filteredItems[selectedIndex] || null;

	// Filter items based on search query
	$: filteredItems = items.filter((item) => {
		const query = searchQuery.toLowerCase();
		return (
			item.label.toLowerCase().includes(query) ||
			(item.description && item.description.toLowerCase().includes(query))
		);
	});

	// Reset selection when filter changes
	$: {
		if (filteredItems.length > 0) {
			const currentSelectedIdx = filteredItems.findIndex((item) => item.id === selectedId);
			selectedIndex = currentSelectedIdx >= 0 ? currentSelectedIdx : 0;
		} else {
			selectedIndex = 0;
		}
	}

	// Reset preview when modal closes
	$: if (!isOpen) {
		showPreview = false;
	}

	function handleKeydown(e: KeyboardEvent) {
		if (!isOpen) return;

		if (e.key === 'Escape') {
			e.preventDefault();
			if (showPreview) {
				showPreview = false;
			} else {
				handleClose();
			}
		} else if (e.key === 'ArrowDown') {
			e.preventDefault();
			selectedIndex = (selectedIndex + 1) % filteredItems.length;
			scrollToSelected();
		} else if (e.key === 'ArrowUp') {
			e.preventDefault();
			selectedIndex = selectedIndex === 0 ? filteredItems.length - 1 : selectedIndex - 1;
			scrollToSelected();
		} else if (e.key === 'Home' && filteredItems.length > 0) {
			e.preventDefault();
			selectedIndex = 0;
			scrollToSelected();
		} else if (e.key === 'End' && filteredItems.length > 0) {
			e.preventDefault();
			selectedIndex = filteredItems.length - 1;
			scrollToSelected();
		} else if (e.key === 'ArrowLeft') {
			e.preventDefault();
			if (currentItem?.imageUrl) {
				showPreview = !showPreview;
			}
		} else if (e.key === 'ArrowRight') {
			e.preventDefault();
			showPreview = false;
		} else if (e.key === 'Enter') {
			e.preventDefault();
			if (filteredItems[selectedIndex]) {
				handleSelect(filteredItems[selectedIndex]);
			}
		}
	}

	function handleInputKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') {
			e.preventDefault();
			e.stopPropagation();
			if (showPreview) {
				showPreview = false;
			} else {
				searchInput?.blur();
			}
		} else if (e.key === 'ArrowDown') {
			e.preventDefault();
			selectedIndex = (selectedIndex + 1) % filteredItems.length;
			scrollToSelected();
		} else if (e.key === 'ArrowUp') {
			e.preventDefault();
			selectedIndex = selectedIndex === 0 ? filteredItems.length - 1 : selectedIndex - 1;
			scrollToSelected();
		} else if (e.key === 'Home' && filteredItems.length > 0) {
			e.preventDefault();
			selectedIndex = 0;
			scrollToSelected();
		} else if (e.key === 'End' && filteredItems.length > 0) {
			e.preventDefault();
			selectedIndex = filteredItems.length - 1;
			scrollToSelected();
		} else if (e.key === 'ArrowLeft' && searchQuery === '') {
			e.preventDefault();
			if (currentItem?.imageUrl) {
				showPreview = !showPreview;
			}
		} else if (e.key === 'ArrowRight') {
			e.preventDefault();
			showPreview = false;
		} else if (e.key === 'Enter') {
			e.preventDefault();
			if (filteredItems[selectedIndex]) {
				handleSelect(filteredItems[selectedIndex]);
			}
		}
	}

	function scrollToSelected() {
		if (!listContainer) return;
		const selectedEl = listContainer.querySelector(`[data-index="${selectedIndex}"]`);
		selectedEl?.scrollIntoView({ block: 'nearest' });
	}

	function handleSelect(item: FuzzyFindItem) {
		dispatch('select', item);
		handleClose();
	}

	function handleClose() {
		searchQuery = '';
		showPreview = false;
		dispatch('close');
	}

	function handleBackdropClick(e: MouseEvent) {
		if (e.target === e.currentTarget) {
			handleClose();
		}
	}

	onMount(() => {
		document.addEventListener('keydown', handleKeydown);
	});

	onDestroy(() => {
		if (typeof document !== 'undefined') {
			document.removeEventListener('keydown', handleKeydown);
		}
	});

	// Focus search input when modal opens
	$: if (isOpen && searchInput) {
		setTimeout(() => searchInput?.focus(), 50);
	}
</script>

<!-- FuzzyFindModal uses its own backdrop to support the side-preview layout -->
{#if isOpen}
	<div
		class="fixed inset-0 z-[9999] flex md:items-center md:justify-center bg-black/60 backdrop-blur-sm md:p-4"
		role="dialog"
		aria-modal="true"
		tabindex="-1"
		on:click={handleBackdropClick}
		on:keydown={handleKeydown}
	>
		<div
			class="flex gap-4 items-start transition-all duration-200"
			role="presentation"
			on:click|stopPropagation
			on:keydown|stopPropagation
		>
			<!-- Preview Pane (left side) -->
			{#if showPreview && currentItem?.imageUrl}
				<div
					class="bg-surface-1 rounded-lg shadow-overlay border border-line p-4 flex flex-col items-center justify-center animate-slide-in"
				>
					<img
						src={currentItem.imageUrl}
						alt={currentItem.label}
						class="{config.previewSize} rounded-lg object-cover bg-black"
					/>
					<div class="mt-3 text-center max-w-[320px]">
						<div class="text-sm font-medium text-fg">{currentItem.label}</div>
						{#if currentItem.description}
							<div class="text-xs text-fg-muted mt-1 line-clamp-2">{currentItem.description}</div>
						{/if}
					</div>
				</div>
			{/if}

			<!-- Main Modal -->
			<div
				class="bg-surface-1 md:rounded-lg rounded-none shadow-overlay md:border border-line w-full h-full md:h-auto {config.modalWidth}"
				role="dialog"
				aria-modal="true"
			>
				<!-- Header with title and close button -->
				<div class="flex items-center justify-between px-4 py-3 border-b border-line">
					<div>
						<h2 class="text-base font-semibold text-fg">{title}</h2>
						{#if subtitle}
							<p class="text-xs text-fg-muted">{subtitle}</p>
						{/if}
					</div>
					<button
						type="button"
						on:click={handleClose}
						class="p-1.5 hover:bg-surface-3/50 rounded transition-colors text-fg-subtle hover:text-fg"
					>
						<Icon name="close" className="w-5 h-5" />
					</button>
				</div>

				<!-- Search Input -->
				<div class="px-4 py-3 border-b border-line">
					<div class="relative flex items-center">
						<Icon name="search" className="absolute left-3 w-4 h-4 text-fg-subtle pointer-events-none" />
						<input
							bind:this={searchInput}
							bind:value={searchQuery}
							on:keydown={handleInputKeydown}
							type="text"
							{placeholder}
							class="input h-9 pl-9 pr-8 text-sm"
						/>
						{#if searchQuery}
							<button
								type="button"
								on:click={() => (searchQuery = '')}
								class="absolute right-2 p-1 hover:bg-surface-3/50 rounded text-fg-subtle hover:text-fg"
							>
								<Icon name="close" className="w-3.5 h-3.5" />
							</button>
						{/if}
					</div>
				</div>

				<!-- Items List -->
				<div bind:this={listContainer} class="{config.listHeight} overflow-y-auto">
					{#if filteredItems.length === 0}
						<div class="px-4 py-8 text-center text-fg-subtle text-sm">
							{emptyMessage}
						</div>
					{:else}
						{#each filteredItems as item, index}
							<button
								type="button"
								data-index={index}
								on:click={() => handleSelect(item)}
								on:mouseenter={() => (selectedIndex = index)}
								class="w-full {config.itemPadding} text-left flex items-center {config.itemGap} transition-colors
									{index === selectedIndex ? 'bg-signal/10' : 'hover:bg-surface-3/50'}
									{item.id === selectedId ? 'border-l-2 border-signal' : ''}"
							>
								{#if showImages && item.imageUrl}
									<img
										src={item.imageUrl}
										alt={item.label}
										class="{config.imageSize} rounded-lg object-cover flex-shrink-0 bg-black"
									/>
								{:else if item.icon}
									<div
										class="w-8 h-8 rounded flex items-center justify-center flex-shrink-0 bg-surface-2 text-fg-muted"
									>
										<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
											<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d={item.icon} />
										</svg>
									</div>
								{/if}
								<div class="flex-1 min-w-0">
									<div class="flex items-center gap-2">
										<span class="text-sm font-medium text-fg truncate">{item.label}</span>
										{#if item.badge}
											<Badge variant={item.badge.variant ?? 'neutral'} size="sm" class="flex-shrink-0">
												{item.badge.text}
											</Badge>
										{/if}
									</div>
									{#if showDescriptions && item.description}
										<div class="text-xs text-fg-subtle truncate mt-0.5">{item.description}</div>
									{/if}
								</div>
								{#if item.id === selectedId}
									<Icon name="check" className="w-4 h-4 text-signal flex-shrink-0" />
								{/if}
							</button>
						{/each}
					{/if}
				</div>

				<!-- Footer -->
				<div class="px-4 py-2 bg-surface-2/50 border-t border-line flex items-center justify-between text-xs text-fg-subtle">
					<div class="hidden md:flex items-center gap-3">
						<span class="flex items-center gap-1"><Kbd keys="↑↓" /> navigate</span>
						<span class="flex items-center gap-1"><Kbd keys="←" /> preview</span>
						<span class="flex items-center gap-1"><Kbd keys="↵" /> select</span>
						<span class="flex items-center gap-1"><Kbd keys="esc" /> close</span>
					</div>
					<span class="font-mono tabular-nums text-fg-muted">{filteredItems.length}/{items.length}</span>
				</div>
			</div>
		</div>
	</div>
{/if}

<style>
	@keyframes slideIn {
		from {
			opacity: 0;
			transform: translateX(20px);
		}
		to {
			opacity: 1;
			transform: translateX(0);
		}
	}

	.animate-slide-in {
		animation: slideIn 0.15s ease-out;
	}
</style>
