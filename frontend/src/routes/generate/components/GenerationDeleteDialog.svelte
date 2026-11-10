<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { api } from '$lib/services/api';
	import { tabsStore } from '$lib/stores/tabs';
	import { toasts } from '$lib/stores/toast';
	import { getApiErrorMessage, logger } from '$lib/utils/logger';
	import { showDeleteError } from '$lib/elixirs/openBrew';
	import { deleteTabGeneration, type TabDeleteTarget } from '$lib/generation/deleteTabGeneration';
	import type { GenerationHistoryItem } from '$lib/types/history';
	import HistoryDeleteModal from '../../history/components/HistoryDeleteModal.svelte';

	let {
		target,
		unsubscribe,
		onDeleted,
		onClose
	}: {
		target: TabDeleteTarget;
		unsubscribe: (generationId: string) => void;
		onDeleted: (target: TabDeleteTarget) => void;
		onClose: () => void;
	} = $props();

	let generation = $state<GenerationHistoryItem | null>(null);
	const request = untrack(() => target);

	onMount(() => {
		let alive = true;
		api
			.getGenerationById(request.generationId, false, true)
			.then((response) => {
				if (!alive) return;
				if (response.success && response.data) generation = response.data as GenerationHistoryItem;
				else {
					toasts.error(response.message || 'This generation could not be found.');
					onClose();
				}
			})
			.catch((error) => {
				if (!alive) return;
				toasts.error(getApiErrorMessage(error, 'This generation could not be loaded.'));
				onClose();
			});
		return () => {
			alive = false;
		};
	});

	async function confirmDelete() {
		try {
			await deleteTabGeneration(request, {
				tabsStore,
				unsubscribe,
				cancel: (id) => api.cancelGeneration(id),
				remove: (id) => api.deleteGenerationHistory(id)
			});
			onDeleted(request);
		} catch (error) {
			logger.error('Failed to delete generation:', error);
			showDeleteError(error, 'Failed to delete generation');
		}
	}
</script>

{#if generation}
	<HistoryDeleteModal {generation} onCancel={onClose} onConfirm={confirmDelete} />
{/if}
