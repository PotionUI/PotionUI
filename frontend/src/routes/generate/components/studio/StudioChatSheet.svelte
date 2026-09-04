<script lang="ts">
	import StudioSheet from './StudioSheet.svelte';
	import UnifiedAIChat from '$lib/components/UnifiedAIChat.svelte';

	export let onClose: () => void;

	let railCollapsed = false;
</script>

<StudioSheet
	maxHeight="100%"
	ariaLabel="AI chat"
	bodyClass="min-h-0 flex-1 overflow-hidden flex flex-col"
	on:close={onClose}
>
	<!-- .chat-shell (chat-concept.css) provides every class UnifiedAIChat
	     renders (.history-rail, .conversation, …); .embedded neutralizes the
	     desktop shell's own fixed positioning since StudioSheet already
	     places this — a state the standalone prototype never shows. -->
	<div class="chat-shell embedded" class:rail-collapsed={railCollapsed}>
		<UnifiedAIChat {onClose} bind:historyRailCollapsed={railCollapsed} />
	</div>
</StudioSheet>
