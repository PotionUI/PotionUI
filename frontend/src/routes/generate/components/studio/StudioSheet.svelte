<script lang="ts">
	import { onMount, createEventDispatcher } from 'svelte';
	import { fade, fly } from 'svelte/transition';
	import portal from '$lib/actions/portal';

	// Bottom-sheet primitive for the mobile Studio shell. Callers mount/unmount
	// this via `{#if openSheet === '...'}` rather than an internal `open` prop —
	// closing is "the parent stops rendering me", which lets Svelte's outro
	// transition play on the elements below before the component is destroyed.
	export let maxHeight = '88%';
	export let ariaLabel: string;
	// Chat content manages its own internal scroll (message list scrolls,
	// composer stays pinned) — the default scrollable/padded body would fight
	// it with a second scroll container and squeeze its width.
	export let bodyClass = 'min-h-0 flex-1 overflow-y-auto px-4';

	const dispatch = createEventDispatcher<{ close: void }>();

	function close() {
		dispatch('close');
	}

	function handleBackdropClick(e: MouseEvent) {
		if (e.target === e.currentTarget) close();
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') close();
	}

	const prefersReducedMotion =
		typeof window !== 'undefined' &&
		typeof window.matchMedia === 'function' &&
		window.matchMedia('(prefers-reduced-motion: reduce)').matches;
	const duration = prefersReducedMotion ? 0 : 220;

	// Document-level listener + body scroll lock, added on mount and torn down
	// on destroy explicitly (the portal-catcher trap: a listener left behind
	// after this sheet is destroyed keeps firing against a detached instance).
	onMount(() => {
		document.addEventListener('keydown', handleKeydown);
		const previousOverflow = document.body.style.overflow;
		document.body.style.overflow = 'hidden';
		return () => {
			document.removeEventListener('keydown', handleKeydown);
			document.body.style.overflow = previousOverflow;
		};
	});
</script>

<div
	class="fixed inset-0 z-[60] flex items-end bg-black/50"
	use:portal
	on:click={handleBackdropClick}
	role="presentation"
	transition:fade={{ duration }}
>
	<div
		class="flex w-full flex-col overflow-hidden rounded-t-xl border-t border-line-strong bg-surface-1 shadow-overlay"
		style="max-height: {maxHeight};"
		role="dialog"
		aria-modal="true"
		aria-label={ariaLabel}
		transition:fly={{ y: 48, duration }}
	>
		<div class="flex-shrink-0 pt-2">
			<div class="mx-auto h-1 w-9 rounded-full bg-line-strong"></div>
		</div>
		{#if $$slots.header}
			<div class="flex-shrink-0 px-4 pt-2">
				<slot name="header" />
			</div>
		{/if}
		<div class={bodyClass}>
			<slot />
		</div>
		{#if $$slots.footer}
			<div class="flex-shrink-0 border-t border-line px-4 py-3">
				<slot name="footer" />
			</div>
		{/if}
	</div>
</div>
