<script lang="ts" generics="S extends string">
	import type { Snippet } from 'svelte';
	import { IconButton, PageHeader, PageTitle } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Pane, PaneRow } from '$lib/components/pane';
	import type { FilterChip, LibrarySectionMeta } from './librarySection';

	let {
		title,
		persistKey,
		sections,
		section,
		onSelectSection,
		sectionCounts = {},
		count = null,
		detailOpen = false,
		toolbar,
		filterChips = [],
		onRemoveChip,
		onClearFilters,
		loadedCount = 0,
		total = 0,
		sidebarTree,
		overflow,
		primary,
		children
	}: {
		title: string;
		persistKey: string;
		sections: readonly LibrarySectionMeta<S>[];
		section: S;
		onSelectSection: (id: S) => void;
		sectionCounts?: Partial<Record<S, number>>;
		count?: number | null;
		detailOpen?: boolean;
		toolbar?: Snippet;
		filterChips?: readonly FilterChip[];
		onRemoveChip?: (key: string) => void;
		onClearFilters?: () => void;
		loadedCount?: number;
		total?: number;
		sidebarTree?: Snippet;
		overflow?: Snippet<[() => void]>;
		primary?: Snippet;
		children: Snippet;
	} = $props();

	const storageKey = $derived(`${persistKey}:sidebar-open`);
	const meta = $derived(sections.find((entry) => entry.id === section) ?? sections[0]);

	let sidebarOpen = $state(readSidebarOpen());
	let overflowOpen = $state(false);
	let overflowEl: HTMLDivElement | undefined = $state();

	function readSidebarOpen(): boolean {
		try {
			return localStorage.getItem(storageKey) !== '0';
		} catch {
			return true;
		}
	}

	function setSidebarOpen(value: boolean) {
		sidebarOpen = value;
		try {
			localStorage.setItem(storageKey, value ? '1' : '0');
		} catch {
			return;
		}
	}

	function selectSection(id: S) {
		if (id === section) return;
		onSelectSection(id);
	}

	function closeOverflow() {
		overflowOpen = false;
	}

	function handleWindowClick(event: MouseEvent) {
		const target = event.target as Element | null;
		if (target?.closest('[role="dialog"], [role="alertdialog"], [aria-label="Close modal"]')) return;
		if (overflowOpen && overflowEl && !overflowEl.contains(event.target as Node)) overflowOpen = false;
	}

	function handleWindowKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') overflowOpen = false;
	}
</script>

<svelte:window onclick={handleWindowClick} onkeydown={handleWindowKeydown} />

<div class="flex h-[100dvh] bg-canvas text-fg">
	{#if sidebarOpen}
		<aside class="hidden w-60 flex-shrink-0 flex-col border-r border-line bg-surface-1 md:flex">
			<Pane label="Library" onCollapse={() => setSidebarOpen(false)}>
				{#snippet subheader()}
					<div class="flex-shrink-0 space-y-0.5 border-b border-line p-2" role="listbox" aria-label="Library sections">
						{#each sections as entry (entry.id)}
							<PaneRow
								icon={entry.icon}
								title={entry.label}
								count={sectionCounts[entry.id]}
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
			{#each sections as entry (entry.id)}
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
				<PageTitle {title} count={count ?? undefined} countLabel={meta.label.toLowerCase()}>
					<label class="block md:hidden">
						<span class="sr-only">Section</span>
						<select
							class="input h-7 appearance-none py-0 pr-6 text-xs font-medium"
							value={section}
							onchange={(event) => selectSection((event.currentTarget as HTMLSelectElement).value as S)}
						>
							{#each sections as entry (entry.id)}
								<option value={entry.id}>{entry.label}</option>
							{/each}
						</select>
					</label>
				</PageTitle>

				{#if !detailOpen}
					<div class="hidden h-6 w-px flex-shrink-0 bg-line-strong md:block"></div>

					{@render toolbar?.()}

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

		{#if !detailOpen && filterChips.length > 0}
			<div class="flex flex-shrink-0 flex-wrap items-center gap-1.5 border-b border-line bg-surface-1 px-4 py-2 sm:px-6">
				{#each filterChips as chip (chip.key)}
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
