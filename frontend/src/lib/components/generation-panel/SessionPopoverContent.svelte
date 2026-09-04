<script lang="ts">
	// The session popover body shared by SessionControl.svelte's dropdown and
	// the generation bar's SessionCluster.svelte — sessions list, session
	// history sub-view, auto-save controls, rename/delete. Purely
	// presentational: every handler below is expected to already close the
	// panel where that's the caller's job (mirrors the old inline markup this
	// was extracted from).
	//
	// Restyled onto generation-panel-concept.html's `#sessionPopover` anatomy
	// (`.popover-header`/`.session-list`/`.session-row`/`.popover-footer`,
	// lines 485-493) — the mock's own popover is a static 3-row demo with no
	// history sub-view, rename/delete or interval picker; those are real
	// functionality it never depicts, added under the same class idiom
	// (`.quiet-button` rows, a second `.popover-footer` for the interval
	// picker) rather than invented from scratch.
	import type { Session, SessionVersionSummary } from '$lib/types/api';
	import { Spinner } from '$lib/components/ui';
	import { timeAgo } from '$lib/utils/relativeTime';

	export let sessions: Session[] = [];
	export let currentSession: Session | null = null;
	export let selectedSessionId = '';
	export let loading = false;
	export let historySessionId: string | null = null;
	export let historyVersions: SessionVersionSummary[] = [];
	export let historyLoading = false;
	export let historyError: string | null = null;
	export let restoringVersion = false;
	export let autoSaveEnabled = false;
	export let autoSaveInterval = 10000;
	export let onSelect: (sessionId: string) => void;
	export let onSaveAs: () => void;
	export let onOpenHistory: (sessionId: string) => void;
	export let onCloseHistory: () => void;
	export let onRestoreVersion: (sessionId: string, versionNumber: number) => void;
	export let onToggleAutoSave: () => void;
	export let onIntervalChange: (interval: number) => void;
	export let onRename: () => void;
	export let onDelete: () => void;

	$: historySessionName = sessions.find((s) => s.id === historySessionId)?.name ?? '';
	const intervals = [
		{ value: 5000, label: '5s' },
		{ value: 10000, label: '10s' },
		{ value: 30000, label: '30s' },
		{ value: 60000, label: '1m' }
	];
</script>

<div class="popover-header">
	{#if historySessionId}
		<strong class="truncate">{historySessionName}</strong>
		<button type="button" class="quiet-button" on:click={onCloseHistory}>
			<svg class="icon"><use href="#i-chevron-left" /></svg>Back
		</button>
	{:else}
		<strong>Sessions</strong>
		<button type="button" class="quiet-button" on:click={onSaveAs}>
			<svg class="icon"><use href="#i-plus" /></svg>New
		</button>
	{/if}
</div>

{#if historySessionId}
	{@const historySessionIdValue = historySessionId}
	<!-- Session history sub-view for one saved session. -->
	<div class="session-list">
		{#if historyLoading}
			<div class="flex items-center justify-center py-6"><Spinner size="sm" /></div>
		{:else if historyError}
			<p class="px-2 py-3 text-xs text-danger">{historyError}</p>
		{:else if historyVersions.length === 0}
			<p class="px-2 py-3 text-xs" style="color: rgb(var(--fg-subtle))">No history yet.</p>
		{:else}
			{#each historyVersions as version, i (version.version_number)}
				<button
					type="button"
					class="session-row"
					on:click={() => onRestoreVersion(historySessionIdValue, version.version_number)}
					disabled={restoringVersion}
					role="menuitem"
				>
					<span class="row-icon"><svg class="icon"><use href="#i-history" /></svg></span>
					<span class="row-copy">
						<span class="row-title">{version.summary}</span>
						<span class="row-meta">{timeAgo(version.created_at)}</span>
					</span>
					{#if i === 0}<span class="row-status live">Latest</span>{/if}
				</button>
			{/each}
		{/if}
	</div>
{:else}
	<div class="session-list">
		{#if loading}
			<div class="flex items-center justify-center py-6"><Spinner size="sm" /></div>
		{:else if sessions.length === 0}
			<p class="px-2 py-3 text-xs" style="color: rgb(var(--fg-subtle))">No sessions saved for this preset.</p>
		{:else}
			{#each sessions as session (session.id)}
				<div class="session-row-wrap">
					<button
						type="button"
						class="session-row {session.id === selectedSessionId ? 'is-current' : ''}"
						on:click={() => onSelect(session.id)}
						role="menuitem"
					>
						<span class="row-icon"><svg class="icon"><use href="#i-session" /></svg></span>
						<span class="row-copy">
							<span class="row-title">{session.name}</span>
							<span class="row-meta">Updated {new Date(session.updated_at).toLocaleDateString()}</span>
						</span>
						{#if session.id === selectedSessionId}<span class="row-status live">current</span>{/if}
					</button>
					<button
						type="button"
						class="quiet-button"
						title="Session history for {session.name}"
						aria-label="Session history for {session.name}"
						on:click={() => onOpenHistory(session.id)}
					>
						<svg class="icon"><use href="#i-history" /></svg>
					</button>
				</div>
			{/each}
		{/if}
	</div>
{/if}

<div class="popover-footer">
	<span>Auto-save {autoSaveEnabled ? `every ${autoSaveInterval / 1000}s` : 'off'}</span>
	<button
		type="button"
		class="switch"
		role="switch"
		aria-checked={autoSaveEnabled}
		aria-label="Toggle auto-save"
		disabled={!currentSession}
		on:click={onToggleAutoSave}
	></button>
</div>

{#if autoSaveEnabled}
	<div class="popover-footer">
		{#each intervals as interval}
			<button
				type="button"
				class="quiet-button {autoSaveInterval === interval.value ? 'is-active' : ''}"
				on:click={() => onIntervalChange(interval.value)}
				aria-pressed={autoSaveInterval === interval.value}
			>{interval.label}</button>
		{/each}
	</div>
{/if}

{#if currentSession}
	<div class="popover-footer">
		<button type="button" class="quiet-button" on:click={onRename}>Rename</button>
		<button type="button" class="quiet-button danger" on:click={onDelete}>Delete</button>
	</div>
{/if}
