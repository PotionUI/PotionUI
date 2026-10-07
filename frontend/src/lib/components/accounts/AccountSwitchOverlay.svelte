<script lang="ts">
	import { fade } from 'svelte/transition';
	import { cubicOut } from 'svelte/easing';
	import Icon from '$lib/components/Icon.svelte';
	import { Spinner } from '$lib/components/ui';
	import { switchOverlay } from '$lib/stores/accounts';

	let state = $derived($switchOverlay);
	let progress = $derived(state?.step === 'reloading' ? 70 : 35);
</script>

{#if state}
	<div
		class="fixed inset-0 z-toast flex flex-col items-center justify-center gap-3 bg-canvas px-6 text-center"
		role="status"
		aria-live="polite"
		data-testid="account-switch-overlay"
		data-kind={state.kind}
		transition:fade={{ duration: 150, easing: cubicOut }}
	>
		<Spinner size="lg" />
		{#if state.kind === 'initiator'}
			<p class="mt-2 text-lg text-fg">Switching to {state.username}</p>
			{#if state.role}
				<p class="font-mono text-xs uppercase tracking-wide text-fg-subtle">
					{state.username} &middot; {state.role}
				</p>
			{/if}
			<div class="mt-2 h-0.5 w-[280px] max-w-full overflow-hidden rounded bg-line">
				<div class="h-full bg-signal transition-[width] duration-150 ease-out" style="width: {progress}%"></div>
			</div>
			<ul class="mt-2 flex flex-col gap-1 text-left font-mono text-xs">
				<li class="flex items-center gap-2 text-success">
					<Icon name="check" className="h-3.5 w-3.5" strokeWidth={1.5} />
					Session token verified
				</li>
				<li class="flex items-center gap-2 text-fg">
					<span class="inline-block h-3.5 w-3.5 animate-spin rounded-full border border-line-strong border-t-fg"></span>
					Reloading your workspace
				</li>
			</ul>
		{:else}
			<p class="mt-2 text-lg text-fg">Account changed in another tab</p>
			<p class="text-sm text-fg-subtle">
				{#if state.username}
					Reloading as <span class="text-fg-muted">{state.username}</span>
				{:else}
					Reloading
				{/if}
			</p>
		{/if}
	</div>
{/if}
