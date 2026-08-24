<script lang="ts">
	import { historyStore } from '$lib/stores/history';
	import CreateTagModal from '$lib/components/modals/CreateTagModal.svelte';

	export let onClose: () => void;
</script>

<CreateTagModal
	{onClose}
	onCreate={(name) => historyStore.createTag(name)}
	description="Tags help you organize and filter your generations. You can add multiple tags to each generation."
/>
