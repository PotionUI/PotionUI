<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import { Button, Input, Alert } from '$lib/components/ui';

	export let isOpen: boolean = false;
	/** 'rename' updates the current session in place; 'save-as' creates a new one. */
	export let mode: 'rename' | 'save-as' = 'rename';
	export let sessionName: string = '';
	export let nameError: string = '';
	export let error: string | null = null;
	export let isSaving: boolean = false;

	const dispatch = createEventDispatcher<{ close: void; confirm: void }>();

	$: title = mode === 'rename' ? 'Rename Session' : 'Save New Session';
	$: confirmLabel = mode === 'rename'
		? (isSaving ? 'Updating...' : 'Update')
		: (isSaving ? 'Saving...' : 'Save');

	function close() {
		dispatch('close');
	}

	function confirm() {
		dispatch('confirm');
	}
</script>

<BaseModal {isOpen} {title} size="sm" on:close={close}>
	<div class="p-6">
		<div class="mb-4">
			<label for="session-save-name" class="block text-sm font-medium text-fg-muted mb-2">Session Name</label>
			<Input
				id="session-save-name"
				type="text"
				bind:value={sessionName}
				placeholder="Enter session name"
				invalid={!!nameError}
			/>
			{#if nameError}
				<p class="mt-1 text-sm text-danger">{nameError}</p>
			{/if}
		</div>

		{#if error}
			<Alert variant="danger" class="mb-4">{error}</Alert>
		{/if}

		<div class="flex gap-2">
			<Button variant="secondary" class="flex-1" onclick={close}>Cancel</Button>
			<Button
				variant="primary"
				class="flex-1"
				disabled={!sessionName.trim() || isSaving}
				onclick={confirm}
			>
				{confirmLabel}
			</Button>
		</div>
	</div>
</BaseModal>
