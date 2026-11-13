<script lang="ts">
	import { onMount } from 'svelte';
	import { logger, getApiErrorMessage } from '$lib/utils/logger';
	import * as plansApi from '$lib/plans/api';
	import type { Plan, PlanBody, PlanDetail } from '$lib/plans/types';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import { Button, Spinner, EmptyState } from '$lib/components/ui';
	import PlanEditor from '../plans/PlanEditor.svelte';
	import DeletePlanDialog, { type PlanAssignments } from '../plans/DeletePlanDialog.svelte';
	import { useUsersDirectory } from './usersDirectory.svelte';
	import { NEW_PLAN_ID } from './usersLibrary';

	let { planId, onBack, onCreated }: { planId: string; onBack: () => void; onCreated: (id: string) => void } = $props();

	const directory = useUsersDirectory();

	const activePlan = $derived(planId !== NEW_PLAN_ID ? (directory.plans.find((p) => p.id === planId) ?? null) : null);

	let detail = $state<PlanDetail | null>(null);
	let saving = $state(false);
	let deletingPlan = $state<Plan | null>(null);
	let deleteAssigned = $state<PlanAssignments>({ groups: [], users: [] });
	let deleteBusy = $state(false);

	onMount(() => {
		if (planId !== NEW_PLAN_ID) void loadDetail(planId);
	});

	async function loadDetail(id: string) {
		try {
			detail = await plansApi.getPlanDetail(id);
		} catch (error) {
			logger.error('Failed to load plan detail:', error);
		}
	}

	async function save(body: PlanBody) {
		saving = true;
		try {
			if (activePlan) {
				await plansApi.updatePlan(activePlan.id, body);
				toasts.success(`${body.name} updated`);
				await Promise.all([directory.loadPlans(), loadDetail(activePlan.id)]);
			} else {
				const created = await plansApi.createPlan(body);
				toasts.success(`${body.name} created`);
				await directory.loadPlans();
				onCreated(created.id);
			}
		} catch (error) {
			logger.error('Failed to save plan:', error);
			toasts.error(getApiErrorMessage(error, 'Failed to save plan.'));
		} finally {
			saving = false;
		}
	}

	async function confirmReassignDelete(reassignTo: string) {
		const plan = deletingPlan;
		if (!plan) return;
		deleteBusy = true;
		try {
			await plansApi.deletePlan(plan.id, reassignTo);
			deletingPlan = null;
			await directory.loadPlans();
			onBack();
		} catch (error) {
			logger.error('Failed to delete plan:', error);
			toasts.error(getApiErrorMessage(error, 'Failed to delete plan.'));
		} finally {
			deleteBusy = false;
		}
	}

	async function remove(plan: Plan) {
		if (
			!(await confirmDialog({
				title: `Delete plan "${plan.name}"?`,
				message: 'A plan that is assigned to a group or a user cannot be deleted. This action cannot be undone.',
				variant: 'danger'
			}))
		)
			return;
		try {
			await plansApi.deletePlan(plan.id);
			await directory.loadPlans();
			onBack();
		} catch (error: any) {
			const body = error?.response?.data?.detail;
			if (body?.error === 'plan_in_use') {
				deletingPlan = plan;
				deleteAssigned = { groups: body.assigned_to?.groups ?? [], users: body.assigned_to?.users ?? [] };
				return;
			}
			logger.error('Failed to delete plan:', error);
			toasts.error(getApiErrorMessage(error, 'Failed to delete plan.'));
		}
	}
</script>

{#if planId !== NEW_PLAN_ID && !activePlan}
	<div class="flex h-full items-center justify-center">
		{#if directory.loadingPlans}
			<Spinner size="lg" />
		{:else}
			<EmptyState title="Plan not found" description="This plan may have been removed." icon="layers" compact>
				{#snippet actions()}<Button size="sm" variant="secondary" onclick={onBack}>Back to plans</Button>{/snippet}
			</EmptyState>
		{/if}
	</div>
{:else}
	<PlanEditor
		plan={activePlan}
		kinds={directory.planKinds}
		{detail}
		groups={directory.groups}
		users={directory.users}
		onAssignmentsChanged={async () => {
			if (activePlan) await Promise.all([directory.loadPlans(), loadDetail(activePlan.id)]);
		}}
		{saving}
		onSave={save}
		onDelete={activePlan ? () => remove(activePlan) : undefined}
		{onBack}
	/>
{/if}

<DeletePlanDialog
	plan={deletingPlan}
	assigned={deleteAssigned}
	plans={directory.plans}
	busy={deleteBusy}
	onConfirm={confirmReassignDelete}
	onClose={() => (deletingPlan = null)}
/>
