<script lang="ts">
	import { goto } from '$app/navigation';
	import { Alert, Button } from '$lib/components/ui';
	import { generateGate, refusalDismissed, serverRefusal } from '../store';
	import { formatCountdown } from '../countdown';

	let gate = $derived($generateGate);
	let visible = $derived(!!$serverRefusal && !$refusalDismissed && gate !== null);
	let countdown = $derived(gate?.remainingMs != null ? formatCountdown(gate.remainingMs) : null);
</script>

{#if visible && gate}
	<div
		class="fixed right-4 bottom-[calc(var(--dock-height,96px)+12px)] z-overlay w-[min(26rem,calc(100vw-2rem))]"
		data-limit-notice
	>
		<Alert variant="danger" icon title={gate.title}>
			<p class="text-sm text-fg-muted">{gate.detail}</p>
			{#if countdown}
				<p class="mt-1 text-xs text-fg-subtle">
					Resets in <span class="font-mono tabular-nums">{countdown}</span>
				</p>
			{/if}
			<p class="mt-1 text-xs text-fg-subtle">Jobs already running will finish.</p>
			{#snippet actions()}
				<Button variant="ghost" size="sm" onclick={() => refusalDismissed.set(true)}>Dismiss</Button>
				{#if gate.canFreeUp}
					<Button variant="secondary" size="sm" onclick={() => goto('/history')}>Free up space</Button>
					<Button variant="secondary" size="sm" onclick={() => goto('/library')}>Library</Button>
				{:else}
					<Button variant="secondary" size="sm" onclick={() => goto('/settings#plan')}>See my plan</Button>
				{/if}
			{/snippet}
		</Alert>
	</div>
{/if}
