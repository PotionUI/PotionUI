<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { historyStore } from '$lib/stores/history';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';

	export let onClose: () => void;

	$: currentState = $historyStore;

	let bulkDeleting = false;
	// Remounts ConfirmModal (fresh settlement gate) after a failed attempt so
	// retry via keyboard or click still works while the modal stays open.
	let attempt = 0;

	async function confirmBulkDelete() {
		bulkDeleting = true;
		try {
			await historyStore.bulkDeleteGenerations();
			onClose();
		} catch (error) {
			logger.error('Failed to bulk delete generations:', error);
			attempt += 1;
		} finally {
			bulkDeleting = false;
		}
	}
</script>

{#key attempt}
	<ConfirmModal
		isOpen={true}
		title="Delete Multiple Generations"
		message={`Are you sure you want to delete ${currentState.selectedGenerationIds.length} generation(s) and all their files? This action cannot be undone.\n\nThis will permanently delete all selected generations and their files from your disk.`}
		variant="danger"
		busy={bulkDeleting}
		on:confirm={confirmBulkDelete}
		on:cancel={onClose}
	/>
{/key}
