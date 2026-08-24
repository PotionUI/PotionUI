<script lang="ts">
	import { onDestroy } from 'svelte';
	import ConfirmModal from './ConfirmModal.svelte';
	import { activeConfirm, settleConfirm, cancelAllConfirms } from '$lib/stores/confirm';

	$: request = $activeConfirm;
	$: requestId = request?.id ?? -1;

	onDestroy(cancelAllConfirms);
</script>

{#if request}
	{#key request.id}
		<ConfirmModal
			isOpen={true}
			title={request.title ?? 'Confirm'}
			message={request.message}
			variant={request.variant ?? 'warning'}
			on:confirm={() => settleConfirm(requestId, true)}
			on:cancel={() => settleConfirm(requestId, false)}
		/>
	{/key}
{/if}
