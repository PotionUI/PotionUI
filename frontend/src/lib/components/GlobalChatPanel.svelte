<script lang="ts">
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
</script>

<svelte:window on:keydown={handleKeydown} />

{#if isOpen}
	<!-- Backdrop -->
	<div
		class="fixed inset-0 bg-black/40 z-40 transition-opacity"
		role="button"
		tabindex="0"
		aria-label="Close chat panel"
		on:click={handleBackdropClick}
		on:keydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleBackdropClick(); } }}
	></div>
{/if}

<!-- Slide-over panel -->
<div
	class="fixed top-0 right-0 bottom-0 z-50 w-full md:w-[1000px] md:max-w-[90vw] bg-surface-1 border-l border-line-strong/50 shadow-overlay flex flex-col transition-transform duration-300 ease-in-out"
	class:translate-x-0={isOpen}
	class:translate-x-full={!isOpen}
>
	{#if isOpen}
		<UnifiedAIChat onClose={handleClose} />
	{/if}
</div>
