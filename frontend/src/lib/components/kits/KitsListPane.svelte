<script lang="ts">
	import { onDestroy, onMount, type Snippet } from 'svelte';
	import { Button, EmptyState, Menu, Pagination, Spinner, LoadErrorState } from '$lib/components/ui';
	import {
		ListPageHeader,
		HeaderTitle,
		HeaderSearch,
		HeaderSort,
		HeaderSelect,
		HeaderViewOptions,
		HeaderAction,
		type HeaderStage
	} from '$lib/components/page-header';
	import { api } from '$lib/services/api/index';
	import { kitTotal, kitsRevision } from '$lib/stores/kits';
	import { debounce } from '$lib/utils/time';
	import type { Kit, KitListPage, KitSortDir, KitSortField } from '$lib/kits/types';
	import KitCard from './KitCard.svelte';
	
	let {
		leading,
		onOpen,
		onNew,
		onPickFromLibrary
	}: {
		leading?: Snippet;
		onOpen: (kit: Kit) => void;
		onNew: () => void;
		onPickFromLibrary: () => void;
	} = $props();

	const PAGE_SIZE = 8;

	let data = $state<KitListPage | null>(null);
	let loading = $state(true);
	let failed = $state(false);
	let label = $state<string | null>(null);
	let search = $state('');
	let page = $state(1);
	let requestId = 0;
	let stage = $state<HeaderStage>('full');
	let sortBy = $state<KitSortField>('manual');
	let sortDir = $state<KitSortDir>('asc');

	let everything = $derived($kitTotal ?? data?.total ?? 0);
	let pages = $derived(data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1);
	let from = $derived(data && data.kits.length > 0 ? (page - 1) * PAGE_SIZE + 1 : 0);
	let to = $derived(data ? (page - 1) * PAGE_SIZE + data.kits.length : 0);
	let filtered = $derived(label !== null || search.trim() !== '');
	let empty = $derived(!loading && !failed && data !== null && data.total === 0 && !filtered);

	async function load() {
		const mine = ++requestId;
		loading = true;
		failed = false;
		try {
			const response = await api.listKits({ label, search, page, page_size: PAGE_SIZE, sort_by: sortBy, sort_dir: sortDir });
			if (mine !== requestId) return;
			if (response.success && response.data) {
				data = response.data;
				if (!filtered) kitTotal.set(response.data.total);
			} else failed = true;
		} catch {
			if (mine === requestId) failed = true;
		} finally {
			if (mine === requestId) loading = false;
		}
	}

	const scheduleSearch = debounce(() => {
		page = 1;
		void load();
	}, 300);

	function pickLabel(next: string | null) {
		label = next;
		page = 1;
		void load();
	}

	const sortOptions = [
		{ value: 'manual', label: 'Manual' },
		{ value: 'name', label: 'Name' },
		{ value: 'created', label: 'Created' },
		{ value: 'updated', label: 'Updated' }
	];
	const sortDefaultDir: Record<string, KitSortDir> = { manual: 'asc', name: 'asc', created: 'desc', updated: 'desc' };

	function pickSort(value: string) {
		sortBy = value as KitSortField;
		sortDir = sortDefaultDir[value];
		page = 1;
		void load();
	}

	function pickSortDir(dir: KitSortDir) {
		sortDir = dir;
		page = 1;
		void load();
	}

	function handleSearchInput(value: string) {
		search = value;
		scheduleSearch();
	}

	function goTo(next: number) {
		page = next;
		void load();
	}

	let primed = false;
	let unsubscribe: (() => void) | null = null;

	onMount(() => {
		void load();
		unsubscribe = kitsRevision.subscribe(() => {
			if (!primed) {
				primed = true;
				return;
			}
			void load();
		});
	});

	onDestroy(() => {
		scheduleSearch.cancel();
		unsubscribe?.();
	});
</script>

{#snippet searchZone(s: HeaderStage)}
	{#if !empty}
		<HeaderSearch value={search} placeholder="Search kits" label="Search kits" oninput={handleSearchInput} narrow={s === 'narrow'} />
	{/if}
{/snippet}

<div class="sticky top-0 z-30">
<ListPageHeader bind:stage {leading} search={searchZone}>
	{#snippet title(s)}
		<HeaderTitle
			title="Kits"
			count={filtered && data ? data.total : everything}
			unit="kits"
			unitOne="kit"
			total={everything}
			filterCount={label !== null ? 1 : 0}
			showUnit={s === 'full'}
			narrow={s === 'narrow'}
		/>
	{/snippet}

	{#snippet facets(s)}
		{#if !empty && data && data.labels.length > 0}
			<HeaderSelect
				label="Label"
				active={label !== null}
				value={label ?? ''}
				options={[
					{ value: '', label: 'All labels', count: everything, pinned: true },
					...data.labels.map((entry) => ({
						value: entry.label,
						label: entry.label,
						count: entry.count ?? undefined
					}))
				]}
				onchange={(value) => pickLabel(value || null)}
			/>
		{/if}
	{/snippet}

	{#snippet sort()}
		{#if !empty}
			<HeaderSort options={sortOptions} value={sortBy} dir={sortDir} onchange={pickSort} ondir={pickSortDir} dirDisabled={sortBy === 'manual'} />
		{/if}
	{/snippet}

	{#snippet view()}
		{#if !empty}
			<HeaderViewOptions onrefresh={() => void load()} />
		{/if}
	{/snippet}

	{#snippet primary(s)}
		<HeaderAction primary icon="plus" label="New kit" compact={s === 'narrow'} onclick={onNew} />
	{/snippet}

	{#snippet more(s)}
		{#if s === 'narrow' && !empty}
			<Menu
				label="More actions"
				tooltip="More actions"
				items={[{ id: 'refresh', label: 'Refresh', icon: 'refresh' }]}
				onselect={() => void load()}
				width={200}
			/>
		{/if}
	{/snippet}
</ListPageHeader>

</div>

<main class="flex flex-1 flex-col">
	{#if failed && !data}
		<div class="p-4 sm:p-6"><LoadErrorState title="Could not load kits" message="Check the connection and try again." onRetry={() => void load()} /></div>
	{:else if loading && !data}
		<div class="flex justify-center py-16"><Spinner /></div>
	{:else if empty}
		<div class="flex flex-1 flex-col">
			<EmptyState
				page
				icon="layers"
				title="No kits yet"
				description="A kit keeps the pictures, clips, sounds and notes that belong together. Load one into a prompt and call its parts with @."
			>
				{#snippet actions()}
					<Button variant="secondary" icon="plus" onclick={onNew}>New kit</Button>
					<Button variant="secondary" icon="photo" onclick={onPickFromLibrary}>Pick items in All uploads</Button>
				{/snippet}
			</EmptyState>
		</div>
	{:else if data && data.kits.length === 0}
		<div class="p-4 sm:p-6">
			<EmptyState
				compact
				icon="search"
				title="No kits match"
				description="Try another label or clear the search."
			>
				{#snippet actions()}
					<Button size="sm" variant="secondary" onclick={() => { search = ''; pickLabel(null); }}>Clear filters</Button>
				{/snippet}
			</EmptyState>
		</div>
	{:else if data}
		<div class="grid grid-cols-2 gap-3 p-4 sm:gap-4 sm:p-6 lg:grid-cols-3 xl:grid-cols-4" data-kit-grid class:opacity-60={loading}>
			{#each data.kits as kit (kit.id)}
				<KitCard {kit} {onOpen} />
			{/each}
		</div>
		<div class="flex flex-wrap items-center justify-between gap-3 px-4 py-5 sm:px-6">
			<span class="font-mono text-sm tabular-nums text-fg-subtle" data-kit-range>
				{#if pages > 1}{from}–{to} of {data.total} {data.total === 1 ? 'kit' : 'kits'}{:else}{data.total} of {everything} {everything === 1 ? 'kit' : 'kits'}{/if}
			</span>
			<Pagination currentPage={page} totalPages={pages} onPageChange={goTo} />
		</div>
	{/if}
</main>
