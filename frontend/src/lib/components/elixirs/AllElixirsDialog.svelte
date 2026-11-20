<script lang="ts">
	import { onDestroy, onMount, tick } from 'svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Button, EmptyState, Menu, Spinner, LoadErrorState } from '$lib/components/ui';
	import { tabsStore } from '$lib/stores/tabs';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import { isToolTab, type ToolTabStatus } from '$lib/types/tabs';
	import { elixirsApi } from '$lib/services/api/elixirs';
	import { ELIXIR_TOOL, closeElixirTabs, elixirIdOf, openElixirTab, renameElixirTabs } from '$lib/elixirs/tab';
	import { elixirRenamed } from '$lib/elixirs/events';
	import { pollDelay } from '$lib/elixirs/listing';
	import { MANAGE_FILTERS, copyName, deleteBlocked as baseBlocked, manageRows, rowStatus, stepWord, type PickerSummary } from '$lib/elixirs/picker';
	import { relativeTime } from '$lib/elixirs/time';
	import ElixirThumbs from './ElixirThumbs.svelte';
	import StatusDot from './StatusDot.svelte';

	let { onclose }: { onclose: () => void } = $props();

	function activeOf(elixir: PickerSummary): number {
		return Number(elixir.active_runs ?? 0);
	}

	function activeText(elixir: PickerSummary): string {
		const count = activeOf(elixir);
		return `${count} active ${count === 1 ? 'brew' : 'brews'}`;
	}

	function deleteBlocked(elixir: PickerSummary, tab: ToolTabStatus | null | undefined): string | null {
		const count = activeOf(elixir);
		if (count > 1) return `Stop its ${count} active brews first`;
		return baseBlocked(elixir, tab) ?? (count === 1 ? 'Stop its active brew first' : null);
	}

	const TONE_TEXT: Record<string, string> = {
		signal: 'text-signal',
		danger: 'text-danger',
		success: 'text-success',
		warning: 'text-warning',
		neutral: 'text-fg-subtle'
	};

	const client = elixirsApi();

	let elixirs = $state.raw<PickerSummary[] | null>(null);
	let loadError = $state<string | null>(null);
	let query = $state('');
	let filter = $state('all');
	let renaming = $state<string | null>(null);
	let renameValue = $state('');
	let renameInput = $state<HTMLInputElement>();
	let busyId = $state<string | null>(null);
	let creating = $state(false);
	let pollTimer: ReturnType<typeof setTimeout> | undefined;
	let alive = true;

	let openTabs = $derived(
		new Map<string, ToolTabStatus | null>(
			$tabsStore.tabs
				.filter(isToolTab)
				.filter((tab) => tab.tool === ELIXIR_TOOL && !!elixirIdOf(tab))
				.map((tab) => [elixirIdOf(tab) as string, tab.status ?? null])
		)
	);
	let rows = $derived(manageRows(elixirs ?? [], query, filter));

	onMount(() => {
		void load();
	});

	onDestroy(() => {
		alive = false;
		clearTimeout(pollTimer);
	});

	async function load() {
		const result = await client.listElixirs();
		if (!alive) return;
		if (result.ok) {
			elixirs = result.data?.elixirs ?? [];
			loadError = null;
		} else if (elixirs === null) {
			loadError = result.error.message;
		}
		clearTimeout(pollTimer);
		const delay = pollDelay(elixirs);
		if (delay !== null) pollTimer = setTimeout(() => void load(), delay);
	}

	function open(elixir: PickerSummary) {
		openElixirTab({ id: elixir.id, name: elixir.name });
		onclose();
	}

	async function create() {
		if (creating) return;
		creating = true;
		const result = await client.createElixir('Untitled elixir');
		creating = false;
		if (!result.ok) {
			toasts.show('error', result.error.message);
			return;
		}
		openElixirTab({ id: result.data.id, name: result.data.name });
		onclose();
	}

	async function startRename(elixir: PickerSummary) {
		renaming = elixir.id;
		renameValue = elixir.name;
		await tick();
		renameInput?.focus();
		renameInput?.select();
	}

	async function commitRename(elixir: PickerSummary) {
		if (renaming !== elixir.id) return;
		const next = renameValue.trim();
		renaming = null;
		if (!next || next === elixir.name) return;
		busyId = elixir.id;
		const result = await client.updateElixir(elixir.id, { expected_revision: elixir.revision, name: next });
		busyId = null;
		if (!result.ok) {
			toasts.show('error', result.error.code === 'revision_conflict' ? 'This elixir changed somewhere else. The list was reloaded.' : result.error.message);
			await load();
			return;
		}
		elixirs = (elixirs ?? []).map((item) =>
			item.id === elixir.id ? { ...item, name: result.data.name, revision: result.data.revision, updated_at: result.data.updated_at } : item
		);
		renameElixirTabs(elixir.id, result.data.name);
		elixirRenamed.set({ id: elixir.id, name: result.data.name, revision: result.data.revision, updated_at: result.data.updated_at });
	}

	function renameKeydown(event: KeyboardEvent, elixir: PickerSummary) {
		if (event.key === 'Enter') {
			event.preventDefault();
			void commitRename(elixir);
		} else if (event.key === 'Escape') {
			event.preventDefault();
			event.stopPropagation();
			renaming = null;
		}
	}

	async function duplicate(elixir: PickerSummary) {
		busyId = elixir.id;
		try {
			const full = await client.getElixir(elixir.id);
			if (!full.ok) {
				toasts.show('error', full.error.message);
				return;
			}
			const name = copyName(full.data.name);
			const created = await client.createElixir(name);
			if (!created.ok) {
				toasts.show('error', created.error.message);
				return;
			}
			const saved = await client.updateElixir(created.data.id, {
				expected_revision: created.data.revision,
				name,
				run_mode: full.data.run_mode,
				definition: full.data.definition
			});
			if (!saved.ok) toasts.show('error', saved.error.message);
			else toasts.show('success', `Duplicated as "${name}"`);
		} finally {
			busyId = null;
			await load();
		}
	}

	async function remove(elixir: PickerSummary) {
		if (deleteBlocked(elixir, openTabs.get(elixir.id))) return;
		const steps = elixir.node_count ?? 0;
		const confirmed = await confirmDialog({
			title: `Delete “${elixir.name}”?`,
			message: `Its ${steps} ${stepWord(steps)} and settings are removed. The pictures and clips it made stay in History.`,
			variant: 'danger',
			confirmLabel: 'Delete elixir'
		});
		if (!confirmed) return;
		busyId = elixir.id;
		const result = await client.deleteElixir(elixir.id);
		busyId = null;
		if (!result.ok) {
			toasts.show('error', result.error.message);
			return;
		}
		closeElixirTabs(elixir.id);
		elixirs = (elixirs ?? []).filter((item) => item.id !== elixir.id);
		toasts.show('success', `Deleted "${elixir.name}"`);
	}

	function when(elixir: PickerSummary, live: boolean): string {
		if (live) return 'now';
		return relativeTime(String(elixir.status_at ?? '')) || 'Not brewed';
	}
</script>

<BaseModal isOpen title="Elixirs" sizeClass="md:w-[min(940px,calc(100vw-64px))] md:max-w-none" on:close={onclose}>
	<div class="flex min-h-0 flex-1 flex-col" data-testid="all-elixirs">
		<div class="tools flex flex-wrap items-center gap-2.5 border-b border-line px-4 py-3">
			<label class="search">
				<Icon name="search" className="h-3.5 w-3.5 flex-shrink-0 text-fg-subtle" />
				<input bind:value={query} type="text" aria-label="Search elixirs" placeholder="Search elixirs" autocomplete="off" />
			</label>
			<div class="seg" role="radiogroup" aria-label="Show">
				{#each MANAGE_FILTERS as option (option.id)}
					<button type="button" role="radio" aria-checked={filter === option.id} onclick={() => (filter = option.id)}>{option.label}</button>
				{/each}
			</div>
			<span class="flex-1"></span>
			<Button variant="primary" size="sm" icon="plus" loading={creating} onclick={create}>New elixir</Button>
		</div>

		<div class="list min-h-0 flex-1 overflow-y-auto" role="table" aria-label="Elixirs">
			{#if loadError}
				<div class="p-6"><LoadErrorState message={loadError} onRetry={load} compact /></div>
			{:else if !elixirs}
				<div class="flex justify-center py-10" role="status" aria-label="Loading elixirs"><Spinner /></div>
			{:else}
				<div class="mrow head" role="row">
					<span class="font-mono text-xs uppercase tracking-wider text-fg-subtle" role="columnheader">Name</span>
					<span class="font-mono text-xs uppercase tracking-wider text-fg-subtle" role="columnheader">Last brew</span>
					<span class="col-outputs font-mono text-xs uppercase tracking-wider text-fg-subtle" role="columnheader">Outputs</span>
					<span class="col-updated font-mono text-xs uppercase tracking-wider text-fg-subtle" role="columnheader">Updated</span>
					<span role="columnheader"><span class="sr-only">Actions</span></span>
				</div>
				{#each rows as elixir (elixir.id)}
					{@const tabStatus = openTabs.get(elixir.id)}
					{@const isOpen = openTabs.has(elixir.id)}
					{@const status = rowStatus(elixir, tabStatus)}
					{@const blocked = deleteBlocked(elixir, tabStatus)}
					<div class="mrow" role="row" data-testid="all-elixirs-row" data-elixir-id={elixir.id}>
						<div class="flex min-w-0 items-center gap-2.5" role="cell">
							<span class="lead"><Icon name="flask" className="h-4 w-4" /></span>
							{#if renaming === elixir.id}
								<input
									bind:this={renameInput}
									bind:value={renameValue}
									class="rename"
									aria-label="Rename {elixir.name}"
									maxlength="120"
									data-keeps-escape
									onkeydown={(event) => renameKeydown(event, elixir)}
									onblur={() => commitRename(elixir)}
								/>
							{:else}
								<span class="min-w-0">
									<span class="block truncate text-sm font-medium text-fg">{elixir.name}</span>
									<span class="block text-xs text-fg-subtle"><span class="font-mono tabular-nums">{elixir.node_count ?? 0}</span> {stepWord(elixir.node_count ?? 0)}</span>
								</span>
							{/if}
						</div>
						<div class="flex min-w-0 items-center gap-1.5 text-sm {TONE_TEXT[status.tone] ?? 'text-fg-subtle'}" role="cell">
							{#if status.tone !== 'neutral'}<StatusDot tone={status.tone} pulse={status.pulse} ring={status.ring} />{/if}
							<span class="truncate">{status.label}</span>
							{#if activeOf(elixir) > 0}
								<Tooltip text={activeText(elixir)}>
									<span class="brews-badge" data-testid="active-brews-badge" role="img" aria-label={activeText(elixir)}>
										<Icon name="flask" className="h-3 w-3" /><span class="font-mono tabular-nums">{activeOf(elixir)}</span>
									</span>
								</Tooltip>
							{/if}
						</div>
						<div class="col-outputs" role="cell">
							{#if (elixir.latest_outputs ?? []).length > 0}
								<ElixirThumbs outputs={elixir.latest_outputs ?? []} />
							{:else}
								<span class="text-sm text-fg-subtle">None yet</span>
							{/if}
						</div>
						<div class="col-updated text-sm text-fg-muted" role="cell">{when(elixir, status.live)}</div>
						<div class="flex items-center justify-end gap-1" role="cell">
							{#if busyId === elixir.id}<Spinner size="sm" />{/if}
							{#if isOpen}
								<button type="button" class="open-chip" onclick={() => open(elixir)}><Icon name="external-link" className="h-3 w-3" />Open</button>
							{:else}
								<Button variant="ghost" size="sm" onclick={() => open(elixir)}>Open</Button>
							{/if}
							<Menu label="Actions for {elixir.name}" tooltip="Actions" width={240} menuAttrs={{ 'data-testid': 'all-elixirs-menu' }}>
								{#snippet children({ close })}
									<button type="button" role="menuitem" class="menu-row" onclick={() => { close(); open(elixir); }}>
										<Icon name="external-link" className="h-4 w-4 flex-shrink-0 text-fg-muted" />
										<span class="text-sm text-fg">{isOpen ? 'Go to its tab' : 'Open in a tab'}</span>
									</button>
									<button type="button" role="menuitem" class="menu-row" onclick={() => { close(); void startRename(elixir); }}>
										<Icon name="pencil" className="h-4 w-4 flex-shrink-0 text-fg-muted" />
										<span class="text-sm text-fg">Rename</span>
									</button>
									<button type="button" role="menuitem" class="menu-row" onclick={() => { close(); void duplicate(elixir); }}>
										<Icon name="copy" className="h-4 w-4 flex-shrink-0 text-fg-muted" />
										<span class="min-w-0">
											<span class="block text-sm text-fg">Duplicate</span>
											<span class="block text-xs text-fg-subtle">Steps and settings, without outputs</span>
										</span>
									</button>
									<div class="my-1 h-px bg-line" role="separator"></div>
									<button type="button" role="menuitem" class="menu-row" disabled={!!blocked} onclick={() => { close(); void remove(elixir); }}>
										<Icon name="trash" className="h-4 w-4 flex-shrink-0 {blocked ? 'text-fg-subtle' : 'text-danger'}" />
										<span class="min-w-0">
											<span class="block text-sm {blocked ? 'text-fg-subtle' : 'text-danger'}">Delete</span>
											{#if blocked}<span class="block text-xs text-fg-subtle">{blocked}</span>{/if}
										</span>
									</button>
								{/snippet}
							</Menu>
						</div>
					</div>
				{:else}
					<EmptyState compact icon="flask" title={query.trim() || filter !== 'all' ? 'No elixir matches' : 'No elixirs yet'} />
				{/each}
			{/if}
		</div>

		<div class="flex items-start gap-2.5 border-t border-line px-4 py-3 text-xs text-fg-subtle">
			<Icon name="info" className="mt-px h-3.5 w-3.5 flex-shrink-0" />
			<span>Deleting an elixir keeps the pictures and clips it made in History. An elixir that is open in a tab closes its tab. Renaming one renames its tab.</span>
		</div>
	</div>
</BaseModal>

<style>
	.search {
		display: flex;
		height: 2rem;
		min-width: 0;
		max-width: 320px;
		flex: 1 1 200px;
		align-items: center;
		gap: 0.5rem;
		padding: 0 0.625rem;
		border: 1px solid rgb(var(--field-border));
		border-radius: 4px;
		background: rgb(var(--field-bg));
	}

	.search:focus-within {
		border-color: rgb(var(--line-hover));
		box-shadow: 0 0 0 1px rgb(var(--accent) / 0.5);
	}

	.search input {
		min-width: 0;
		flex: 1;
		border: 0;
		background: none;
		color: rgb(var(--fg));
		font-size: 0.875rem;
		outline: none;
	}

	.seg {
		display: inline-flex;
		gap: 2px;
		padding: 2px;
		border: 1px solid rgb(var(--line));
		border-radius: 4px;
		background: rgb(var(--surface-2));
	}

	.seg button {
		height: 26px;
		padding: 0 10px;
		border-radius: 4px;
		color: rgb(var(--fg-muted));
		font-size: 12px;
	}

	.seg button[aria-checked='true'] {
		background: rgb(var(--surface-3));
		box-shadow: inset 0 0 0 1px rgb(var(--line-strong));
		color: rgb(var(--fg));
		font-weight: 600;
	}

	.mrow {
		display: grid;
		min-height: 56px;
		grid-template-columns: minmax(0, 1.5fr) 232px 104px 96px 136px;
		align-items: center;
		gap: 12px;
		padding: 8px 16px;
		border-bottom: 1px solid rgb(var(--line));
	}

	.mrow.head {
		position: sticky;
		top: 0;
		z-index: 1;
		min-height: 34px;
		padding-top: 6px;
		padding-bottom: 6px;
		background: rgb(var(--surface-1));
	}

	.mrow:not(.head):hover {
		background: rgb(var(--surface-2) / 0.6);
	}

	.lead {
		display: grid;
		width: 30px;
		height: 30px;
		flex-shrink: 0;
		place-items: center;
		border-radius: 4px;
		background: rgb(var(--surface-3));
		color: rgb(var(--fg-muted));
	}

	.rename {
		height: 32px;
		width: 100%;
		padding: 0 10px;
		border: 1px solid rgb(var(--line-hover));
		border-radius: 4px;
		background: rgb(var(--field-bg));
		box-shadow: 0 0 0 1px rgb(var(--accent) / 0.5);
		color: rgb(var(--fg));
		font-size: 13px;
		font-weight: 600;
		outline: none;
	}

	.brews-badge {
		display: inline-flex;
		height: 20px;
		flex-shrink: 0;
		align-items: center;
		gap: 4px;
		padding: 0 6px;
		border: 1px solid rgb(var(--signal) / 0.4);
		border-radius: 4px;
		color: rgb(var(--signal));
		font-size: 12px;
	}

	.open-chip {
		display: inline-flex;
		height: 24px;
		align-items: center;
		gap: 4px;
		padding: 0 8px;
		border-radius: 4px;
		background: rgb(var(--surface-3));
		color: rgb(var(--fg-muted));
		font-size: 12px;
	}

	.open-chip:hover {
		color: rgb(var(--fg));
	}

	.menu-row {
		display: flex;
		width: 100%;
		align-items: flex-start;
		gap: 0.625rem;
		border-radius: 4px;
		padding: 0.5rem 0.625rem;
		text-align: left;
	}

	.menu-row:hover:not(:disabled),
	.menu-row:focus-visible {
		background: rgb(var(--surface-3));
		outline: none;
	}

	.menu-row:disabled {
		cursor: not-allowed;
	}

	@media (max-width: 767px) {
		.mrow {
			grid-template-columns: minmax(0, 1fr) auto;
		}

		.mrow > :nth-child(2),
		.col-outputs,
		.col-updated {
			display: none;
		}

		.mrow.head {
			display: none;
		}
	}
</style>
