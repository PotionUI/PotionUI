<script lang="ts">
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import ConfirmFooter from '$lib/components/modals/ConfirmFooter.svelte';
	import DetectRootForm from './DetectRootForm.svelte';
	import type { ModelRootsState } from './state.svelte';
	import type { ModelRoot } from '$lib/services/api/models';

	let {
		isOpen,
		rootsState,
		pathStyle,
		initialPath = '',
		onClose,
		onCreated
	}: {
		isOpen: boolean;
		rootsState: ModelRootsState;
		pathStyle: 'windows' | 'posix' | undefined;
		initialPath?: string;
		onClose: () => void;
		onCreated: (root: ModelRoot) => void;
	} = $props();

	let submit = $state<(() => void) | null>(null);
	let canSubmit = $state(false);
	let busy = $state(false);

	function handleCreated(root: ModelRoot) {
		onCreated(root);
		onClose();
	}
</script>

<BaseModal {isOpen} title="Add a model folder" size="md" on:close={onClose}>
	{#if isOpen}
		{#key initialPath}
			<div class="px-6 py-5">
				<DetectRootForm
					{rootsState}
					{pathStyle}
					{initialPath}
					onCreated={handleCreated}
					showActions={false}
					bind:submitRef={submit}
					bind:canSubmitRef={canSubmit}
					bind:busyRef={busy}
				/>
			</div>
		{/key}
	{/if}

	<svelte:fragment slot="footer">
		<ConfirmFooter
			confirmLabel="Add folder"
			confirmDisabled={!canSubmit}
			{busy}
			onCancel={onClose}
			onConfirm={() => submit?.()}
		/>
	</svelte:fragment>
</BaseModal>
