<script lang="ts">
	import { onMount } from 'svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, EmptyState, LoadErrorState, Pagination, SegmentedControl, Spinner } from '$lib/components/ui';
	import { useOrganizeApi } from '$lib/organize/scope';
	import { toasts } from '$lib/stores/toast';
	import { outcomesOf, runTitle } from '$lib/organize/activity';
	import { parseOrganizeError } from '$lib/organize/errors';
	import { timeAgo } from '$lib/utils/relativeTime';
	import type { OrganizeOutcome, OrganizeRun, OrganizeRunItem } from '$lib/types/organize';

	const api = useOrganizeApi();

	let { run, onclose, onretried }: { run: OrganizeRun; onclose: () => void; onretried?: () => void } = $props();

	const PAGE_SIZE = 25;
	const outcomes = $derived(outcomesOf(run));
	const total = $derived(outcomes.updated + outcomes.skipped + outcomes.waiting);

	const filters = $derived([
		{ id: 'all', label: `All ${total}` },
		{ id: 'updated', label: `Updated ${outcomes.updated}` },
		{ id: 'skipped', label: `Skipped ${outcomes.skipped}` },
		{ id: 'waiting', label: `Waiting ${outcomes.waiting}` }
	]);

	let filter = $state('all');
	let page = $state(1);
	let rows = $state<OrganizeRunItem[]>([]);
	let loadedCount = $state<number | null>(null);
	let waitingLeft = $state<number | null>(null);
	let loading = $state(true);
	let failed = $state(false);
	let retrying = $state(false);
	let sequence = 0;

	const count = $derived(loadedCount ?? total);
	const waiting = $derived(waitingLeft ?? outcomes.waiting);
	const pages = $derived(Math.max(1, Math.ceil(count / PAGE_SIZE)));
	const from = $derived(count === 0 ? 0 : (page - 1) * PAGE_SIZE + 1);
	const to = $derived(Math.min(count, page * PAGE_SIZE));

	async function load() {
		const mine = ++sequence;
		loading = true;
		failed = false;
		try {
			const response = await api.getOrganizeRunItems(run.id, {
				status: filter === 'all' ? undefined : (filter as OrganizeOutcome),
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

	async function retry() {
		retrying = true;
		try {
			const response = await api.retryOrganizeRun(run.id);
			if (!response.success) {
				toasts.error(response.message || 'They could not be queued. Try again in a moment.');
				return;
			}
			toasts.success(`Trying ${waiting} ${waiting === 1 ? 'model' : 'models'} again`);
			onretried?.();
			onclose();
		} catch (err) {
			const info = parseOrganizeError(err, 'They could not be queued. Try again in a moment.');
			if (info.code === 'nothing_waiting') {
				waitingLeft = 0;
				toasts.info('Nothing is waiting in this run any more.');
				void load();
			} else {
				toasts.error(info.message);
			}
		} finally {
			retrying = false;
		}
	}

	function tone(status: OrganizeOutcome): string {
		return status === 'updated' ? 'text-success' : status === 'waiting' ? 'text-warning' : 'text-fg-subtle';
	}
</script>

<BaseModal
	isOpen
	title="Run details"
	subtitle={runTitle(run)}
	sizeClass="md:w-[min(820px,calc(100vw-64px))] md:max-w-none md:h-[min(720px,88vh)]"
	on:close={onclose}
>
	<div class="flex h-full min-h-0 flex-1 flex-col" data-testid="run-details">
		<div class="flex flex-wrap items-center justify-between gap-3 border-b border-line px-3 py-3 md:px-4">
			<SegmentedControl items={filters} selected={filter} onSelect={setFilter} ariaLabel="Show models" variant="toggle" />
			{#if waiting > 0}
				<Button size="sm" variant="secondary" icon="refresh" loading={retrying} onclick={retry}>
					Try the {waiting} waiting again now
				</Button>
			{/if}
		</div>
		<div class="min-h-0 flex-1 overflow-y-auto" class:opacity-60={loading && rows.length > 0}>
			{#if failed}
				<LoadErrorState message="The details could not be loaded." onRetry={load} retrying={loading} />
			{:else if loading && rows.length === 0}
				<div class="flex h-40 items-center justify-center"><Spinner size="lg" /></div>
			{:else if rows.length === 0}
				<EmptyState icon="check-circle" title="Nothing here" description="No model had this outcome." compact />
			{:else}
				<ul class="divide-y divide-line" data-testid="run-items">
					{#each rows as row (`${row.item_id}:${row.status}`)}
						<li class="flex items-start gap-3 px-3 py-2.5 md:px-4" data-testid="run-item" data-outcome={row.status}>
							<Icon
								name={row.status === 'updated' ? 'check-circle' : row.status === 'waiting' ? 'clock' : 'warning'}
								className="mt-0.5 h-4 w-4 flex-shrink-0 {tone(row.status)}"
							/>
							<div class="min-w-0 flex-1">
								<p class="truncate font-mono text-sm text-fg">{row.name}</p>
								{#if row.detail}
									<p class="text-[12px] text-fg-muted">{row.detail}</p>
								{/if}
							</div>
							{#if row.status === 'waiting' && row.attempts > 0}
								<span class="flex-shrink-0 text-right font-mono text-[12px] tabular-nums text-fg-subtle">
									try {row.attempts}{#if row.next_try_at}<br />{timeAgo(row.next_try_at)}{/if}
								</span>
							{/if}
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
