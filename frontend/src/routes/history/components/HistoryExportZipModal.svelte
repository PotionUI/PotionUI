<script lang="ts">
	import { untrack } from 'svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import ConfirmFooter from '$lib/components/modals/ConfirmFooter.svelte';
	import {
		createConfirmSettlementGate,
		getConfirmKeyboardAction,
		settleIfEligible
	} from '$lib/components/modals/confirmKeyboard';
	import Icon from '$lib/components/Icon.svelte';
	import { api } from '$lib/services/api/index';
	import { toasts } from '$lib/stores/toast';
	import { logger, getErrorMessage } from '$lib/utils/logger';
	import type { MediaToolModalProps } from '$lib/tools/tools';

	// Downloading reads the selection, it does not change it, so a finished
	// export closes rather than settling the tool as "data changed".
	let { context, onClose }: MediaToolModalProps = $props();

	let stripMetadata = $state(false);
	let exporting = $state(false);
	const settlementGate = createConfirmSettlementGate();

	let count = $derived(context.items.length);

	$effect(() => {
		context;
		untrack(() => settlementGate.reset());
	});

	async function download() {
		exporting = true;
		try {
			if (context.scope === 'library') {
				await api.exportLibraryItems(context.items.map((item) => item.id));
			} else {
				await api.exportGenerations(context.generationIds, stripMetadata);
			}
			toasts.success('Export started');
			onClose();
		} catch (error) {
			logger.error('Export failed:', getErrorMessage(error));
			toasts.error('Export failed');
			settlementGate.reset();
		} finally {
			exporting = false;
		}
	}

	function handleCancel() {
		settlementGate.settle(onClose);
	}

	function handleConfirm() {
		settleIfEligible(settlementGate, count > 0 && !exporting, download);
	}

	function handleKeydown(event: KeyboardEvent) {
		if (exporting) return;
		const { action, suppress } = getConfirmKeyboardAction(event);
		if (action === 'cancel') handleCancel();
		else if (action === 'confirm') handleConfirm();
		if (suppress) event.preventDefault();
	}
</script>

<svelte:window on:keydown|capture={handleKeydown} />

<BaseModal
	isOpen={true}
	title="Download .zip"
	size="md"
	closeable={!exporting}
	handleEscapeKey={false}
	on:close={handleCancel}
>
	<svelte:fragment slot="headerIcon">
		<Icon name="download" className="h-5 w-5 text-fg-muted" />
	</svelte:fragment>

	<div class="space-y-4 p-4 sm:p-6">
		<p class="text-sm text-fg-muted tabular-nums">
			{count} {context.scope === 'library' ? 'item' : 'generation'}{count === 1 ? '' : 's'} in the archive.
		</p>

		{#if context.scope === 'history'}
			<label class="flex items-center gap-2 text-sm text-fg-muted cursor-pointer">
				<input type="checkbox" bind:checked={stripMetadata} class="accent-accent" />
				Strip metadata
			</label>
		{/if}
	</div>

	<svelte:fragment slot="footer">
		<ConfirmFooter
			confirmLabel="Download"
			busy={exporting}
			confirmDisabled={count === 0}
			onCancel={handleCancel}
			onConfirm={handleConfirm}
		/>
	</svelte:fragment>
</BaseModal>
