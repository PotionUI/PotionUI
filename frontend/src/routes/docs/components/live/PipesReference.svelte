<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/services/api/index';
	import { Badge, Button } from '$lib/components/ui';
	import { authStore } from '$lib/stores/auth';
	import { adminWebSocket } from '$lib/services/adminWebsocket';
	import { logger } from '$lib/utils/logger';
	import LiveReferenceDataShell from './LiveReferenceDataShell.svelte';
	import DisclosureRow from './DisclosureRow.svelte';

	interface PipeSpec {
		name?: string;
		id?: string;
		description?: string;
		status?: string;
		manual_install?: string | null;
		requirements?: unknown;
		inputs?: unknown;
		outputs?: unknown;
		config?: unknown;
		[key: string]: unknown;
	}

	// `$state`, not a plain `let`: the install affordance below puts this
	// component in runes mode, where a plain `let` no longer repaints.
	let expanded = $state<Record<string, boolean>>({});
	// What the install of a pipe has reported since this page loaded. Overlays
	// the status the reference was fetched with, which cannot update itself.
	let live = $state<Record<string, { status: string; message: string | null }>>({});

	let isAdmin = $derived($authStore.user?.account_type === 'ADMIN');

	const STATUS_LABELS: Record<string, string> = {
		installed: 'installed',
		installing: 'installing',
		not_installed: 'not installed',
		error: 'install failed'
	};

	const STATUS_VARIANTS: Record<string, 'success' | 'info' | 'warning' | 'danger'> = {
		installed: 'success',
		installing: 'info',
		not_installed: 'warning',
		error: 'danger'
	};

	function pipeKey(pipe: PipeSpec, index: number): string {
		return String(pipe.id ?? pipe.name ?? index);
	}

	function pipeLabel(pipe: PipeSpec): string {
		return String(pipe.name ?? pipe.id ?? 'unknown');
	}

	function statusOf(pipe: PipeSpec): string {
		return live[pipeLabel(pipe)]?.status ?? String(pipe.status ?? '');
	}

	function messageOf(pipe: PipeSpec): string | null {
		return live[pipeLabel(pipe)]?.message ?? null;
	}

	function toggle(key: string) {
		expanded = { ...expanded, [key]: !expanded[key] };
	}

	function matches(pipe: PipeSpec, query: string): boolean {
		const needle = query.toLowerCase();
		return (
			pipeLabel(pipe).toLowerCase().includes(needle) ||
			(pipe.description || '').toLowerCase().includes(needle)
		);
	}

	async function install(pipe: PipeSpec) {
		const name = pipeLabel(pipe);
		live = { ...live, [name]: { status: 'installing', message: 'Starting install...' } };
		try {
			const response = await api.installPipe(name);
			if (!response.success) {
				throw new Error(response.message || response.error || 'Install failed');
			}
		} catch (err) {
			// A refusal carries the commands that do work (422) - show those
			// rather than a generic failure.
			const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data
				?.detail;
			live = {
				...live,
				[name]: {
					status: 'error',
					message:
						typeof detail === 'string'
							? detail
							: err instanceof Error
								? err.message
								: 'Install failed'
				}
			};
		}
	}

	async function load(): Promise<PipeSpec[]> {
		const response = await api.getDocsLivePipes();
		if (response.success && response.data) {
			const raw = response.data;
			return Array.isArray(raw) ? raw : raw.pipes || [];
		}
		throw new Error(response.message || response.error || 'Failed to load pipes reference');
	}

	onMount(() => {
		const unsubscribe = adminWebSocket.onPipeInstallStatus(({ pipe, status, message }) => {
			live = { ...live, [pipe]: { status, message } };
		});

		if (isAdmin && !adminWebSocket.isConnected()) {
			adminWebSocket.connectAsync().catch((err) => {
				logger.error('Admin WebSocket unavailable - install progress will not stream:', err);
			});
		}

		// The socket is a shared singleton; only this subscription is ours to drop.
		return unsubscribe;
	});
</script>

<LiveReferenceDataShell {load} filter={matches} label="pipes">
	{#snippet content({ items })}
		<div class="space-y-2">
			{#each items as pipe, index (pipeKey(pipe, index))}
				{@const key = pipeKey(pipe, index)}
				{@const status = statusOf(pipe)}
				<DisclosureRow expanded={!!expanded[key]} onToggle={() => toggle(key)}>
					{#snippet trigger()}
						<div class="min-w-0 flex-1">
							<div class="flex items-center gap-2">
								<code class="text-sm font-mono text-fg">{pipeLabel(pipe)}</code>
								{#if status && status !== 'installed'}
									<Badge size="sm" variant={STATUS_VARIANTS[status] ?? 'neutral'}>
										{STATUS_LABELS[status] ?? status}
									</Badge>
								{/if}
								{#if pipe.manual_install}
									<Badge size="sm" variant="neutral">manual setup</Badge>
								{/if}
							</div>
							{#if pipe.description}
								<p class="text-xs text-fg-muted truncate">{pipe.description}</p>
							{/if}
						</div>
					{/snippet}

					{#if status !== 'installed' || pipe.manual_install}
						<div class="space-y-2">
							<h4 class="text-xs font-semibold uppercase tracking-wide text-fg-muted">
								Installation
							</h4>

							{#if pipe.manual_install}
								<p class="text-xs text-fg-muted">
									This pipe's requirements are built on this machine, not fetched from a package
									index - nothing here can install them for you. Run these, then reload:
								</p>
								<pre
									class="text-xs font-mono bg-surface-2 border border-warning/25 rounded p-2 overflow-x-auto text-fg whitespace-pre-wrap">{pipe.manual_install}</pre>
							{:else if isAdmin}
								<div class="flex items-center gap-2">
									<Button
										size="xs"
										onclick={() => install(pipe)}
										loading={status === 'installing'}
										disabled={status === 'installing'}
									>
										{status === 'error' ? 'Retry install' : 'Install requirements'}
									</Button>
									{#if pipe.requirements !== undefined}
										<span class="text-xs text-fg-subtle">runs pip / git on the server</span>
									{/if}
								</div>
							{:else}
								<p class="text-xs text-fg-muted">
									An administrator has to install this pipe's requirements.
								</p>
							{/if}

							{#if messageOf(pipe)}
								<pre
									class="text-xs font-mono bg-surface-2 rounded p-2 overflow-x-auto whitespace-pre-wrap {status ===
									'error'
										? 'text-danger'
										: 'text-fg-muted'}">{messageOf(pipe)}</pre>
							{/if}
						</div>
					{/if}

					{#if pipe.requirements !== undefined}
						<div>
							<h4 class="text-xs font-semibold uppercase tracking-wide text-fg-muted mb-1">
								Requirements
							</h4>
							<pre
								class="text-xs font-mono bg-surface-2 rounded p-2 overflow-x-auto text-fg-muted">{JSON.stringify(
									pipe.requirements,
									null,
									2
								)}</pre>
						</div>
					{/if}
					{#if pipe.inputs !== undefined}
						<div>
							<h4 class="text-xs font-semibold uppercase tracking-wide text-fg-muted mb-1">Inputs</h4>
							<pre class="text-xs font-mono bg-surface-2 rounded p-2 overflow-x-auto text-fg-muted">{JSON.stringify(pipe.inputs, null, 2)}</pre>
						</div>
					{/if}
					{#if pipe.outputs !== undefined}
						<div>
							<h4 class="text-xs font-semibold uppercase tracking-wide text-fg-muted mb-1">Outputs</h4>
							<pre class="text-xs font-mono bg-surface-2 rounded p-2 overflow-x-auto text-fg-muted">{JSON.stringify(pipe.outputs, null, 2)}</pre>
						</div>
					{/if}
					{#if pipe.config !== undefined}
						<div>
							<h4 class="text-xs font-semibold uppercase tracking-wide text-fg-muted mb-1">Config</h4>
							<pre class="text-xs font-mono bg-surface-2 rounded p-2 overflow-x-auto text-fg-muted">{JSON.stringify(pipe.config, null, 2)}</pre>
						</div>
					{/if}
					{#if pipe.inputs === undefined && pipe.outputs === undefined && pipe.config === undefined}
						<p class="text-xs text-fg-subtle">No additional spec available for this pipe.</p>
					{/if}
				</DisclosureRow>
			{/each}
		</div>
	{/snippet}
</LiveReferenceDataShell>
