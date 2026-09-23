<script lang="ts">
	import { tick, type Snippet } from 'svelte';
	import { browser } from '$app/environment';
	import portal from '$lib/actions/portal';
	import { computeFlippedMenuPosition, type FlippedMenuPosition } from '$lib/utils/menuPosition';
	import Icon from '$lib/components/Icon.svelte';
	import type { SortOption } from './librarySection';

	const PANEL_GAP = 6;

	let {
		q,
		onQueryChange,
		searchPlaceholder,
		searchHint = null,
		searchInputEl = $bindable(undefined),
		sortBy,
		sortOptions,
		onSortChange,
		filterCount = 0,
		popover
	}: {
		q: string;
		onQueryChange: (value: string) => void;
		searchPlaceholder: string;
		searchHint?: string | null;
		searchInputEl?: HTMLInputElement;
		sortBy: string;
		sortOptions: readonly SortOption[];
		onSortChange: (value: string) => void;
		filterCount?: number;
		popover?: Snippet<[() => void]>;
	} = $props();

	let filtersOpen = $state(false);
	let triggerEl: HTMLDivElement | undefined = $state();
	let panelEl: HTMLDivElement | undefined = $state();
	let panelPosition = $state<FlippedMenuPosition | null>(null);

	function closeFilters() {
		filtersOpen = false;
	}

	async function reposition() {
		if (!filtersOpen) return;
		await tick();
		if (!filtersOpen || !triggerEl || !panelEl) return;
		panelPosition = computeFlippedMenuPosition(triggerEl, {
			width: panelEl.offsetWidth,
			heightEstimate: panelEl.offsetHeight,
			gap: PANEL_GAP,
			align: 'right',
			preferred: 'down'
		});
	}

	function panelStyle(pos: FlippedMenuPosition | null): string {
		if (!pos) return 'visibility: hidden; top: 0; left: 0;';
		const vertical = pos.top !== undefined ? `top: ${pos.top}px;` : `bottom: ${pos.bottom}px;`;
		return `${vertical} left: ${pos.left}px;`;
	}

	$effect(() => {
		if (filtersOpen) reposition();
		else panelPosition = null;
	});

	$effect(() => {
		if (!browser || !filtersOpen) return;
		window.addEventListener('resize', reposition);
		window.addEventListener('scroll', reposition, true);
		return () => {
			window.removeEventListener('resize', reposition);
			window.removeEventListener('scroll', reposition, true);
		};
	});

	function handleWindowClick(event: MouseEvent) {
		const target = event.target as Element | null;
		if (target?.closest('[role="dialog"], [role="alertdialog"], [aria-label="Close modal"]')) return;
		if (filtersOpen && triggerEl && !triggerEl.contains(event.target as Node)) filtersOpen = false;
	}

	function handleWindowKeydown(event: KeyboardEvent) {
		const target = event.target as HTMLElement | null;
		const isTyping = !!target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable);
		if (event.key === '/' && !isTyping) {
			event.preventDefault();
			searchInputEl?.focus();
		} else if (event.key === 'Escape') {
			filtersOpen = false;
		}
	}
</script>

<svelte:window onclick={handleWindowClick} onkeydown={handleWindowKeydown} />

<div class="order-last flex w-full items-center gap-1.5 xl:order-none xl:w-auto xl:min-w-[12rem] xl:max-w-md xl:flex-1">
	<div class="input flex h-8 min-w-0 flex-1 items-center gap-2">
		<Icon name="search" className="h-3.5 w-3.5 flex-shrink-0 text-fg-subtle" />
		<input
			bind:this={searchInputEl}
			type="search"
			class="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-fg-subtle"
			placeholder={searchPlaceholder}
			value={q}
			oninput={(event) => onQueryChange((event.currentTarget as HTMLInputElement).value)}
		/>
		{#if searchHint}
			<span class="whitespace-nowrap font-mono text-xs tabular-nums text-fg-subtle">{searchHint}</span>
		{/if}
	</div>
</div>

{#if popover}
	<div class="relative flex-shrink-0" bind:this={triggerEl}>
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
	</div>
	{#if filtersOpen}
		<div bind:this={panelEl} use:portal style={panelStyle(panelPosition)} class="fixed z-[99999]">
			{@render popover(closeFilters)}
		</div>
	{/if}
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
