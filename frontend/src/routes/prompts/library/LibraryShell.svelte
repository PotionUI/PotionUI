<script lang="ts">
	import type { Snippet } from 'svelte';
	import { goto } from '$app/navigation';
	import { IconButton, PageHeader, PageTitle } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Pane, PaneRow } from '$lib/components/pane';
	import { libraryCounts } from './libraryCounts';
	import { LIBRARY_SECTIONS, sectionHref, sectionMeta, type FilterChip, type LibrarySection, type SortOption } from './librarySection';

	const SIDEBAR_KEY = 'prompt-library-sidebar-open';

	let {
		section,
		count = null,
		detailOpen = false,
		q,
		onQueryChange,
		searchPlaceholder,
		searchHint = null,
		sortBy,
		sortOptions,
		onSortChange,
		filterCount = 0,
		chips = [],
		onRemoveChip,
		onClearFilters,
		loadedCount = 0,
		total = 0,
		searchInputEl = $bindable(undefined),
		sidebarTree,
		filtersPopover,
		overflow,
		primary,
		children
	}: {
		section: LibrarySection;
		count?: number | null;
		detailOpen?: boolean;
		q: string;
		onQueryChange: (value: string) => void;
		searchPlaceholder?: string;
		searchHint?: string | null;
		sortBy: string;
		sortOptions: readonly SortOption[];
		onSortChange: (value: string) => void;
		filterCount?: number;
		chips?: readonly FilterChip[];
		onRemoveChip?: (key: string) => void;
		onClearFilters?: () => void;
		loadedCount?: number;
		total?: number;
		searchInputEl?: HTMLInputElement;
		sidebarTree?: Snippet;
		filtersPopover?: Snippet<[() => void]>;
		overflow?: Snippet<[() => void]>;
		primary?: Snippet;
		children: Snippet;
	} = $props();

	const meta = $derived(sectionMeta(section));
	const placeholder = $derived(searchPlaceholder ?? meta.searchPlaceholder);

	let sidebarOpen = $state(readSidebarOpen());
	let filtersOpen = $state(false);
	let overflowOpen = $state(false);
	let filtersTriggerEl: HTMLDivElement | undefined = $state();
	let overflowEl: HTMLDivElement | undefined = $state();

	function readSidebarOpen(): boolean {
		try {
			return localStorage.getItem(SIDEBAR_KEY) !== '0';
		} catch {
			return true;
		}
	}

	function setSidebarOpen(value: boolean) {
		sidebarOpen = value;
		try {
			localStorage.setItem(SIDEBAR_KEY, value ? '1' : '0');
		} catch {
			return;
		}
	}

	function selectSection(id: LibrarySection) {
		if (id === section) return;
		void goto(sectionHref(id));
	}

	function closeFilters() {
		filtersOpen = false;
	}

	function closeOverflow() {
		overflowOpen = false;
	}

	function handleWindowClick(event: MouseEvent) {
		const target = event.target as Element | null;
		if (target?.closest('[role="dialog"], [role="alertdialog"], [aria-label="Close modal"]')) return;
		if (filtersOpen && filtersTriggerEl && !filtersTriggerEl.contains(event.target as Node)) filtersOpen = false;
		if (overflowOpen && overflowEl && !overflowEl.contains(event.target as Node)) overflowOpen = false;
	}

	function handleWindowKeydown(event: KeyboardEvent) {
		const target = event.target as HTMLElement | null;
		const isTyping = !!target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable);
		if (event.key === '/' && !isTyping && !detailOpen) {
			event.preventDefault();
			searchInputEl?.focus();
		} else if (event.key === 'Escape') {
			filtersOpen = false;
			overflowOpen = false;
		}
	}
</script>

<svelte:window onclick={handleWindowClick} onkeydown={handleWindowKeydown} />

<div class="flex h-[100dvh] bg-canvas text-fg">
	{#if sidebarOpen}
		<aside class="hidden w-60 flex-shrink-0 flex-col border-r border-line bg-surface-1 md:flex">
			<Pane label="Library" onCollapse={() => setSidebarOpen(false)}>
				{#snippet subheader()}
					<div class="flex-shrink-0 space-y-0.5 border-b border-line p-2" role="listbox" aria-label="Library sections">
						{#each LIBRARY_SECTIONS as entry (entry.id)}
							<PaneRow
								icon={entry.icon}
								title={entry.label}
								count={$libraryCounts[entry.id]}
								selected={entry.id === section}
								onclick={() => selectSection(entry.id)}
							/>
						{/each}
					</div>
				{/snippet}
				{@render sidebarTree?.()}
			</Pane>
		</aside>
	{:else}
		<aside class="hidden w-8 flex-shrink-0 flex-col items-center gap-2 border-r border-line bg-surface-1 pt-3 md:flex">
			<Tooltip text="Show sidebar" position="right">
				<button
					type="button"
					class="flex h-6 w-6 items-center justify-center rounded text-fg-subtle transition-colors hover:bg-surface-2 hover:text-fg"
					aria-label="Show sidebar"
					onclick={() => setSidebarOpen(true)}
				>
					<Icon name="chevron-right" className="h-4 w-4" />
				</button>
			</Tooltip>
			<div class="h-px w-4 bg-line"></div>
			{#each LIBRARY_SECTIONS as entry (entry.id)}
				<Tooltip text={entry.label} position="right">
					<button
						type="button"
						class="flex h-6 w-6 items-center justify-center rounded transition-colors {entry.id === section
							? 'bg-signal/10 text-signal'
							: 'text-fg-subtle hover:bg-surface-2 hover:text-fg'}"
						aria-label={entry.label}
						aria-current={entry.id === section ? 'page' : undefined}
						onclick={() => selectSection(entry.id)}
					>
						<Icon name={entry.icon} className="h-4 w-4" />
					</button>
				</Tooltip>
			{/each}
		</aside>
	{/if}

	<div class="flex min-w-0 flex-1 flex-col">
		<PageHeader sticky={false} wrap>
			<div class="flex w-full flex-wrap items-center gap-2 xl:gap-4">
				<PageTitle title="Prompt Library" count={count ?? undefined} countLabel={meta.label.toLowerCase()}>
					<label class="block md:hidden">
						<span class="sr-only">Section</span>
						<select
							class="input h-7 appearance-none py-0 pr-6 text-xs font-medium"
							value={section}
							onchange={(event) => selectSection((event.currentTarget as HTMLSelectElement).value as LibrarySection)}
						>
							{#each LIBRARY_SECTIONS as entry (entry.id)}
								<option value={entry.id}>{entry.label}</option>
							{/each}
						</select>
					</label>
				</PageTitle>

				{#if !detailOpen}
					<div class="hidden h-6 w-px flex-shrink-0 bg-line-strong md:block"></div>

					<div class="order-last flex w-full items-center gap-1.5 xl:order-none xl:w-auto xl:min-w-[12rem] xl:max-w-md xl:flex-1">
						<div class="input flex h-8 min-w-0 flex-1 items-center gap-2">
							<Icon name="search" className="h-3.5 w-3.5 flex-shrink-0 text-fg-subtle" />
							<input
								bind:this={searchInputEl}
								type="search"
								class="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-fg-subtle"
								placeholder={placeholder}
								value={q}
								oninput={(event) => onQueryChange((event.currentTarget as HTMLInputElement).value)}
							/>
							{#if searchHint}
								<span class="whitespace-nowrap font-mono text-xs tabular-nums text-fg-subtle">{searchHint}</span>
							{/if}
						</div>
					</div>

					{#if filtersPopover}
						<div class="relative flex-shrink-0" bind:this={filtersTriggerEl}>
							<button
								type="button"
								class="input flex h-8 items-center gap-1.5 whitespace-nowrap text-xs font-medium"
								aria-haspopup="dialog"
								aria-expanded={filtersOpen}
								onclick={() => (filtersOpen = !filtersOpen)}
							>
								<span>Filters</span>
								{#if filterCount > 0}
									<span class="font-mono tabular-nums text-signal">{filterCount}</span>
								{/if}
								<Icon name="chevron-down" className="h-3 w-3 text-fg-subtle" />
							</button>
							{#if filtersOpen}
								<div class="absolute right-0 top-[calc(100%+6px)] z-40">
									{@render filtersPopover(closeFilters)}
								</div>
							{/if}
						</div>
					{/if}

					<label class="relative flex-shrink-0">
						<span class="sr-only">Sort</span>
						<select
							class="input h-8 appearance-none pr-6 text-xs font-medium"
							value={sortBy}
							onchange={(event) => onSortChange((event.currentTarget as HTMLSelectElement).value)}
						>
							{#each sortOptions as option (option.value)}
								<option value={option.value}>{option.label}</option>
							{/each}
						</select>
					</label>

					<div class="ml-auto flex flex-shrink-0 items-center gap-2">
						{@render primary?.()}
						{#if overflow}
							<div class="relative" bind:this={overflowEl}>
								<Tooltip text="More actions" position="bottom">
									<IconButton
										icon="more"
										label="More actions"
										ariaExpanded={overflowOpen}
										active={overflowOpen}
										onclick={() => (overflowOpen = !overflowOpen)}
									/>
								</Tooltip>
								{#if overflowOpen}
									<div
										class="absolute right-0 top-[calc(100%+6px)] z-40 min-w-[200px] overflow-hidden rounded-xl border border-line-strong bg-surface-2 py-1 shadow-floating"
										role="menu"
									>
										{@render overflow(closeOverflow)}
									</div>
								{/if}
							</div>
						{/if}
					</div>
				{:else}
					<div class="ml-auto"></div>
				{/if}
			</div>
		</PageHeader>

		{#if !detailOpen && chips.length > 0}
			<div class="flex flex-shrink-0 flex-wrap items-center gap-1.5 border-b border-line bg-surface-1 px-4 py-2 sm:px-6">
				{#each chips as chip (chip.key)}
					<span class="inline-flex h-6 items-center gap-1.5 rounded border border-signal/28 bg-signal/10 px-2 font-mono text-xs text-signal">
						{chip.label}
						<button
							type="button"
							aria-label={`Remove filter ${chip.label}`}
							class="opacity-70 hover:opacity-100"
							onclick={() => onRemoveChip?.(chip.key)}
						>
							<Icon name="close" className="h-2.5 w-2.5" />
						</button>
					</span>
				{/each}
				<button type="button" class="text-xs text-fg-subtle hover:text-fg" onclick={() => onClearFilters?.()}>Clear all</button>
				<span class="ml-auto font-mono text-xs tabular-nums text-fg-subtle">{loadedCount} of {total}</span>
			</div>
		{/if}

		<main class="min-h-0 min-w-0 flex-1 overflow-hidden">
			{@render children()}
		</main>
	</div>
</div>
