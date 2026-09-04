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
	// segment-composer only: jump straight to an arbitrary ancestor path (a
	// breadcrumb crumb), vs. `onNavigateUp`'s single "one level up" step.
	export let onNavigateToPath: ((path: string) => void) | undefined = undefined;
	export let parentRef: HTMLElement | undefined = undefined;
	export let getImageUrl: ((fileId: string) => string) | undefined = undefined;
	// The segment composer's own picker anatomy (`.picker` head/breadcrumb/
	// rows/footer) — see prompt-segments-concept.html `.phrasebook-picker` /
	// `.variable-picker`. Chat's ChatChipInput keeps `variant="default"`
	// (today's Tailwind card) untouched.
	export let variant: 'default' | 'segment-composer' = 'default';

	let dropdownRef: HTMLDivElement;
	let selectedItemRef: HTMLElement | null = null;
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
	function trackSelectedItem(node: HTMLElement, isSelected: boolean) {
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

{#if variant === 'segment-composer'}
	<!-- `display: contents`: a pure CSS-scope carrier for the portaled content,
	     invisible to layout/positioning — the mock's `.picker` class carries its
	     own static prototype `left`/`top` (a fixed demo position), so the real,
	     caret-anchored position (identical to the default variant's own
	     computeAutocompletePlacement) has to be inline on the SAME element as
	     `.picker`, not on a wrapper `.picker` can out-position. All four
	     offsets are set explicitly (not just the ones currently in use) so the
	     mock's own `left`/`right`/`top` values can never leak through when
	     `left`+`width`+`right`, or `top`+`bottom` with no explicit height, would
	     otherwise both be "specified" at once. -->
	<div use:portal class="segment-composer" style="display: contents;">
		<section
			class="floating picker {triggerChar === '$' ? 'variable-picker' : 'phrasebook-picker'}"
			aria-label={contextLabel}
			style="position: fixed; z-index: 99999; left: {dropdownPosition.left}px; right: auto; width: {dropdownPosition.width}px;
				{dropdownPosition.openAbove
				? `bottom: ${dropdownPosition.bottom}px; top: auto;`
				: `top: ${dropdownPosition.top}px; bottom: auto;`}"
		>
			<header class="picker-head">
				<span class="picker-symbol">{triggerChar}</span>
				<div class="picker-search">
					<label for="acdd-path-{triggerChar}">{contextLabel}</label>
					<input id="acdd-path-{triggerChar}" readonly tabindex="-1" value="{triggerChar}{currentPath}" aria-label="{contextLabel} path" />
				</div>
				<span class="esc">ESC</span>
			</header>

			{#if triggerChar === '$'}
				<div class="picker-breadcrumb"><span>Shared by every segment in this prompt</span></div>
			{:else}
				{@const pathParts = currentPath.split('.').filter(Boolean)}
				<div class="picker-breadcrumb">
					{#if onNavigateToPath}
						<button type="button" class="crumb" on:click={() => onNavigateToPath?.('')}>Phrasebook</button>
					{:else}
						<span>Phrasebook</span>
					{/if}
					{#each pathParts as part, i (i)}
						<span>/</span>
						{#if onNavigateToPath && i < pathParts.length - 1}
							<button
								type="button"
								class="crumb"
								on:click={() => onNavigateToPath?.(pathParts.slice(0, i + 1).join('.') + '.')}
							>
								<strong>{part}</strong>
							</button>
						{:else}
							<strong>{part}</strong>
						{/if}
					{/each}
				</div>
			{/if}

			{#if isLoading}
				<div class="px-3 py-2 text-sm text-fg-subtle">Loading suggestions...</div>
			{:else if categories.length > 0 || suggestions.length > 0}
				<div bind:this={dropdownRef} class="max-h-[300px] overflow-y-auto" role="listbox">
					{#if categories.length > 0}
						<div class="picker-section-title">Categories <span>Browse deeper</span></div>
						{#each categories as category, index}
							{@const isSelected = index === selectedIndex}
							{@const displayName = category.path.split('.').pop() || category.name}
							<button
								type="button"
								use:trackSelectedItem={isSelected}
								class="picker-row category-row"
								class:selected={isSelected}
								on:click={() => onSelectCategory(category)}
								role="option"
								aria-selected={isSelected}
							>
								<span class="row-thumb"><svg class="icon"><use href="#i-folder" /></svg></span>
								<span class="row-copy"><strong>{displayName}</strong>{#if category.description}<span>{category.description}</span>{/if}</span>
								<span class="row-meta">{isSelected ? 'Enter' : 'Open →'}</span>
							</button>
						{/each}
					{/if}

					{#if suggestions.length > 0}
						<div class="picker-section-title">Values <span>{suggestions.length} {suggestions.length === 1 ? 'match' : 'matches'}</span></div>
						{#each suggestions as suggestion, index}
							{@const actualIndex = categories.length + index}
							{@const isSelected = actualIndex === selectedIndex}
							<button
								type="button"
								use:trackSelectedItem={isSelected}
								class="picker-row phrase-value"
								class:selected={isSelected}
								data-value={suggestion.value}
								on:click={() => onSelectValue(suggestion)}
								role="option"
								aria-selected={isSelected}
							>
								<span class="row-thumb">
									{#if suggestion.preview_file_id && getImageUrl}
										<img src={getImageUrl(suggestion.preview_file_id)} alt={suggestion.label} />
									{:else}
										<svg class="icon"><use href="#i-braces" /></svg>
									{/if}
								</span>
								<span class="row-copy">
									<strong>{suggestion.label}</strong>
									{#if suggestion.label !== suggestion.value}<span>{suggestion.value}</span>{/if}
								</span>
								<span class="row-meta">{isSelected ? 'Enter' : 'Value'}</span>
							</button>
						{/each}
					{/if}
				</div>
			{:else}
				<div class="px-3 py-2 text-sm text-fg-subtle">
					{#if currentPath}
						No suggestions for <span class="font-mono">{triggerChar}{currentPath}</span>
					{:else}
						{emptyHint}
					{/if}
				</div>
			{/if}

			<footer class="picker-footer">
				<span><span class="kbd">↑↓</span> Navigate</span>
				<span><span class="kbd">↵</span> {triggerChar === '$' ? 'Insert ${name}' : 'Insert'}</span>
				<span><span class="kbd">Esc</span> Close</span>
			</footer>
		</section>
	</div>
{:else}
	<div
		use:portal
		class="fixed z-[99999] px-[2px]"
		style="{dropdownPosition.openAbove
			? `bottom: ${dropdownPosition.bottom}px;`
		: `top: ${dropdownPosition.top}px;`} left: {dropdownPosition.left}px; width: {dropdownPosition.width}px;"
>
	<div class="bg-surface-2 shadow-overlay border border-line-strong rounded-xl overflow-hidden">
		{#if isLoading}
			<div class="px-3 py-2 text-sm text-fg-subtle">
				Loading suggestions...
			</div>
		{:else if categories.length > 0 || suggestions.length > 0}
			<div bind:this={dropdownRef} class="max-h-[300px] overflow-y-auto" role="listbox">
				<!-- Command Palette Header -->
				{#if currentPath || categories.length > 0 || suggestions.length > 0}
					<div class="px-3 py-2 text-sm font-medium text-fg-muted border-b border-line bg-surface-2 sticky top-0 z-10 flex items-center justify-between gap-2">
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
							class="px-3 py-2 cursor-pointer transition-colors duration-100 {isSelected ? 'bg-signal/10 text-signal' : 'text-fg hover:bg-surface-3'}"
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
							class="px-3 py-2 cursor-pointer transition-colors duration-100 {isSelected ? 'bg-signal/10 text-signal' : 'text-fg hover:bg-surface-3'}"
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
{/if}
