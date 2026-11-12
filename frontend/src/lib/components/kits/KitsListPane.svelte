<script lang="ts">
	import { onDestroy, onMount, type Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, EmptyState, IconButton, PageHeader, PageTitle, Pagination, Spinner, LoadErrorState } from '$lib/components/ui';
	import { api } from '$lib/services/api/index';
	import { kitTotal, kitsRevision } from '$lib/stores/kits';
	import { debounce } from '$lib/utils/time';
	import type { Kit, KitListPage } from '$lib/kits/types';
	import KitCard from './KitCard.svelte';
	import KitLabelFilter from './KitLabelFilter.svelte';

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
			const response = await api.listKits({ label, search, page, page_size: PAGE_SIZE });
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

<div class="sticky top-0 z-30">
<PageHeader wrap sticky={false}>
	<div class="flex w-full flex-wrap items-center gap-2 xl:gap-4">
		{@render leading?.()}
		<PageTitle title="Kits" count={everything} countLabel="kits" countLabelOne="kit" />
		{#if !empty}
			<div class="hidden h-6 w-px flex-shrink-0 bg-line-strong md:block"></div>
			<div class="relative min-w-[8rem] max-w-md flex-1">
				<Icon name="search" className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-fg-subtle" />
				<input
					type="text"
					class="input w-full bg-surface-2/50 py-1.5 pl-8 pr-3 text-sm"
					placeholder="Search kits"
					aria-label="Search kits"
					bind:value={search}
					oninput={scheduleSearch}
				/>
			</div>
			{#if data && data.labels.length > 0}
				<div class="no-scrollbar order-last w-full overflow-x-auto md:order-none md:w-auto">
					<KitLabelFilter labels={data.labels} total={everything} value={label} onChange={pickLabel} />
				</div>
			{/if}
		{/if}
		<div class="ml-auto flex flex-shrink-0 items-center gap-2">
			<Button variant="primary" size="sm" icon="plus" onclick={onNew}>New kit</Button>
			{#if !empty}
				<IconButton icon="refresh" label="Refresh" onclick={() => void load()} />
			{/if}
		</div>
	</div>
</PageHeader>

{#if label !== null}
	<div class="flex flex-shrink-0 flex-wrap items-center gap-1.5 border-b border-line bg-surface-1 px-4 py-2 sm:px-6">
		<span class="inline-flex h-6 items-center gap-1.5 rounded border border-signal/25 bg-signal/10 px-2 font-mono text-sm text-signal">
			Label: {label}
			<button type="button" class="opacity-70 hover:opacity-100" aria-label="Remove filter Label: {label}" onclick={() => pickLabel(null)}>
				<Icon name="close" className="h-3 w-3" />
			</button>
		</span>
		<button type="button" class="text-sm text-fg-subtle hover:text-fg" onclick={() => pickLabel(null)}>Clear all</button>
		<span class="ml-auto font-mono text-sm tabular-nums text-fg-subtle">{data?.total ?? 0} of {everything}</span>
	</div>
{/if}
</div>

<main>
	{#if failed && !data}
		<div class="p-4 sm:p-6"><LoadErrorState title="Could not load kits" message="Check the connection and try again." onRetry={() => void load()} /></div>
	{:else if loading && !data}
		<div class="flex justify-center py-16"><Spinner /></div>
	{:else if empty}
		<div class="p-4 sm:p-6">
			<div class="mx-auto max-w-3xl">
				<EmptyState
					icon="layers"
					title="No kits yet"
					description="A kit keeps the pictures, clips, sounds and notes that belong together. Load one into a prompt and call its parts with @."
				>
					{#snippet actions()}
						<Button variant="primary" size="md" icon="plus" onclick={onNew}>New kit</Button>
						<Button variant="secondary" size="md" icon="photo" onclick={onPickFromLibrary}>Pick items in All uploads</Button>
					{/snippet}
				</EmptyState>
			</div>
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
					<Button variant="secondary" size="sm" onclick={() => { search = ''; pickLabel(null); }}>Clear filters</Button>
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
