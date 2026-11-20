<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { page as pageStore } from '$app/stores';
	import { api } from '$lib/services/api/index';
	import * as adminApi from '$lib/services/admin-api';
	import type { AdminApiKey, AdminApiKeysPage } from '$lib/services/api/apiKeys';
	import { confirmDialog } from '$lib/stores/confirm';
	import { toasts } from '$lib/stores/toast';
	import { parseApiError } from '$lib/utils/apiErrors';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, EmptyState, Input, Pagination, SegmentedControl, Spinner } from '$lib/components/ui';
	import LoadErrorState from '$lib/components/ui/LoadErrorState.svelte';
	import type { UserPickerUser } from '$lib/components/ui/userPicker';
	import AdminKeyDetail from './AdminKeyDetail.svelte';
	import AdminKeyRow from './AdminKeyRow.svelte';
	import OwnerFilter from './OwnerFilter.svelte';
	import { ADMIN_KEY_GRID } from './adminKeyColumns';
	import {
		ADMIN_KEYS_PAGE_SIZE,
		changeView,
		goToPage,
		isFiltered,
		listQuery,
		rangeText,
		revokeCopy,
		statusItems,
		totalPages,
		type AdminKeysView
	} from './adminKeysView';

	let { onTotal }: { onTotal?: (total: number) => void } = $props();

	const initialOwner = $pageStore.url.searchParams.get('owner');

	let view = $state<AdminKeysView>({ status: 'all', owner: initialOwner, page: 1, search: '' });
	let data = $state<AdminApiKeysPage | null>(null);
	let loading = $state(true);
	let failed = $state(false);
	let people = $state<UserPickerUser[]>([]);
	let opened = $state<AdminApiKey | null>(null);
	let revokingId = $state<string | null>(null);
	let seq = 0;
	let searchTimer: ReturnType<typeof setTimeout> | undefined;

	const items = $derived(data?.items ?? []);
	const total = $derived(data?.total ?? 0);
	const pages = $derived(totalPages(total, ADMIN_KEYS_PAGE_SIZE));
	const filtered = $derived(isFiltered(view));
	const emptyIsNone = $derived(total === 0 && !filtered);
	const chips = $derived(statusItems(data?.counts));

	async function load() {
		const mine = ++seq;
		loading = true;
		failed = false;
		try {
			const response = await api.listAdminApiKeys(listQuery(view));
			if (mine !== seq) return;
			if (response.success && response.data) {
				data = response.data;
				onTotal?.(response.data.total);
				const last = totalPages(response.data.total);
				if (view.page > last) {
					view = goToPage(view, last, last);
					void load();
				}
			} else failed = true;
		} catch {
			if (mine === seq) failed = true;
		} finally {
			if (mine === seq) loading = false;
		}
	}

	async function loadPeople() {
		try {
			const response = await adminApi.getUsers();
			if (response.success && response.data) {
				people = response.data.map((user) => ({ id: user.id, username: user.username, email: user.email }));
			}
		} catch {
			people = [];
		}
	}

	function setStatus(id: string) {
		view = changeView(view, { status: id as AdminKeysView['status'] });
		void load();
	}

	function setOwner(id: string | null) {
		view = changeView(view, { owner: id });
		void load();
	}

	function setPage(next: number) {
		view = goToPage(view, next, pages);
		void load();
	}

	function onSearch(event: Event) {
		view = changeView(view, { search: (event.currentTarget as HTMLInputElement).value });
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => void load(), 250);
	}

	function clearAll() {
		clearTimeout(searchTimer);
		view = { status: 'all', owner: null, page: 1, search: '' };
		void load();
	}

	async function revoke(key: AdminApiKey) {
		const copy = revokeCopy(key);
		const ok = await confirmDialog({ title: copy.title, message: copy.message, variant: 'danger', confirmLabel: copy.confirmLabel });
		if (!ok) return;
		revokingId = key.id;
		try {
			const response = await api.revokeAdminApiKey(key.id);
			if (!response.success) throw new Error(response.message || 'Failed to revoke the key.');
			toasts.success('Key revoked');
			opened = null;
			await load();
		} catch (err) {
			toasts.error(parseApiError(err).message || 'Failed to revoke the key.');
		} finally {
			revokingId = null;
		}
	}

	onDestroy(() => clearTimeout(searchTimer));

	onMount(() => {
		void load();
		void loadPeople();
	});
</script>

<div class="space-y-4" data-testid="admin-api-keys">
	<div class="flex flex-wrap items-center gap-2">
		<div class="relative min-w-[11rem] flex-1 sm:max-w-sm">
			<span class="pointer-events-none absolute inset-y-0 left-3 flex items-center text-fg-subtle"><Icon name="search" className="h-4 w-4" /></span>
			<Input
				type="search"
				class="pl-9 text-sm"
				placeholder="Search keys"
				aria-label="Search keys"
				autocomplete="off"
				value={view.search}
				oninput={onSearch}
			/>
		</div>
		<OwnerFilter users={people} selectedId={view.owner} onSelect={setOwner} />
		<div class="w-full overflow-x-auto sm:ml-auto sm:w-auto">
			<SegmentedControl variant="toggle" ariaLabel="Key status" items={chips} selected={view.status} onSelect={setStatus} />
		</div>
	</div>

	{#if loading && !data}
		<div class="flex items-center justify-center py-16"><Spinner size="lg" /></div>
	{:else if failed && !data}
		<LoadErrorState message="The keys could not be loaded." onRetry={load} retrying={loading} />
	{:else}
		{#if items.length === 0}
			<EmptyState
				icon={emptyIsNone ? 'key' : 'search'}
				title={emptyIsNone ? 'No keys yet' : 'No keys match'}
				description={emptyIsNone ? 'Keys people create show up here.' : 'Change the search or the filters.'}
				compact
			>
				{#snippet actions()}
					{#if !emptyIsNone}
						<Button size="sm" variant="secondary" onclick={clearAll}>Clear filters</Button>
					{/if}
				{/snippet}
			</EmptyState>
		{:else}
			<div class="overflow-hidden rounded-lg border border-line bg-surface-1 shadow-raised" class:opacity-60={loading}>
				<div class="hidden border-b border-line bg-surface-2/40 py-2 pl-4 pr-11 font-mono text-sm uppercase tracking-[0.07em] text-fg-subtle lg:grid lg:gap-x-4 {ADMIN_KEY_GRID}">
					<span>Owner</span>
					<span>Key</span>
					<span>Can do</span>
					<span>Last used</span>
					<span>Expires</span>
					<span></span>
				</div>
				<div class="divide-y divide-line" data-list>
					{#each items as apiKey (apiKey.id)}
						<AdminKeyRow {apiKey} revoking={revokingId === apiKey.id} onOpen={(key) => (opened = key)} onRevoke={revoke} />
					{/each}
				</div>
			</div>
		{/if}

		<div class="flex flex-wrap items-center justify-between gap-3">
			<p class="font-mono text-sm tabular-nums text-fg-subtle" data-testid="admin-keys-range">
				{rangeText(view.page, ADMIN_KEYS_PAGE_SIZE, items.length, total)}
			</p>
			{#if pages > 1}
				<Pagination currentPage={view.page} totalPages={pages} size="sm" onPageChange={setPage} />
			{/if}
		</div>
	{/if}
</div>

{#if opened}
	<AdminKeyDetail apiKey={opened} revoking={revokingId === opened.id} onClose={() => (opened = null)} onRevoke={revoke} />
{/if}
