<script lang="ts">
	import { onMount, tick } from 'svelte';
	import type { Session, SessionVersionSummary } from '$lib/types/api';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
	import { Button, IconButton, Menu, Spinner } from '$lib/components/ui';
	import type { MenuItem } from '$lib/components/ui/Menu.svelte';
	import DropdownShell from '$lib/components/drawer/DropdownShell.svelte';
	import DrawerHeader from '$lib/components/drawer/DrawerHeader.svelte';
	import { timeAgo } from '$lib/utils/relativeTime';
	import {
		filterSessions,
		newestFirst,
		splitPinned,
		versionHeadline,
		versionChanges,
		discardConsequence,
		fullDateTime,
		type DiscardAction,
		type FieldLabels
	} from '$lib/session/sessionDrawerModel';
	import type { SessionPanelActions, SessionPanelState } from '$lib/session/sessionDialogs';

	const VERSION_PAGE = 20;
	const AUTOSAVE_OPTIONS = [
		{ id: 'off', label: 'Off', short: 'off' },
		{ id: '5000', label: 'Every 5s', short: '5s' },
		{ id: '10000', label: 'Every 10s', short: '10s' },
		{ id: '30000', label: 'Every 30s', short: '30s' },
		{ id: '60000', label: 'Every minute', short: '1m' }
	];

	let {
		fieldLabels = {},
		session,
		actions,
		anchor,
		fullscreen = false,
		onClose
	}: {
		fieldLabels?: FieldLabels;
		session: SessionPanelState;
		actions: SessionPanelActions;
		anchor: HTMLElement | undefined;
		fullscreen?: boolean;
		onClose: () => void;
	} = $props();

	let {
		sessions,
		currentSession,
		selectedSessionId,
		isSessionLoading: loading,
		hasUnsavedChanges: dirty,
		autoSaveEnabled,
		autoSaveInterval,
		historySessionId,
		historyVersions,
		isHistoryLoading: historyLoading,
		historyError,
		isRestoringVersion: restoringVersion
	} = $derived(session);

	let root = $state<HTMLElement>();
	let searchEl = $state<HTMLInputElement>();
	let query = $state('');
	let view = $state<'list' | 'versions'>('list');
	let versionsFor = $state('');
	let versionsShown = $state(VERSION_PAGE);
	let pendingDiscard = $state<{ action: DiscardAction; run: () => void | Promise<void> } | null>(null);

	let filtered = $derived(filterSessions(newestFirst(sessions), query));
	let split = $derived(splitPinned(filtered));
	let versionsSession = $derived(sessions.find((s) => s.id === versionsFor) ?? null);
	let versionsReady = $derived(historySessionId === versionsFor);
	let versionsLeft = $derived(Math.max(0, historyVersions.length - versionsShown));
	let autosaveChoice = $derived(autoSaveEnabled ? String(autoSaveInterval) : 'off');
	let autosaveShort = $derived(
		AUTOSAVE_OPTIONS.find((option) => option.id === autosaveChoice)?.short ?? `${autoSaveInterval / 1000}s`
	);
	let searchPlaceholder = $derived(`Search ${sessions.length} ${sessions.length === 1 ? 'session' : 'sessions'}`);

	onMount(() => {
		return () => actions.closeHistory();
	});

	function guarded(action: DiscardAction, run: () => void | Promise<void>) {
		if (dirty) pendingDiscard = { action, run };
		else run();
	}

	function confirmDiscard() {
		const pending = pendingDiscard;
		pendingDiscard = null;
		pending?.run();
	}

	function loadSession(sessionId: string) {
		if (sessionId === selectedSessionId) {
			onClose();
			return;
		}
		guarded({ kind: 'load' }, async () => {
			await actions.select(sessionId);
			onClose();
		});
	}

	function restore(versionNumber: number) {
		const sessionId = versionsFor;
		guarded({ kind: 'restore', versionNumber }, () => actions.restoreVersion(sessionId, versionNumber));
	}

	async function reload() {
		onClose();
		await tick();
		actions.reload();
	}

	function startNew() {
		guarded({ kind: 'new' }, () => {
			actions.startNew();
			onClose();
		});
	}

	async function openVersions(sessionId: string) {
		versionsFor = sessionId;
		versionsShown = VERSION_PAGE;
		view = 'versions';
		if (historySessionId !== sessionId) actions.openHistory(sessionId);
		await tick();
		root?.querySelector<HTMLElement>('[data-versions-back] button')?.focus();
	}

	async function backToList() {
		const from = versionsFor;
		view = 'list';
		actions.closeHistory();
		await tick();
		const row = Array.from(root?.querySelectorAll<HTMLElement>('[data-session-row]') ?? []).find(
			(el) => el.dataset.sessionRow === from
		);
		(row ?? searchEl ?? root)?.focus();
	}

	function handleEscape(): boolean {
		if (query) {
			query = '';
			return true;
		}
		if (view === 'versions') {
			void backToList();
			return true;
		}
		return false;
	}

	function rowActions(item: Session): MenuItem[] {
		const items: MenuItem[] = [
			{ id: 'pin', label: item.pinned ? 'Unpin' : 'Pin', icon: 'pin' },
			{ id: 'versions', label: 'Version history', icon: 'panel-history' }
		];
		if (actions.openScript) items.push({ id: 'script', label: 'Use from a script…', icon: 'code' });
		if (item.id === currentSession?.id) {
			items.push(
				{ id: 'divider', label: '', separator: true },
				{ id: 'rename', label: 'Rename…', icon: 'edit' },
				{ id: 'delete', label: 'Delete…', icon: 'trash', danger: true }
			);
		}
		return items;
	}

	function runRowAction(item: Session, id: string) {
		if (id === 'pin') actions.togglePin(item.id);
		else if (id === 'versions') void openVersions(item.id);
		else if (id === 'script') actions.openScript?.(item.id);
		else if (id === 'rename') actions.rename();
		else if (id === 'delete') actions.remove();
	}

	function chooseAutosave(id: string) {
		if (id === 'off') {
			if (autoSaveEnabled) actions.toggleAutosave();
			return;
		}
		const interval = Number(id);
		if (interval !== autoSaveInterval) actions.setAutosaveInterval(interval);
		if (!autoSaveEnabled) actions.toggleAutosave();
	}

	function versionTip(version: SessionVersionSummary): string {
		const changes = versionChanges(version, fieldLabels);
		const when = fullDateTime(version.created_at);
		return changes ? `changed ${changes.all.join(', ')} · ${when}` : when;
	}

	function matchParts(name: string): [string, string, string] | null {
		const needle = query.trim().toLowerCase();
		if (!needle) return null;
		const at = name.toLowerCase().indexOf(needle);
		if (at < 0) return null;
		return [name.slice(0, at), name.slice(at, at + needle.length), name.slice(at + needle.length)];
	}

	function handleKeydown(event: KeyboardEvent) {
		const target = event.target as HTMLElement;
		if (!root?.contains(target)) return;
		const rowId = target.dataset?.sessionRow;
		if (view === 'list' && rowId && event.key === 'ArrowRight') {
			event.preventDefault();
			void openVersions(rowId);
		} else if (view === 'versions' && event.key === 'ArrowLeft' && target !== searchEl) {
			event.preventDefault();
			void backToList();
		} else if (view === 'list' && event.key === 'Enter' && target === searchEl && filtered.length > 0) {
			event.preventDefault();
			loadSession(filtered[0].id);
		}
	}
</script>

<svelte:window onkeydown={handleKeydown} />

<DropdownShell label="Sessions" {anchor} {fullscreen} {onClose} onEscape={handleEscape} bind:root bind:searchEl>
	{#if fullscreen}
		<DrawerHeader title="Sessions" closeLabel="Close sessions" {onClose} />
	{/if}

	{#if view === 'versions'}
		<div class="flex h-11 flex-shrink-0 items-center gap-1.5 border-b border-line pl-1.5 pr-3.5">
			<span class="flex" data-versions-back>
				<Tooltip text="Back to sessions" kbd="Esc" position="bottom" delay={150}>
					<IconButton icon="chevron-left" label="Back to sessions" size="sm" onclick={backToList} />
				</Tooltip>
			</span>
			<span class="min-w-0 flex-1 truncate text-base font-semibold text-fg">{versionsSession?.name ?? ''}</span>
			{#if versionsReady && !historyLoading}
				<span class="flex-shrink-0 font-mono text-sm tabular-nums text-fg-subtle">{historyVersions.length} versions</span>
			{/if}
		</div>
		<div class="min-h-0 flex-1 overflow-y-auto overscroll-contain py-1" data-testid="session-versions-scroll">
			{#if historyLoading || !versionsReady}
				<div class="flex justify-center py-6"><Spinner size="sm" /></div>
			{:else if historyError}
				<p class="px-4 py-4 text-sm text-danger">{historyError}</p>
			{:else if historyVersions.length === 0}
				<p class="px-4 py-5 text-sm text-fg-muted">No versions yet.</p>
			{:else}
				<ul class="m-0 list-none p-0">
					{#each historyVersions.slice(0, versionsShown) as version, i (version.version_number)}
						<li class="mx-1.5">{@render versionRow(version, i === 0)}</li>
					{/each}
				</ul>
				{#if versionsLeft > 0}
					<div class="px-3 pb-1 pt-2">
						<Button variant="secondary" size="sm" onclick={() => (versionsShown += VERSION_PAGE)}>
							Show {Math.min(VERSION_PAGE, versionsLeft)} older
						</Button>
					</div>
				{/if}
			{/if}
		</div>
	{:else if sessions.length === 0 && !loading}
		<div class="flex flex-col items-start gap-1 px-4 py-5 {fullscreen ? 'flex-1' : ''}">
			<p class="m-0 text-base font-semibold text-fg">No saved sessions for this preset yet</p>
			<p class="m-0 text-sm text-fg-muted">Save this draft to come back to it, or to keep versions of it.</p>
			<Button variant="primary" size="sm" class="mt-3" onclick={actions.saveAs}>Save as new session</Button>
		</div>
		{@render footer()}
	{:else}
		<div class="flex h-11 flex-shrink-0 items-center gap-2.5 border-b border-line pl-3.5 pr-2">
			<Icon name="search" className="h-4 w-4 flex-shrink-0 text-fg-subtle" />
			<input
				bind:this={searchEl}
				bind:value={query}
				type="text"
				class="min-w-0 flex-1 border-0 bg-transparent p-0 text-base text-fg outline-none placeholder:text-fg-subtle focus:ring-0"
				placeholder={searchPlaceholder}
				aria-label="Search sessions"
				data-autofocus={fullscreen ? undefined : ''}
			/>
			{#if query}
				<span class="flex-shrink-0 font-mono text-sm tabular-nums text-fg-subtle">{filtered.length} of {sessions.length}</span>
				<Tooltip text="Clear search" position="bottom" delay={150}>
					<IconButton icon="close" label="Clear search" size="xs" onclick={() => { query = ''; searchEl?.focus(); }} />
				</Tooltip>
			{/if}
		</div>

		{#if dirty && currentSession}
			<div class="flex h-11 flex-shrink-0 items-center gap-2 border-b border-line pl-3.5 pr-2" data-testid="session-reload-row">
				<span class="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-warning-solid" aria-hidden="true"></span>
				<span class="min-w-0 flex-1 truncate text-sm text-fg-muted">Unsaved changes</span>
				<Button variant="secondary" size="sm" icon="undo" onclick={reload}>Reload last snapshot</Button>
			</div>
		{/if}

		<div class="min-h-0 flex-1 overflow-y-auto overscroll-contain py-1" data-testid="session-list-scroll">
			{#if loading}
				<div class="flex justify-center py-6"><Spinner size="sm" /></div>
			{:else if filtered.length === 0}
				<p class="px-4 py-5 text-sm text-fg-muted">No sessions match “{query.trim()}”</p>
			{:else}
				{#if split.pinned.length > 0}
					<div class="px-3.5 pb-0.5 pt-1.5 text-sm text-fg-subtle" data-testid="pinned-head">Pinned</div>
					<ul class="m-0 list-none p-0" data-testid="pinned-list">
						{#each split.pinned as item (item.id)}
							{@render row(item)}
						{/each}
					</ul>
					{#if split.rest.length > 0}
						<div class="mx-3 my-1.5 h-px bg-line" role="separator"></div>
					{/if}
				{/if}
				<ul class="m-0 list-none p-0">
					{#each split.rest as item (item.id)}
						{@render row(item)}
					{/each}
				</ul>
			{/if}
		</div>
		{@render footer()}
	{/if}
</DropdownShell>

{#snippet row(item: Session)}
	{@const current = item.id === currentSession?.id}
	{@const parts = matchParts(item.name)}
	<li class="session-row relative mx-1.5 flex items-center rounded" class:is-current={current} data-session-id={item.id}>
		<button
			type="button"
			class="row-main flex min-w-0 flex-1 items-center gap-2 rounded pl-2.5 pr-1 text-left {fullscreen ? 'h-11' : 'h-9'}"
			data-nav
			data-session-row={item.id}
			aria-current={current ? 'true' : undefined}
			onclick={() => loadSession(item.id)}
		>
			<Tooltip text={item.name} truncateOnly position="bottom" delay={500} wrapperClass="flex min-w-0 flex-1">
				<span class="min-w-0 flex-1 truncate text-base text-fg">{#if parts}{parts[0]}<b class="font-semibold">{parts[1]}</b>{parts[2]}{:else}{item.name}{/if}</span>
			</Tooltip>
			{#if current && dirty}
				<span class="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-warning-solid" aria-hidden="true"></span>
				<span class="sr-only">Unsaved changes</span>
			{/if}
			<Tooltip text={fullDateTime(item.updated_at)} position="bottom" delay={400} wrapperClass="flex flex-shrink-0">
				<span class="font-mono text-sm tabular-nums text-fg-subtle">{timeAgo(item.updated_at)}</span>
			</Tooltip>
		</button>
		<span class="row-more flex flex-shrink-0 pr-1" class:always={fullscreen}>
			<Menu
				label={`More actions for ${item.name}`}
				menuLabel={`Actions for ${item.name}`}
				tooltip="More actions"
				items={rowActions(item)}
				width={200}
				onselect={(id) => runRowAction(item, id)}
			/>
		</span>
	</li>
{/snippet}

{#snippet versionRow(version: SessionVersionSummary, latest: boolean)}
	<Tooltip text={versionTip(version)} position="bottom" delay={300} wrapperClass="flex w-full">
		<button
			type="button"
			class="version-row flex w-full min-w-0 items-center gap-2.5 rounded px-2.5 text-left hover:bg-surface-2 {fullscreen ? 'h-11' : 'h-9'}"
			data-nav
			data-version-row={version.version_number}
			disabled={restoringVersion}
			onclick={() => restore(version.version_number)}
		>
			<span class="w-9 flex-shrink-0 font-mono text-sm tabular-nums text-fg-muted">v{version.version_number}</span>
			<span class="min-w-0 flex-1 truncate text-base text-fg">{versionHeadline(version)}</span>
			{#if latest}
				<span class="flex-shrink-0 rounded-sm border border-signal/35 bg-signal/10 px-1.5 font-mono text-sm text-signal">Latest</span>
			{/if}
			<span class="flex-shrink-0 font-mono text-sm tabular-nums text-fg-subtle">{timeAgo(version.created_at)}</span>
		</button>
	</Tooltip>
{/snippet}

{#snippet footer()}
	<footer class="flex min-h-[48px] flex-shrink-0 items-center gap-1.5 border-t border-line px-2.5 py-2">
		<Button variant="secondary" size="sm" icon="plus" onclick={startNew}>New session</Button>
		{#if sessions.length > 0}
			<Button variant="secondary" size="sm" onclick={actions.saveAs}>Save as new</Button>
		{/if}
		<span class="flex-1"></span>
		{#if currentSession}
			<Menu label="Auto-save" menuLabel="Auto-save interval" width={180} preferred="up">
				{#snippet trigger({ open, toggle })}
					<button
						type="button"
						class="inline-flex h-8 items-center gap-1.5 rounded px-2 text-sm text-fg-muted transition-colors hover:bg-surface-3/50 hover:text-fg"
						aria-haspopup="menu"
						aria-expanded={open}
						onclick={toggle}
					>
						Auto-save <span class="font-mono tabular-nums text-fg">{autosaveShort}</span>
						<Icon name="chevron-down" className="h-3.5 w-3.5 {open ? 'rotate-180' : ''}" />
					</button>
				{/snippet}
				{#snippet children({ close })}
					{#each AUTOSAVE_OPTIONS as option (option.id)}
						<button
							type="button"
							role="menuitemradio"
							aria-checked={autosaveChoice === option.id}
							class="flex h-9 w-full items-center gap-2.5 rounded px-2.5 text-left text-base text-fg hover:bg-surface-3 focus-visible:bg-surface-3 focus-visible:outline-none"
							onclick={() => {
								close(true);
								chooseAutosave(option.id);
							}}
						>
							<span class="flex w-4 flex-shrink-0 justify-center text-signal">
								{#if autosaveChoice === option.id}<Icon name="check" className="h-4 w-4" />{/if}
							</span>
							<span>{option.label}</span>
						</button>
					{/each}
				{/snippet}
			</Menu>
		{:else}
			<Tooltip text="Save the draft as a session to turn on auto-save" position="top" delay={150}>
				<button type="button" class="inline-flex h-8 items-center gap-1.5 rounded px-2 text-sm text-fg-muted opacity-50" disabled>
					Auto-save <span class="font-mono">off</span>
				</button>
			</Tooltip>
		{/if}
	</footer>
{/snippet}

{#if pendingDiscard}
	<ConfirmModal
		isOpen
		title="Discard unsaved changes?"
		message={`"${currentSession?.name ?? 'This draft'}" has changes that are not saved. ${discardConsequence(pendingDiscard.action)}`}
		variant="warning"
		on:confirm={confirmDiscard}
		on:cancel={() => (pendingDiscard = null)}
	/>
{/if}

<style>
	.session-row:hover {
		background: rgb(var(--surface-2));
	}

	.session-row.is-current {
		background: rgb(var(--signal) / 0.1);
		box-shadow: inset 2px 0 0 rgb(var(--signal));
	}

	.session-row.is-current:hover {
		background: rgb(var(--signal) / 0.14);
	}

	:global(:where(html:not([data-input='pointer']))) .row-main:focus-visible,
	:global(:where(html:not([data-input='pointer']))) .version-row:focus-visible {
		outline: none;
		background: rgb(var(--surface-2));
		box-shadow: inset 0 0 0 1px rgb(var(--signal) / 0.6);
	}

	.row-more {
		opacity: 0;
		transition: opacity 100ms;
	}

	.session-row:hover .row-more,
	.session-row:focus-within .row-more,
	.row-more:has(:global([aria-expanded='true'])),
	.row-more.always {
		opacity: 1;
	}
</style>
