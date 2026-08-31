<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { isAxiosError } from 'axios';
	import { Badge, Button } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
	import { getApiErrorMessage } from '$lib/utils/logger';
	import { toasts } from '$lib/stores/toast';
	import {
		getProvisionedComputeByBackend,
		refreshProvisionedComputeStatus,
		stopProvisionedCompute,
		terminateProvisionedCompute
	} from '$lib/services/admin-api';
	import type { ProvisionedCompute } from '$lib/services/admin-api';

	/**
	 * Shown on a backend's detail pane when it has linked provisioned
	 * infrastructure (`GET by-backend/{id}` returns a row rather than 404 -
	 * most backends are plain and this renders nothing for them). Owns its own
	 * status polling the same way `RemoteComputeCard` did: a plain
	 * `setInterval` started in `onMount` and torn down in `onDestroy`, never an
	 * `$effect`. The caller re-mounts this with `{#key backendId}` when the
	 * selected backend changes, so onMount re-runs cleanly instead of reacting
	 * to a changing prop.
	 */
	let {
		backendId,
		onStopped,
		onTerminated
	}: {
		backendId: string;
		onStopped: () => void;
		onTerminated: () => void;
	} = $props();

	const POLL_INTERVAL_MS = 8000;
	let pollHandle: ReturnType<typeof setInterval> | null = null;

	let loading = $state(true);
	let row = $state<ProvisionedCompute | null>(null);
	let pendingAction = $state<'stop' | 'terminate' | null>(null);
	let acting = $state(false);

	const statusVariant: Record<string, 'success' | 'warning' | 'danger' | 'neutral'> = {
		running: 'success',
		stopped: 'neutral',
		missing: 'danger',
		unreachable: 'danger',
		unknown: 'warning'
	};

	async function pollStatus() {
		if (!row) return;
		try {
			const response = await refreshProvisionedComputeStatus(row.id);
			if (response.success && response.data) row = response.data;
		} catch {
			// Transient poll failure - the next tick retries, nothing to surface.
		}
	}

	onMount(async () => {
		try {
			const response = await getProvisionedComputeByBackend(backendId);
			if (response.success && response.data) {
				row = response.data;
				pollHandle = setInterval(pollStatus, POLL_INTERVAL_MS);
			}
		} catch (e: unknown) {
			if (!isAxiosError(e) || e.response?.status !== 404) {
				toasts.error(getApiErrorMessage(e, 'Failed to load infrastructure status'));
			}
			row = null;
		} finally {
			loading = false;
		}
	});

	onDestroy(() => {
		if (pollHandle !== null) {
			clearInterval(pollHandle);
			pollHandle = null;
		}
	});

	function requestStop() {
		pendingAction = 'stop';
	}

	function requestTerminate() {
		pendingAction = 'terminate';
	}

	async function confirmPendingAction() {
		if (!pendingAction || !row) return;
		const kind = pendingAction;
		acting = true;
		try {
			if (kind === 'stop') {
				const response = await stopProvisionedCompute(row.id);
				if (response.success && response.data) {
					row = response.data;
					toasts.success(`"${row.profile_name}" stopped`);
					onStopped();
				} else {
					toasts.error(response.message || 'Failed to stop compute');
				}
			} else {
				const response = await terminateProvisionedCompute(row.id);
				if (response.success) {
					toasts.success(`"${row.profile_name}" terminated`);
					onTerminated();
				} else {
					toasts.error(response.message || 'Failed to terminate compute');
				}
			}
		} catch (e: unknown) {
			toasts.error(getApiErrorMessage(e, kind === 'stop' ? 'Failed to stop compute' : 'Failed to terminate compute'));
		} finally {
			acting = false;
			pendingAction = null;
		}
	}
</script>

{#if !loading && row}
	<section class="rounded-lg border border-line bg-surface-1 shadow-raised">
		<div class="px-4 sm:px-5 py-3 border-b border-line flex items-center justify-between gap-2">
			<h3 class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted flex items-center gap-1.5">
				<Icon name="database" className="w-3.5 h-3.5" />
				Infrastructure
			</h3>
			<Badge variant={statusVariant[row.status] ?? 'neutral'} size="sm" dot class="uppercase">{row.status}</Badge>
		</div>
		<div class="px-4 sm:px-5 py-4 space-y-4">
			<dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-sm">
				<dt class="text-fg-subtle">Pod ID</dt>
				<dd class="font-mono tabular-nums text-fg-muted truncate min-w-0">{row.resource_ref ?? row.handle}</dd>

				<dt class="text-fg-subtle">Provider</dt>
				<dd class="font-mono text-fg-muted truncate min-w-0">{row.provider_id}</dd>
			</dl>
			<div class="flex items-center justify-end gap-2">
				<Button variant="secondary" size="sm" icon="pause" disabled={acting || row.status === 'stopped'} onclick={requestStop}>
					Stop
				</Button>
				<Button variant="danger" size="sm" icon="trash" disabled={acting} onclick={requestTerminate}>
					Terminate
				</Button>
			</div>
		</div>
	</section>

	<ConfirmModal
		isOpen={pendingAction !== null}
		title={pendingAction === 'terminate' ? 'Terminate compute' : 'Stop compute'}
		message={pendingAction === 'terminate'
			? `Terminate "${row.profile_name}"? This tears down the pod and removes its linked backend row. This cannot be undone.`
			: `Stop "${row.profile_name}"? This backend is disabled until it's re-provisioned. Models on its volume persist.`}
		variant={pendingAction === 'terminate' ? 'danger' : 'warning'}
		busy={acting}
		on:confirm={confirmPendingAction}
		on:cancel={() => (pendingAction = null)}
	/>
{/if}
