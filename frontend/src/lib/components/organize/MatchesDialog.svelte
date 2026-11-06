<script lang="ts">
	import { onMount } from 'svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { EmptyState, LoadErrorState, Pagination, SegmentedControl, Spinner } from '$lib/components/ui';
	import { useOrganizeApi } from '$lib/organize/scope';
	import { subjectIcon } from '$lib/organize/icons';
	import { modelTypePresentation } from '$lib/utils/modelPresentation';
	import type { OrganizeMatchItem, OrganizeMatchStatus, OrganizePreviewRequest } from '$lib/types/organize';

	const api = useOrganizeApi();

	let {
		request,
		total,
		onclose
	}: {
		request: OrganizePreviewRequest;
		total: number;
		onclose: () => void;
	} = $props();

	const PAGE_SIZE = 25;
	const filters = [
		{ id: 'all', label: 'All' },
		{ id: 'would_change', label: 'Would change' },
		{ id: 'already_done', label: 'Already done' }
	];

	let filter = $state('all');
	let page = $state(1);
	let rows = $state<OrganizeMatchItem[]>([]);
	let loadedCount = $state<number | null>(null);
	let loading = $state(true);
	let failed = $state(false);
	let sequence = 0;

	const count = $derived(loadedCount ?? total);
	const pages = $derived(Math.max(1, Math.ceil(count / PAGE_SIZE)));
	const from = $derived(count === 0 ? 0 : (page - 1) * PAGE_SIZE + 1);
	const to = $derived(Math.min(count, page * PAGE_SIZE));

	async function load() {
		const mine = ++sequence;
		loading = true;
		failed = false;
		try {
			const response = await api.previewOrganizeMatches({
				...request,
				status: filter === 'all' ? undefined : (filter as OrganizeMatchStatus),
				page,
				page_size: PAGE_SIZE
			});
			if (mine !== sequence) return;
			if (!response.success || !response.data) {
				failed = true;
			} else {
				rows = response.data.items;
				loadedCount = response.data.total;
			}
		} catch {
			if (mine !== sequence) return;
			failed = true;
		}
		loading = false;
	}

	onMount(load);

	function setFilter(next: string) {
		filter = next;
		page = 1;
		void load();
	}

	function goTo(next: number) {
		page = next;
		void load();
	}
</script>

<BaseModal
	isOpen
	title="Models this rule matches"
	subtitle="Checked against the conditions as they are now, before anything is saved."
	sizeClass="md:w-[min(820px,calc(100vw-64px))] md:max-w-none md:h-[min(720px,88vh)]"
	on:close={onclose}
>
	<div class="flex h-full min-h-0 flex-1 flex-col" data-testid="matches-dialog">
		<div class="flex flex-wrap items-center justify-between gap-3 border-b border-line px-3 py-3 md:px-4">
			<SegmentedControl items={filters} selected={filter} onSelect={setFilter} ariaLabel="Show models" variant="toggle" />
			<p class="flex items-center gap-2 font-mono text-[12px] tabular-nums text-fg-muted" role="status">
				{#if loading}<Spinner size="sm" />{/if}
				{count} {count === 1 ? 'model' : 'models'}
			</p>
		</div>
		<div class="min-h-0 flex-1 overflow-y-auto" class:opacity-60={loading && rows.length > 0}>
			{#if failed}
				<LoadErrorState message="The list could not be loaded." onRetry={load} retrying={loading} />
			{:else if loading && rows.length === 0}
				<div class="flex h-40 items-center justify-center"><Spinner size="lg" /></div>
			{:else if rows.length === 0}
				<EmptyState icon="search" title="Nothing here" description="No model is in this list." compact />
			{:else}
				<ul class="divide-y divide-line" data-testid="matches-list">
					{#each rows as row (row.item_id)}
						<li class="flex items-center gap-3 px-3 py-2.5 md:px-4" data-testid="match-row">
							<Icon name={subjectIcon('model')} className="h-4 w-4 flex-shrink-0 text-fg-subtle" />
							<div class="min-w-0 flex-1">
								<p class="truncate font-mono text-sm text-fg">{row.name}</p>
							</div>
							{#if row.model_type}
								<span class="hidden flex-shrink-0 text-[12px] text-fg-muted sm:block">{modelTypePresentation(row.model_type).label}</span>
							{/if}
							<span class="w-24 flex-shrink-0 text-right text-[12px] {row.status === 'would_change' ? 'text-signal' : 'text-fg-subtle'}">
								{row.status === 'would_change' ? 'Would change' : 'Already done'}
							</span>
						</li>
					{/each}
				</ul>
			{/if}
		</div>
		<div class="flex flex-col items-center gap-2 border-t border-line px-3 py-3 sm:flex-row sm:justify-between md:px-4">
			<p class="font-mono text-[12px] tabular-nums text-fg-muted">Showing {from}-{to} of {count}</p>
			{#if pages > 1}
				<Pagination currentPage={page} totalPages={pages} size="sm" onPageChange={goTo} />
			{/if}
		</div>
	</div>
</BaseModal>
