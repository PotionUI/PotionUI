<script lang="ts">
	import { api } from '$lib/services/api/index';
	import { invokeBackendQuickAction } from '$lib/services/admin-api';
	import type { BackendQuickAction } from '$lib/services/admin-api';
	import { toasts } from '$lib/stores/toast';
	import { waitForHealthy } from '$lib/utils/healthPoll';
	import { Button, Spinner, Alert } from '$lib/components/ui';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';

	// Shared confirm -> running -> (waiting) -> done/error/timeout flow for a
	// backend's self-described quick action (Backend.quick_actions). Used by
	// both the admin panel's per-backend button row (BackendQuickActions.svelte)
	// and the core navbar quick-actions palette (QuickActions.svelte) so the
	// two surfaces behave identically instead of duplicating this state
	// machine.
	export let runningId: string | null = null;
	export let onDone: (() => void) | undefined = undefined;

	type Phase = 'confirm' | 'running' | 'waiting' | 'error' | 'done' | 'timeout';

	let modalOpen = false;
	let pendingAction: BackendQuickAction | null = null;
	let pendingBackendName = '';
	let phase: Phase = 'confirm';
	let errorMessage = '';
	// Raw `data` payload from a successful action's APIResponse, rendered by
	// resultSummaryFor() below. Schema-tolerant on purpose: the backend is an
	// open set of engines/plugins, none of which this component knows about.
	let resultData: Record<string, unknown> | null = null;

	/** One prose part (`plain`) or one number-formatted part (`num`, rendered
	 * mono + tabular-nums) that together read as a single sentence/line. */
	type SummaryPart = { plain: string } | { num: string };
	type ResultSummary = { lines: SummaryPart[][]; keys?: string[] };

	/** Describes what an action's result data means, tailored to the actions
	 * the native engine ships today, with a schema-tolerant fallback for
	 * anything else (future actions, plugins). */
	function resultSummaryFor(
		action: BackendQuickAction,
		data: Record<string, unknown> | null
	): ResultSummary {
		if (!data) return { lines: [[{ plain: 'Completed' }]] };

		if (action.id === 'clear-vram') {
			const offloaded = Number(data.offloaded_count ?? 0);
			const freedGb = Number(data.freed_gb ?? 0);
			if (offloaded === 0) {
				return {
					lines: [
						[{ plain: 'Nothing was GPU-resident — VRAM was already clear.' }],
						[
							{
								plain: "Models cached in RAM are unaffected; use 'Clear VRAM & Cache (RAM)' to drop those."
							}
						]
					]
				};
			}
			return {
				lines: [
					[
						{ plain: 'Offloaded ' },
						{ num: `${offloaded}` },
						{ plain: ` model${offloaded === 1 ? '' : 's'}, freed ` },
						{ num: `${freedGb.toFixed(2)} GB` },
						{ plain: ' of VRAM' }
					]
				]
			};
		}

		if (action.id === 'clear-cache') {
			const keys = Array.isArray(data.cache_keys_cleared)
				? (data.cache_keys_cleared as unknown[]).map(String)
				: [];
			return {
				lines: [
					[
						{ plain: 'Evicted ' },
						{ num: `${keys.length}` },
						{ plain: ` cached model${keys.length === 1 ? '' : 's'}, RAM returned to the OS` }
					]
				],
				keys: keys.length > 0 && keys.length <= 8 ? keys : undefined
			};
		}

		// Generic fallback: render whatever summary-shaped fields the response
		// has, schema-tolerant so plugin-provided actions still show something.
		const lines: SummaryPart[][] = [];
		if (typeof data.message === 'string' && data.message) lines.push([{ plain: data.message }]);
		if (typeof data.freed_gb === 'number') {
			lines.push([{ plain: 'Freed ' }, { num: `${(data.freed_gb as number).toFixed(2)} GB` }]);
		}
		if (typeof data.offloaded_count === 'number') {
			lines.push([{ plain: 'Offloaded ' }, { num: `${data.offloaded_count}` }, { plain: ' item(s)' }]);
		}
		return { lines: lines.length > 0 ? lines : [[{ plain: 'Completed' }]] };
	}

	let resultSummary: ResultSummary;
	$: resultSummary = pendingAction ? resultSummaryFor(pendingAction, resultData) : { lines: [] };

	/** One health check attempt. A short per-call timeout matters here: without
	 * it, a request stuck against a mid-restart connection can eat up to the
	 * client's full default timeout (30s) *per attempt*, which is what made a
	 * restart look permanently stuck rather than just slow. `validateStatus`
	 * lets us read the status directly instead of relying on axios throwing
	 * for non-2xx. */
	async function checkHealthOnce(): Promise<boolean> {
		try {
			const response = await api
				.getClient()
				.get('/health', { timeout: 3000, validateStatus: () => true });
			return response.status === 200;
		} catch {
			return false;
		}
	}

	async function waitForServerBack(): Promise<boolean> {
		return waitForHealthy({ check: checkHealthOnce });
	}

	function closeModal() {
		modalOpen = false;
		pendingAction = null;
		pendingBackendName = '';
		phase = 'confirm';
		errorMessage = '';
		resultData = null;
	}

	/** Entry point for callers: start the confirm/execute flow for one backend
	 * action. `backendName` is passed per-call (rather than as a prop) since a
	 * single modal instance may be shared across actions from different
	 * backends, e.g. in the navbar palette. */
	export function requestAction(action: BackendQuickAction, backendName = '') {
		if (runningId) return;

		if (action.confirm) {
			pendingAction = action;
			pendingBackendName = backendName;
			phase = 'confirm';
			errorMessage = '';
			modalOpen = true;
		} else {
			void execute(action, backendName);
		}
	}

	function retry() {
		if (pendingAction) void execute(pendingAction, pendingBackendName);
	}

	/** From the 'timeout' escape hatch: don't re-POST the action, just give the
	 * health poll another budget - the restart already happened, we just
	 * haven't observed it come back yet. */
	async function keepWaiting() {
		if (!pendingAction) return;
		runningId = pendingAction.id;
		phase = 'waiting';
		try {
			const recovered = await waitForServerBack();
			if (recovered) {
				toasts.success(`${pendingAction.label} complete`);
				closeModal();
			} else {
				phase = 'timeout';
			}
		} finally {
			runningId = null;
			onDone?.();
		}
	}

	function refreshPage() {
		window.location.reload();
	}

	async function execute(action: BackendQuickAction, backendName: string) {
		pendingAction = action;
		pendingBackendName = backendName;
		runningId = action.id;
		phase = 'running';
		errorMessage = '';

		try {
			if (action.poll_health_after) {
				try {
					await invokeBackendQuickAction(action);
				} catch {
					// The connection typically drops mid-response as the process
					// restarts - expected, not a failure.
				}
				phase = 'waiting';
				const recovered = await waitForServerBack();
				if (recovered) {
					toasts.success(`${action.label} complete`);
					closeModal();
				} else {
					// Budget exhausted without observing a healthy response. The
					// restart itself may well have succeeded - we just can't
					// confirm it - so don't silently report success: give the user
					// an explicit escape hatch instead of hanging forever.
					phase = 'timeout';
				}
			} else {
				const response = await invokeBackendQuickAction(action);
				if (response.success) {
					if (modalOpen) {
						resultData = (response.data as Record<string, unknown>) ?? null;
						phase = 'done';
					} else {
						toasts.success(response.message || `${action.label} complete`);
						closeModal();
					}
				} else {
					const message = response.message || `${action.label} failed`;
					if (modalOpen) {
						errorMessage = message;
						phase = 'error';
					} else {
						toasts.error(message);
					}
				}
			}
		} catch (e: any) {
			const message = e.response?.data?.message || e.message || `${action.label} failed`;
			if (modalOpen) {
				errorMessage = message;
				phase = 'error';
			} else {
				toasts.error(message);
			}
		} finally {
			runningId = null;
			onDone?.();
		}
	}

	$: canDismiss = phase === 'error' || phase === 'done' || phase === 'timeout';

	$: confirmMessage =
		pendingAction && pendingBackendName
			? `Backend: ${pendingBackendName}\n\n${pendingAction.confirm}`
			: (pendingAction?.confirm ?? '');
</script>

{#if pendingAction && phase === 'confirm'}
	<ConfirmModal
		isOpen={modalOpen}
		title={pendingAction.label}
		message={confirmMessage}
		variant={pendingAction.danger ? 'danger' : 'info'}
		on:confirm={retry}
		on:cancel={closeModal}
	/>
{:else}
<BaseModal isOpen={modalOpen} title="" size="md" hideCloseButton closeable={canDismiss} on:close={closeModal}>
	{#if pendingAction}
		<div class="p-7">
			<div class="flex items-start gap-4 mb-6">
				<div
					class="w-11 h-11 rounded-full flex items-center justify-center flex-shrink-0
						{phase === 'error'
							? 'bg-danger/10'
							: phase === 'done'
								? 'bg-success/10'
								: phase === 'timeout'
									? 'bg-warning/10'
									: pendingAction.danger
										? 'bg-danger/10'
										: 'bg-info/10'}"
				>
					{#if phase === 'running' || phase === 'waiting'}
						<Spinner size="sm" />
					{:else if phase === 'done'}
						<Icon name="check" className="w-5 h-5 text-success" strokeWidth={1.5} />
					{:else if phase === 'timeout'}
						<Icon name="warning" className="w-5 h-5 text-warning" strokeWidth={1.5} />
					{:else}
						<Icon
							name={phase === 'error' || pendingAction.danger ? 'warning' : 'info'}
							className="w-5 h-5 {phase === 'error' || pendingAction.danger ? 'text-danger' : 'text-info'}"
							strokeWidth={1.5}
						/>
					{/if}
				</div>
				<div class="min-w-0 pt-0.5">
					<h3 class="text-base font-semibold text-fg mb-1.5 break-words">{pendingAction.label}</h3>
					{#if pendingBackendName}
						<p class="text-xs text-fg-subtle mb-1.5">
							Backend: <span class="font-medium text-fg-muted">{pendingBackendName}</span>
						</p>
					{/if}

					{#if phase === 'running'}
						<p class="text-sm leading-relaxed text-fg-muted">Working…</p>
					{:else if phase === 'waiting'}
						<p class="text-sm leading-relaxed text-fg-muted">
							Waiting for the backend to come back online. This can take a minute — don't
							navigate away.
						</p>
					{:else if phase === 'error'}
						<p class="text-sm leading-relaxed text-fg-muted">{pendingAction.label} failed.</p>
					{:else if phase === 'timeout'}
						<p class="text-sm leading-relaxed text-fg-muted">
							Still waiting to confirm the backend came back — this is taking longer than
							expected. It may already be done; refresh the page to check, or keep waiting.
						</p>
					{/if}
				</div>
			</div>

			{#if phase === 'error' && errorMessage}
				<Alert variant="danger" density="compact" class="mb-5">
					<span class="break-words">{errorMessage}</span>
				</Alert>
			{/if}

			{#if phase === 'done'}
				<div class="rounded-lg bg-surface-2 border border-line px-3 py-2.5 mb-5 space-y-1">
					{#each resultSummary.lines as line}
						<p class="text-sm text-fg-muted leading-snug">
							{#each line as part}
								{#if 'num' in part}
									<span class="font-mono tabular-nums text-fg">{part.num}</span>
								{:else}
									{part.plain}
								{/if}
							{/each}
						</p>
					{/each}
					{#if resultSummary.keys}
						<ul class="mt-1.5 space-y-0.5">
							{#each resultSummary.keys as key}
								<li class="text-xs text-fg-subtle font-mono truncate">{key}</li>
							{/each}
						</ul>
					{/if}
				</div>
			{/if}

			<div class="flex items-center justify-end gap-3">
				{#if phase === 'running' || phase === 'waiting'}
					<Button variant="secondary" disabled loading>
						{phase === 'waiting' ? 'Waiting…' : 'Working…'}
					</Button>
				{:else if phase === 'error'}
					<Button variant="secondary" onclick={closeModal}>Close</Button>
					<Button variant={pendingAction.danger ? 'danger' : 'primary'} onclick={retry}>
						Retry
					</Button>
				{:else if phase === 'done'}
					<Button variant="secondary" onclick={closeModal}>Close</Button>
				{:else if phase === 'timeout'}
					<Button variant="secondary" onclick={closeModal}>Close</Button>
					<Button variant="secondary" onclick={keepWaiting}>Keep waiting</Button>
					<Button variant="primary" onclick={refreshPage}>Refresh page</Button>
				{/if}
			</div>
		</div>
	{/if}
</BaseModal>
{/if}
