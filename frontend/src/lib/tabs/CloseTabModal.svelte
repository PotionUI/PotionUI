<script lang="ts">
	import { onDestroy } from 'svelte';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
	import { tabsStore } from '$lib/stores/tabs';
	import {
		pendingTabClose,
		confirmCloseTab,
		cancelCloseTab,
		describeTabClose
	} from './closeConfirm';

	$: tab = $pendingTabClose ? $tabsStore.tabs.find((t) => t.id === $pendingTabClose) : undefined;
	$: copy = tab ? describeTabClose(tab) : null;

	onDestroy(cancelCloseTab);
</script>

<ConfirmModal
	isOpen={!!tab}
	title={copy?.title ?? 'Close tab?'}
	message={copy?.message ?? ''}
	variant="danger"
	on:confirm={confirmCloseTab}
	on:cancel={cancelCloseTab}
/>
