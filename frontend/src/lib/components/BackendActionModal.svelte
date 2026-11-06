<script lang="ts">
	import { api } from '$lib/services/api/index';
	import { getApiErrorMessage } from '$lib/utils/logger';
	import { invokeBackendQuickAction } from '$lib/services/admin-api';
	import type { BackendQuickAction } from '$lib/services/admin-api';
	import { toasts } from '$lib/stores/toast';
	import { serverIsReady, waitForHealthy } from '$lib/utils/healthPoll';
	import { Button, Spinner, Alert } from '$lib/components/ui';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import ConfirmDialog from '$lib/components/modals/ConfirmDialog.svelte';
	import { resultSummaryFor, type ResultSummary } from '$lib/components/backendActionSummary';

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
	let resultData: Record<string, unknown> | null = null;

	let resultSummary: ResultSummary;
	$: resultSummary = pendingAction ? resultSummaryFor(pendingAction, resultData) : { lines: [] };

	async function waitForServerBack(): Promise<boolean> {
		return waitForHealthy({ check: () => serverIsReady(api.getClient()) });
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
			const message = getApiErrorMessage(e, `${action.label} failed`);
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

	$: interactivePhase = phase === 'error' || phase === 'timeout';
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
<ConfirmDialog
	isOpen={modalOpen}
	size="md"
	hideCloseButton
	closeable={canDismiss}
	resetKey={phase}
	cancelLabel="Close"
	confirmLabel={phase === 'timeout' ? 'Refresh page' : 'Retry'}
	confirmVariant={phase === 'error' && pendingAction?.danger ? 'danger' : 'primary'}
	confirmDisabled={!interactivePhase}
	onConfirm={phase === 'timeout' ? refreshPage : retry}
	onCancel={closeModal}
	footerContent={interactivePhase ? undefined : idleFooter}
	leftActions={phase === 'timeout' ? keepWaitingAction : undefined}
>
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
		</div>
	{/if}
</ConfirmDialog>

{#snippet idleFooter()}
	<div class="flex items-center justify-end gap-3 px-6 py-4">
		{#if phase === 'done'}
			<Button variant="secondary" onclick={closeModal}>Close</Button>
		{:else}
			<Button variant="secondary" disabled loading>
				{phase === 'waiting' ? 'Waiting…' : 'Working…'}
			</Button>
		{/if}
	</div>
{/snippet}

{#snippet keepWaitingAction()}
	<Button variant="secondary" size="sm" onclick={keepWaiting}>Keep waiting</Button>
{/snippet}
{/if}
