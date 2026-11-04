<script lang="ts">
	import { Button, EmptyState, LoadErrorState, Spinner } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { onMount } from 'svelte';
	import { relativeTime } from '$lib/elixirs/time';
	import type { ElixirsApi } from '$lib/services/api/elixirs';

	interface Props {
		client: ElixirsApi;
		presetId: string;
		mode: string;
		presetLabel: string;
		purpose?: 'copy' | 'follow';
		current?: string | null;
		busy?: boolean;
		onpick: (pick: { session_id: string; name: string; version: number | null }) => void;
		onclose: () => void;
	}

	let { client, presetId, mode, presetLabel, purpose = 'copy', current = null, busy = false, onpick, onclose }: Props = $props();

	let follow = $derived(purpose === 'follow');

	let sessions = $state<any[] | null>(null);
	let loadError = $state<string | null>(null);
	let chosen = $state<any>(null);
	let versions = $state<any[] | null>(null);
	let version = $state('');
	let dialog = $state<HTMLDivElement>();

	let usable = $derived((sessions ?? []).filter((session: any) => session.data && typeof session.data[mode] === 'object'));
	let otherModes = $derived((sessions ?? []).length - usable.length);

	async function load() {
		loadError = null;
		const result = await client.listSessions(presetId);
		if (result.ok) sessions = result.data;
		else loadError = result.error.message;
	}

	async function choose(session: any) {
		chosen = session;
		version = '';
		versions = null;
		if (follow) return;
		const result = await client.listVersions(session.id);
		versions = result.ok ? result.data : [];
	}

	function confirm() {
		if (!chosen) return;
		onpick({ session_id: chosen.id, name: chosen.name, version: follow || version === '' ? null : Number(version) });
	}

	function onkeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') {
			event.stopPropagation();
			onclose();
		}
	}

	function portal(node: HTMLElement) {
		document.body.appendChild(node);
		return { destroy: () => node.remove() };
	}

	onMount(() => {
		load();
		dialog?.focus();
	});
</script>

<div
	use:portal
	class="fixed inset-0 z-50 flex items-end justify-center bg-canvas/70 sm:items-center sm:p-6"
	role="presentation"
	onclick={(event) => event.target === event.currentTarget && onclose()}
>
	<div
		bind:this={dialog}
		class="flex max-h-[90dvh] w-full max-w-lg flex-col rounded-t-xl border border-line bg-surface-1 shadow-overlay sm:rounded-xl"
		role="dialog"
		aria-modal="true"
		aria-labelledby="elixir-session-picker-title"
		tabindex="-1"
		{onkeydown}
	>
		<header class="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
			<div class="min-w-0">
				<h2 id="elixir-session-picker-title" class="text-md font-semibold text-fg">{follow ? 'Follow a session' : 'Copy a session'}</h2>
				<p class="truncate text-sm text-fg-muted">
					{follow ? `Saved ${presetLabel} sessions. Edits there reach this step.` : `Saved ${presetLabel} sessions. The step keeps its own copy.`}
				</p>
			</div>
			<Tooltip text="Close">
				<button type="button" class="picker-close" aria-label="Close session picker" onclick={onclose}>
					<Icon name="close" className="h-4 w-4" />
				</button>
			</Tooltip>
		</header>

		<div class="min-h-0 flex-1 overflow-y-auto px-4 py-3">
			{#if loadError}
				<LoadErrorState title="Sessions could not be loaded" message={loadError} onRetry={load} />
			{:else if sessions === null}
				<div class="flex justify-center py-10" role="status" aria-label="Loading sessions">
					<Spinner size="md" />
				</div>
			{:else if usable.length === 0}
				<EmptyState title="No saved sessions for this mode" description={otherModes > 0
							? 'Your sessions for this preset use another mode. Save one in this mode on the Generate page first.'
							: `Save a session for this preset on the Generate page, then come back to ${follow ? 'follow' : 'copy'} it here.`} icon="save" compact />
			{:else}
				<ul class="flex flex-col gap-1.5" aria-label="Saved sessions">
					{#each usable as session (session.id)}
						<li>
							<button
								type="button"
								class="flex w-full min-w-0 items-center justify-between gap-3 rounded border px-3 py-2 text-left transition-colors {chosen?.id === session.id
									? 'border-signal bg-signal/10'
									: 'border-line hover:border-line-hover hover:bg-surface-2'}"
								aria-pressed={chosen?.id === session.id}
								onclick={() => choose(session)}
							>
								<span class="min-w-0 truncate text-base text-fg">{session.name}</span>
								<span class="shrink-0 text-sm text-fg-subtle">
									{session.id === current ? 'Followed now' : relativeTime(session.updated_at || session.created_at)}
								</span>
							</button>
						</li>
					{/each}
				</ul>
			{/if}
		</div>

		{#if chosen && !follow}
			<div class="flex flex-col gap-1.5 border-t border-line px-4 py-3">
				<label for="elixir-session-version" class="text-sm font-medium text-fg-muted">Version</label>
				<select id="elixir-session-version" class="input w-full" bind:value={version} disabled={versions === null}>
					<option value="">Latest saved settings</option>
					{#each versions ?? [] as item (item.version_number)}
						<option value={String(item.version_number)}>
							Version {item.version_number} · {relativeTime(item.created_at)}
						</option>
					{/each}
				</select>
			</div>
		{/if}

		<footer class="flex justify-end gap-2 border-t border-line px-4 py-3">
			<Button variant="ghost" size="sm" onclick={onclose}>Cancel</Button>
			<Button variant="primary" size="sm" disabled={!chosen || chosen.id === current} loading={busy} onclick={confirm}>
				{follow ? 'Follow session' : 'Copy settings'}
			</Button>
		</footer>
	</div>
</div>

<style>
	.picker-close {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		min-width: 2rem;
		min-height: 2rem;
		border-radius: 4px;
		color: rgb(var(--fg-muted));
	}
	.picker-close:hover {
		background: rgb(var(--surface-3));
		color: rgb(var(--fg));
	}
</style>
