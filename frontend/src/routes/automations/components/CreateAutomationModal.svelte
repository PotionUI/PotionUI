<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import { Button, Input } from '$lib/components/ui';
	import { api } from '$lib/services/api';
	import { toasts } from '$lib/stores/toast';
	import type { Automation } from '$lib/types/automations';

	export let isOpen = false;

	const dispatch = createEventDispatcher<{ close: void; created: Automation }>();

	let name = '';
	let description = '';
	let creating = false;

	function resetAndClose() {
		name = '';
		description = '';
		dispatch('close');
	}

	function handleClose() {
		if (creating) return;
		resetAndClose();
	}

	async function handleCreate() {
		if (!name.trim()) return;
		creating = true;
		try {
			const response = await api.createAutomation({
				name: name.trim(),
				description: description.trim() || undefined,
				graph: { nodes: [], edges: [] }
			});
			if (response.success && response.data) {
				dispatch('created', response.data);
				// Close and reset directly — handleClose()'s `creating` guard (meant to
				// block Cancel/backdrop dismissal mid-request) would otherwise no-op
				// here since `creating` is still true until the `finally` below runs.
				resetAndClose();
			} else {
				toasts.error(response.error || 'Failed to create automation');
			}
		} catch {
			toasts.error('Failed to create automation');
		} finally {
			creating = false;
		}
	}
</script>

<BaseModal {isOpen} title="New automation" size="sm" on:close={handleClose}>
	<div class="p-6 space-y-4">
		<div>
			<label for="automation-name" class="block text-xs font-medium text-fg-muted mb-1.5">Name</label>
			<Input id="automation-name" bind:value={name} placeholder="e.g. Index new LoRAs" />
		</div>
		<div>
			<label for="automation-description" class="block text-xs font-medium text-fg-muted mb-1.5">
				Description <span class="text-fg-subtle">(optional)</span>
			</label>
			<Input id="automation-description" bind:value={description} placeholder="What does this automation do?" />
		</div>
	</div>

	<svelte:fragment slot="footer">
		<div class="flex items-center justify-end gap-2 px-6 py-4">
			<Button variant="ghost" onclick={handleClose} disabled={creating}>Cancel</Button>
			<Button variant="primary" onclick={handleCreate} disabled={!name.trim()} loading={creating}>
				Create
			</Button>
		</div>
	</svelte:fragment>
</BaseModal>
