<script lang="ts">
	import '$lib/styles/chat-concept.css';
	import StudioSheet from '$lib/components/StudioSheet.svelte';
	import UnifiedAIChat from '$lib/components/UnifiedAIChat.svelte';
	import { chatPanelStore } from '$lib/stores/chatPanel';
	import { loadHistoryRailCollapsed } from '$lib/utils/chatHistoryRail';

	let railCollapsed = loadHistoryRailCollapsed();

	function close() {
		chatPanelStore.close();
	}
</script>

<StudioSheet
	maxHeight="100%"
	ariaLabel="AI chat"
	bodyClass="min-h-0 flex-1 overflow-hidden flex flex-col"
	on:close={close}
>
	<div class="chat-shell embedded" class:rail-collapsed={railCollapsed}>
		<UnifiedAIChat onClose={close} bind:historyRailCollapsed={railCollapsed} />
	</div>
</StudioSheet>
