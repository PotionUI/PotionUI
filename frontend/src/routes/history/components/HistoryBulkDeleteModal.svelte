<script lang="ts">
	import { get } from 'svelte/store';
	import { logger } from '$lib/utils/logger';
	import { showDeleteError } from '$lib/elixirs/openBrew';
	import { toasts } from '$lib/stores/toast';
	import { historyStore } from '$lib/stores/history';
	import { bulkDeleteMessage, planBulkDelete } from '$lib/utils/historyDeleteCopy';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';

	export let onClose: () => void;

	$: currentState = $historyStore;

	const initial = get(historyStore);
	const message = bulkDeleteMessage(
		planBulkDelete(initial.selectedGenerationIds, initial.generations)
	);

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
			showDeleteError(error);
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
		{message}
		variant="danger"
		confirmLabel="Delete"
		busy={bulkDeleting}
		on:confirm={confirmBulkDelete}
		on:cancel={onClose}
	/>
{/key}
