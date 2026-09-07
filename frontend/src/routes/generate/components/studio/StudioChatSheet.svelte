<script lang="ts">
	import StudioSheet from './StudioSheet.svelte';
	import UnifiedAIChat from '$lib/components/UnifiedAIChat.svelte';
	import { loadHistoryRailCollapsed } from '$lib/utils/chatHistoryRail';

	export let onClose: () => void;

	// Seeded from the persisted preference: a bound prop with a parent-side
	// initial value overrides the child's own default on every mount, which is
	// what kept resetting the rail (maintainer report 09-07).
	let railCollapsed = loadHistoryRailCollapsed();
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
