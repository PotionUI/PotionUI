<script lang="ts">
	import StudioSheet from './StudioSheet.svelte';
	import UnifiedAIChat from '$lib/components/UnifiedAIChat.svelte';

	export let onClose: () => void;
</script>

<StudioSheet
	maxHeight="100%"
	ariaLabel="AI chat"
	bodyClass="min-h-0 flex-1 overflow-hidden flex flex-col"
	on:close={onClose}
>
	<UnifiedAIChat {onClose} />
</StudioSheet>
