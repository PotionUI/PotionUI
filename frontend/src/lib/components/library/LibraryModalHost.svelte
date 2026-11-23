<script lang="ts">
	import { pushState } from '$app/navigation';
	import { page } from '$app/state';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
	import { closeLibraryResultModal, libraryModalRequest } from '$lib/library/landing';
	import { libraryItemDisplayName } from '$lib/library/libraryItemMeta';
	import type { LibraryItem } from '$lib/services/api/library';
	import { libraryStore } from '$lib/stores/library';
	import { toasts } from '$lib/stores/toast';
	import { logger } from '$lib/utils/logger';
	import { returnToDetails } from '$lib/tools/detailsReturn';

	let request = $derived($libraryModalRequest);
	let onLibraryPage = $derived(page.url.pathname.startsWith('/library'));
	let item = $derived(request ? ($libraryStore.selectedItem ?? request.item) : null);
	let pushedFor = $state<string | null>(null);
	let stateSeen = $state(false);
	let itemToDelete = $state<LibraryItem | null>(null);
	let ItemModal = $state<any>(null);

	$effect(() => {
		if (request && !ItemModal) {
			import('$lib/components/library/LibraryItemModal.svelte')
				.then((module) => (ItemModal = module.default))
				.catch((error) => {
					logger.error('Failed to load the library item:', error);
					toasts.error('Could not open that item. Try again.');
					dismiss();
				});
		}
	});

	$effect(() => {
		if (!request || onLibraryPage) return;
		const id = request.item.id;
		if (pushedFor === id) return;
		pushedFor = id;
		libraryStore.setSelectedItem(request.item);
		pushState('', { libraryModal: id });
	});

	$effect(() => {
		if (request && pushedFor && page.state.libraryModal === pushedFor) stateSeen = true;
	});

	$effect(() => {
		if (request && pushedFor && stateSeen && !page.state.libraryModal) finish();
	});

	function finish() {
		const source = request?.source ?? null;
		pushedFor = null;
		stateSeen = false;
		closeLibraryResultModal();
		libraryStore.setSelectedItem(null);
		if (source) returnToDetails(source);
	}

	function dismiss() {
		if (page.state.libraryModal) history.back();
		else finish();
	}

	async function confirmDelete() {
		const target = itemToDelete;
		if (!target) return;
		itemToDelete = null;
		const response = await libraryStore.deleteItem(target.id);
		if (response.success) dismiss();
		else toasts.error('Could not delete that item');
	}
</script>

{#if request && item && !onLibraryPage && ItemModal}
	<ItemModal
		{item}
		generationLink={request.source}
		onBackToGeneration={dismiss}
		onClose={dismiss}
		onDeleteRequest={(target: LibraryItem) => (itemToDelete = target)}
	/>
{/if}

{#if itemToDelete}
	<ConfirmModal
		isOpen={true}
		title="Delete library item"
		message={`Delete "${libraryItemDisplayName(itemToDelete)}" from your library? The file is removed from disk and this cannot be undone.`}
		variant="danger"
		confirmLabel="Delete"
		on:confirm={confirmDelete}
		on:cancel={() => (itemToDelete = null)}
	/>
{/if}
