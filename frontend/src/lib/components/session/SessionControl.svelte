<script lang="ts">
	import type { Session, SessionVersionSummary } from '$lib/types/api';
	import Icon from '$lib/components/Icon.svelte';
	import { Spinner } from '$lib/components/ui';
	import SessionPopoverContent from '$lib/components/generation-panel/SessionPopoverContent.svelte';

	export let enabled = false;
	// A tight single-line pill (dot + name + Save) for the tabs-row
	// mount, in place of the wide two-line trigger. The dropdown panel below
	// is unchanged either way.
	export let compact = false;
	export let sessions: Session[] = [];
	export let currentSession: Session | null = null;
	export let selectedSessionId = '';
	export let loading = false;
	export let saving = false;
	export let dirty = false;
	export let lastSavedTime: Date | null = null;
	export let autoSaveEnabled = false;
	export let autoSaveInterval = 10000;
	// Session history — which saved session's history is open (if any), its
	// versions (newest first), and whether a restore is in flight.
	export let historySessionId: string | null = null;
	export let historyVersions: SessionVersionSummary[] = [];
	export let historyLoading = false;
	export let historyError: string | null = null;
	export let restoringVersion = false;
	export let onSelect: (sessionId: string) => void;
	export let onSave: () => void;
	export let onSaveAs: () => void;
	export let onRename: () => void;
	export let onDelete: () => void;
	export let onToggleAutoSave: () => void;
	export let onIntervalChange: (interval: number) => void;
	export let onOpenHistory: (sessionId: string) => void;
	export let onCloseHistory: () => void;
	export let onRestoreVersion: (sessionId: string, versionNumber: number) => void;

	let open = false;
	let root: HTMLDivElement;

	function relativeTime(date: Date | null): string {
		if (!date) return 'Saved';
		const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
		if (seconds < 10) return 'Saved just now';
		if (seconds < 60) return `Saved ${seconds}s ago`;
		const minutes = Math.floor(seconds / 60);
		if (minutes < 60) return `Saved ${minutes}m ago`;
		return `Saved ${date.toLocaleDateString()}`;
	}

	$: status = saving
		? 'Saving…'
		: dirty
			? autoSaveEnabled
				? `Unsaved · auto-save ${autoSaveInterval / 1000}s`
				: 'Unsaved changes'
			: currentSession
				? autoSaveEnabled
					? `Saved · auto-save ${autoSaveInterval / 1000}s`
					: relativeTime(lastSavedTime)
				: 'Choose or create a session';

	// Closing the whole panel also resets the history sub-view — otherwise
	// reopening it later would briefly flash the previous session's history.
	function closePanel() {
		open = false;
		onCloseHistory();
	}

	function toggleOpen() {
		if (open) {
			closePanel();
		} else {
			open = true;
		}
	}

	function select(sessionId: string) {
		onSelect(sessionId);
		closePanel();
	}

	function handleWindowClick(event: MouseEvent) {
		if (!open || !root) return;
		const target = event.target as Node;
		// A click inside the panel can re-render it (e.g. the history button
		// swaps the list for the history sub-view) before this window-level
		// handler runs — the clicked node is then detached, root.contains()
		// says "outside", and the panel wrongly closes. A detached target can
		// only come from inside the document we just re-rendered, never from a
		// genuine outside click, so treat it as inside.
		if (!target.isConnected) return;
		if (!root.contains(target)) closePanel();
	}
</script>

<svelte:window on:click={handleWindowClick} />

<div
	class="relative session-control flex items-stretch overflow-visible rounded-lg border border-line-strong bg-surface-2 transition-colors hover:border-line-hover {compact ? 'h-[34px] flex-shrink-0' : 'w-full min-w-0 sm:w-auto'}"
	bind:this={root}
>
	{#if compact}
		<button
			type="button"
			class="flex min-w-0 items-center gap-2 pl-3 pr-2.5 text-left transition-colors hover:bg-surface-3 disabled:opacity-50 disabled:cursor-not-allowed {enabled && ((!currentSession) || (currentSession && dirty)) ? 'rounded-l-lg' : 'rounded-lg'}"
			on:click={toggleOpen}
			disabled={!enabled}
			aria-haspopup="menu"
			aria-expanded={open}
			aria-label="Session"
		>
			<span
				class="h-[7px] w-[7px] flex-shrink-0 rounded-full {dirty
					? 'bg-warning-solid'
					: saving
						? 'bg-signal-solid'
						: currentSession
							? 'bg-success-solid'
							: 'bg-fg-subtle/40'}"
				aria-hidden="true"
			></span>
			<span class="max-w-[9rem] truncate text-xs font-medium text-fg">{currentSession?.name || (enabled ? 'No saved session' : 'Session unavailable')}</span>
			<Icon name="chevron-down" className="h-3.5 w-3.5 flex-shrink-0 text-fg-subtle transition-transform {open ? 'rotate-180' : ''}" />
		</button>
	{:else}
		<button
			type="button"
			class="min-w-0 flex-1 sm:w-60 sm:flex-none flex items-center gap-2.5 px-2.5 py-1.5 text-left rounded-l-lg hover:bg-surface-3 transition-colors disabled:opacity-50 disabled:cursor-not-allowed {enabled && ((!currentSession) || (currentSession && dirty)) ? 'rounded-r-none' : 'rounded-r-lg'}"
			on:click={toggleOpen}
			disabled={!enabled}
			aria-haspopup="menu"
			aria-expanded={open}
		>
			<span class="w-7 h-7 flex-shrink-0 flex items-center justify-center rounded bg-surface-3 text-fg-muted">
				<Icon name="document" className="w-4 h-4" />
			</span>
			<span class="min-w-0 flex-1">
				<span class="block text-sm font-medium text-fg truncate">{currentSession?.name || (enabled ? 'No saved session' : 'Session unavailable')}</span>
				<span class="block font-mono text-2xs truncate {dirty ? 'text-warning' : saving ? 'text-signal' : 'text-fg-subtle'}">{enabled ? status : 'Select a preset and mode'}</span>
			</span>
			<Icon name="chevron-down" className="w-4 h-4 flex-shrink-0 text-fg-subtle transition-transform {open ? 'rotate-180' : ''}" />
		</button>
	{/if}

	{#if enabled && currentSession && dirty}
		<button
			type="button"
			class="inline-flex items-center justify-center gap-1.5 rounded-r-lg border-l border-line-strong text-xs font-semibold text-signal transition-colors hover:bg-signal/10 disabled:cursor-wait disabled:opacity-60 {compact ? 'px-2.5' : 'min-w-16 px-3'}"
			on:click={onSave}
			disabled={saving}
			aria-label={saving ? 'Saving session' : 'Save session'}
		>
			{#if saving}<Spinner size="sm" />{:else if !compact}<Icon name="save" className="h-3.5 w-3.5" />{/if}
			<span>{saving ? 'Saving' : 'Save'}</span>
		</button>
	{:else if enabled && !currentSession}
		<button
			type="button"
			class="inline-flex items-center justify-center gap-1.5 rounded-r-lg border-l border-line-strong text-xs font-semibold text-signal transition-colors hover:bg-signal/10 {compact ? 'px-2.5' : 'px-3'}"
			on:click={onSaveAs}
			aria-label="Save as a new session"
		>
			{#if !compact}<Icon name="plus" className="h-3.5 w-3.5" />{/if}
			<span>Save</span>
		</button>
	{/if}

	{#if open}
		<div class="absolute right-0 top-full z-50 mt-1 w-[min(24rem,calc(100vw-2rem))] overflow-hidden rounded-xl border border-line-strong bg-surface-1 shadow-floating" role="menu">
			<SessionPopoverContent
				{sessions}
				{currentSession}
				{selectedSessionId}
				{loading}
				{historySessionId}
				{historyVersions}
				{historyLoading}
				{historyError}
				{restoringVersion}
				{autoSaveEnabled}
				{autoSaveInterval}
				onSelect={select}
				onSaveAs={() => { closePanel(); onSaveAs(); }}
				{onOpenHistory}
				{onCloseHistory}
				{onRestoreVersion}
				{onToggleAutoSave}
				{onIntervalChange}
				onRename={() => { closePanel(); onRename(); }}
				onDelete={() => { closePanel(); onDelete(); }}
			/>
		</div>
	{/if}
</div>
