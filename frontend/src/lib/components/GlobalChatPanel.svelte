<script lang="ts">
	import { fade, scale } from 'svelte/transition';
	import { chatPanelStore } from '$lib/stores/chatPanel';
	import UnifiedAIChat from '$lib/components/UnifiedAIChat.svelte';

	$: isOpen = $chatPanelStore.isOpen;

	function handleClose() {
		chatPanelStore.close();
	}

	function handleBackdropClick() {
		chatPanelStore.close();
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape' && isOpen) {
			chatPanelStore.close();
		}
	}

	const prefersReducedMotion =
		typeof window !== 'undefined' &&
		typeof window.matchMedia === 'function' &&
		window.matchMedia('(prefers-reduced-motion: reduce)').matches;
	const motionDuration = prefersReducedMotion ? 0 : 160;
</script>

<svelte:window on:keydown={handleKeydown} />

{#if isOpen}
	<!-- Backdrop -->
	<div
		class="fixed inset-0 bg-black/40 z-40"
		role="button"
		tabindex="0"
		aria-label="Close chat panel"
		on:click={handleBackdropClick}
		on:keydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleBackdropClick(); } }}
		transition:fade={{ duration: motionDuration }}
	></div>

	<!-- Floating shell: a window over the app, not a right-docked drawer —
	     geometry from the chat-rework brief (top-3/right-3/bottom-3, width
	     capped so it never spans edge-to-edge on a wide monitor). -->
	<div
		class="fixed top-3 right-3 bottom-3 z-50 flex flex-col overflow-hidden rounded-xl border border-line-strong bg-surface-1 shadow-overlay"
		style="width: min(1080px, calc(100vw - 76px));"
		role="dialog"
		aria-modal="true"
		aria-label="AI chat"
		transition:scale={{ duration: motionDuration, start: 0.98, opacity: 0 }}
	>
		<UnifiedAIChat onClose={handleClose} />
	</div>
{/if}
