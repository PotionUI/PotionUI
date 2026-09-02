<script lang="ts">
	import type { Snippet } from 'svelte';
	import { Input, IconButton, Spinner } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import { debounce } from '$lib/stores/tabPersistence';
	import { nextIndex, focusableRows, type NavKey } from './keynav';

	let {
		label,
		count,
		onCollapse,
		searchable = false,
		search = $bindable(''),
		searchPlaceholder = 'Search...',
		onSearch,
		searchDebounceMs = 250,
		loading = false,
		isEmpty = false,
		bodyRole = 'list',
		bodyPadding = 'none',
		ariaLabel,
		class: className = '',
		headerActions,
		subheader,
		filters,
		children,
		empty,
		footer
	}: {
		label: string;
		count?: number;
		onCollapse?: () => void;
		searchable?: boolean;
		search?: string;
		searchPlaceholder?: string;
		onSearch?: (value: string) => void;
		searchDebounceMs?: number;
		loading?: boolean;
		isEmpty?: boolean;
		bodyRole?: 'list' | 'listbox' | 'tree';
		bodyPadding?: 'none' | 'sm';
		ariaLabel?: string;
		class?: string;
		headerActions?: Snippet;
		subheader?: Snippet;
		filters?: Snippet;
		children?: Snippet;
		empty?: Snippet;
		footer?: Snippet;
	} = $props();

	let bodyEl: HTMLDivElement | undefined = $state();

	// Depends only on onSearch/searchDebounceMs, never on `search` itself —
	// recreating the debounced function on every keystroke would reset its
	// internal timer each time and defeat the debounce entirely.
	let emitSearch = $derived(onSearch ? debounce(onSearch, searchDebounceMs) : undefined);

	// Native `input` events bubble, so this fires after Input's own
	// bind:value has already updated `search` — reading it here still comes
	// from the event handler, never from an $effect watching the bound value.
	function handleSearchBubble() {
		emitSearch?.(search);
	}

	function clearSearch() {
		search = '';
		onSearch?.('');
		// Re-invoking the debounced fn cancels any pending stale-value call
		// (debounce clears its previous timer before scheduling a new one).
		emitSearch?.('');
	}

	function handleSearchKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') clearSearch();
	}

	const NAV_KEYS = new Set(['ArrowUp', 'ArrowDown', 'Home', 'End']);

	function handleBodyKeydown(e: KeyboardEvent) {
		if (!NAV_KEYS.has(e.key) || !bodyEl) return;
		const rows = focusableRows(bodyEl);
		if (rows.length === 0) return;
		const active = document.activeElement as HTMLElement | null;
		const current = active ? rows.indexOf(active) : -1;
		const idx = nextIndex(rows.length, current, e.key as NavKey);
		if (idx < 0) return;
		e.preventDefault();
		rows[idx]?.focus();
	}
</script>

<div class="flex flex-col h-full min-h-0 text-sm {className}">
	<div class="flex items-center gap-2 px-3 min-h-header border-b border-line flex-shrink-0">
		<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle truncate">{label}</span>
		{#if count !== undefined}
			<span class="font-mono text-2xs tabular-nums text-fg-subtle">{count}</span>
		{/if}
		<div class="ml-auto flex items-center gap-1 flex-shrink-0">
			{@render headerActions?.()}
			{#if onCollapse}
				<IconButton icon="chevron-left" label="Collapse" size="sm" onclick={onCollapse} />
			{/if}
		</div>
	</div>

	{#if searchable}
		<div class="px-3 py-2 border-b border-line flex-shrink-0" oninput={handleSearchBubble}>
			<div class="relative">
				<Icon
					name="search"
					className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-fg-subtle pointer-events-none"
				/>
				<Input
					type="text"
					bind:value={search}
					placeholder={searchPlaceholder}
					class="pl-9 {search ? 'pr-8' : ''} rounded shadow-well"
					onkeydown={handleSearchKeydown}
				/>
				{#if search}
					<button
						type="button"
						class="absolute right-2 top-1/2 -translate-y-1/2 text-fg-subtle hover:text-fg"
						aria-label="Clear search"
						onclick={clearSearch}
					>
						<Icon name="close" className="w-3.5 h-3.5" />
					</button>
				{/if}
			</div>
		</div>
	{/if}

	{@render subheader?.()}
	{@render filters?.()}

	<div
		bind:this={bodyEl}
		class="pane-scroll flex-1 min-h-0 overflow-y-auto {bodyPadding === 'sm' ? 'p-2' : ''}"
		role={bodyRole}
		aria-label={ariaLabel ?? label}
		onkeydown={handleBodyKeydown}
	>
		{#if loading}
			<div class="flex items-center justify-center h-full py-8">
				<Spinner />
			</div>
		{:else if isEmpty}
			{@render empty?.()}
		{:else}
			{@render children?.()}
		{/if}
	</div>

	{#if footer}
		<div class="border-t border-line flex-shrink-0">
			{@render footer()}
		</div>
	{/if}
</div>

<style>
	.pane-scroll {
		scrollbar-width: thin;
		scrollbar-color: rgb(var(--line-strong)) transparent;
	}

	.pane-scroll::-webkit-scrollbar {
		width: 6px;
	}

	.pane-scroll::-webkit-scrollbar-track {
		background: transparent;
	}

	.pane-scroll::-webkit-scrollbar-thumb {
		background-color: rgb(var(--line-strong));
		border-radius: 3px;
	}

	.pane-scroll::-webkit-scrollbar-thumb:hover {
		background-color: rgb(var(--line-hover));
	}
</style>
