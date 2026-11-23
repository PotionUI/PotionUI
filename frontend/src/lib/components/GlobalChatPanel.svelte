<script lang="ts">
	import { fade, scale } from 'svelte/transition';
	import { hasOpenDismissLayer } from '$lib/actions/layerStack';
	import { chatPanelStore } from '$lib/stores/chatPanel';
	import '$lib/styles/chat-concept.css';
	import { loadHistoryRailCollapsed } from '$lib/utils/chatHistoryRail';

	$: isOpen = $chatPanelStore.isOpen;

	let ChatComponent: any = null;
	$: if (isOpen && !ChatComponent) {
		void import('$lib/components/UnifiedAIChat.svelte').then((module) => (ChatComponent = module.default));
	}

	function handleClose() {
		chatPanelStore.close();
	}

	function handleBackdropClick() {
		chatPanelStore.close();
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape' && isOpen && !hasOpenDismissLayer()) {
			chatPanelStore.close();
		}
	}

	const prefersReducedMotion =
		typeof window !== 'undefined' &&
		typeof window.matchMedia === 'function' &&
		window.matchMedia('(prefers-reduced-motion: reduce)').matches;
	const motionDuration = prefersReducedMotion ? 0 : 160;

	// Bound from UnifiedAIChat: the shell's own `&.rail-collapsed .history-rail`
	// CSS rule needs the class on this ancestor, not on .history-rail itself.
	// Seeded from the persisted preference: a bound prop with a parent-side
	// initial value overrides the child's own default on every mount, which is
	// what kept resetting the rail (maintainer report 09-07).
	let railCollapsed = loadHistoryRailCollapsed();
</script>

<svelte:window on:keydown={handleKeydown} />

{#if isOpen}
	<!-- Backdrop (the mock's page-dimmer, which is decoration-only in the
	     standalone prototype and has no equivalent token here). -->
	<div
		class="fixed inset-0 bg-black/40 z-40"
		role="button"
		tabindex="0"
		aria-label="Close chat panel"
		on:click={handleBackdropClick}
		on:keydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleBackdropClick(); } }}
		transition:fade={{ duration: motionDuration }}
	></div>

	<!-- The floating window itself: geometry, grid, border, radius and shadow
	     all come from .chat-shell's own CSS (chat-concept.css), ported
	     verbatim from the mock — nothing here overrides it. -->
	<div
		class="chat-shell floating-panel"
		class:rail-collapsed={railCollapsed}
		role="dialog"
		aria-modal="true"
		aria-label="AI chat"
		transition:scale={{ duration: motionDuration, start: 0.98, opacity: 0 }}
	>
		{#if ChatComponent}
			<svelte:component this={ChatComponent} onClose={handleClose} bind:historyRailCollapsed={railCollapsed} />
		{/if}
	</div>
{/if}
