<script lang="ts">
	import type { GenerationHistoryItem } from '$lib/types/history';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';

	export let generation: GenerationHistoryItem;
	export let onCancel: () => void;
	export let onConfirm: () => void;

	// Remounts ConfirmModal (fresh settlement gate) per attempt so a failed
	// delete that leaves the modal open can still be retried via keyboard.
	let attempt = 0;
	function handleConfirm() {
		attempt += 1;
		onConfirm();
	}

	$: message =
		generation.files.length > 0
			? `Are you sure you want to delete this generation and all its files? This action cannot be undone.\n\nThis will permanently delete ${generation.files.length} generated file(s) from your disk.`
			: 'Are you sure you want to delete this generation and all its files? This action cannot be undone.';
</script>

{#key attempt}
	<ConfirmModal
		isOpen={true}
		title="Delete Generation"
		{message}
		variant="danger"
		on:confirm={handleConfirm}
		on:cancel={onCancel}
	/>
{/key}
