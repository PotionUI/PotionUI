<script lang="ts">
	import { onMount } from 'svelte';
	import * as adminApi from '$lib/services/admin-api';
	import type { AdminGenerationDetailResult, AdminGenerationListItem } from '$lib/services/admin-api';
	import type { User } from '$lib/stores/auth';
	import { debounce } from '$lib/stores/tabPersistence';
	import { timeAgo } from '$lib/utils/relativeTime';
	import { formatDurationMs } from '$lib/components/generation-panel/barState';
	import { Badge, EmptyState, Input, Spinner } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import MasterDetailLayout from '$lib/components/master-detail/MasterDetailLayout.svelte';
	import { Pane, PaneRow, PanePager } from '$lib/components/pane';
	import AdminTabShell from './AdminTabShell.svelte';
	import AdminFilterBar from './AdminFilterBar.svelte';
	import GenerationRunReport from './GenerationRunReport.svelte';

	const PAGE_SIZE = 20;

	const STATUS_VARIANT: Record<string, 'neutral' | 'success' | 'warning' | 'danger' | 'info'> = {
		completed: 'success',
		running: 'info',
		pending: 'neutral',
		failed: 'danger',
		cancelled: 'warning'
	};

	// Left pane: generation list
	let generations: AdminGenerationListItem[] = [];
	let total = 0;
	let offset = 0;
	let statusFilter = '';
	let userFilter = '';
	let searchQuery = '';
	let createdFrom = '';
	let createdTo = '';
	let listLoading = true;
	let listError: string | null = null;

	// Filter dropdown source - also doubles as the user_id -> username lookup
	// for list rows and the detail header (the row itself only carries `user_id`).
	let users: User[] = [];
	$: usersById = new Map(users.map((u) => [u.id, u]));

	// Right pane: selected generation detail
	let selectedGenerationId: string | null = null;
	let detail: AdminGenerationDetailResult | null = null;
	let detailLoading = false;
	let detailError: string | null = null;

	onMount(async () => {
		const usersResponse = await adminApi.getUsers();
		if (usersResponse.success && usersResponse.data) users = usersResponse.data;
		await loadGenerations();
	});

	async function loadGenerations() {
		listLoading = true;
		listError = null;
		try {
			const response = await adminApi.getAdminGenerations({
				limit: PAGE_SIZE,
				offset,
				status: statusFilter || undefined,
				userId: userFilter || undefined,
				search: searchQuery || undefined,
				createdFrom: createdFrom || undefined,
				createdTo: createdTo || undefined
			});
			if (response.success && response.data) {
				generations = response.data.generations;
				total = response.data.total;
			} else {
				listError = response.message || 'Failed to load generations';
			}
		} catch (e: any) {
			listError = e.response?.data?.message || e.message || 'Failed to load generations';
		} finally {
			listLoading = false;
		}
	}

	const debouncedSearch = debounce(() => {
		offset = 0;
		loadGenerations();
	}, 300);

	function onFilterChange() {
		offset = 0;
		loadGenerations();
	}

	function nextPage() {
		if (offset + PAGE_SIZE >= total) return;
		offset += PAGE_SIZE;
		loadGenerations();
	}

	function prevPage() {
		if (offset === 0) return;
		offset = Math.max(0, offset - PAGE_SIZE);
		loadGenerations();
	}

	function clearFilters() {
		statusFilter = '';
		userFilter = '';
		searchQuery = '';
		createdFrom = '';
		createdTo = '';
		onFilterChange();
	}

	$: activeFilterCount = [statusFilter, userFilter, searchQuery, createdFrom, createdTo].filter(Boolean).length;

	async function selectGeneration(generationId: string) {
		selectedGenerationId = generationId;
		detail = null;
		detailError = null;
		detailLoading = true;
		try {
			const response = await adminApi.getAdminGenerationDetail(generationId);
			if (response.success && response.data) {
				detail = response.data;
			} else {
				detailError = response.message || 'Failed to load generation detail';
			}
		} catch (e: any) {
			detailError = e.response?.data?.message || e.message || 'Failed to load generation detail';
		} finally {
			detailLoading = false;
		}
	}

	function usernameFor(userId: string): string {
		return usersById.get(userId)?.username ?? userId;
	}

	function durationFor(row: AdminGenerationListItem): string {
		if (!row.completed_at) return row.status === 'running' ? 'running' : '-';
		const ms = new Date(row.completed_at).getTime() - new Date(row.created_at).getTime();
		return Number.isFinite(ms) && ms >= 0 ? formatDurationMs(ms) : '-';
	}
</script>

<div class="flex h-[calc(100dvh-var(--header-h)-2rem)] min-h-[36rem] flex-col gap-4 sm:h-[calc(100dvh-var(--header-h)-3rem)]">
	<AdminTabShell
		title="Generations"
		icon="generation"
		counts={[{ label: total === 1 ? 'generation' : 'generations', value: total }]}
	/>

	{#snippet generationSearch()}
		<div class="relative">
			<Icon name="search" className="w-4 h-4 text-fg-subtle absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
			<Input
				bind:value={searchQuery}
				oninput={debouncedSearch}
				type="search"
				class="pl-9"
				placeholder="Search prompt, preset…"
				aria-label="Search generations"
			/>
		</div>
	{/snippet}

	{#snippet generationFilters()}
		<div class="flex items-center gap-2">
			<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">Status</span>
			<select class="input w-36" bind:value={statusFilter} onchange={onFilterChange} aria-label="Filter by status">
				<option value="">All</option>
				<option value="completed">Completed</option>
				<option value="running">Running</option>
				<option value="pending">Pending</option>
				<option value="failed">Failed</option>
				<option value="cancelled">Cancelled</option>
			</select>
		</div>

		<div class="flex items-center gap-2">
			<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">User</span>
			<select class="input w-40" bind:value={userFilter} onchange={onFilterChange} aria-label="Filter by user">
				<option value="">All users</option>
				{#each users as user (user.id)}
					<option value={user.id}>{user.username}</option>
				{/each}
			</select>
		</div>

		<div class="flex items-center gap-2">
			<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">From</span>
			<input type="date" class="input" bind:value={createdFrom} onchange={onFilterChange} aria-label="Created from" />
		</div>

		<div class="flex items-center gap-2">
			<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">To</span>
			<input type="date" class="input" bind:value={createdTo} onchange={onFilterChange} aria-label="Created to" />
		</div>
	{/snippet}

	<AdminFilterBar
		search={generationSearch}
		filters={generationFilters}
		activeCount={activeFilterCount}
		onClear={clearFilters}
	/>

	<section class="flex-1 min-h-0 rounded-lg border border-line bg-surface-1 overflow-hidden">
		<MasterDetailLayout leftWidth={360} minWidth={300} maxWidth={480} storageKey="admin-generations-width">
			<div slot="list" class="h-full min-h-0">
				<Pane
					label="Generations"
					count={total}
					loading={listLoading}
					isEmpty={!listLoading && (Boolean(listError) || generations.length === 0)}
					bodyRole="listbox"
					ariaLabel="Generations"
				>
					{#snippet empty()}
						<div class="p-4 h-full flex items-center justify-center">
							{#if listError}
								<EmptyState title="Could not load generations" description={listError} icon="warning" compact />
							{:else}
								<EmptyState
									icon="generation"
									title={activeFilterCount > 0 ? 'No generations match your filters' : 'No generations yet'}
									description={activeFilterCount > 0
										? 'Try a different status, user, or date range.'
										: 'Generations show up here once a user runs one.'}
									compact
								/>
							{/if}
						</div>
					{/snippet}

					{#snippet children()}
						{#each generations as row (row.id)}
							{#snippet rowBody()}
								<div class="flex items-center justify-between gap-2 mb-1">
									<span class="text-sm font-medium truncate text-fg">{row.preset_name || row.mode || 'Untitled generation'}</span>
									<Badge variant={STATUS_VARIANT[row.status] ?? 'neutral'} size="sm" dot class="uppercase flex-shrink-0">
										{row.status}
									</Badge>
								</div>
								<div class="flex items-center justify-between gap-2 text-xs text-fg-subtle">
									<span class="truncate">{usernameFor(row.user_id)}</span>
									<span class="font-mono tabular-nums flex-shrink-0">{timeAgo(row.created_at)}</span>
								</div>
								<div class="flex items-center justify-between gap-2 text-2xs font-mono tabular-nums text-fg-subtle mt-0.5">
									<span>{durationFor(row)} · {row.files?.length ?? 0} file{(row.files?.length ?? 0) === 1 ? '' : 's'}</span>
									{#if !row.has_run_report}
										<span class="text-fg-disabled normal-case" title="No run report recorded for this generation">no report</span>
									{/if}
								</div>
							{/snippet}
							<PaneRow
								selected={selectedGenerationId === row.id}
								onclick={() => selectGeneration(row.id)}
								children={rowBody}
							/>
						{/each}
					{/snippet}

					{#snippet footer()}
						<PanePager {offset} limit={PAGE_SIZE} {total} onPrev={prevPage} onNext={nextPage} />
					{/snippet}
				</Pane>
			</div>

			<div slot="detail" class="h-full min-h-0 flex flex-col overflow-y-auto">
				{#if !selectedGenerationId}
					<div class="flex-1 p-5 flex items-center justify-center">
						<EmptyState
							icon="generation"
							title="Select a generation"
							description="Choose a generation from the list to inspect its run report."
							compact
						/>
					</div>
				{:else if detailLoading}
					<div class="flex-1 flex items-center justify-center">
						<Spinner size="lg" />
					</div>
				{:else if detailError}
					<div class="flex-1 p-5 flex items-center justify-center">
						<EmptyState title="Could not load generation" description={detailError} icon="warning" compact />
					</div>
				{:else if detail}
					<div class="p-4 sm:p-5">
						<GenerationRunReport
							generation={detail.generation}
							report={detail.run_report}
							username={usernameFor(detail.generation.user_id)}
						/>
					</div>
				{/if}
			</div>
		</MasterDetailLayout>
	</section>
</div>
