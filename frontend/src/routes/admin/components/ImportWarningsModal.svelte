<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import { Button, Badge } from '$lib/components/ui';
	import type { AutomationImportWarning } from '$lib/types/automations';

	export let isOpen = false;
	export let automationName = '';
	export let warnings: AutomationImportWarning[] = [];

	const dispatch = createEventDispatcher<{ close: void }>();

	function handleClose() {
		dispatch('close');
	}
</script>

<BaseModal {isOpen} title="Imported with warnings" size="md" on:close={handleClose}>
	<div class="p-6 space-y-4">
		<p class="text-sm text-fg-muted">
			<span class="font-medium text-fg">{automationName}</span> was imported successfully but landed
			<Badge variant="neutral" size="sm">disabled</Badge> — review the items below before enabling
			it, since this environment can't satisfy them yet.
		</p>
		<ul class="space-y-2">
			{#each warnings as warning, i (i)}
				<li class="flex items-start gap-2 text-sm p-2.5 rounded bg-surface-2 border border-line">
					<Badge variant="warning" size="sm">{warning.severity}</Badge>
					<div class="min-w-0">
						<p class="text-fg">{warning.message}</p>
						{#if warning.node_id}
							<p class="text-2xs font-mono text-fg-subtle mt-0.5">node: {warning.node_id}</p>
						{/if}
					</div>
				</li>
			{/each}
		</ul>
	</div>

	<svelte:fragment slot="footer">
		<div class="flex items-center justify-end gap-2 px-6 py-4">
			<Button variant="primary" onclick={handleClose}>Got it</Button>
		</div>
	</svelte:fragment>
</BaseModal>
