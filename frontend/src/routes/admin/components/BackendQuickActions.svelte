<script lang="ts">
	import type { BackendQuickAction } from '$lib/services/admin-api';
	import { Spinner } from '$lib/components/ui';
	import BackendActionModal from '$lib/components/BackendActionModal.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';

	// Rendered purely from what the backend told us about itself
	// (Backend.quick_actions) - this component never assumes which engine it's
	// looking at or what any given action does. The confirm/running/done
	// state machine itself lives in BackendActionModal.svelte, shared with the
	// navbar quick-actions palette so both surfaces behave identically.
	//
	export let actions: BackendQuickAction[] = [];
	export let backendName: string = '';
	export let onDone: (() => void) | undefined = undefined;

	let runningId: string | null = null;
	let actionModal: BackendActionModal;

	function requestAction(action: BackendQuickAction) {
		actionModal.requestAction(action, backendName);
	}
</script>

{#each actions as action (action.id)}
	<Tooltip text={runningId === action.id ? (action.poll_health_after ? 'Restarting…' : 'Working…') : action.label}>
		<button
			type="button"
			class="inline-flex items-center justify-center min-w-8 min-h-8 p-1.5 rounded transition-colors duration-100 touch-manipulation disabled:opacity-50 disabled:cursor-not-allowed text-fg-muted hover:text-fg hover:bg-surface-3/50"
			disabled={runningId !== null}
			aria-label={action.label}
			onclick={() => requestAction(action)}
		>
			{#if runningId === action.id}
				<Spinner size="sm" />
			{:else}
				<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d={action.icon} />
				</svg>
			{/if}
		</button>
	</Tooltip>
{/each}

<BackendActionModal bind:this={actionModal} bind:runningId {onDone} />
