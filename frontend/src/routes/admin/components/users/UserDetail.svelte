<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { logger } from '$lib/utils/logger';
	import * as adminApi from '$lib/services/admin-api';
	import * as plansApi from '$lib/plans/api';
	import type { UserPlanDetail } from '$lib/plans/types';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import type { User } from '$lib/stores/auth';
	import { AssignedList, type PickerDiff } from '$lib/components/picker';
	import { presetsKind, llmsKind, groupsKind, modelsKind, type PickerModel } from '$lib/components/picker/kinds';
	import { Badge, Input, IconButton } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { DetailHeader, DetailTabs, DetailBody, DetailLayout, DetailSection, DetailFooter, DetailField } from '$lib/components/detail';
	import { adminSectionIcon } from '../../adminSections';
	import UserPlanSection from '../plans/UserPlanSection.svelte';
	import UserApiAccessSection from '../apikeys/UserApiAccessSection.svelte';
	import UserResolutionCard from '../plans/UserResolutionCard.svelte';
	import { createModelRemote, fetchAssignedModels } from './modelAssignmentSource';
	import { applyAssignments, ensureOk } from './assignments';
	import { useUsersDirectory } from './usersDirectory.svelte';
	import { userEditFormFrom, userUpdatePayload } from './usersLibrary';

	type Tab = 'overview' | 'groups' | 'presets' | 'llms' | 'models';

	let { user, currentUser, onBack }: { user: User; currentUser: any; onBack: () => void } = $props();

	const directory = useUsersDirectory();

	let tab = $state<Tab>('overview');

	const initialForm = untrack(() => userEditFormFrom(user));
	let form = $state(initialForm);
	let snapshot = $state(JSON.stringify(initialForm));
	let saving = $state(false);
	let planDraft = $state<string | null>(null);
	let planSaved = $state<string | null>(null);
	let planDetail = $state<UserPlanDetail | null>(null);
	const dirty = $derived(JSON.stringify(form) !== snapshot || planDraft !== planSaved);

	let presetAssignments = $state<any[] | null>(null);
	let llmAssignments = $state<any[] | null>(null);
	let modelRows = $state<PickerModel[] | null>(null);
	let loadingPresets = $state(false);
	let loadingLLMs = $state(false);

	const isSelf = $derived(user.id === currentUser?.id);
	const groupIds = $derived(directory.userGroupIds[user.id] || []);
	const groupRows = $derived(directory.groups.filter((g) => groupIds.includes(g.id)));
	const presetRows = $derived(
		directory.allPresets.filter((p) => (presetAssignments ?? []).some((a: any) => a.preset_id === p.preset_db_id))
	);
	const llmRows = $derived(directory.allLLMConfigs.filter((l) => (llmAssignments ?? []).some((a: any) => a.id === l.id)));
	const modelRemote = $derived(createModelRemote({ userId: user.id }, directory.modelTypeOptions));

	const tabs = $derived([
		{ id: 'overview', label: 'Overview', icon: 'info' },
		{ id: 'groups', label: 'Groups', icon: 'group', count: groupIds.length },
		{ id: 'presets', label: 'Presets', icon: 'layers', count: presetAssignments?.length },
		{ id: 'llms', label: 'LLMs', icon: 'chat', count: llmAssignments?.length },
		{ id: 'models', label: 'Models', icon: 'cube', count: modelRows?.length }
	]);

	onMount(() => {
		void loadUserPlan();
		loadTabData('overview');
	});

	function selectTab(id: string) {
		tab = id as Tab;
		loadTabData(tab);
	}

	function loadTabData(next: Tab) {
		if (next === 'presets' && !presetAssignments) void loadPresetAssignments();
		else if (next === 'llms' && !llmAssignments) void loadLLMAssignments();
		else if (next === 'models') {
			void directory.ensureModelTypeOptions();
			if (!modelRows) void loadModelAssignments();
		}
	}

	async function loadUserPlan() {
		try {
			const detail = await plansApi.getUserPlanDetail(user.id);
			planDetail = detail;
			planSaved = detail.override_plan?.id ?? null;
			planDraft = planSaved;
		} catch (error) {
			logger.error('Failed to load user plan:', error);
		}
	}

	async function loadLLMAssignments() {
		try {
			loadingLLMs = true;
			const response = await adminApi.getUserLLMAssignments(user.id);
			if (response.success && response.data) llmAssignments = response.data.llm_configs || [];
		} catch (error) {
			logger.error('Failed to load user LLM assignments:', error);
		} finally {
			loadingLLMs = false;
		}
	}

	async function loadPresetAssignments() {
		try {
			loadingPresets = true;
			const assignments: any[] = [];
			for (const preset of directory.allPresets) {
				try {
					const response = await adminApi.getPresetAssignments(preset.id);
					if (response.success && response.data) {
						assignments.push(...(response.data.assignments || []).filter((a: any) => a.user_id === user.id));
					}
				} catch (error) {
					logger.error(`Failed to load assignments for preset ${preset.id}:`, error);
				}
			}
			presetAssignments = assignments;
		} catch (error) {
			logger.error('Failed to load user preset assignments:', error);
		} finally {
			loadingPresets = false;
		}
	}

	async function loadModelAssignments() {
		try {
			modelRows = await fetchAssignedModels({ userId: user.id });
		} catch (error) {
			logger.error('Failed to load user model assignments:', error);
			modelRows = [];
			toasts.error('Failed to load assigned models.');
		}
	}

	function applyGroups(diff: PickerDiff) {
		return applyAssignments(
			diff,
			{
				assign: async (groupId) => ensureOk(await adminApi.addUsersToGroup(groupId, [user.id]), 'Could not add to group'),
				unassign: async (groupId) => ensureOk(await adminApi.removeUserFromGroup(groupId, user.id), 'Could not remove from group')
			},
			async () => {
				await Promise.all([...new Set([...diff.add, ...diff.remove])].map((id) => directory.refreshGroupMembers(id)));
			},
			'groups'
		);
	}

	function applyPresets(diff: PickerDiff) {
		return applyAssignments(
			diff,
			{
				assign: async (id) => ensureOk(await adminApi.assignPresetToUsers(id, [user.id]), 'Could not assign preset'),
				unassign: async (id) => ensureOk(await adminApi.unassignPresetFromUser(id, user.id), 'Could not unassign preset')
			},
			loadPresetAssignments,
			'presets'
		);
	}

	function applyLLMs(diff: PickerDiff) {
		return applyAssignments(
			diff,
			{
				assign: async (id) => ensureOk(await adminApi.assignLLMToUser(user.id, id), 'Could not assign LLM'),
				unassign: async (id) => ensureOk(await adminApi.unassignLLMFromUser(user.id, id), 'Could not unassign LLM')
			},
			loadLLMAssignments,
			'LLMs'
		);
	}

	function applyModels(diff: PickerDiff) {
		return applyAssignments(
			diff,
			{
				assign: async (id) => ensureOk(await adminApi.assignModelToUser(user.id, id), 'Could not assign model'),
				unassign: async (id) => ensureOk(await adminApi.unassignModelFromUser(user.id, id), 'Could not unassign model')
			},
			loadModelAssignments,
			'models'
		);
	}

	function loadForm(source: User | null) {
		form = userEditFormFrom(source);
		snapshot = JSON.stringify(form);
	}

	function discard() {
		loadForm(user);
		planDraft = planSaved;
	}

	async function save() {
		saving = true;
		try {
			const response: any = await adminApi.updateUser(user.id, userUpdatePayload(form));
			if (response.success !== false) {
				if (planDraft !== planSaved) {
					await plansApi.setUserPlan(user.id, planDraft);
					await Promise.all([loadUserPlan(), directory.loadPlans()]);
				}
				toasts.success(`${form.username || user.username} updated`);
				await directory.loadUsers();
				loadForm(directory.users.find((u) => u.id === user.id) ?? null);
			} else {
				toasts.error(response.message || 'Failed to save user');
			}
		} catch (error) {
			logger.error('Failed to save user:', error);
			toasts.error('Failed to save user. Please check the form and try again.');
		} finally {
			saving = false;
		}
	}

	async function remove() {
		if (
			await confirmDialog({
				title: `Are you sure you want to delete user "${user.username}"?`,
				message: 'This action cannot be undone.',
				variant: 'danger'
			})
		) {
			try {
				await adminApi.deleteUser(user.id);
				await directory.loadUsers();
				await directory.loadMemberships();
				onBack();
			} catch (error) {
				logger.error('Failed to delete user:', error);
				toasts.error('Failed to delete user.');
			}
		}
	}
</script>

<div class="flex h-full flex-col">
	<DetailHeader title={user.username} icon={adminSectionIcon('users')} backLabel="Users" {onBack}>
		{#snippet chips()}
			<Badge variant={user.account_type === 'ADMIN' ? 'warning' : 'neutral'} size="sm" class="font-mono uppercase">
				{user.account_type}
			</Badge>
			{#if directory.isUserRestricted(user.id)}<Badge variant="danger" size="sm">Restricted</Badge>{/if}
		{/snippet}
		{#snippet subtitle()}
			{user.email}
		{/snippet}
		{#snippet actions()}
			<Tooltip text={isSelf ? 'Cannot delete your own account' : 'Delete user'}>
				<IconButton
					icon="trash"
					label="Delete user"
					class="text-danger hover:text-danger hover:bg-danger/10"
					disabled={isSelf}
					onclick={remove}
				/>
			</Tooltip>
		{/snippet}
	</DetailHeader>

	<DetailTabs tabs={tabs} active={tab} onSelect={selectTab} ariaLabel="User details" />

	{#if tab === 'overview'}
		<DetailBody>
			<DetailLayout>
				{#snippet main()}
					<DetailSection label="Identity">
						<div class="space-y-4">
							<DetailField label="Username" id="edit-user-username">
								<Input id="edit-user-username" type="text" bind:value={form.username} />
							</DetailField>
							<DetailField label="Email" id="edit-user-email">
								<Input id="edit-user-email" type="text" bind:value={form.email} />
							</DetailField>
							<DetailField label="New Password" id="edit-user-password" help="Leave empty to keep current">
								<Input id="edit-user-password" type="password" bind:value={form.password} />
							</DetailField>
							<DetailField label="Account Type" id="edit-user-type">
								<select id="edit-user-type" class="input" bind:value={form.account_type}>
									<option value="USER">Regular User</option>
									<option value="ADMIN">Administrator</option>
								</select>
							</DetailField>
						</div>
					</DetailSection>
					<UserPlanSection
						plans={directory.plans}
						kinds={directory.planKinds}
						detail={planDetail}
						overrideId={planDraft}
						onChange={(planId) => (planDraft = planId)}
					/>
				{/snippet}

				{#snippet aside()}
					{#if planDetail}<UserResolutionCard detail={planDetail} />{/if}
					<UserApiAccessSection userId={user.id} name={user.username} />
				{/snippet}
			</DetailLayout>
		</DetailBody>
	{:else if tab === 'groups'}
		<DetailBody>
			<DetailLayout>
				{#snippet main()}
					<AssignedList
						kind={groupsKind}
						label="Groups"
						assignedRows={groupRows}
						pickerItems={directory.groups}
						onApply={applyGroups}
						loading={directory.loadingMemberships}
						pickerSubtitle={`Add ${user.username} to groups`}
					/>
				{/snippet}
			</DetailLayout>
		</DetailBody>
	{:else if tab === 'presets'}
		<DetailBody>
			<DetailLayout>
				{#snippet main()}
					<AssignedList
						kind={presetsKind}
						label="Presets"
						assignedRows={presetRows}
						pickerItems={directory.allPresets}
						onApply={applyPresets}
						loading={!presetAssignments && loadingPresets}
						pickerSubtitle={`Grant ${user.username} access to presets`}
					/>
				{/snippet}
			</DetailLayout>
		</DetailBody>
	{:else if tab === 'llms'}
		<DetailBody>
			<DetailLayout>
				{#snippet main()}
					<AssignedList
						kind={llmsKind}
						label="LLM configurations"
						assignedRows={llmRows}
						pickerItems={directory.allLLMConfigs}
						onApply={applyLLMs}
						loading={!llmAssignments && loadingLLMs}
						pickerSubtitle={`Grant ${user.username} access to LLM configurations`}
					/>
				{/snippet}
			</DetailLayout>
		</DetailBody>
	{:else if tab === 'models'}
		<DetailBody>
			<DetailLayout>
				{#snippet main()}
					<AssignedList
						kind={modelsKind}
						label="Models"
						assignedRows={modelRows ?? []}
						remote={modelRemote}
						onApply={applyModels}
						loading={!modelRows}
						pickerSubtitle={`Grant ${user.username} access to models`}
					/>
				{/snippet}
			</DetailLayout>
		</DetailBody>
	{/if}

	{#if tab === 'overview'}
		<DetailFooter dirtyCount={dirty ? 1 : 0} saving={saving} onSave={save} onDiscard={discard} />
	{/if}
</div>
