<script lang="ts">
	import { onMount } from 'svelte';
	import { Button, Card, EmptyState, Input, LoadErrorState, Spinner, Switch } from '$lib/components/ui';
	import LibraryShell from '$lib/components/library/LibraryShell.svelte';
	import { DetailSection } from '$lib/components/detail';
	import { DataTable, type DataTableColumn } from '$lib/components/table';
	import { api } from '$lib/services/api';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import { getApiErrorMessage } from '$lib/utils/logger';
	import { timeAgo } from '$lib/utils/relativeTime';
	import type { OrganizeAdminOverview, OrganizeAdminUser } from '$lib/types/organize';
	import {
		controlsDirty,
		controlsDraftFrom,
		controlsPayload,
		isForbidden,
		parseUserCap,
		replaceUser,
		userCapText,
		validateControls,
		type ControlsDraft
	} from './autoOrganizeAdmin';

	const SECTIONS = [{ id: 'overview', label: 'Auto-organize', icon: 'wand' }] as const;

	let overview = $state<OrganizeAdminOverview | null>(null);
	let loading = $state(true);
	let loadError = $state<string | null>(null);
	let draft = $state<ControlsDraft>({ defaultRuleCap: '', hourlyLimit: '' });
	let saving = $state(false);
	let pausingAll = $state(false);
	let busyUserId = $state<string | null>(null);
	let capEdits = $state<Record<string, string>>({});
	let capErrors = $state<Record<string, string>>({});

	const errors = $derived(validateControls(draft));
	const hasErrors = $derived(Object.keys(errors).length > 0);
	const dirty = $derived(overview ? controlsDirty(overview, draft) : false);

	function adopt(next: OrganizeAdminOverview) {
		overview = next;
		draft = controlsDraftFrom(next);
		capEdits = {};
		capErrors = {};
	}

	async function load() {
		loading = true;
		loadError = null;
		try {
			const response = await api.getOrganizeAdminOverview();
			if (response.success && response.data) adopt(response.data);
			else loadError = response.message || 'Auto-organize settings could not be loaded.';
		} catch (error) {
			loadError = isForbidden(error)
				? 'Only administrators can see these settings.'
				: getApiErrorMessage(error, 'Auto-organize settings could not be loaded.');
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		void load();
	});

	async function toggleAll(next: boolean) {
		if (!overview) return;
		if (next) {
			const confirmed = await confirmDialog({
				title: 'Pause Auto-organize for everyone?',
				message:
					'Nothing will be filed for anyone and running backfills stop after their current batch. Nobody loses a rule, and filing resumes when you turn this off.',
				variant: 'danger'
			});
			if (!confirmed) return;
		}
		pausingAll = true;
		try {
			const response = await api.updateOrganizeAdminControls({ paused_all: next });
			if (response.success && response.data) {
				overview = response.data;
				toasts.success(next ? 'Auto-organize is paused for everyone' : 'Auto-organize is running again');
			} else {
				toasts.error(response.message || 'Could not change the setting');
			}
		} catch (error) {
			toasts.error(getApiErrorMessage(error, 'Could not change the setting'));
		} finally {
			pausingAll = false;
		}
	}

	async function saveControls() {
		if (!overview || hasErrors || !dirty) return;
		saving = true;
		try {
			const response = await api.updateOrganizeAdminControls(controlsPayload(overview, draft));
			if (response.success && response.data) {
				adopt(response.data);
				toasts.success('Auto-organize limits saved');
			} else {
				toasts.error(response.message || 'Could not save the limits');
			}
		} catch (error) {
			toasts.error(getApiErrorMessage(error, 'Could not save the limits'));
		} finally {
			saving = false;
		}
	}

	function discardControls() {
		if (overview) draft = controlsDraftFrom(overview);
	}

	async function updateUser(user: OrganizeAdminUser, body: { paused?: boolean; rule_cap?: number | null }) {
		busyUserId = user.user_id;
		try {
			const response = await api.updateOrganizeAdminUser(user.user_id, body);
			if (response.success && response.data && overview) {
				overview = replaceUser(overview, response.data);
				const { [user.user_id]: _edit, ...restEdits } = capEdits;
				capEdits = restEdits;
				const { [user.user_id]: _error, ...restErrors } = capErrors;
				capErrors = restErrors;
			} else {
				toasts.error(response.message || 'Could not update this person');
			}
		} catch (error) {
			toasts.error(getApiErrorMessage(error, 'Could not update this person'));
		} finally {
			busyUserId = null;
		}
	}

	function commitCap(user: OrganizeAdminUser) {
		const text = capEdits[user.user_id];
		if (text === undefined || text === userCapText(user)) return;
		const parsed = parseUserCap(text);
		if (!parsed.ok) {
			capErrors = { ...capErrors, [user.user_id]: parsed.message };
			return;
		}
		void updateUser(user, { rule_cap: parsed.value });
	}

	const columns: DataTableColumn<OrganizeAdminUser>[] = [
		{ key: 'username', label: 'Person', width: 'minmax(140px,1.5fr)', accessor: (user) => user.username },
		{ key: 'rules', label: 'Rules', width: '80px', mono: true, accessor: (user) => user.rules },
		{ key: 'enabled', label: 'On', width: '70px', mono: true, priority: 1, accessor: (user) => user.enabled_rules },
		{ key: 'paused_rules', label: 'Rules paused', width: '110px', mono: true, priority: 1, accessor: (user) => user.paused_rules },
		{ key: 'filed24', label: 'Filed 24h', width: '100px', mono: true, priority: 1, accessor: (user) => user.items_filed_24h },
		{ key: 'filedTotal', label: 'Filed total', width: '110px', mono: true, priority: 2, accessor: (user) => user.items_filed_total },
		{ key: 'lastRun', label: 'Last run', width: '120px', mono: true, priority: 2, cell: lastRunCell },
		{ key: 'cap', label: 'Rule limit', width: '150px', cell: capCell },
		{ key: 'paused', label: 'Filing paused', width: '120px', cell: pauseCell }
	];
</script>

{#snippet lastRunCell(user: OrganizeAdminUser)}
	{#if user.last_run_at}
		<span>{timeAgo(user.last_run_at)}</span>
	{:else}
		<span class="text-fg-subtle">Never</span>
	{/if}
{/snippet}

{#snippet capCell(user: OrganizeAdminUser)}
	<div class="flex flex-col gap-0.5" role="presentation" onclick={(event) => event.stopPropagation()}>
		<Input
			class="h-7 w-24 font-mono text-xs tabular-nums"
			inputmode="numeric"
			aria-label={`Rule limit for ${user.username}`}
			placeholder={String(user.effective_rule_cap)}
			disabled={busyUserId === user.user_id}
			invalid={!!capErrors[user.user_id]}
			value={capEdits[user.user_id] ?? userCapText(user)}
			oninput={(event: Event) => {
				capEdits = { ...capEdits, [user.user_id]: (event.currentTarget as HTMLInputElement).value };
			}}
			onblur={() => commitCap(user)}
			onkeydown={(event: KeyboardEvent) => {
				if (event.key === 'Enter') commitCap(user);
			}}
		/>
		{#if capErrors[user.user_id]}
			<span class="text-2xs text-danger">{capErrors[user.user_id]}</span>
		{/if}
	</div>
{/snippet}

{#snippet pauseCell(user: OrganizeAdminUser)}
	<Switch
		label={user.paused ? `Resume filing for ${user.username}` : `Pause filing for ${user.username}`}
		checked={user.paused}
		busy={busyUserId === user.user_id}
		onclick={(event) => event.stopPropagation()}
		onchange={(next) => updateUser(user, { paused: next })}
	/>
{/snippet}

<LibraryShell
	title="Auto-organize"
	persistKey="admin-auto-organize"
	heightClass="h-full"
	sections={SECTIONS}
	section="overview"
	onSelectSection={() => {}}
	count={overview?.users.length ?? null}
>
	{#if loading && !overview}
		<div class="flex h-40 items-center justify-center">
			<Spinner size="lg" />
		</div>
	{:else if loadError || !overview}
		<LoadErrorState message={loadError ?? 'Auto-organize settings could not be loaded.'} onRetry={load} retrying={loading} />
	{:else}
		<div class="mx-auto flex max-w-5xl flex-col gap-4 p-4 sm:p-6">
			<p class="text-sm text-fg-muted">
				Rules file people's own items into their own collections and tags. You only see totals here, never what a rule says.
			</p>

			<div class="grid grid-cols-2 gap-3 lg:grid-cols-5">
				{#each [{ label: 'People with rules', value: overview.totals.users_with_rules }, { label: 'Rules', value: overview.totals.rules }, { label: 'Switched on', value: overview.totals.enabled_rules }, { label: 'Paused', value: overview.totals.paused_rules }, { label: 'Filed in 24h', value: overview.totals.items_filed_24h }] as tile (tile.label)}
					<Card padding="sm">
						<div class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">{tile.label}</div>
						<div class="mt-1 font-mono text-xl font-semibold tabular-nums text-fg">{tile.value}</div>
					</Card>
				{/each}
			</div>

			<div class="flex flex-wrap gap-x-6 gap-y-1 font-mono text-xs tabular-nums text-fg-subtle">
				<span>Filed in total {overview.totals.items_filed_total}</span>
				<span>Backfills running {overview.totals.running_jobs}</span>
			</div>

			<DetailSection label="Pause everything">
				<div class="flex items-center justify-between gap-4">
					<div class="min-w-0">
						<p class="text-sm font-medium text-fg">Pause Auto-organize for everyone</p>
						<p class="text-xs text-fg-muted">
							Nothing is filed while this is on. Rules are kept and start working again when you turn it off.
						</p>
					</div>
					<Switch
						label={overview.paused_all ? 'Resume Auto-organize for everyone' : 'Pause Auto-organize for everyone'}
						checked={overview.paused_all}
						busy={pausingAll}
						onchange={toggleAll}
					/>
				</div>
			</DetailSection>

			<DetailSection label="Limits">
				<div class="grid gap-4 sm:grid-cols-2">
					<label class="flex flex-col gap-1">
						<span class="text-xs font-medium text-fg">Rules per person</span>
						<Input
							class="font-mono tabular-nums"
							inputmode="numeric"
							bind:value={draft.defaultRuleCap}
							invalid={!!errors.defaultRuleCap}
						/>
						<span class="text-xs {errors.defaultRuleCap ? 'text-danger' : 'text-fg-subtle'}">
							{errors.defaultRuleCap ?? 'The default for everyone. You can set a different limit for one person below.'}
						</span>
					</label>
					<label class="flex flex-col gap-1">
						<span class="text-xs font-medium text-fg">Items one rule may file per hour</span>
						<Input
							class="font-mono tabular-nums"
							inputmode="numeric"
							bind:value={draft.hourlyLimit}
							invalid={!!errors.hourlyLimit}
						/>
						<span class="text-xs {errors.hourlyLimit ? 'text-danger' : 'text-fg-subtle'}">
							{errors.hourlyLimit ?? 'A rule that goes past this is paused so a mistake cannot file everything.'}
						</span>
					</label>
				</div>
				<div class="mt-4 flex items-center justify-end gap-2">
					<Button variant="ghost" size="sm" disabled={!dirty || saving} onclick={discardControls}>Discard</Button>
					<Button variant="primary" size="sm" loading={saving} disabled={!dirty || hasErrors} onclick={saveControls}>
						Save limits
					</Button>
				</div>
			</DetailSection>

			<DetailSection label="People" padded={false}>
				<DataTable {columns} rows={overview.users} getRowId={(user) => user.user_id} loading={false}>
					{#snippet emptyState()}
						<EmptyState icon="group" title="Nobody has rules yet" description="People show up here once they make their first rule." compact />
					{/snippet}
				</DataTable>
			</DetailSection>
		</div>
	{/if}
</LibraryShell>
