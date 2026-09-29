<script lang="ts">
	import { onMount, tick, untrack } from 'svelte';
	import type { Session, SessionVersionSummary } from '$lib/types/api';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
	import { Button, IconButton, Spinner, Switch, SegmentedControl } from '$lib/components/ui';
	import portal from '$lib/actions/portal';
	import overlayLayer from '$lib/actions/overlayLayer';
	import focusTrap from '$lib/actions/focusTrap';
	import { storage } from '$lib/utils/storage';
	import { timeAgo } from '$lib/utils/relativeTime';
	import {
		filterSessions,
		groupSessions,
		groupVersionsByDay,
		versionHeadline,
		versionChanges,
		discardConsequence,
		fullDateTime,
		type DiscardAction,
		type FieldLabels
	} from '$lib/session/sessionDrawerModel';

	const KEEP_OPEN_KEY = 'sessions-drawer-keep-open';
	const KEEP_OPEN_MIN_WIDTH = 1200;
	const INLINE_VERSIONS = 5;
	const VERSION_PAGE = 20;

	let {
		fieldLabels = {},
		sessions,
		currentSession,
		selectedSessionId,
		loading,
		saving,
		dirty,
		autoSaveEnabled,
		autoSaveInterval,
		historySessionId,
		historyVersions,
		historyLoading,
		historyError,
		restoringVersion,
		onSelect,
		onSave,
		onSaveAs,
		onNew,
		onRename,
		onDelete,
		onToggleAutoSave,
		onIntervalChange,
		onOpenHistory,
		onCloseHistory,
		onRestoreVersion,
		onClose
	}: {
		fieldLabels?: FieldLabels;
		sessions: Session[];
		currentSession: Session | null;
		selectedSessionId: string;
		loading: boolean;
		saving: boolean;
		dirty: boolean;
		autoSaveEnabled: boolean;
		autoSaveInterval: number;
		historySessionId: string | null;
		historyVersions: SessionVersionSummary[];
		historyLoading: boolean;
		historyError: string | null;
		restoringVersion: boolean;
		onSelect: (sessionId: string) => void;
		onSave: () => void;
		onSaveAs: () => void;
		onNew: () => void;
		onRename: () => void;
		onDelete: () => void;
		onToggleAutoSave: () => void;
		onIntervalChange: (interval: number) => void;
		onOpenHistory: (sessionId: string) => void;
		onCloseHistory: () => void;
		onRestoreVersion: (sessionId: string, versionNumber: number) => void;
		onClose: () => void;
	} = $props();

	const intervals = [
		{ id: '5000', label: '5s' },
		{ id: '10000', label: '10s' },
		{ id: '30000', label: '30s' },
		{ id: '60000', label: '1m' }
	];

	let root = $state<HTMLElement>();
	let searchEl = $state<HTMLInputElement>();
	let query = $state('');
	let view = $state<'list' | 'versions'>('list');
	let versionsShown = $state(VERSION_PAGE);
	let keepOpenPref = $state(false);
	let viewportWidth = $state(typeof window === 'undefined' ? 1440 : window.innerWidth);
	let pendingDiscard = $state<{ action: DiscardAction; run: () => void | Promise<void> } | null>(null);
	let historyOpen = $state(false);
	let autoOpenedFor = $state('');

	let keepOpen = $derived(keepOpenPref && viewportWidth >= KEEP_OPEN_MIN_WIDTH);
	let desktopSearchFocus = $derived(viewportWidth > 640);
	let filtered = $derived(filterSessions(sessions, query, currentSession?.id ?? ''));
	let groups = $derived(groupSessions(filtered));
	let currentExpanded = $derived(!!currentSession && historySessionId === currentSession.id);
	let versionsSession = $derived(sessions.find((s) => s.id === historySessionId) ?? null);
	let versionGroups = $derived(groupVersionsByDay(historyVersions.slice(0, versionsShown)));
	let versionsLeft = $derived(Math.max(0, historyVersions.length - versionsShown));
	let stateText = $derived(
		saving
			? 'Saving…'
			: !currentSession
				? 'Unsaved draft'
				: dirty
					? 'Unsaved changes'
					: 'Saved'
	);
	let autoSaveText = $derived(
		!currentSession
			? 'Save the draft to enable auto-save'
			: autoSaveEnabled
				? `Auto-saving every ${autoSaveInterval / 1000}s`
				: 'Auto-save is off'
	);

	onMount(() => {
		try {
			keepOpenPref = storage.get(KEEP_OPEN_KEY) === '1';
		} catch {
			keepOpenPref = false;
		}
		return () => onCloseHistory();
	});

	$effect(() => {
		const id = currentSession?.id ?? '';
		if (!id || id === autoOpenedFor) return;
		untrack(() => {
			autoOpenedFor = id;
			onOpenHistory(id);
		});
	});

	function setKeepOpen(next: boolean) {
		keepOpenPref = next;
		try {
			storage.set(KEEP_OPEN_KEY, next ? '1' : '0');
		} catch {
			return;
		}
	}

	function guarded(action: DiscardAction, run: () => void | Promise<void>) {
		if (dirty) pendingDiscard = { action, run };
		else run();
	}

	function confirmDiscard() {
		const pending = pendingDiscard;
		pendingDiscard = null;
		pending?.run();
	}

	function afterLoad() {
		if (!keepOpen) onClose();
	}

	function loadSession(sessionId: string) {
		if (sessionId === selectedSessionId) {
			afterLoad();
			return;
		}
		guarded({ kind: 'load' }, async () => {
			await onSelect(sessionId);
			afterLoad();
		});
	}

	function restore(sessionId: string, versionNumber: number) {
		guarded({ kind: 'restore', versionNumber }, async () => {
			await onRestoreVersion(sessionId, versionNumber);
			view = 'list';
			afterLoad();
		});
	}

	function startNew() {
		guarded({ kind: 'new' }, () => {
			onNew();
			afterLoad();
		});
	}

	function toggleCurrentHistory(sessionId: string) {
		historyOpen = !historyOpen;
		if (historyOpen && historySessionId !== sessionId) onOpenHistory(sessionId);
	}

	function toggleVersions(sessionId: string) {
		if (historySessionId === sessionId) onCloseHistory();
		else onOpenHistory(sessionId);
	}

	async function showAllVersions(sessionId: string) {
		if (historySessionId !== sessionId) onOpenHistory(sessionId);
		versionsShown = VERSION_PAGE;
		view = 'versions';
		await tick();
		root?.querySelector<HTMLElement>('header button')?.focus();
	}

	async function backToList() {
		view = 'list';
		await tick();
		const trigger = root?.querySelector<HTMLElement>('[data-nav]:not([data-session-row])');
		(trigger ?? root)?.focus();
	}

	function navItems(): HTMLElement[] {
		if (!root) return [];
		return Array.from(root.querySelectorAll<HTMLElement>('[data-nav]')).filter(
			(el) => !(el as HTMLButtonElement).disabled
		);
	}

	function isEditable(target: EventTarget | null): boolean {
		const el = target as HTMLElement | null;
		return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable);
	}

	function handleKeydown(event: KeyboardEvent) {
		const target = event.target as HTMLElement;
		const inside = !!root && root.contains(target);
		if (!inside && !(target === document.body && !keepOpen)) return;

		if (event.key === 'Escape') {
			event.preventDefault();
			event.stopPropagation();
			if (query) query = '';
			else if (view === 'versions') backToList();
			else onClose();
			return;
		}

		if (event.key === '/' && !isEditable(target)) {
			event.preventDefault();
			searchEl?.focus();
			return;
		}

		if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
			const items = navItems();
			if (items.length === 0) return;
			event.preventDefault();
			const index = items.indexOf(target);
			if (event.key === 'ArrowDown') items[index < 0 ? 0 : Math.min(items.length - 1, index + 1)].focus();
			else if (index > 0) items[index - 1].focus();
			else if (index === 0) searchEl?.focus();
			return;
		}

		const rowId = target.dataset?.sessionRow;
		if (rowId && event.key === 'ArrowRight' && historySessionId !== rowId) {
			event.preventDefault();
			onOpenHistory(rowId);
		} else if (rowId && event.key === 'ArrowLeft' && historySessionId === rowId) {
			event.preventDefault();
			onCloseHistory();
		}

		if (event.key === 'Enter' && target === searchEl && filtered.length > 0) {
			event.preventDefault();
			loadSession(filtered[0].id);
		}
	}
</script>

<svelte:window onresize={() => (viewportWidth = window.innerWidth)} onkeydown={handleKeydown} />

{#if !keepOpen}
	<div class="scrim" use:portal use:overlayLayer role="presentation" onclick={onClose}></div>
{/if}

<div
	class="sessions-drawer flex flex-col overflow-hidden border border-line-strong bg-surface-1 shadow-overlay"
	role="dialog"
	tabindex="-1"
	aria-label="Sessions"
	aria-modal={keepOpen ? undefined : 'true'}
	bind:this={root}
	use:portal
	use:overlayLayer
	use:focusTrap
>
	<header class="flex h-[52px] flex-shrink-0 items-center gap-2 border-b border-line pl-4 pr-2">
		{#if view === 'versions'}
			<Button variant="ghost" size="sm" icon="chevron-left" onclick={backToList}>
				Back
			</Button>
			<h2 class="min-w-0 flex-1 truncate text-md font-semibold text-fg">Versions</h2>
		{:else}
			<h2 class="text-md font-semibold text-fg">Sessions</h2>
			<span class="font-mono text-xs tabular-nums text-fg-subtle">{sessions.length}</span>
			<span class="flex-1"></span>
			<label class="keep-open flex items-center gap-2 text-xs text-fg-muted">
				<span>Keep open</span>
				<Switch size="sm" label="Keep open" checked={keepOpen} onchange={setKeepOpen} />
			</label>
		{/if}
		<Tooltip text="Close" kbd="Esc" position="bottom" delay={150}>
			<IconButton icon="close" label="Close sessions" size="sm" onclick={onClose} />
		</Tooltip>
	</header>

	{#if view === 'versions'}
		<div class="flex-shrink-0 border-b border-line px-4 py-3">
			<div class="truncate text-md font-semibold text-fg">{versionsSession?.name ?? currentSession?.name ?? ''}</div>
			<div class="font-mono text-xs tabular-nums text-fg-subtle">{historyVersions.length} versions</div>
		</div>
		<div class="scroll min-h-0 flex-1 overflow-y-auto pb-2" data-testid="session-versions-scroll">
			{#if historyLoading}
				<div class="flex justify-center py-6"><Spinner size="sm" /></div>
			{:else if historyError}
				<p class="px-4 py-3 text-xs text-danger">{historyError}</p>
			{:else}
				{#each versionGroups as group (group.label)}
					<div class="group-head">{group.label}<span>{group.items.length}</span></div>
					{#each group.items as version (version.version_number)}
						{@render versionRow(versionsSession?.id ?? historySessionId ?? '', version, version.version_number === historyVersions[0]?.version_number)}
					{/each}
				{/each}
				{#if versionsLeft > 0}
					<div class="px-4 pt-2">
						<Button variant="secondary" size="xs" onclick={() => (versionsShown += VERSION_PAGE)}>
							Show {Math.min(VERSION_PAGE, versionsLeft)} older · {versionsLeft} left
						</Button>
					</div>
				{/if}
			{/if}
		</div>
	{:else}
		<div class="flex-shrink-0 px-3 pb-2 pt-3">
			<div class="search flex h-9 items-center gap-2 rounded border border-field-border bg-field-bg px-2.5 text-fg-subtle">
				<Icon name="search" className="h-4 w-4 flex-shrink-0" />
				<input
					bind:this={searchEl}
					bind:value={query}
					type="text"
					class="min-w-0 flex-1 border-0 bg-transparent p-0 text-sm text-fg outline-none placeholder:text-fg-subtle focus:ring-0"
					placeholder="Search sessions in this preset"
					aria-label="Search sessions"
					data-autofocus={desktopSearchFocus ? '' : undefined}
				/>
				{#if query}
					<Tooltip text="Clear search" position="bottom" delay={150}>
						<IconButton icon="close" label="Clear search" size="xs" onclick={() => { query = ''; searchEl?.focus(); }} />
					</Tooltip>
				{:else}
					<kbd class="rounded-sm border border-line-strong bg-surface-1 px-1.5 font-mono text-xs text-fg-subtle">/</kbd>
				{/if}
			</div>
		</div>

		<section class="mx-3 mb-2 flex-shrink-0 rounded-lg border border-line-strong bg-surface-2/60" aria-label="Current session">
			<div class="px-3 pt-3">
				<div class="flex items-center gap-2">
					<span
						class="h-2 w-2 flex-shrink-0 rounded-full {saving ? 'bg-signal-solid' : dirty || !currentSession ? 'bg-warning-solid' : 'bg-success-solid'}"
						aria-hidden="true"
					></span>
					<span class="min-w-0 flex-1 truncate text-md font-semibold text-fg" data-testid="current-session-name">{currentSession?.name ?? 'No saved session'}</span>
					{#if currentSession}
						<Tooltip text="Rename session" position="bottom" delay={150}>
							<IconButton icon="edit" label="Rename session" size="sm" onclick={onRename} />
						</Tooltip>
						<Tooltip text="Delete session" position="bottom" delay={150}>
							<IconButton icon="trash" label="Delete session" size="sm" onclick={onDelete} />
						</Tooltip>
					{/if}
				</div>
				<div class="mt-0.5 text-xs {dirty || !currentSession ? 'text-warning' : 'text-fg-subtle'}">
					{stateText}
					<span class="text-fg-subtle"> · {autoSaveText}</span>
				</div>
				<div class="my-2.5 flex gap-1.5">
					{#if currentSession}
						<Button variant="primary" size="sm" class="flex-1" disabled={!dirty || saving} loading={saving} onclick={onSave}>Save</Button>
						<Button variant="secondary" size="sm" class="flex-1" onclick={onSaveAs}>Save as new</Button>
					{:else}
						<Button variant="primary" size="sm" class="flex-1" onclick={onSaveAs}>Save as new</Button>
					{/if}
					<Button variant="secondary" size="sm" class="flex-1" icon="plus" onclick={startNew}>New</Button>
				</div>
			</div>
			{#if currentSession}
				<div class="border-t border-line px-1.5 pb-1.5 pt-1">
					<button
						type="button"
						class="hist-toggle flex h-8 w-full items-center gap-2 rounded px-1.5 text-left text-xs text-fg-muted hover:bg-surface-3 hover:text-fg"
						aria-expanded={historyOpen}
						aria-controls="session-history-panel"
						onclick={() => toggleCurrentHistory(currentSession.id)}
					>
						<Icon name="chevron-right" className="tw h-3.5 w-3.5 {historyOpen ? 'rotate-90' : ''}" />
						<span>History</span>
						{#if currentExpanded && !historyLoading && !historyError && historyVersions.length > 0}
							<span class="font-mono tabular-nums text-fg-subtle">{historyVersions.length} versions</span>
							<span class="font-mono tabular-nums text-fg-subtle">latest {timeAgo(historyVersions[0].created_at)}</span>
						{/if}
					</button>
					{#if historyOpen}
						<div id="session-history-panel">
							{#if currentExpanded}
								{@render versionList(currentSession.id)}
							{:else}
								<div class="flex justify-center py-3"><Spinner size="sm" /></div>
							{/if}
						</div>
					{/if}
				</div>
			{/if}
		</section>

		<div class="scroll min-h-0 flex-1 overflow-y-auto pb-2" data-testid="session-list-scroll">
			{#if loading}
				<div class="flex justify-center py-6"><Spinner size="sm" /></div>
			{:else if filtered.length === 0}
				<div class="px-6 py-8 text-center text-sm text-fg-muted">
					<b class="mb-1 block font-semibold text-fg">{query ? `No sessions match “${query}”` : 'No other sessions'}</b>
					{query ? 'Only this preset’s sessions are searched.' : 'Sessions you save for this preset appear here.'}
				</div>
			{:else}
				{#each groups as group (group.label)}
					<div class="group-head">{group.label}<span>{group.items.length}</span></div>
					<ul class="m-0 list-none p-0">
						{#each group.items as session (session.id)}
							{@const expanded = historySessionId === session.id}
							<li class="item mx-2 rounded" data-session-id={session.id}>
								<div class="row relative flex min-h-[52px] items-center gap-0.5 rounded pr-1 hover:bg-surface-2">
									<button
										type="button"
										class="rmain flex min-h-[52px] min-w-0 flex-1 flex-col justify-center rounded px-3 text-left"
										data-nav
										data-session-row={session.id}
										onclick={() => loadSession(session.id)}
									>
										<span class="block truncate text-sm text-fg">{session.name}</span>
										<Tooltip text={fullDateTime(session.updated_at)} position="bottom" delay={300}>
											<span class="font-mono text-xs tabular-nums text-fg-subtle">Updated {timeAgo(session.updated_at)}</span>
										</Tooltip>
									</button>
									<Tooltip text={expanded ? 'Hide versions' : 'Show versions'} position="left" delay={150}>
										<IconButton
											icon="chevron-right"
											label={expanded ? `Hide versions of ${session.name}` : `Show versions of ${session.name}`}
											size="sm"
											ariaExpanded={expanded}
											class={expanded ? 'rotate-90' : ''}
											onclick={() => toggleVersions(session.id)}
										/>
									</Tooltip>
								</div>
								{#if expanded}
									<div class="acc">{@render versionList(session.id)}</div>
								{/if}
							</li>
						{/each}
					</ul>
				{/each}
			{/if}
		</div>
	{/if}

	<footer class="flex min-h-[48px] flex-shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-t border-line px-4 py-1.5 text-xs text-fg-muted">
		<span>Auto-save</span>
		<Switch
			size="sm"
			label="Toggle auto-save"
			checked={autoSaveEnabled}
			disabled={!currentSession}
			onchange={() => onToggleAutoSave()}
		/>
		{#if autoSaveEnabled && currentSession}
			<SegmentedControl
				variant="toggle"
				ariaLabel="Auto-save interval"
				items={intervals}
				selected={String(autoSaveInterval)}
				onSelect={(id) => onIntervalChange(Number(id))}
			/>
		{/if}
	</footer>
</div>

{#snippet versionList(sessionId: string)}
	<div class="pt-0.5">
		{#if historyLoading}
			<div class="flex justify-center py-3"><Spinner size="sm" /></div>
		{:else if historyError}
			<p class="px-2 py-2 text-xs text-danger">{historyError}</p>
		{:else if historyVersions.length === 0}
			<p class="px-2 py-2 text-xs text-fg-subtle">No history yet.</p>
		{:else}
			{#each historyVersions.slice(0, INLINE_VERSIONS) as version, i (version.version_number)}
				{@render versionRow(sessionId, version, i === 0)}
			{/each}
			{#if historyVersions.length > INLINE_VERSIONS}
				<button
					type="button"
					class="mt-0.5 h-8 rounded px-2 text-xs font-medium text-signal hover:bg-signal/10"
					data-nav
					onclick={() => showAllVersions(sessionId)}
				>
					All <span class="font-mono tabular-nums">{historyVersions.length}</span> versions
				</button>
			{/if}
		{/if}
	</div>
{/snippet}

{#snippet versionRow(sessionId: string, version: SessionVersionSummary, latest: boolean)}
	{@const changes = versionChanges(version, fieldLabels)}
	<button
		type="button"
		class="vrow flex min-h-[48px] w-full items-center gap-2.5 rounded px-2 text-left hover:bg-surface-3"
		data-nav
		data-version-row={version.version_number}
		disabled={restoringVersion}
		onclick={() => restore(sessionId, version.version_number)}
	>
		<span class="w-9 flex-shrink-0 font-mono text-xs font-medium tabular-nums text-fg-muted">v{version.version_number}</span>
		<span class="w-[4.5rem] flex-shrink-0 font-mono text-xs tabular-nums text-fg-subtle">
			<Tooltip text={fullDateTime(version.created_at)} position="top" delay={200}>
				<span>{timeAgo(version.created_at)}</span>
			</Tooltip>
		</span>
		<span class="min-w-0 flex-1">
			<span class="block truncate text-sm text-fg">{versionHeadline(version)}</span>
			{#if changes}
				<span class="flex min-w-0 items-center gap-1 font-mono text-xs text-fg-subtle">
					<span class="truncate">changed {changes.shown.join(', ')}</span>
					{#if changes.more > 0}
						<Tooltip text={changes.all.join(', ')} position="top" delay={200}>
							<span class="flex-shrink-0">+{changes.more} more</span>
						</Tooltip>
					{/if}
				</span>
			{/if}
		</span>
		{#if latest}<span class="flex-shrink-0 rounded-sm border border-signal/35 bg-signal/10 px-1.5 font-mono text-xs text-signal">Latest</span>{/if}
	</button>
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
	.scrim {
		position: fixed;
		inset: 0;
		bottom: var(--dock-height, 0px);
		background: rgb(0 0 0 / 0.5);
	}

	.sessions-drawer {
		position: fixed;
		top: 12px;
		right: 12px;
		bottom: calc(var(--dock-height, 0px) + 12px);
		--sessions-drawer-width: clamp(480px, 36vw, 600px);
		width: min(var(--sessions-drawer-width), calc(100vw - 24px));
		border-radius: 10px;
		animation: drawer-slide 180ms cubic-bezier(0.25, 1, 0.5, 1);
	}

	@keyframes drawer-slide {
		from {
			opacity: 0;
			transform: translateX(18px);
		}
	}

	@media (max-width: 640px) {
		.scrim {
			bottom: 0;
		}
		.sessions-drawer {
			inset: 0;
			width: auto;
			border: 0;
			border-radius: 0;
		}
		.keep-open {
			display: none;
		}
	}

	.scroll {
		overscroll-behavior: contain;
		scrollbar-width: thin;
		scrollbar-color: rgb(var(--line-hover)) transparent;
	}

	.group-head {
		position: sticky;
		top: 0;
		z-index: 2;
		display: flex;
		align-items: center;
		justify-content: space-between;
		height: 28px;
		padding: 0 16px;
		background: rgb(var(--surface-1));
		font-family: var(--font-mono, ui-monospace, monospace);
		font-size: 12px;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: rgb(var(--fg-subtle));
	}

	.group-head span {
		color: rgb(var(--fg-disabled));
		font-variant-numeric: tabular-nums;
	}

	.search:focus-within {
		border-color: rgb(var(--accent) / 0.6);
	}

	.rmain:focus-visible,
	.vrow:focus-visible {
		outline: none;
		background: rgb(var(--surface-2));
		box-shadow: inset 2px 0 0 rgb(var(--signal));
	}

	.vrow:focus-visible {
		background: rgb(var(--surface-3));
	}

	.acc {
		margin: 0 4px 6px 20px;
		padding: 2px 0 4px 6px;
		border-left: 1px solid rgb(var(--line-strong));
	}

	:global(.tw) {
		transition: transform 120ms;
	}
</style>
