<script lang="ts">
	import { chatPanelStore } from '$lib/stores/chatPanel';

	export let currentContent: string;

	$: hasContent = currentContent && currentContent.trim().length > 0;

	function handleClick() {
		chatPanelStore.open();
	}
</script>

{#if hasContent}
	<button
		class="inline-flex items-center gap-1 px-2 py-1 text-xs text-fg-muted hover:text-fg hover:bg-surface-3 rounded transition-colors"
		title="Open AI Chat"
		on:click={handleClick}
	>
		<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
			<path
				stroke-linecap="round"
				stroke-linejoin="round"
				stroke-width="2"
				d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z"
			/>
		</svg>
		AI Assist
	</button>
{/if}
