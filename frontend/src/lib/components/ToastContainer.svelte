<script lang="ts">
	import { fly, fade } from 'svelte/transition';
	import { toasts } from '$lib/stores/toast';
	import type { Toast } from '$lib/stores/toast';

	function getColors(type: Toast['type']) {
		switch (type) {
			case 'success':
				return 'bg-success/15 border border-success/30 text-success';
			case 'error':
				return 'bg-danger/15 border border-danger/30 text-danger';
			case 'warning':
				return 'bg-warning/15 border border-warning/30 text-warning';
			case 'info':
			default:
				return 'bg-surface-2/90 border border-line-strong/50 text-fg';
		}
	}
</script>

<!--
	Top-right stack (z above the notifications panel at z-9991 so toasts stay
	readable when the panel is open). `top-6` clears the header bar; items stack
	downward and enter from the top-right so the motion never feels inverted.
-->
<div class="fixed top-6 right-6 z-[9998] flex flex-col gap-2 pointer-events-none">
	{#each $toasts as toast (toast.id)}
		<div
			class="pointer-events-auto max-w-sm w-full rounded-lg px-4 py-3 flex items-start gap-3 shadow-lg backdrop-blur-sm {getColors(toast.type)}"
			in:fly={{ y: -24, x: 24, duration: 250 }}
			out:fade={{ duration: 200 }}
		>
			<!-- Icon -->
			<div class="flex-shrink-0 mt-0.5">
				{#if toast.type === 'success'}
					<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
					</svg>
				{:else if toast.type === 'error'}
					<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />
					</svg>
				{:else if toast.type === 'warning'}
					<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01M5.07 19h13.86a2 2 0 001.74-2.99L13.73 4a2 2 0 00-3.46 0L3.33 16.01A2 2 0 005.07 19z" />
					</svg>
				{:else}
					<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
					</svg>
				{/if}
			</div>

			<!-- Message -->
			<div class="flex-1 min-w-0">
				{#if toast.title}
					<p class="text-sm font-semibold leading-snug">{toast.title}</p>
				{/if}
				<p class="text-sm leading-snug {toast.title ? 'text-fg-muted' : ''}">{toast.message}</p>
			</div>

			<!-- Close button -->
			<button
				class="flex-shrink-0 opacity-60 hover:opacity-100 transition-opacity -mt-0.5 -mr-1"
				on:click={() => toasts.remove(toast.id)}
				aria-label="Dismiss notification"
			>
				<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
				</svg>
			</button>
		</div>
	{/each}
</div>
