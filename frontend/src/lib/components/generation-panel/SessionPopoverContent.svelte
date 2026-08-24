<script lang="ts">
	// The session popover body shared by SessionControl.svelte's dropdown and
	// the generation bar's SessionCluster.svelte — sessions list, session
	// history sub-view, auto-save controls, rename/delete. Purely
	// presentational: every handler below is expected to already close the
	// panel where that's the caller's job (mirrors the old inline markup this
	// was extracted from).
	import type { Session, SessionVersionSummary } from '$lib/types/api';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Badge, Button, Spinner, Switch } from '$lib/components/ui';
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

<div class="flex items-center justify-between gap-3 px-4 py-3 border-b border-line">
	<div class="min-w-0">
		<p class="label mb-0">Session</p>
		<p class="text-sm text-fg truncate">{currentSession?.name || 'No active session'}</p>
	</div>
	<Button variant="secondary" size="xs" icon="plus" onclick={onSaveAs}>New</Button>
</div>

<div class="max-h-56 overflow-y-auto border-b border-line">
	{#if historySessionId}
		{@const historySessionIdValue = historySessionId}
		<!-- Session history sub-view for one saved session -->
		<div class="sticky top-0 z-10 flex items-center gap-2 px-2 py-2 bg-surface-1/95">
			<button
				type="button"
				class="p-1 text-fg-subtle hover:text-fg hover:bg-surface-3 rounded flex-shrink-0"
				on:click={onCloseHistory}
				aria-label="Back to sessions"
			>
				<Icon name="chevron-left" className="w-4 h-4" />
			</button>
			<div class="min-w-0">
				<span class="label mb-0 block">Session history</span>
				<span class="block text-xs text-fg-subtle truncate">{historySessionName}</span>
			</div>
		</div>
		{#if historyLoading}
			<div class="flex items-center justify-center py-6"><Spinner size="sm" /></div>
		{:else if historyError}
			<p class="px-4 pb-4 text-sm text-danger">{historyError}</p>
		{:else if historyVersions.length === 0}
			<p class="px-4 pb-4 text-sm text-fg-subtle">No history yet.</p>
		{:else}
			{#each historyVersions as version, i (version.version_number)}
				<Tooltip text="Load this save" position="left" delay={200} wrapperClass="block w-full">
					<button
						type="button"
						class="w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-surface-2 transition-colors disabled:opacity-50 disabled:cursor-wait"
						on:click={() => onRestoreVersion(historySessionIdValue, version.version_number)}
						disabled={restoringVersion}
						role="menuitem"
					>
						<Icon name="clock" className="w-4 h-4 flex-shrink-0 text-fg-subtle" />
						<span class="min-w-0 flex-1">
							<span class="block text-sm text-fg truncate">{version.summary}</span>
							<span class="block font-mono text-2xs tabular-nums text-fg-subtle">{timeAgo(version.created_at)}</span>
						</span>
						{#if i === 0}<Badge variant="signal" size="sm">Latest</Badge>{/if}
					</button>
				</Tooltip>
			{/each}
		{/if}
	{:else}
		<div class="sticky top-0 z-10 flex items-center justify-between px-4 py-2 bg-surface-1/95">
			<span class="label mb-0">Saved sessions</span>
			<span class="font-mono text-2xs tabular-nums text-fg-subtle">{sessions.length}</span>
		</div>
		{#if loading}
			<div class="flex items-center justify-center py-6"><Spinner size="sm" /></div>
		{:else if sessions.length === 0}
			<p class="px-4 pb-4 text-sm text-fg-subtle">No sessions saved for this preset.</p>
		{:else}
			{#each sessions as session (session.id)}
				<div
					class="flex items-center gap-1 pr-2 hover:bg-surface-2 transition-colors {session.id === selectedSessionId ? 'bg-signal/10' : ''}"
				>
					<button
						type="button"
						class="min-w-0 flex-1 flex items-center gap-3 pl-4 py-2.5 text-left"
						on:click={() => onSelect(session.id)}
						role="menuitem"
					>
						<Icon name={session.id === selectedSessionId ? 'check' : 'document'} className="w-4 h-4 flex-shrink-0 {session.id === selectedSessionId ? 'text-signal' : 'text-fg-subtle'}" />
						<span class="min-w-0 flex-1">
							<span class="block text-sm text-fg truncate">{session.name}</span>
							<span class="block font-mono text-2xs text-fg-subtle">Updated {new Date(session.updated_at).toLocaleDateString()}</span>
						</span>
						{#if session.id === selectedSessionId}<Badge variant="signal" size="sm">current</Badge>{/if}
					</button>
					<Tooltip text="Session history" position="left" delay={200}>
						<button
							type="button"
							class="p-1.5 text-fg-subtle hover:text-fg hover:bg-surface-3 rounded flex-shrink-0"
							on:click={() => onOpenHistory(session.id)}
							aria-label="Session history for {session.name}"
						>
							<Icon name="clock" className="w-3.5 h-3.5" />
						</button>
					</Tooltip>
				</div>
			{/each}
		{/if}
	{/if}
</div>

<div class="p-3 border-b border-line">
	<div class="flex items-center justify-between gap-3 px-1 py-1">
		<div>
			<p class="text-sm text-fg">Auto-save</p>
			<p class="text-xs text-fg-subtle">Save changes on an interval</p>
		</div>
		<Switch
			checked={autoSaveEnabled}
			onchange={onToggleAutoSave}
			disabled={!currentSession}
			label="Auto-save session"
		/>
	</div>
	{#if autoSaveEnabled}
		<div class="flex items-center gap-1 mt-2 px-1" aria-label="Auto-save interval">
			{#each intervals as interval}
				<button
					type="button"
					class="flex-1 px-2 py-1 text-xs rounded {autoSaveInterval === interval.value ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2'}"
					on:click={() => onIntervalChange(interval.value)}
					aria-pressed={autoSaveInterval === interval.value}
				>{interval.label}</button>
			{/each}
		</div>
	{/if}
</div>

{#if currentSession}
	<div class="flex items-center gap-1 p-2">
		<button type="button" class="flex-1 px-3 py-2 text-xs text-fg-muted hover:text-fg hover:bg-surface-2 rounded" on:click={onRename}>Rename</button>
		<button type="button" class="flex-1 px-3 py-2 text-xs text-danger hover:bg-danger/10 rounded" on:click={onDelete}>Delete</button>
	</div>
{/if}
