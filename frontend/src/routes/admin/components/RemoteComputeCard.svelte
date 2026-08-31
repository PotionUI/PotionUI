<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { Badge, Button } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import { refreshProvisionedComputeStatus } from '$lib/services/admin-api';
	import type { Backend, ProvisionedCompute } from '$lib/services/admin-api';

	/**
	 * One provisioned-compute card. Owns its own status polling (started on
	 * mount, stopped on unmount) - the same idiom as BackendOptimizations'
	 * install-job poll: a plain `setInterval` torn down in `onDestroy`, never
	 * an `$effect`. Polling therefore only runs while the card is actually in
	 * the DOM, i.e. while it's visible.
	 */
	let {
		row,
		backends,
		busy = false,
		onOpenBackend,
		onStatusUpdate,
		onStop,
		onTerminate
	}: {
		row: ProvisionedCompute;
		backends: Backend[];
		busy?: boolean;
		onOpenBackend: (backendId: string) => void;
		onStatusUpdate: (updated: ProvisionedCompute) => void;
		onStop: (row: ProvisionedCompute) => void;
		onTerminate: (row: ProvisionedCompute) => void;
	} = $props();

	const POLL_INTERVAL_MS = 8000;
	let pollHandle: ReturnType<typeof setInterval> | null = null;

	let linkedBackend = $derived(backends.find((b) => b.id === row.backend_id) ?? null);

	const statusVariant: Record<string, 'success' | 'warning' | 'danger' | 'neutral'> = {
		running: 'success',
		stopped: 'neutral',
		missing: 'danger',
		unreachable: 'danger',
		unknown: 'warning'
	};

	async function pollStatus() {
		try {
			const response = await refreshProvisionedComputeStatus(row.id);
			if (response.success && response.data) onStatusUpdate(response.data);
		} catch {
			// Transient poll failure - the next tick retries, nothing to surface.
		}
	}

	onMount(() => {
		pollHandle = setInterval(pollStatus, POLL_INTERVAL_MS);
	});

	onDestroy(() => {
		if (pollHandle !== null) {
			clearInterval(pollHandle);
			pollHandle = null;
		}
	});
</script>

<div class="rounded-lg border border-line bg-surface-1 p-4 flex flex-col gap-3 min-w-0">
	<div class="flex items-center justify-between gap-2 flex-wrap">
		<div class="flex items-center gap-2 min-w-0">
			<Icon name="cpu" className="w-4 h-4 text-fg-muted flex-shrink-0" />
			<span class="text-sm font-medium text-fg truncate" title={row.profile_name}>{row.profile_name}</span>
		</div>
		<Badge variant={statusVariant[row.status] ?? 'neutral'} size="sm" dot class="uppercase flex-shrink-0">
			{row.status}
		</Badge>
	</div>

	<dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-xs">
		<dt class="text-fg-subtle">Pod ID</dt>
		<dd class="font-mono tabular-nums text-fg-muted truncate min-w-0">{row.resource_ref ?? row.handle}</dd>

		<dt class="text-fg-subtle">GPU</dt>
		<dd class="font-mono tabular-nums text-fg-muted truncate min-w-0">{row.gpu_type_id ?? '—'}</dd>

		<dt class="text-fg-subtle">Backend</dt>
		<dd class="truncate min-w-0">
			{#if linkedBackend}
				<button
					type="button"
					class="text-signal hover:underline truncate"
					onclick={() => onOpenBackend(linkedBackend!.id)}
				>
					{linkedBackend.name}
				</button>
			{:else}
				<span class="text-fg-subtle">—</span>
			{/if}
		</dd>
	</dl>

	<div class="flex items-center justify-end gap-2 pt-1 flex-wrap">
		<Button
			variant="secondary"
			size="sm"
			icon="pause"
			disabled={busy || row.status === 'stopped'}
			onclick={() => onStop(row)}
		>
			Stop
		</Button>
		<Button variant="danger" size="sm" icon="trash" disabled={busy} onclick={() => onTerminate(row)}>
			Terminate
		</Button>
	</div>
</div>
