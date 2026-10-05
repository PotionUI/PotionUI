<script lang="ts">
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import ConfirmFooter from '$lib/components/modals/ConfirmFooter.svelte';
	import CustomSelect from '$lib/components/CustomSelect.svelte';
	import type { Plan } from '$lib/plans/types';

	export interface PlanAssignments {
		groups: { id: string; name: string }[];
		users: { id: string; username: string }[];
	}

	let {
		plan,
		assigned,
		plans,
		busy = false,
		onConfirm,
		onClose
	}: {
		plan: Plan | null;
		assigned: PlanAssignments;
		plans: readonly Plan[];
		busy?: boolean;
		onConfirm: (reassignTo: string) => void;
		onClose: () => void;
	} = $props();

	let target = $state('');
	const choices = $derived(plans.filter((p) => p.id !== plan?.id).map((p) => ({ value: p.id, label: p.name })));
</script>

<BaseModal isOpen={plan !== null} title={plan ? `Delete "${plan.name}"` : ''} size="md" on:close={onClose}>
	<div class="space-y-4 p-6" data-delete-plan>
		<p class="text-sm text-fg-muted">This plan is still assigned. Move its assignments to another plan, or clear them.</p>
		<ul class="space-y-1 text-sm text-fg" data-delete-plan-assigned>
			{#each assigned.groups as group (group.id)}<li>Group {group.name}</li>{/each}
			{#each assigned.users as user (user.id)}<li>User {user.username} (personal override)</li>{/each}
		</ul>
		<div data-reassign-select>
			<p class="mb-1 text-sm font-medium text-fg-muted">Reassign to</p>
			<CustomSelect size="sm" value={target} options={choices} placeholder="Choose a plan…" on:change={(e) => (target = String(e.detail))} />
		</div>
		<p class="text-sm text-fg-subtle">Clearing makes groups inherit the default plan and removes personal overrides.</p>
	</div>
	<svelte:fragment slot="footer">
		<ConfirmFooter
			confirmLabel="Reassign and delete"
			confirmVariant="danger"
			confirmDisabled={!target}
			secondaryLabel="Clear assignments and delete"
			{busy}
			onCancel={onClose}
			onConfirm={() => onConfirm(target)}
			onSecondary={() => onConfirm('none')}
		/>
	</svelte:fragment>
</BaseModal>
