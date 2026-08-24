<script context="module" lang="ts">
	// Types
	export interface AutocompleteCategory {
		id: string;
		name: string;
		path: string;
		parent_id?: string;
		description: string;
		created_at: string;
		updated_at: string;
		user_id?: string;
		/**
		 * When true (and `onAttachCategory` is provided), the row itself
		 * attaches this category directly; the chevron becomes a separate
		 * "browse into" target instead of the whole row navigating.
		 */
		attachable?: boolean;
	}

	export interface AutocompleteValue {
		id: string;
		category_id: string;
		label: string;
		value: string;
		sort_order: number;
		created_at: string;
		updated_at: string;
		user_id?: string;
		category_path?: string;
		category_name?: string;
		preview_file_id?: string;
	}
</script>

<script lang="ts">
	import { onMount, afterUpdate } from 'svelte';
	import portal from '$lib/actions/portal';
	import { resolveMentionRowAction } from '$lib/utils/mentionRowAction';
	import { computeAutocompletePlacement } from '$lib/utils/autocompleteAnchor';

	// Props
	export let categories: AutocompleteCategory[] = [];
	export let suggestions: AutocompleteValue[] = [];
	export let selectedIndex: number = 0;
	export let onSelectCategory: (category: AutocompleteCategory) => void;
	export let onSelectValue: (suggestion: AutocompleteValue) => void;
	// Optional: when a category is `attachable`, the row itself calls this
	// instead of onSelectCategory, and the chevron becomes the explicit
	// "browse into" affordance. Consumers that never mark categories
	// attachable (the default) see no behavior change.
	export let onAttachCategory: ((category: AutocompleteCategory) => void) | undefined = undefined;
	export let isLoading: boolean = false;
	export let currentPath: string = '';
	export let triggerChar: string = '#';
	export let emptyHint: string = 'Phrasebook — type # + category path';
	export let contextLabel: string = 'Suggestions';
	export const onClose: (() => void) | undefined = undefined;
	export let onNavigateUp: (() => void) | undefined = undefined;
	export let parentRef: HTMLElement | undefined = undefined;
	export let getImageUrl: ((fileId: string) => string) | undefined = undefined;

	let dropdownRef: HTMLDivElement;
	let selectedItemRef: HTMLDivElement | null = null;
	let dropdownPosition = { top: 0, bottom: 0, left: 0, width: 0, openAbove: false };

	// `position: fixed` on the dropdown is viewport-relative only when no
	// ancestor has a `transform` (e.g. GlobalChatPanel's translate-x-0/
	// translate-x-full slide-over) — such an ancestor becomes the containing
	// block instead, and this component's getBoundingClientRect-based
	// dropdownPosition would place it far off from the trigger. `portal` (see
	// $lib/actions/portal.ts) moves the node to <body> to sidestep that.

	// Calculate position when parent is available
	$: if (parentRef) {
		updatePosition();
	}

	function updatePosition() {
		if (!parentRef) return;
		const rect = parentRef.getBoundingClientRect();
		dropdownPosition = computeAutocompletePlacement(rect, {
			width: window.innerWidth,
			height: window.innerHeight
		});
	}

	onMount(() => {
		updatePosition();
		window.addEventListener('resize', updatePosition);
		window.addEventListener('scroll', updatePosition, true);

		return () => {
			window.removeEventListener('resize', updatePosition);
			window.removeEventListener('scroll', updatePosition, true);
		};
	});

	// Check if we can navigate up
	$: canNavigateUp = currentPath ? (() => {
		const cleanPath = currentPath.endsWith('.') ? currentPath.slice(0, -1) : currentPath;
		return cleanPath.length > 0;
	})() : false;

	// Action to handle selected item ref and scrolling
	function trackSelectedItem(node: HTMLDivElement, isSelected: boolean) {
		if (isSelected) {
			selectedItemRef = node;
			scrollSelectedIntoView();
		}

		return {
			update(newIsSelected: boolean) {
				if (newIsSelected) {
					selectedItemRef = node;
					scrollSelectedIntoView();
				} else if (selectedItemRef === node) {
					selectedItemRef = null;
				}
			}
		};
	}

	function scrollSelectedIntoView() {
		if (selectedItemRef && dropdownRef) {
			const item = selectedItemRef;
			const dropdown = dropdownRef;

			const itemTop = item.offsetTop;
			const itemBottom = itemTop + item.offsetHeight;
			const dropdownTop = dropdown.scrollTop;
			const dropdownBottom = dropdownTop + dropdown.clientHeight;

			if (itemTop < dropdownTop) {
				dropdown.scrollTop = itemTop;
			} else if (itemBottom > dropdownBottom) {
				dropdown.scrollTop = itemBottom - dropdown.clientHeight;
			}
		}
	}

	// Scroll selected item into view when selection changes
	afterUpdate(() => {
		scrollSelectedIntoView();
	});
</script>

<div
	use:portal
	class="fixed z-[99999] px-[2px]"
	style="{dropdownPosition.openAbove
		? `bottom: ${dropdownPosition.bottom}px;`
		: `top: ${dropdownPosition.top}px;`} left: {dropdownPosition.left}px; width: {dropdownPosition.width}px;"
>
	<div class="bg-surface-1 shadow-floating border border-line-strong rounded-lg overflow-hidden">
		{#if isLoading}
			<div class="px-3 py-2 text-sm text-fg-subtle">
				Loading suggestions...
			</div>
		{:else if categories.length > 0 || suggestions.length > 0}
			<div bind:this={dropdownRef} class="max-h-[300px] overflow-y-auto" role="listbox">
				<!-- Command Palette Header -->
				{#if currentPath || categories.length > 0 || suggestions.length > 0}
					<div class="px-3 py-2 text-sm font-medium text-fg-muted border-b border-line bg-surface-2/50 sticky top-0 z-10 flex items-center justify-between gap-2">
						<div class="flex items-center gap-2 flex-1 min-w-0">
							{#if canNavigateUp && onNavigateUp}
								<button
									type="button"
									class="h-6 w-6 min-w-6 flex items-center justify-center text-fg-muted hover:text-fg hover:bg-surface-3 rounded transition-colors duration-100"
									on:click={onNavigateUp}
									aria-label="Navigate up"
								>
									<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
										<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
									</svg>
								</button>
							{/if}
							<div class="flex-1 min-w-0">
								{#if currentPath}
									<span class="font-mono text-signal">{triggerChar}{currentPath}</span>
									<span class="ml-2 font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">{contextLabel}</span>
								{:else}
									<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">{emptyHint}</span>
								{/if}
							</div>
						</div>
						<div class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-disabled">ESC</div>
					</div>
				{/if}

				<!-- Categories Section -->
				{#if categories.length > 0}
					{#if categories.length > 0 && suggestions.length > 0}
						<div class="px-3 py-1 font-mono text-2xs font-medium uppercase tracking-[0.07em] text-fg-subtle bg-surface-2/50 border-b border-line">
							Categories
						</div>
					{/if}
					{#each categories as category, index}
						{@const isSelected = index === selectedIndex}
						{@const displayName = category.path.split('.').pop() || category.name}
						{@const canAttach = onAttachCategory !== undefined && resolveMentionRowAction({ hasChildren: true, attachable: category.attachable }) === 'attach-category'}
						<div
							use:trackSelectedItem={isSelected}
							class="px-3 py-2 cursor-pointer transition-colors duration-100 {isSelected ? 'bg-signal/10 text-signal' : 'text-fg hover:bg-surface-2'}"
							on:click={() => (canAttach ? onAttachCategory?.(category) : onSelectCategory(category))}
							on:keydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); canAttach ? onAttachCategory?.(category) : onSelectCategory(category); } }}
							role="option"
							aria-selected={isSelected}
							tabindex="-1"
						>
							<div class="flex items-center justify-between gap-2">
								<div class="flex items-center gap-2 flex-1 min-w-0">
									<svg class="w-4 h-4 text-fg-subtle flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
										<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
									</svg>
									<div class="flex-1 min-w-0">
										<div class="font-medium text-sm truncate">
											{displayName}
										</div>
										{#if category.description}
											<div class="text-xs text-fg-subtle mt-0.5 truncate">
												{category.description}
											</div>
										{/if}
									</div>
									{#if canAttach}
										<button
											type="button"
											class="h-6 w-6 min-w-6 flex items-center justify-center text-fg-subtle hover:text-fg hover:bg-surface-3 rounded transition-colors duration-100 flex-shrink-0"
											on:click|stopPropagation={() => onSelectCategory(category)}
											aria-label="Browse into {displayName}"
										>
											<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
												<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
											</svg>
										</button>
									{:else}
										<svg class="w-4 h-4 text-fg-subtle flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
											<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
										</svg>
									{/if}
								</div>
								{#if isSelected}
									<div class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle flex-shrink-0">
										{canAttach ? 'Attach' : 'Enter'}
									</div>
								{/if}
							</div>
						</div>
					{/each}
				{/if}

				<!-- Values Section -->
				{#if suggestions.length > 0}
					{#if categories.length > 0 && suggestions.length > 0}
						<div class="px-3 py-1 font-mono text-2xs font-medium uppercase tracking-[0.07em] text-fg-subtle bg-surface-2/50 border-b border-line">
							Values
						</div>
					{/if}
					{#each suggestions as suggestion, index}
						{@const actualIndex = categories.length + index}
						{@const isSelected = actualIndex === selectedIndex}
						<div
							use:trackSelectedItem={isSelected}
							class="px-3 py-2 cursor-pointer transition-colors duration-100 {isSelected ? 'bg-signal/10 text-signal' : 'text-fg hover:bg-surface-2'}"
							on:click={() => onSelectValue(suggestion)}
							on:keydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelectValue(suggestion); } }}
							role="option"
							aria-selected={isSelected}
							tabindex="-1"
						>
							<div class="flex items-center justify-between gap-2">
								<div class="flex items-center gap-2 flex-1 min-w-0">
									{#if suggestion.preview_file_id && getImageUrl}
										<img
											src={getImageUrl(suggestion.preview_file_id)}
											alt={suggestion.label}
											class="w-8 h-8 rounded object-cover flex-shrink-0"
										/>
									{:else}
										<svg class="w-4 h-4 text-fg-subtle flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
											<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
										</svg>
									{/if}
									<div class="flex-1 min-w-0">
										<div class="font-medium text-sm truncate">
											{suggestion.label}
										</div>
										{#if suggestion.label !== suggestion.value}
											<div class="text-xs text-fg-subtle mt-0.5 truncate">
												{suggestion.value}
											</div>
										{/if}
									</div>
								</div>
								{#if isSelected}
									<div class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle flex-shrink-0">
										Enter
									</div>
								{/if}
							</div>
						</div>
					{/each}
				{/if}
			</div>
		{:else}
			<div class="px-3 py-2 text-sm text-fg-subtle">
				{#if currentPath}
					No suggestions for <span class="font-mono">{triggerChar}{currentPath}</span>
				{:else}
					Type a path after {triggerChar} to see suggestions
				{/if}
			</div>
		{/if}
	</div>
</div>
