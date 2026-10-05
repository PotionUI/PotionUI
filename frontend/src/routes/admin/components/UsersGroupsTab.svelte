<script lang="ts">
	import { logger, getApiErrorMessage } from '$lib/utils/logger';
	import { onMount, untrack } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import * as adminApi from '$lib/services/admin-api';
	import { api } from '$lib/services/api/index';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import type { User } from '$lib/stores/auth';
	import { timeAgo } from '$lib/utils/relativeTime';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import ConfirmFooter from '$lib/components/modals/ConfirmFooter.svelte';
	import { createConfirmSettlementGate, getConfirmKeyboardAction, settleIfEligible } from '$lib/components/modals/confirmKeyboard';
	import ModelAssignmentPicker from '$lib/components/modals/ModelAssignmentPicker.svelte';
	import { Button, IconButton, Badge, Input, Spinner, EmptyState, LoadErrorState, Switch, SegmentedControl } from '$lib/components/ui';
	import {
		CONTENT_POLICY_INHERIT,
		CONTENT_POLICY_OPTIONS,
		isContentPolicy,
		isSystemRestrictedGroup
	} from '$lib/contentSafety/policy';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import LibraryShell from '$lib/components/library/LibraryShell.svelte';
	import LibraryFilterBar from '$lib/components/library/LibraryFilterBar.svelte';
	import FilterPopoverFrame from '$lib/components/library/FilterPopoverFrame.svelte';
	import SegmentedFilterGroup from '$lib/components/library/SegmentedFilterGroup.svelte';
	import { PaneRow } from '$lib/components/pane';
	import { DetailHeader, DetailTabs, DetailBody, DetailLayout, DetailSection, DetailFooter, DetailField } from '$lib/components/detail';
	import { DataTable, TablePager, pageCount, clampPage, type DataTableColumn } from '$lib/components/table';
	import { selectPage, clearAll } from '$lib/components/table/selection';
	import SelectionActionBar from '$lib/components/collections/SelectionActionBar.svelte';
	import AssignmentList from './AssignmentList.svelte';
	import BulkPickerModal from './users/BulkPickerModal.svelte';
	import { adminSectionIcon } from '../adminSections';
	import {
		USERS_LIBRARY_SECTIONS,
		accountTypeCounts,
		deletableGroupIds,
		deletableUserIds,
		usersSubViewFromParam,
		type UsersSubView
	} from './users/usersLibrary';
	import * as plansApi from '$lib/plans/api';
	import type {
		GroupImpact,
		LimitKindDescriptor,
		Plan,
		PlanBody,
		PlanDetail,
		PlansSettings,
		UserPlanDetail,
		UserUsageRow
	} from '$lib/plans/types';
	import { isUsageSort, sortByUsage, sourceLabel, usageSortOptions } from '$lib/plans/usage';
	import PlansList from './plans/PlansList.svelte';
	import PlanEditor from './plans/PlanEditor.svelte';
	import GroupPlanSection from './plans/GroupPlanSection.svelte';
	import UserPlanSection from './plans/UserPlanSection.svelte';
	import UserResolutionCard from './plans/UserResolutionCard.svelte';
	import { usageColumnCell } from './plans/usageColumnCell';
	import {
		applyPlansFilters,
		plansFiltersFromSearchParams,
		plansFiltersToSearchParams,
		type PlansFilters
	} from './plansFilters';
	import {
		USER_ACCOUNT_TYPE_OPTIONS,
		USERS_SORT_OPTIONS,
		applyUsersFilters,
		clearAllUsersFilters,
		clearUsersFilterChip,
		usersFilterActiveCount,
		usersFilterChips,
		usersFiltersFromSearchParams,
		usersFiltersToSearchParams,
		type UserAccountTypeFilter,
		type UserSortBy,
		type UsersFilters
	} from './usersFilters';
	import {
		GROUPS_SORT_OPTIONS,
		applyGroupsFilters,
		clearAllGroupsFilters,
		clearGroupsFilterChip,
		groupsFilterChips,
		groupsFiltersFromSearchParams,
		groupsFiltersToSearchParams,
		type GroupSortBy,
		type GroupsFilters
	} from './groupsFilters';

	let { currentUser }: { currentUser: any } = $props();

	type SubView = UsersSubView;
	type UserDetailTab = 'overview' | 'groups' | 'presets' | 'llms' | 'models';
	type GroupDetailTab = 'overview' | 'users' | 'presets' | 'llms' | 'models';

	const subView = $derived(usersSubViewFromParam($page.url.searchParams.get('view')));
	const viewId = $derived($page.url.searchParams.get('id'));
	const detailOpen = $derived(!!viewId);

	const usersFilters = $derived(usersFiltersFromSearchParams($page.url.searchParams));
	const groupsFilters = $derived(groupsFiltersFromSearchParams($page.url.searchParams));
	const plansFilters = $derived(plansFiltersFromSearchParams($page.url.searchParams));

	function buildUrl(overrides: { view?: SubView; id?: string | null } = {}): string {
		const url = new URL($page.url);
		url.searchParams.set('tab', 'users');
		const nextView = overrides.view ?? subView;
		if (nextView !== 'users') url.searchParams.set('view', nextView);
		else url.searchParams.delete('view');
		const id = overrides.id !== undefined ? overrides.id : viewId;
		if (id) url.searchParams.set('id', id);
		else url.searchParams.delete('id');
		return `${url.pathname}?${url.searchParams.toString()}`;
	}

	function navigate(overrides: { view?: SubView; id?: string | null } = {}) {
		void goto(buildUrl(overrides), { keepFocus: true, noScroll: true });
	}

	function setSubView(next: SubView) {
		if (next === subView) return;
		selectedUserIds = new Set();
		selectedGroupIds = new Set();
		usersPage = 1;
		groupsPage = 1;
		navigate({ view: next, id: null });
	}

	let usersFiltersDebounce: ReturnType<typeof setTimeout> | undefined;
	function updateUsersFilters(next: UsersFilters) {
		clearTimeout(usersFiltersDebounce);
		usersFiltersDebounce = setTimeout(() => {
			const url = new URL($page.url);
			for (const key of ['q', 'account_type', 'sort_by']) url.searchParams.delete(key);
			for (const [key, value] of usersFiltersToSearchParams(next)) url.searchParams.set(key, value);
			usersPage = 1;
			void goto(`${url.pathname}?${url.searchParams.toString()}`, { replaceState: true, keepFocus: true, noScroll: true });
		}, 250);
	}

	let groupsFiltersDebounce: ReturnType<typeof setTimeout> | undefined;
	function updateGroupsFilters(next: GroupsFilters) {
		clearTimeout(groupsFiltersDebounce);
		groupsFiltersDebounce = setTimeout(() => {
			const url = new URL($page.url);
			for (const key of ['group_q', 'group_sort_by']) url.searchParams.delete(key);
			for (const [key, value] of groupsFiltersToSearchParams(next)) url.searchParams.set(key, value);
			groupsPage = 1;
			void goto(`${url.pathname}?${url.searchParams.toString()}`, { replaceState: true, keepFocus: true, noScroll: true });
		}, 250);
	}

	let plansFiltersDebounce: ReturnType<typeof setTimeout> | undefined;
	function updatePlansFilters(next: PlansFilters) {
		clearTimeout(plansFiltersDebounce);
		plansFiltersDebounce = setTimeout(() => {
			const url = new URL($page.url);
			url.searchParams.delete('plan_q');
			for (const [key, value] of plansFiltersToSearchParams(next)) url.searchParams.set(key, value);
			void goto(`${url.pathname}?${url.searchParams.toString()}`, { replaceState: true, keepFocus: true, noScroll: true });
		}, 250);
	}

	const NEW_PLAN_ID = 'new';
	const EMPTY_PLANS_SETTINGS: PlansSettings = { default_plan_id: null, exempt_admins: true, day_timezone: 'UTC', contact_line: '' };
	let plans = $state<Plan[]>([]);
	let planKinds = $state<LimitKindDescriptor[]>([]);
	let plansSettings = $state<PlansSettings>(EMPTY_PLANS_SETTINGS);
	let usageByUser = $state<Map<string, UserUsageRow>>(new Map());
	let loadingPlans = $state(true);
	let plansError = $state<string | null>(null);
	let savingPlan = $state(false);
	let groupImpact = $state<GroupImpact | null>(null);
	let loadingGroupImpact = $state(false);
	let userPlanDetail = $state<UserPlanDetail | null>(null);
	let planDetail = $state<PlanDetail | null>(null);
	let planKindsInUse = $state<LimitKindDescriptor[]>([]);
	let groupPlanById = $state<Record<string, string | null>>({});

	let users = $state<User[]>([]);
	let loadingUsers = $state(true);
	let usersError = $state<string | null>(null);

	let showUserModal = $state(false);
	let userFormData = $state({ username: '', email: '', password: '', account_type: 'USER' });
	let savingNewUser = $state(false);
	const createUserGate = createConfirmSettlementGate();
	$effect(() => {
		if (showUserModal) createUserGate.reset();
	});

	type UserEditFormData = { username: string; email: string; password: string; account_type: string };
	function emptyUserFormData(): UserEditFormData {
		return { username: '', email: '', password: '', account_type: 'USER' };
	}
	let editUserFormData = $state<UserEditFormData>(emptyUserFormData());
	let editUserSnapshot = $state(JSON.stringify(emptyUserFormData()));
	let editUserSaving = $state(false);
	let userPlanDraft = $state<string | null>(null);
	let userPlanSaved = $state<string | null>(null);
	const editUserDirty = $derived(JSON.stringify(editUserFormData) !== editUserSnapshot || userPlanDraft !== userPlanSaved);

	let groups = $state<adminApi.UserGroup[]>([]);
	let loadingGroups = $state(true);
	let groupsError = $state<string | null>(null);

	let showGroupModal = $state(false);
	let groupFormData = $state({ name: '', description: '', content_policy: CONTENT_POLICY_INHERIT });
	let savingNewGroup = $state(false);
	const createGroupGate = createConfirmSettlementGate();
	$effect(() => {
		if (showGroupModal) createGroupGate.reset();
	});

	type GroupEditFormData = { name: string; description: string; content_policy: string };
	const emptyGroupEditForm = (): GroupEditFormData => ({ name: '', description: '', content_policy: CONTENT_POLICY_INHERIT });
	const contentPolicyItems = [{ id: CONTENT_POLICY_INHERIT, label: 'Inherit' }, ...CONTENT_POLICY_OPTIONS];
	let editGroupFormData = $state<GroupEditFormData>(emptyGroupEditForm());
	let editGroupSnapshot = $state(JSON.stringify(emptyGroupEditForm()));
	let editGroupSaving = $state(false);
	let groupPlanDraft = $state<string | null>(null);
	let groupPlanSaved = $state<string | null>(null);
	const editGroupDirty = $derived(JSON.stringify(editGroupFormData) !== editGroupSnapshot || groupPlanDraft !== groupPlanSaved);

	let membersByGroup = $state<Record<string, adminApi.UserGroupMember[]>>({});
	let loadingMemberships = $state(true);

	const restrictedGroupIds = $derived(groups.filter((g) => isSystemRestrictedGroup(g)).map((g) => g.id));
	function isUserRestricted(userId: string): boolean {
		return (userGroupIds[userId] || []).some((id) => restrictedGroupIds.includes(id));
	}

	const userGroupIds = $derived.by(() => {
		const map: Record<string, string[]> = {};
		for (const [groupId, members] of Object.entries(membersByGroup)) {
			for (const member of members) {
				(map[member.user_id] ||= []).push(groupId);
			}
		}
		return map;
	});

	let togglingMembership = $state<string | null>(null);

	let allLLMConfigs = $state<any[]>([]);
	let allPresets = $state<any[]>([]);

	let userLLMAssignments = $state<Record<string, any[]>>({});
	let userPresetAssignments = $state<Record<string, any[]>>({});
	let userModelAssignments = $state<Record<string, string[]>>({});
	let loadingUserLLMs = $state(false);
	let loadingUserPresets = $state(false);
	let loadingUserModelAssignments = $state(false);
	let assigningUserLLM = $state<string | null>(null);
	let assigningUserPreset = $state<string | null>(null);
	let assigningUserModel = $state<string | null>(null);

	let userMcpEnabled = $state<Record<string, boolean>>({});
	let togglingUserMcp = $state<string | null>(null);

	let groupLLMsByGroup = $state<Record<string, any[]>>({});
	let groupPresetsByGroup = $state<Record<string, any[]>>({});
	let groupModelsByGroup = $state<Record<string, any[]>>({});
	let loadingGroupLLMs = $state(false);
	let loadingGroupPresets = $state(false);
	let loadingGroupModelAssignments = $state(false);
	let assigningGroupLLM = $state<string | null>(null);
	let assigningGroupPreset = $state<string | null>(null);
	let assigningGroupModel = $state<string | null>(null);

	const activeUser = $derived(subView === 'users' && viewId ? (users.find((u) => u.id === viewId) ?? null) : null);
	const activeGroupEntity = $derived(subView === 'groups' && viewId ? (groups.find((g) => g.id === viewId) ?? null) : null);
	const selectedUserId = $derived(activeUser?.id ?? null);
	const selectedGroupId = $derived(activeGroupEntity?.id ?? null);

	const groupLLMs = $derived(selectedGroupId ? (groupLLMsByGroup[selectedGroupId] ?? []) : []);
	const groupPresets = $derived(selectedGroupId ? (groupPresetsByGroup[selectedGroupId] ?? []) : []);
	const groupModels = $derived(selectedGroupId ? (groupModelsByGroup[selectedGroupId] ?? []) : []);

	let userDetailTab = $state<UserDetailTab>('overview');
	let groupDetailTab = $state<GroupDetailTab>('overview');

	let selectedUserIds = $state<Set<string>>(new Set());
	let selectedGroupIds = $state<Set<string>>(new Set());
	let usersPage = $state(1);
	let usersPageSize = $state(25);
	let groupsPage = $state(1);
	let groupsPageSize = $state(25);
	let showAddToGroupModal = $state(false);
	let showAssignPresetModal = $state(false);
	let bulkAdding = $state(false);
	let bulkAssigning = $state(false);
	let bulkDeletingUsers = $state(false);
	let bulkDeletingGroups = $state(false);

	async function bootstrap() {
		await Promise.all([loadUsers(), loadGroups(), loadPlans()]);
		await loadMemberships();
		await Promise.all([loadLLMConfigs(), loadPresets()]);
	}

	async function loadPlans() {
		loadingPlans = true;
		try {
			const [overview, usage, groupRows] = await Promise.all([
				plansApi.getPlansOverview(),
				plansApi.listUsersUsage(),
				plansApi.listGroupPlans()
			]);
			planKinds = overview.kinds;
			plans = overview.plans;
			plansSettings = overview.settings;
			planKindsInUse = usage.kinds;
			usageByUser = new Map(usage.users.map((row) => [row.user_id, row]));
			groupPlanById = Object.fromEntries(groupRows.map((row) => [row.id, row.plan?.id ?? null]));
			plansError = null;
		} catch (error) {
			logger.error('Failed to load plans:', error);
			plansError = getApiErrorMessage(error, 'Failed to load plans');
		} finally {
			loadingPlans = false;
		}
	}

	async function savePlansSettings(patch: Partial<PlansSettings>) {
		const previous = plansSettings;
		plansSettings = { ...plansSettings, ...patch };
		try {
			plansSettings = await plansApi.updatePlansSettings(patch);
			if ('default_plan_id' in patch) await loadPlans();
		} catch (error) {
			plansSettings = previous;
			logger.error('Failed to save plan settings:', error);
			toasts.error(getApiErrorMessage(error, 'Failed to save plan settings.'));
		}
	}

	async function loadUserEffective(userId: string) {
		try {
			const detail = await plansApi.getUserPlanDetail(userId);
			if (selectedUserId !== userId) return;
			userPlanDetail = detail;
			userPlanSaved = detail.override_plan?.id ?? null;
			userPlanDraft = userPlanSaved;
		} catch (error) {
			logger.error('Failed to load user plan:', error);
		}
	}

	async function loadPlanDetail(planId: string) {
		try {
			const detail = await plansApi.getPlanDetail(planId);
			if (planDetailId === planId) planDetail = detail;
		} catch (error) {
			logger.error('Failed to load plan detail:', error);
		}
	}

	async function loadGroupImpact(groupId: string, planId: string | null) {
		loadingGroupImpact = true;
		try {
			const impact = await plansApi.getGroupPlanImpact(groupId, planId);
			if (selectedGroupId === groupId && groupPlanDraft === planId) groupImpact = impact;
		} catch (error) {
			logger.error('Failed to load plan impact:', error);
			groupImpact = null;
		} finally {
			loadingGroupImpact = false;
		}
	}

	function changeGroupPlan(planId: string | null) {
		groupPlanDraft = planId;
	}

	const planDetailId = $derived(subView === 'plans' ? viewId : null);
	const activePlan = $derived(planDetailId && planDetailId !== NEW_PLAN_ID ? (plans.find((p) => p.id === planDetailId) ?? null) : null);
	const defaultPlanName = $derived(plans.find((p) => p.id === plansSettings.default_plan_id)?.name ?? null);

	async function savePlan(body: PlanBody) {
		savingPlan = true;
		try {
			if (activePlan) {
				await plansApi.updatePlan(activePlan.id, body);
				toasts.success(`${body.name} updated`);
				await Promise.all([loadPlans(), loadPlanDetail(activePlan.id)]);
			} else {
				const created = await plansApi.createPlan(body);
				toasts.success(`${body.name} created`);
				await loadPlans();
				navigate({ view: 'plans', id: created.id });
			}
		} catch (error) {
			logger.error('Failed to save plan:', error);
			toasts.error(getApiErrorMessage(error, 'Failed to save plan.'));
		} finally {
			savingPlan = false;
		}
	}

	async function handleDeletePlan(plan: Plan) {
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
			await loadPlans();
			navigate({ id: null });
		} catch (error: any) {
			const detail = error?.response?.data?.detail;
			if (detail?.error === 'plan_in_use') {
				const groupNames = (detail.assigned_to?.groups ?? []).map((g: { name: string }) => g.name);
				const userNames = (detail.assigned_to?.users ?? []).map((u: { username: string }) => u.username);
				const assigned = [...groupNames.map((n: string) => `group ${n}`), ...userNames.map((n: string) => `user ${n}`)].join(', ');
				if (
					await confirmDialog({
						title: `"${plan.name}" is still assigned`,
						message: `It is assigned to ${assigned}. Delete it anyway and clear those assignments? Groups go back to inheriting the default and personal overrides are removed.`,
						variant: 'danger'
					})
				) {
					try {
						await plansApi.deletePlan(plan.id, 'none');
						await loadPlans();
						navigate({ id: null });
					} catch (retryError) {
						logger.error('Failed to delete plan:', retryError);
						toasts.error(getApiErrorMessage(retryError, 'Failed to delete plan.'));
					}
				}
				return;
			}
			logger.error('Failed to delete plan:', error);
			toasts.error(getApiErrorMessage(error, 'Failed to delete plan.'));
		}
	}

	onMount(() => {
		void bootstrap();
	});

	async function loadUsers() {
		try {
			loadingUsers = true;
			const response = await adminApi.getUsers();
			if (response.success && response.data) {
				users = response.data;
				usersError = null;
			} else {
				usersError = response.message || 'Failed to load users';
			}
		} catch (error) {
			logger.error('Failed to load users:', error);
			usersError = getApiErrorMessage(error, 'Failed to load users');
		} finally {
			loadingUsers = false;
		}
	}

	async function loadGroups() {
		try {
			loadingGroups = true;
			const response = await adminApi.getUserGroups();
			if (response.success) {
				groups = response.data || [];
				groupsError = null;
			} else {
				groupsError = response.message || 'Failed to load user groups';
			}
		} catch (error) {
			logger.error('Failed to load user groups:', error);
			groupsError = getApiErrorMessage(error, 'Failed to load user groups');
		} finally {
			loadingGroups = false;
		}
	}

	async function loadMemberships() {
		if (groups.length === 0) {
			membersByGroup = {};
			loadingMemberships = false;
			return;
		}
		loadingMemberships = true;
		try {
			const results = await Promise.all(groups.map((g) => adminApi.getGroupMembers(g.id)));
			const map: Record<string, adminApi.UserGroupMember[]> = {};
			groups.forEach((g, i) => {
				map[g.id] = results[i].success ? (results[i].data as any[]) || [] : [];
			});
			membersByGroup = map;
		} catch (error) {
			logger.error('Failed to load group memberships:', error);
		} finally {
			loadingMemberships = false;
		}
	}

	async function loadLLMConfigs() {
		try {
			const response = await api.getLLMConfigurations();
			if (response.success && response.data) {
				allLLMConfigs = response.data.configurations || [];
			}
		} catch (error) {
			logger.error('Failed to load LLM configurations:', error);
		}
	}

	async function loadPresets() {
		try {
			const response = await api.listPresets(true);
			if (response.success && response.data) {
				allPresets = response.data.filter((preset: any) => preset.installed);
			}
		} catch (error) {
			logger.error('Failed to load presets:', error);
		}
	}

	async function openUser(id: string) {
		if (
			editUserDirty &&
			!(await confirmDialog({ title: 'Discard unsaved changes', message: 'Discard unsaved changes to this user?', variant: 'warning' }))
		)
			return;
		navigate({ id });
	}

	function loadUserEditForm(user: any | null) {
		editUserFormData = user
			? { username: user.username, email: user.email, password: '', account_type: user.account_type }
			: emptyUserFormData();
		editUserSnapshot = JSON.stringify(editUserFormData);
	}

	function discardUserEdit() {
		loadUserEditForm(activeUser);
		userPlanDraft = userPlanSaved;
	}

	async function saveUserEdit() {
		if (!activeUser) return;
		editUserSaving = true;
		try {
			const updateData: any = {};
			if (editUserFormData.username) updateData.username = editUserFormData.username;
			if (editUserFormData.email) updateData.email = editUserFormData.email;
			if (editUserFormData.password) updateData.password = editUserFormData.password;
			if (editUserFormData.account_type) updateData.account_type = editUserFormData.account_type;
			const response: any = await adminApi.updateUser(activeUser.id, updateData);
			if (response.success !== false) {
				if (userPlanDraft !== userPlanSaved) {
					await plansApi.setUserPlan(activeUser.id, userPlanDraft);
					await Promise.all([loadUserEffective(activeUser.id), loadPlans()]);
				}
				toasts.success(`${editUserFormData.username || activeUser.username} updated`);
				await loadUsers();
				loadUserEditForm(users.find((u) => u.id === activeUser!.id) ?? null);
			} else {
				toasts.error(response.message || 'Failed to save user');
			}
		} catch (error) {
			logger.error('Failed to save user:', error);
			toasts.error('Failed to save user. Please check the form and try again.');
		} finally {
			editUserSaving = false;
		}
	}

	function openCreateUserModal() {
		userFormData = { username: '', email: '', password: '', account_type: 'USER' };
		showUserModal = true;
	}

	async function handleSaveNewUser() {
		savingNewUser = true;
		try {
			const response: any = await adminApi.createUser(userFormData);
			await loadUsers();
			showUserModal = false;
			const created = response?.data;
			if (created?.id) navigate({ view: 'users', id: created.id });
		} catch (error) {
			logger.error('Failed to save user:', error);
			toasts.error('Failed to save user. Please check the form and try again.');
		} finally {
			savingNewUser = false;
			createUserGate.reset();
		}
	}

	function closeCreateUserModal() {
		showUserModal = false;
	}

	function cancelCreateUser() {
		createUserGate.settle(closeCreateUserModal);
	}

	function confirmCreateUser() {
		settleIfEligible(
			createUserGate,
			!!userFormData.username && !!userFormData.email && !!userFormData.password && !savingNewUser,
			handleSaveNewUser
		);
	}

	async function handleDeleteUser(userId: string, username: string) {
		if (userId === currentUser?.id) {
			toasts.error('You cannot delete your own account.');
			return;
		}

		if (
			await confirmDialog({
				title: `Are you sure you want to delete user "${username}"?`,
				message: 'This action cannot be undone.',
				variant: 'danger'
			})
		) {
			try {
				await adminApi.deleteUser(userId);
				const wasSelected = viewId === userId;
				await loadUsers();
				await loadMemberships();
				if (wasSelected) navigate({ id: null });
			} catch (error) {
				logger.error('Failed to delete user:', error);
				toasts.error('Failed to delete user.');
			}
		}
	}

	async function handleBulkDeleteUsers() {
		const { deletableIds, skippedCount } = deletableUserIds(selectedUserIds, currentUser?.id);
		if (deletableIds.length === 0) {
			if (skippedCount > 0) toasts.error('You cannot delete your own account.');
			return;
		}
		if (
			!(await confirmDialog({
				title: `Delete ${deletableIds.length} ${deletableIds.length === 1 ? 'user' : 'users'}?`,
				message: `This action cannot be undone.${skippedCount ? ' Your own account was left out of this selection.' : ''}`,
				variant: 'danger'
			}))
		)
			return;
		bulkDeletingUsers = true;
		try {
			await Promise.all(deletableIds.map((id) => adminApi.deleteUser(id)));
			const closeDetail = viewId ? deletableIds.includes(viewId) : false;
			selectedUserIds = new Set();
			await loadUsers();
			await loadMemberships();
			if (closeDetail) navigate({ id: null });
			toasts.success(`${deletableIds.length} ${deletableIds.length === 1 ? 'user' : 'users'} deleted`);
		} catch (error) {
			logger.error('Failed to bulk delete users:', error);
			toasts.error('Failed to delete some users.');
		} finally {
			bulkDeletingUsers = false;
		}
	}

	async function handleAddSelectedUsersToGroup(groupId: string) {
		bulkAdding = true;
		try {
			const result = await adminApi.addUsersToGroup(groupId, Array.from(selectedUserIds));
			if (result.success) {
				await loadMemberships();
				const groupName = groups.find((g) => g.id === groupId)?.name ?? 'the group';
				toasts.success(`Added ${selectedUserIds.size} ${selectedUserIds.size === 1 ? 'user' : 'users'} to ${groupName}`);
				selectedUserIds = new Set();
				showAddToGroupModal = false;
			} else {
				toasts.error(`Failed to add users to group: ${result.message || 'Unknown error'}`);
			}
		} catch (error) {
			logger.error('Failed to add users to group:', error);
			toasts.error('Failed to add users to group.');
		} finally {
			bulkAdding = false;
		}
	}

	async function handleAssignSelectedUsersToPreset(presetId: string) {
		bulkAssigning = true;
		try {
			const result = await adminApi.assignPresetToUsers(presetId, Array.from(selectedUserIds));
			if (result.success) {
				const presetName = allPresets.find((p) => p.id === presetId)?.name ?? 'the preset';
				toasts.success(`Assigned ${selectedUserIds.size} ${selectedUserIds.size === 1 ? 'user' : 'users'} to ${presetName}`);
				selectedUserIds = new Set();
				showAssignPresetModal = false;
			} else {
				toasts.error(`Failed to assign preset: ${result.message || result.error || 'Unknown error'}`);
			}
		} catch (error) {
			logger.error('Failed to assign preset to users:', error);
			toasts.error('Failed to assign preset to users.');
		} finally {
			bulkAssigning = false;
		}
	}

	async function openGroup(id: string) {
		if (
			editGroupDirty &&
			!(await confirmDialog({ title: 'Discard unsaved changes', message: 'Discard unsaved changes to this group?', variant: 'warning' }))
		)
			return;
		navigate({ id });
	}

	function loadGroupEditForm(group: any | null) {
		editGroupFormData = group
			? {
					name: group.name,
					description: group.description || '',
					content_policy: isContentPolicy(group.content_policy) ? group.content_policy : CONTENT_POLICY_INHERIT
				}
			: emptyGroupEditForm();
		editGroupSnapshot = JSON.stringify(editGroupFormData);
	}

	function discardGroupEdit() {
		loadGroupEditForm(activeGroupEntity);
		groupPlanDraft = groupPlanSaved;
	}

	async function saveGroupEdit() {
		if (!activeGroupEntity) return;
		editGroupSaving = true;
		try {
			const updateData: any = {};
			if (editGroupFormData.name) updateData.name = editGroupFormData.name;
			updateData.description = editGroupFormData.description;
			if (!isSystemRestrictedGroup(activeGroupEntity)) {
				updateData.content_policy = isContentPolicy(editGroupFormData.content_policy) ? editGroupFormData.content_policy : null;
			}
			const response: any = await adminApi.updateUserGroup(activeGroupEntity.id, updateData);
			if (response.success !== false) {
				if (groupPlanDraft !== groupPlanSaved) {
					await plansApi.setGroupPlan(activeGroupEntity.id, groupPlanDraft);
					groupPlanSaved = groupPlanDraft;
					await loadPlans();
				}
				toasts.success(`${editGroupFormData.name || activeGroupEntity.name} updated`);
				await loadGroups();
				loadGroupEditForm(groups.find((g) => g.id === activeGroupEntity!.id) ?? null);
			} else {
				toasts.error(response.message || 'Failed to save group');
			}
		} catch (error) {
			logger.error('Failed to save group:', error);
			toasts.error('Failed to save group. Please check the form and try again.');
		} finally {
			editGroupSaving = false;
		}
	}

	function openCreateGroupModal() {
		groupFormData = { name: '', description: '', content_policy: CONTENT_POLICY_INHERIT };
		showGroupModal = true;
	}

	async function handleSaveNewGroup() {
		savingNewGroup = true;
		try {
			const response: any = await adminApi.createUserGroup({
				name: groupFormData.name,
				description: groupFormData.description,
				content_policy: isContentPolicy(groupFormData.content_policy) ? groupFormData.content_policy : null
			});
			await loadGroups();
			await loadMemberships();
			showGroupModal = false;
			const created = response?.data;
			if (created?.id) navigate({ view: 'groups', id: created.id });
		} catch (error) {
			logger.error('Failed to save group:', error);
			toasts.error('Failed to save group. Please check the form and try again.');
		} finally {
			savingNewGroup = false;
			createGroupGate.reset();
		}
	}

	function closeCreateGroupModal() {
		showGroupModal = false;
	}

	function cancelCreateGroup() {
		createGroupGate.settle(closeCreateGroupModal);
	}

	function confirmCreateGroup() {
		settleIfEligible(createGroupGate, !!groupFormData.name && !savingNewGroup, handleSaveNewGroup);
	}

	function handleCreateModalsKeydown(e: KeyboardEvent) {
		if (showUserModal) {
			const { action, suppress } = getConfirmKeyboardAction(e);
			if (action === 'cancel') cancelCreateUser();
			else if (action === 'confirm') confirmCreateUser();
			if (suppress) e.preventDefault();
		} else if (showGroupModal) {
			const { action, suppress } = getConfirmKeyboardAction(e);
			if (action === 'cancel') cancelCreateGroup();
			else if (action === 'confirm') confirmCreateGroup();
			if (suppress) e.preventDefault();
		}
	}

	async function handleDeleteGroup(group: any) {
		if (group.is_system) return;
		if (
			await confirmDialog({
				title: `Are you sure you want to delete group "${group.name}"?`,
				message: 'This action cannot be undone.',
				variant: 'danger'
			})
		) {
			try {
				await adminApi.deleteUserGroup(group.id);
				const wasSelected = viewId === group.id;
				await loadGroups();
				await loadMemberships();
				if (wasSelected) navigate({ id: null });
			} catch (error: any) {
				logger.error('Failed to delete group:', error);
				const message = error?.response?.data?.detail || 'Failed to delete group.';
				toasts.error(message);
			}
		}
	}

	async function handleBulkDeleteGroups() {
		const { deletableIds, skippedCount } = deletableGroupIds(groups, selectedGroupIds);
		if (deletableIds.length === 0) {
			if (skippedCount > 0) toasts.error('Built-in groups cannot be deleted.');
			return;
		}
		if (
			!(await confirmDialog({
				title: `Delete ${deletableIds.length} ${deletableIds.length === 1 ? 'group' : 'groups'}?`,
				message: `This action cannot be undone.${skippedCount ? ' Built-in groups were left out of this selection.' : ''}`,
				variant: 'danger'
			}))
		)
			return;
		bulkDeletingGroups = true;
		try {
			await Promise.all(deletableIds.map((id) => adminApi.deleteUserGroup(id)));
			const closeDetail = viewId ? deletableIds.includes(viewId) : false;
			selectedGroupIds = new Set();
			await loadGroups();
			await loadMemberships();
			if (closeDetail) navigate({ id: null });
			toasts.success(`${deletableIds.length} ${deletableIds.length === 1 ? 'group' : 'groups'} deleted`);
		} catch (error) {
			logger.error('Failed to bulk delete groups:', error);
			toasts.error('Failed to delete some groups.');
		} finally {
			bulkDeletingGroups = false;
		}
	}

	async function toggleUserGroupMembership(userId: string, groupId: string, isMember: boolean) {
		const key = `${userId}:${groupId}`;
		togglingMembership = key;
		try {
			const result = isMember
				? await adminApi.removeUserFromGroup(groupId, userId)
				: await adminApi.addUsersToGroup(groupId, [userId]);
			if (result.success) {
				const membersResult = await adminApi.getGroupMembers(groupId);
				if (membersResult.success) {
					const members = membersResult.data || [];
					membersByGroup = { ...membersByGroup, [groupId]: members };
					groups = groups.map((g) => (g.id === groupId ? { ...g, member_count: members.length } : g));
				}
			} else {
				toasts.error(`Failed to update group membership: ${result.message || 'Unknown error'}`);
			}
		} catch (error) {
			logger.error('Failed to update group membership:', error);
			toasts.error('Failed to update group membership.');
		} finally {
			togglingMembership = null;
		}
	}

	async function loadUserLLMAssignments(userId: string) {
		try {
			loadingUserLLMs = true;
			const response = await adminApi.getUserLLMAssignments(userId);
			if (response.success && response.data) {
				userLLMAssignments = { ...userLLMAssignments, [userId]: response.data.llm_configs || [] };
			}
		} catch (error) {
			logger.error('Failed to load user LLM assignments:', error);
		} finally {
			loadingUserLLMs = false;
		}
	}

	async function handleAssignUserLLM(llmConfigId: string) {
		if (!selectedUserId) return;
		try {
			assigningUserLLM = llmConfigId;
			const result = await adminApi.assignLLMToUser(selectedUserId, llmConfigId);
			if (result.success) {
				await loadUserLLMAssignments(selectedUserId);
			} else {
				toasts.error(`Failed to assign LLM: ${result.message || 'Unknown error'}`);
			}
		} catch (error) {
			logger.error('Failed to assign LLM:', error);
			toasts.error('Failed to assign LLM to user.');
		} finally {
			assigningUserLLM = null;
		}
	}

	async function handleUnassignUserLLM(llmConfigId: string) {
		if (!selectedUserId) return;
		try {
			assigningUserLLM = llmConfigId;
			const result = await adminApi.unassignLLMFromUser(selectedUserId, llmConfigId);
			if (result.success) {
				await loadUserLLMAssignments(selectedUserId);
			} else {
				toasts.error(`Failed to unassign LLM: ${result.message || 'Unknown error'}`);
			}
		} catch (error) {
			logger.error('Failed to unassign LLM:', error);
			toasts.error('Failed to unassign LLM from user.');
		} finally {
			assigningUserLLM = null;
		}
	}

	async function loadUserPresetAssignments(userId: string) {
		try {
			loadingUserPresets = true;
			const assignments: any[] = [];
			for (const preset of allPresets) {
				try {
					const response = await adminApi.getPresetAssignments(preset.id);
					if (response.success && response.data) {
						const presetAssignments = response.data.assignments || [];
						assignments.push(...presetAssignments.filter((a: any) => a.user_id === userId));
					}
				} catch (error) {
					logger.error(`Failed to load assignments for preset ${preset.id}:`, error);
				}
			}
			userPresetAssignments = { ...userPresetAssignments, [userId]: assignments };
		} catch (error) {
			logger.error('Failed to load user preset assignments:', error);
		} finally {
			loadingUserPresets = false;
		}
	}

	async function handleAssignUserPreset(presetId: string) {
		if (!selectedUserId) return;
		try {
			assigningUserPreset = presetId;
			const result = await adminApi.assignPresetToUsers(presetId, [selectedUserId]);
			if (result.success) {
				await loadUserPresetAssignments(selectedUserId);
			} else {
				toasts.error(`Failed to assign preset: ${result.message || result.error || 'Unknown error'}`);
			}
		} catch (error) {
			logger.error('Failed to assign preset:', error);
			toasts.error('Failed to assign preset to user.');
		} finally {
			assigningUserPreset = null;
		}
	}

	async function handleUnassignUserPreset(presetId: string) {
		if (!selectedUserId) return;
		try {
			assigningUserPreset = presetId;
			const result = await adminApi.unassignPresetFromUser(presetId, selectedUserId);
			if (result.success) {
				await loadUserPresetAssignments(selectedUserId);
			} else {
				toasts.error(`Failed to unassign preset: ${result.message || 'Unknown error'}`);
			}
		} catch (error) {
			logger.error('Failed to unassign preset:', error);
			toasts.error('Failed to unassign preset from user.');
		} finally {
			assigningUserPreset = null;
		}
	}

	async function loadUserModelAssignments(userId: string) {
		try {
			loadingUserModelAssignments = true;
			const response = await adminApi.getUserModelAssignments(userId);
			if (response.success && response.data) {
				userModelAssignments = { ...userModelAssignments, [userId]: (response.data.assignments || []).map((a: any) => a.model_id) };
			}
		} catch (error) {
			logger.error('Failed to load user model assignments:', error);
		} finally {
			loadingUserModelAssignments = false;
		}
	}

	async function assignUserModel(modelId: string) {
		if (!selectedUserId) return;
		try {
			assigningUserModel = modelId;
			const result = await adminApi.assignModelToUser(selectedUserId, modelId);
			if (result.success) {
				await loadUserModelAssignments(selectedUserId);
			} else {
				toasts.error(`Failed to assign model: ${result.message || 'Unknown error'}`);
			}
		} catch (error) {
			logger.error('Failed to assign model:', error);
			toasts.error('Failed to assign model to user.');
		} finally {
			assigningUserModel = null;
		}
	}

	async function unassignUserModel(modelId: string) {
		if (!selectedUserId) return;
		try {
			assigningUserModel = modelId;
			const result = await adminApi.unassignModelFromUser(selectedUserId, modelId);
			if (result.success) {
				await loadUserModelAssignments(selectedUserId);
			} else {
				toasts.error(`Failed to unassign model: ${result.message || 'Unknown error'}`);
			}
		} catch (error) {
			logger.error('Failed to unassign model:', error);
			toasts.error('Failed to unassign model from user.');
		} finally {
			assigningUserModel = null;
		}
	}

	async function loadUserMcpSetting(userId: string) {
		try {
			const response = await adminApi.getMcpUserSetting(userId);
			if (response.success && response.data) {
				userMcpEnabled = { ...userMcpEnabled, [userId]: response.data.enabled };
			}
		} catch (error) {
			logger.error('Failed to load MCP access setting:', error);
		}
	}

	async function toggleUserMcp(userId: string, next: boolean) {
		togglingUserMcp = userId;
		try {
			const result = await adminApi.setMcpUserSetting(userId, next);
			if (result.success && result.data) {
				userMcpEnabled = { ...userMcpEnabled, [userId]: result.data.enabled };
			} else {
				toasts.error(`Failed to update MCP access: ${result.message || 'Unknown error'}`);
			}
		} catch (error) {
			logger.error('Failed to update MCP access:', error);
			toasts.error('Failed to update MCP access.');
		} finally {
			togglingUserMcp = null;
		}
	}

	async function loadGroupLLMs(groupId: string) {
		loadingGroupLLMs = true;
		try {
			const result = await adminApi.getGroupLLMs(groupId);
			if (result.success) groupLLMsByGroup = { ...groupLLMsByGroup, [groupId]: result.data || [] };
		} catch (error) {
			logger.error('Failed to load group LLMs:', error);
		} finally {
			loadingGroupLLMs = false;
		}
	}

	async function handleAssignGroupLLM(llmConfigId: string) {
		if (!selectedGroupId) return;
		try {
			assigningGroupLLM = llmConfigId;
			const result = await adminApi.assignLLMsToGroup(selectedGroupId, [llmConfigId]);
			if (result.success) {
				await loadGroupLLMs(selectedGroupId);
			} else {
				toasts.error(`Failed to assign LLM: ${result.message || 'Unknown error'}`);
			}
		} catch (error) {
			logger.error('Failed to assign LLM:', error);
			toasts.error('Failed to assign LLM to group.');
		} finally {
			assigningGroupLLM = null;
		}
	}

	async function handleUnassignGroupLLM(llmConfigId: string) {
		if (!selectedGroupId) return;
		try {
			assigningGroupLLM = llmConfigId;
			const result = await adminApi.unassignLLMFromGroup(selectedGroupId, llmConfigId);
			if (result.success) {
				await loadGroupLLMs(selectedGroupId);
			} else {
				toasts.error(`Failed to unassign LLM: ${result.message || 'Unknown error'}`);
			}
		} catch (error) {
			logger.error('Failed to unassign LLM:', error);
			toasts.error('Failed to unassign LLM from group.');
		} finally {
			assigningGroupLLM = null;
		}
	}

	async function loadGroupPresets(groupId: string) {
		loadingGroupPresets = true;
		try {
			const result = await adminApi.getGroupPresets(groupId);
			if (result.success) groupPresetsByGroup = { ...groupPresetsByGroup, [groupId]: result.data || [] };
		} catch (error) {
			logger.error('Failed to load group presets:', error);
		} finally {
			loadingGroupPresets = false;
		}
	}

	async function handleAssignGroupPreset(presetId: string) {
		if (!selectedGroupId) return;
		try {
			assigningGroupPreset = presetId;
			const result = await adminApi.assignPresetsToGroup(selectedGroupId, [presetId]);
			if (result.success) {
				await loadGroupPresets(selectedGroupId);
			} else {
				toasts.error(`Failed to assign preset: ${result.message || 'Unknown error'}`);
			}
		} catch (error) {
			logger.error('Failed to assign preset:', error);
			toasts.error('Failed to assign preset to group.');
		} finally {
			assigningGroupPreset = null;
		}
	}

	async function handleUnassignGroupPreset(presetId: string) {
		if (!selectedGroupId) return;
		try {
			assigningGroupPreset = presetId;
			const result = await adminApi.unassignPresetFromGroup(selectedGroupId, presetId);
			if (result.success) {
				await loadGroupPresets(selectedGroupId);
			} else {
				toasts.error(`Failed to unassign preset: ${result.message || 'Unknown error'}`);
			}
		} catch (error) {
			logger.error('Failed to unassign preset:', error);
			toasts.error('Failed to unassign preset from group.');
		} finally {
			assigningGroupPreset = null;
		}
	}

	async function loadGroupModelAssignments(groupId: string) {
		loadingGroupModelAssignments = true;
		try {
			const result = await adminApi.getGroupModels(groupId);
			if (result.success) groupModelsByGroup = { ...groupModelsByGroup, [groupId]: result.data || [] };
		} catch (error) {
			logger.error('Failed to load group model assignments:', error);
		} finally {
			loadingGroupModelAssignments = false;
		}
	}

	async function handleAssignGroupModel(modelId: string) {
		if (!selectedGroupId) return;
		try {
			assigningGroupModel = modelId;
			const result = await adminApi.assignModelsToGroup(selectedGroupId, [modelId]);
			if (result.success) {
				await loadGroupModelAssignments(selectedGroupId);
			} else {
				toasts.error(`Failed to assign model: ${result.message || 'Unknown error'}`);
			}
		} catch (error) {
			logger.error('Failed to assign model:', error);
			toasts.error('Failed to assign model to group.');
		} finally {
			assigningGroupModel = null;
		}
	}

	async function handleUnassignGroupModel(modelId: string) {
		if (!selectedGroupId) return;
		try {
			assigningGroupModel = modelId;
			const result = await adminApi.unassignModelFromGroup(selectedGroupId, modelId);
			if (result.success) {
				await loadGroupModelAssignments(selectedGroupId);
			} else {
				toasts.error(`Failed to unassign model: ${result.message || 'Unknown error'}`);
			}
		} catch (error) {
			logger.error('Failed to unassign model:', error);
			toasts.error('Failed to unassign model from group.');
		} finally {
			assigningGroupModel = null;
		}
	}

	const typeCounts = $derived(accountTypeCounts(users));

	const userDetailTabs = $derived(
		activeUser
			? [
					{ id: 'overview', label: 'Overview', icon: 'info' },
					{ id: 'groups', label: 'Groups', icon: 'group', count: (userGroupIds[activeUser.id] || []).length },
					{ id: 'presets', label: 'Presets', icon: 'layers', count: userPresetAssignments[activeUser.id]?.length },
					{ id: 'llms', label: 'LLMs', icon: 'chat', count: userLLMAssignments[activeUser.id]?.length },
					{ id: 'models', label: 'Models', icon: 'cube', count: userModelAssignments[activeUser.id]?.length }
				]
			: []
	);

	const groupDetailTabs = $derived(
		activeGroupEntity
			? [
					{ id: 'overview', label: 'Overview', icon: 'info' },
					{ id: 'users', label: 'Users', icon: 'user', count: activeGroupEntity.member_count ?? 0 },
					{ id: 'presets', label: 'Presets', icon: 'layers', count: groupPresets.length },
					{ id: 'llms', label: 'LLMs', icon: 'chat', count: groupLLMs.length },
					{ id: 'models', label: 'Models', icon: 'cube', count: groupModels.length }
				]
			: []
	);

	const usageKinds = $derived(planKindsInUse);
	const usersSortOptions = $derived([...USERS_SORT_OPTIONS, ...(usageKinds.length ? usageSortOptions(usageKinds) : [])]);
	const filteredUsers = $derived.by(() => {
		const rows = applyUsersFilters(users, usersFilters);
		return isUsageSort(usersFilters.sortBy) ? sortByUsage(rows, usageByUser, usersFilters.sortBy) : rows;
	});
	const userColumns = $derived<DataTableColumn<User>[]>([
		{ key: 'username', label: 'Username', width: 'minmax(180px,1.4fr)', priority: 0 as const, accessor: (u: User) => u.username },
		{ key: 'account_type', label: 'Account type', width: '130px', priority: 0 as const, cell: userAccountTypeCell },
		{ key: 'email', label: 'Email', width: 'minmax(160px,1.6fr)', priority: 1 as const, mono: true, accessor: (u: User) => u.email },
		...(usageKinds.length
			? [
					{ key: 'plan', label: 'Plan', width: '120px', priority: 1 as const, accessor: (u: User) => usageByUser.get(u.id)?.plan?.name ?? '—' },
					{ key: 'plan_source', label: 'Source', width: '90px', priority: 2 as const, accessor: (u: User) => sourceLabel(usageByUser.get(u.id)?.source ?? 'none', usageByUser.get(u.id)?.exempt) },
					...usageKinds.map((kind) => ({
						key: `usage:${kind.key}`,
						label: kind.short_label ?? kind.label,
						width: 'minmax(190px,1.2fr)',
						priority: 1 as const,
						cell: usageColumnCell<User>(kind, () => usageByUser)
					}))
				]
			: []),
		{ key: 'groups', label: 'Groups', width: '90px', priority: 2 as const, align: 'right', mono: true, accessor: (u: User) => (userGroupIds[u.id] || []).length },
		{ key: 'created', label: 'Created', width: '140px', priority: 2 as const, mono: true, accessor: (u: User) => (u.created_at ? timeAgo(u.created_at) : '—') },
		{ key: 'last_login', label: 'Last login', width: '140px', priority: 2 as const, mono: true, accessor: (u: User) => (u.last_login ? timeAgo(u.last_login) : '—') }
	]);

	const filteredPlans = $derived(applyPlansFilters(plans, plansFilters));
	const userFilterChips = $derived(usersFilterChips(usersFilters));
	const activeUserFilterCount = $derived(usersFilterActiveCount(usersFilters));
	function clearUserFilters() {
		updateUsersFilters(clearAllUsersFilters(usersFilters));
	}
	function removeUserFilterChip(key: string) {
		updateUsersFilters(clearUsersFilterChip(usersFilters, key));
	}
	function toggleAccountTypeFilter(type: UserAccountTypeFilter) {
		updateUsersFilters({ ...usersFilters, accountType: usersFilters.accountType === type ? 'all' : type });
	}

	const filteredGroups = $derived(applyGroupsFilters(groups, groupsFilters));
	const groupFilterChips = $derived(groupsFilterChips(groupsFilters));
	function clearGroupFilters() {
		updateGroupsFilters(clearAllGroupsFilters(groupsFilters));
	}
	function removeGroupFilterChip(key: string) {
		updateGroupsFilters(clearGroupsFilterChip(groupsFilters, key));
	}

	const usersPageCount = $derived(pageCount(filteredUsers.length, usersPageSize));
	const pagedUsers = $derived(
		filteredUsers.slice((clampPage(usersPage, usersPageCount) - 1) * usersPageSize, clampPage(usersPage, usersPageCount) * usersPageSize)
	);
	const groupsPageCount = $derived(pageCount(filteredGroups.length, groupsPageSize));
	const pagedGroups = $derived(
		filteredGroups.slice(
			(clampPage(groupsPage, groupsPageCount) - 1) * groupsPageSize,
			clampPage(groupsPage, groupsPageCount) * groupsPageSize
		)
	);

	let lastViewId: string | null = null;
	$effect(() => {
		if (viewId === untrack(() => lastViewId)) return;
		lastViewId = viewId;
		untrack(() => {
			userDetailTab = 'overview';
			groupDetailTab = 'overview';
		});
	});

	let editUserLoadedFor: string | null = null;
	$effect(() => {
		const id = activeUser?.id ?? null;
		if (id === editUserLoadedFor) return;
		editUserLoadedFor = id;
		untrack(() => loadUserEditForm(activeUser));
	});

	let editGroupLoadedFor: string | null = null;
	$effect(() => {
		const id = activeGroupEntity?.id ?? null;
		if (id === editGroupLoadedFor) return;
		editGroupLoadedFor = id;
		untrack(() => {
			loadGroupEditForm(activeGroupEntity);
			groupPlanSaved = groupPlanById[activeGroupEntity?.id ?? ''] ?? null;
			groupPlanDraft = groupPlanSaved;
			groupImpact = null;
		});
	});

	$effect(() => {
		const id = selectedGroupId;
		const planId = groupPlanDraft;
		if (!id || groupDetailTab !== 'overview') return;
		untrack(() => void loadGroupImpact(id, planId));
	});

	let planDetailLoadedFor: string | null = null;
	$effect(() => {
		const id = planDetailId && planDetailId !== NEW_PLAN_ID ? planDetailId : null;
		if (id === planDetailLoadedFor) return;
		planDetailLoadedFor = id;
		untrack(() => {
			planDetail = null;
			if (id) void loadPlanDetail(id);
		});
	});

	let userEffectiveLoadedFor: string | null = null;
	$effect(() => {
		const id = selectedUserId;
		if (id === userEffectiveLoadedFor) return;
		userEffectiveLoadedFor = id;
		untrack(() => {
			userPlanDetail = null;
			userPlanSaved = null;
			userPlanDraft = null;
			if (id) void loadUserEffective(id);
		});
	});

	$effect(() => {
		if (userDetailTab === 'presets' && selectedUserId && !userPresetAssignments[selectedUserId]) {
			void loadUserPresetAssignments(selectedUserId);
		}
	});
	$effect(() => {
		if (userDetailTab === 'llms' && selectedUserId && !userLLMAssignments[selectedUserId]) {
			void loadUserLLMAssignments(selectedUserId);
		}
	});
	$effect(() => {
		if (userDetailTab === 'models' && selectedUserId && !userModelAssignments[selectedUserId]) {
			void loadUserModelAssignments(selectedUserId);
		}
	});
	$effect(() => {
		if (userDetailTab === 'overview' && selectedUserId && !(selectedUserId in userMcpEnabled)) {
			void loadUserMcpSetting(selectedUserId);
		}
	});

	$effect(() => {
		if (groupDetailTab === 'presets' && selectedGroupId && !groupPresetsByGroup[selectedGroupId] && !loadingGroupPresets) {
			void loadGroupPresets(selectedGroupId);
		}
	});
	$effect(() => {
		if (groupDetailTab === 'llms' && selectedGroupId && !groupLLMsByGroup[selectedGroupId] && !loadingGroupLLMs) {
			void loadGroupLLMs(selectedGroupId);
		}
	});
	$effect(() => {
		if (groupDetailTab === 'models' && selectedGroupId && !groupModelsByGroup[selectedGroupId] && !loadingGroupModelAssignments) {
			void loadGroupModelAssignments(selectedGroupId);
		}
	});
</script>

{#snippet userAccountTypeCell(user: User)}
	<Badge size="sm" variant={user.account_type === 'ADMIN' ? 'warning' : 'neutral'} class="font-mono uppercase">{user.account_type}</Badge>
	{#if isUserRestricted(user.id)}<Badge size="sm" variant="danger">Restricted</Badge>{/if}
{/snippet}

{#snippet groupBuiltInCell(group: adminApi.UserGroup)}
	{#if group.is_system}<Badge size="sm" variant="neutral">Built-in</Badge>{/if}
	{#if isSystemRestrictedGroup(group)}<Badge size="sm" variant="danger">Restricted</Badge>{/if}
{/snippet}

{#snippet groupAccessCell(group: adminApi.UserGroup)}
	<Tooltip text="{group.preset_count ?? 0} presets · {group.llm_count ?? 0} LLM configs · {group.model_count ?? 0} models">
		<span class="font-mono text-xs tabular-nums text-fg-muted">{group.preset_count ?? 0} / {group.llm_count ?? 0} / {group.model_count ?? 0}</span>
	</Tooltip>
{/snippet}

{#snippet userBulkActions()}
	<button
		class="px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors flex items-center gap-1.5"
		onclick={() => (showAddToGroupModal = true)}
	>
		<Icon name="group" className="w-4 h-4" />
		Add to group
	</button>
	<button
		class="px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors flex items-center gap-1.5"
		onclick={() => (showAssignPresetModal = true)}
	>
		<Icon name="layers" className="w-4 h-4" />
		Assign preset
	</button>
	<button
		class="px-4 py-1.5 bg-danger-solid text-white text-sm rounded hover:bg-danger-solid/90 transition-colors flex items-center gap-2 font-medium"
		onclick={handleBulkDeleteUsers}
	>
		<Icon name="trash" className="w-4 h-4" />
		{bulkDeletingUsers ? 'Deleting…' : 'Delete'}
	</button>
{/snippet}

{#snippet groupBulkActions()}
	<button
		class="px-4 py-1.5 bg-danger-solid text-white text-sm rounded hover:bg-danger-solid/90 transition-colors flex items-center gap-2 font-medium"
		onclick={handleBulkDeleteGroups}
	>
		<Icon name="trash" className="w-4 h-4" />
		{bulkDeletingGroups ? 'Deleting…' : 'Delete'}
	</button>
{/snippet}

<LibraryShell
	title="Users"
	persistKey="admin-users-library"
	heightClass="h-full"
	sections={USERS_LIBRARY_SECTIONS}
	section={subView}
	onSelectSection={setSubView}
	sectionCounts={{ users: users.length, groups: groups.length, plans: plans.length }}
	count={subView === 'users' ? filteredUsers.length : subView === 'groups' ? filteredGroups.length : filteredPlans.length}
	{detailOpen}
	filterChips={subView === 'users' ? userFilterChips : subView === 'groups' ? groupFilterChips : []}
	onRemoveChip={(key) => (subView === 'users' ? removeUserFilterChip(key) : removeGroupFilterChip(key))}
	onClearFilters={() => (subView === 'users' ? clearUserFilters() : clearGroupFilters())}
	loadedCount={subView === 'users' ? filteredUsers.length : subView === 'groups' ? filteredGroups.length : filteredPlans.length}
	total={subView === 'users' ? users.length : subView === 'groups' ? groups.length : plans.length}
>
	{#snippet sidebarTree()}
		{#if subView === 'users'}
			<div class="border-t border-line">
				<div class="px-3 pb-1 pt-2 font-mono text-xs uppercase tracking-[0.07em] text-fg-subtle">By account type</div>
				<div class="space-y-0.5 p-2 pt-0">
					<PaneRow
						title="Administrators"
						count={typeCounts.ADMIN}
						selected={usersFilters.accountType === 'ADMIN'}
						onclick={() => toggleAccountTypeFilter('ADMIN')}
					/>
					<PaneRow
						title="Regular users"
						count={typeCounts.USER}
						selected={usersFilters.accountType === 'USER'}
						onclick={() => toggleAccountTypeFilter('USER')}
					/>
				</div>
			</div>
		{/if}
	{/snippet}

	{#snippet toolbar()}
		{#if subView === 'users'}
			<LibraryFilterBar
				q={usersFilters.q}
				onQueryChange={(value) => updateUsersFilters({ ...usersFilters, q: value })}
				searchPlaceholder="Search by name or email…"
				sortBy={usersFilters.sortBy}
				sortOptions={usersSortOptions}
				onSortChange={(value) => updateUsersFilters({ ...usersFilters, sortBy: value as UserSortBy })}
				filterCount={activeUserFilterCount}
			>
				{#snippet popover(close: () => void)}
					<FilterPopoverFrame label="User filters" onClearAll={clearUserFilters} onClose={close}>
						<SegmentedFilterGroup
							label="Account type"
							options={USER_ACCOUNT_TYPE_OPTIONS}
							value={usersFilters.accountType}
							onChange={(accountType: UserAccountTypeFilter) => updateUsersFilters({ ...usersFilters, accountType })}
						/>
					</FilterPopoverFrame>
				{/snippet}
			</LibraryFilterBar>
		{:else if subView === 'plans'}
			<LibraryFilterBar
				q={plansFilters.q}
				onQueryChange={(value) => updatePlansFilters({ ...plansFilters, q: value })}
				searchPlaceholder="Search plans…"
			/>
		{:else}
			<LibraryFilterBar
				q={groupsFilters.q}
				onQueryChange={(value) => updateGroupsFilters({ ...groupsFilters, q: value })}
				searchPlaceholder="Search by name or description…"
				sortBy={groupsFilters.sortBy}
				sortOptions={GROUPS_SORT_OPTIONS}
				onSortChange={(value) => updateGroupsFilters({ ...groupsFilters, sortBy: value as GroupSortBy })}
			/>
		{/if}
	{/snippet}

	{#snippet primary()}
		{#if subView === 'users'}
			<Button variant="primary" size="sm" icon="plus" onclick={openCreateUserModal}>Add user</Button>
		{:else if subView === 'plans'}
			<Button variant="primary" size="sm" icon="plus" onclick={() => navigate({ view: 'plans', id: NEW_PLAN_ID })}>New plan</Button>
		{:else}
			<Button variant="primary" size="sm" icon="plus" onclick={openCreateGroupModal}>Add group</Button>
		{/if}
	{/snippet}

	{#if detailOpen}
		{#if subView === 'users'}
			{#if !activeUser}
				<div class="flex h-full items-center justify-center">
					{#if loadingUsers}
						<Spinner size="lg" />
					{:else}
						<EmptyState title="User not found" description="This user may have been removed." icon="group" compact>
							{#snippet actions()}<Button variant="ghost" size="sm" onclick={() => navigate({ id: null })}>Back to users</Button>{/snippet}
						</EmptyState>
					{/if}
				</div>
			{:else}
				<div class="flex h-full flex-col">
					<DetailHeader title={activeUser.username} icon={adminSectionIcon('users')} backLabel="Users" onBack={() => navigate({ id: null })}>
						{#snippet chips()}
							<Badge variant={activeUser.account_type === 'ADMIN' ? 'warning' : 'neutral'} size="sm" class="font-mono uppercase">
								{activeUser.account_type}
							</Badge>
							{#if isUserRestricted(activeUser.id)}<Badge variant="danger" size="sm">Restricted</Badge>{/if}
						{/snippet}
						{#snippet subtitle()}
							{activeUser.email}
						{/snippet}
						{#snippet actions()}
							<Tooltip text={activeUser.id === currentUser?.id ? 'Cannot delete your own account' : 'Delete user'}>
								<IconButton
									icon="trash"
									label="Delete user"
									class="text-danger hover:text-danger hover:bg-danger/10"
									disabled={activeUser.id === currentUser?.id}
									onclick={() => handleDeleteUser(activeUser.id, activeUser.username)}
								/>
							</Tooltip>
						{/snippet}
					</DetailHeader>

					<DetailTabs tabs={userDetailTabs} active={userDetailTab} onSelect={(id) => (userDetailTab = id as UserDetailTab)} ariaLabel="User details" />

					{#if userDetailTab === 'overview'}
						<DetailBody>
							<DetailLayout>
								{#snippet main()}
									<DetailSection label="Identity">
										<div class="space-y-4">
											<DetailField label="Username" id="edit-user-username">
												<Input id="edit-user-username" type="text" bind:value={editUserFormData.username} />
											</DetailField>
											<DetailField label="Email" id="edit-user-email">
												<Input id="edit-user-email" type="text" bind:value={editUserFormData.email} />
											</DetailField>
											<DetailField label="New Password" id="edit-user-password" help="Leave empty to keep current">
												<Input id="edit-user-password" type="password" bind:value={editUserFormData.password} />
											</DetailField>
											<DetailField label="Account Type" id="edit-user-type">
												<select id="edit-user-type" class="input" bind:value={editUserFormData.account_type}>
													<option value="USER">Regular User</option>
													<option value="ADMIN">Administrator</option>
												</select>
											</DetailField>
										</div>
									</DetailSection>
									<UserPlanSection
										plans={plans}
										kinds={planKinds}
										detail={userPlanDetail}
										overrideId={userPlanDraft}
										onChange={(planId) => (userPlanDraft = planId)}
									/>
								{/snippet}

								{#snippet aside()}
									{#if userPlanDetail}<UserResolutionCard detail={userPlanDetail} />{/if}
									<DetailSection label="MCP Access">
										<div class="flex items-start justify-between gap-6">
											<div>
												<p class="text-sm font-medium text-fg mb-1">Allow MCP connections</p>
												<p class="text-sm text-fg-muted">Lets this user mint tokens external MCP clients can use to act as them.</p>
											</div>
											<Switch
												checked={userMcpEnabled[activeUser.id] ?? true}
												busy={togglingUserMcp === activeUser.id}
												onchange={(next) => toggleUserMcp(activeUser.id, next)}
												label="Allow MCP connections"
											/>
										</div>
									</DetailSection>
								{/snippet}
							</DetailLayout>
						</DetailBody>
					{:else if userDetailTab === 'groups'}
						<DetailBody>
							<DetailLayout>
								{#snippet main()}
									<AssignmentList
										items={groups}
										getId={(g) => g.id}
										getSearchText={(g) => `${g.name} ${g.description || ''}`}
										isAssigned={(g) => (userGroupIds[activeUser.id] || []).includes(g.id)}
										isToggling={(g) => togglingMembership === `${activeUser.id}:${g.id}`}
										onToggle={(g) => toggleUserGroupMembership(activeUser.id, g.id, (userGroupIds[activeUser.id] || []).includes(g.id))}
										searchPlaceholder="Search groups…"
										ariaLabel="Groups"
										emptyIcon="group"
										emptyTitle="No groups yet"
										emptyDescription="Create one from the Groups view, then come back here to add this user to it."
									>
										{#snippet row(group)}
											<div class="flex items-center gap-2">
												<p class="text-sm font-medium text-fg truncate">{group.name}</p>
												{#if group.is_system}<Badge variant="neutral" size="sm">Built in</Badge>{/if}
											</div>
											{#if group.description}<p class="font-mono text-xs text-fg-subtle truncate mt-0.5">{group.description}</p>{/if}
										{/snippet}
									</AssignmentList>
								{/snippet}
							</DetailLayout>
						</DetailBody>
					{:else if userDetailTab === 'presets'}
						<DetailBody>
							<DetailLayout>
								{#snippet main()}
									<AssignmentList
										items={allPresets}
										getId={(p) => p.id}
										getSearchText={(p) => `${p.name} ${p.id}`}
										isAssigned={(p) => (userPresetAssignments[activeUser.id] || []).some((a: any) => a.preset_id === p.preset_db_id)}
										isToggling={(p) => assigningUserPreset === p.id}
										onToggle={(p) => {
											const assigned = (userPresetAssignments[activeUser.id] || []).some((a: any) => a.preset_id === p.preset_db_id);
											assigned ? handleUnassignUserPreset(p.id) : handleAssignUserPreset(p.id);
										}}
										loading={loadingUserPresets}
										searchPlaceholder="Search presets…"
										ariaLabel="Presets"
										emptyIcon="layers"
										emptyTitle="No presets installed"
										emptyDescription="Install presets in the Presets tab, then come back here to grant this user access."
									>
										{#snippet row(preset)}
											<p class="text-sm font-medium text-fg truncate">{preset.name}</p>
											<p class="font-mono text-xs text-fg-subtle truncate mt-0.5">{preset.id}</p>
										{/snippet}
									</AssignmentList>
								{/snippet}
							</DetailLayout>
						</DetailBody>
					{:else if userDetailTab === 'llms'}
						<DetailBody>
							<DetailLayout>
								{#snippet main()}
									<AssignmentList
										items={allLLMConfigs}
										getId={(l) => l.id}
										getSearchText={(l) => `${l.name} ${l.type} ${l.model}`}
										isAssigned={(l) => (userLLMAssignments[activeUser.id] || []).some((a: any) => a.id === l.id)}
										isToggling={(l) => assigningUserLLM === l.id}
										onToggle={(l) => {
											const assigned = (userLLMAssignments[activeUser.id] || []).some((a: any) => a.id === l.id);
											assigned ? handleUnassignUserLLM(l.id) : handleAssignUserLLM(l.id);
										}}
										loading={loadingUserLLMs}
										searchPlaceholder="Search LLM configurations…"
										ariaLabel="LLM configurations"
										emptyIcon="chat"
										emptyTitle="No LLM configurations yet"
										emptyDescription="Create an LLM configuration in the LLM Configuration tab, then come back here to grant this user access."
									>
										{#snippet row(llm)}
											<div class="flex items-center gap-2">
												<p class="text-sm font-medium text-fg truncate">{llm.name}</p>
												{#if !llm.enabled}<Badge variant="warning" size="sm">Disabled</Badge>{/if}
											</div>
											<p class="font-mono text-xs text-fg-subtle truncate mt-0.5">{llm.type} · {llm.model}</p>
										{/snippet}
									</AssignmentList>
								{/snippet}
							</DetailLayout>
						</DetailBody>
					{:else if userDetailTab === 'models'}
						<DetailBody>
							<DetailLayout>
								{#snippet main()}
									<DetailSection label="Models" padded={false}>
										<ModelAssignmentPicker
											assignedModelIds={userModelAssignments[activeUser.id] || []}
											processingModelId={assigningUserModel}
											assignedUserId={activeUser.id}
											onAssign={(modelId) => assignUserModel(modelId)}
											onUnassign={(modelId) => unassignUserModel(modelId)}
										/>
									</DetailSection>
								{/snippet}
							</DetailLayout>
						</DetailBody>
					{/if}

					{#if userDetailTab === 'overview'}
						<DetailFooter
							dirtyCount={editUserDirty ? 1 : 0}
							saving={editUserSaving}
							onSave={saveUserEdit}
							onDiscard={discardUserEdit}
						/>
					{/if}
				</div>
			{/if}
		{:else if subView === 'plans'}
			{#if planDetailId !== NEW_PLAN_ID && !activePlan}
				<div class="flex h-full items-center justify-center">
					{#if loadingPlans}
						<Spinner size="lg" />
					{:else}
						<EmptyState title="Plan not found" description="This plan may have been removed." icon="layers" compact>
							{#snippet actions()}<Button variant="ghost" size="sm" onclick={() => navigate({ id: null })}>Back to plans</Button>{/snippet}
						</EmptyState>
					{/if}
				</div>
			{:else}
				{#key planDetailId}
					<PlanEditor
						plan={activePlan}
						kinds={planKinds}
						detail={planDetail}
						saving={savingPlan}
						onSave={savePlan}
						onDelete={activePlan ? () => handleDeletePlan(activePlan) : undefined}
						onBack={() => navigate({ id: null })}
					/>
				{/key}
			{/if}
		{:else if !activeGroupEntity}
			<div class="flex h-full items-center justify-center">
				{#if loadingGroups}
					<Spinner size="lg" />
				{:else}
					<EmptyState title="Group not found" description="This group may have been removed." icon="group" compact>
						{#snippet actions()}<Button variant="ghost" size="sm" onclick={() => navigate({ id: null })}>Back to groups</Button>{/snippet}
					</EmptyState>
				{/if}
			</div>
		{:else}
			<div class="flex h-full flex-col">
				<DetailHeader title={activeGroupEntity.name} icon={adminSectionIcon('users')} backLabel="Users" onBack={() => navigate({ id: null })}>
					{#snippet chips()}
						{#if activeGroupEntity.is_system}<Badge variant="neutral" size="sm">Built-in</Badge>{/if}
						{#if isSystemRestrictedGroup(activeGroupEntity)}<Badge variant="danger" size="sm">Restricted</Badge>{/if}
					{/snippet}
					{#snippet subtitle()}
						{activeGroupEntity.member_count ?? 0} members
					{/snippet}
					{#snippet actions()}
						<Tooltip text={activeGroupEntity.is_system ? "Built-in group — can't be deleted" : 'Delete group'}>
							<IconButton
								icon="trash"
								label="Delete group"
								class="text-danger hover:text-danger hover:bg-danger/10"
								disabled={activeGroupEntity.is_system}
								onclick={() => handleDeleteGroup(activeGroupEntity)}
							/>
						</Tooltip>
					{/snippet}
				</DetailHeader>

				<DetailTabs tabs={groupDetailTabs} active={groupDetailTab} onSelect={(id) => (groupDetailTab = id as GroupDetailTab)} ariaLabel="Group details" />

				{#if groupDetailTab === 'overview'}
					<DetailBody>
						<DetailLayout>
							{#snippet main()}
								<DetailSection label="Identity">
									<div class="space-y-4">
										<DetailField label="Group Name" id="edit-group-name">
											<Input id="edit-group-name" type="text" bind:value={editGroupFormData.name} />
										</DetailField>
										<DetailField label="Description" id="edit-group-description" wide>
											<textarea id="edit-group-description" class="input" rows="3" bind:value={editGroupFormData.description} placeholder="Optional description"></textarea>
										</DetailField>
									</div>
								</DetailSection>
								<DetailSection label="Content policy">
									{#if isSystemRestrictedGroup(activeGroupEntity)}
										<p class="text-sm text-fg-muted" data-restricted-group-note>
											Members are always Blocked. They cannot generate or see NSFW content, cannot change their
											own content mode, and see only media that has been rated. This cannot be overridden.
										</p>
									{:else}
										<div class="flex items-start justify-between gap-6">
											<p class="text-sm text-fg-muted">
												Applies to members of this group. Inherit uses the instance policy.
											</p>
											<SegmentedControl
												variant="toggle"
												ariaLabel="Group content policy"
												items={contentPolicyItems}
												selected={editGroupFormData.content_policy}
												onSelect={(id) => (editGroupFormData.content_policy = id)}
											/>
										</div>
									{/if}
								</DetailSection>
								<GroupPlanSection
									plans={plans}
									kinds={planKinds}
									planId={groupPlanDraft}
									{defaultPlanName}
									impact={groupImpact}
									loadingImpact={loadingGroupImpact}
									onChange={changeGroupPlan}
								/>
							{/snippet}
						</DetailLayout>
					</DetailBody>
				{:else if groupDetailTab === 'users'}
					<DetailBody>
						<DetailLayout>
							{#snippet main()}
								<AssignmentList
									items={users}
									getId={(u) => u.id}
									getSearchText={(u) => `${u.username} ${u.email}`}
									isAssigned={(u) => (userGroupIds[u.id] || []).includes(activeGroupEntity.id)}
									isToggling={(u) => togglingMembership === `${u.id}:${activeGroupEntity.id}`}
									onToggle={(u) => toggleUserGroupMembership(u.id, activeGroupEntity.id, (userGroupIds[u.id] || []).includes(activeGroupEntity.id))}
									searchPlaceholder="Search users…"
									ariaLabel="Users"
									emptyIcon="user"
									emptyTitle="No users yet"
									emptyDescription="Add users from the Users view, then come back here to add them to this group."
								>
									{#snippet row(user)}
										<p class="text-sm font-medium text-fg truncate">{user.username}</p>
										<p class="font-mono text-xs text-fg-subtle truncate mt-0.5">{user.email}</p>
									{/snippet}
								</AssignmentList>
							{/snippet}
						</DetailLayout>
					</DetailBody>
				{:else if groupDetailTab === 'presets'}
					<DetailBody>
						<DetailLayout>
							{#snippet main()}
								<AssignmentList
									items={allPresets}
									getId={(p) => p.id}
									getSearchText={(p) => `${p.name} ${p.id}`}
									isAssigned={(p) => groupPresets.some((gp) => gp.preset_id === p.preset_db_id)}
									isToggling={(p) => assigningGroupPreset === p.id}
									onToggle={(p) => {
										const assigned = groupPresets.some((gp) => gp.preset_id === p.preset_db_id);
										assigned ? handleUnassignGroupPreset(p.id) : handleAssignGroupPreset(p.id);
									}}
									loading={loadingGroupPresets}
									searchPlaceholder="Search presets…"
									ariaLabel="Presets"
									emptyIcon="layers"
									emptyTitle="No presets installed"
									emptyDescription="Install presets in the Presets tab, then come back here to grant this group access."
								>
									{#snippet row(preset)}
										<p class="text-sm font-medium text-fg truncate">{preset.name}</p>
										<p class="font-mono text-xs text-fg-subtle truncate mt-0.5">{preset.id}</p>
									{/snippet}
								</AssignmentList>
							{/snippet}
						</DetailLayout>
					</DetailBody>
				{:else if groupDetailTab === 'llms'}
					<DetailBody>
						<DetailLayout>
							{#snippet main()}
								<AssignmentList
									items={allLLMConfigs}
									getId={(l) => l.id}
									getSearchText={(l) => `${l.name} ${l.type} ${l.model}`}
									isAssigned={(l) => groupLLMs.some((gl) => gl.llm_config_id === l.id)}
									isToggling={(l) => assigningGroupLLM === l.id}
									onToggle={(l) => {
										const assigned = groupLLMs.some((gl) => gl.llm_config_id === l.id);
										assigned ? handleUnassignGroupLLM(l.id) : handleAssignGroupLLM(l.id);
									}}
									loading={loadingGroupLLMs}
									searchPlaceholder="Search LLM configurations…"
									ariaLabel="LLM configurations"
									emptyIcon="chat"
									emptyTitle="No LLM configurations yet"
									emptyDescription="Create an LLM configuration in the LLM Configuration tab, then come back here to grant this group access."
								>
									{#snippet row(llm)}
										<div class="flex items-center gap-2">
											<p class="text-sm font-medium text-fg truncate">{llm.name}</p>
											{#if !llm.enabled}<Badge variant="warning" size="sm">Disabled</Badge>{/if}
										</div>
										<p class="font-mono text-xs text-fg-subtle truncate mt-0.5">{llm.type} · {llm.model}</p>
									{/snippet}
								</AssignmentList>
							{/snippet}
						</DetailLayout>
					</DetailBody>
				{:else if groupDetailTab === 'models'}
					<DetailBody>
						<DetailLayout>
							{#snippet main()}
								<DetailSection label="Models" padded={false}>
									<ModelAssignmentPicker
										assignedModelIds={groupModels.map((gm) => gm.model_id)}
										processingModelId={assigningGroupModel}
										assignedGroupId={activeGroupEntity.id}
										onAssign={(modelId) => handleAssignGroupModel(modelId)}
										onUnassign={(modelId) => handleUnassignGroupModel(modelId)}
									/>
								</DetailSection>
							{/snippet}
						</DetailLayout>
					</DetailBody>
				{/if}

				{#if groupDetailTab === 'overview'}
					<DetailFooter
						dirtyCount={editGroupDirty ? 1 : 0}
						saving={editGroupSaving}
						onSave={saveGroupEdit}
						onDiscard={discardGroupEdit}
					/>
				{/if}
			</div>
		{/if}
	{:else if subView === 'plans'}
		{#if loadingPlans}
			<div class="flex h-full flex-col items-center justify-center">
				<Spinner size="lg" />
				<p class="text-sm text-fg-muted mt-4">Loading plans…</p>
			</div>
		{:else if plansError && plans.length === 0}
			<LoadErrorState message={plansError} onRetry={loadPlans} retrying={loadingPlans} />
		{:else}
			<PlansList
				plans={filteredPlans}
				kinds={planKinds}
				settings={plansSettings}
				onOpen={(plan) => navigate({ view: 'plans', id: plan.id })}
				onSettings={savePlansSettings}
			/>
		{/if}
	{:else if subView === 'users'}
		{#if loadingUsers || loadingMemberships}
			<div class="flex h-full flex-col items-center justify-center">
				<Spinner size="lg" />
				<p class="text-sm text-fg-muted mt-4">Loading users…</p>
			</div>
		{:else if usersError && users.length === 0}
			<LoadErrorState message={usersError} onRetry={loadUsers} retrying={loadingUsers} />
		{:else}
			<div class="flex flex-col gap-3 p-4">
				<DataTable
					columns={userColumns}
					rows={pagedUsers}
					getRowId={(u) => u.id}
					selected={selectedUserIds}
					onSelectedChange={(next) => (selectedUserIds = next)}
					onRowClick={(u) => openUser(u.id)}
					isFiltered={!!usersFilters.q || activeUserFilterCount > 0}
				>
					{#snippet emptyState()}
						<EmptyState icon="group" title="No users yet" description="Accounts show up here once people sign up or you add them yourself." compact>
							{#snippet actions()}<Button variant="primary" size="sm" icon="plus" onclick={openCreateUserModal}>Add user</Button>{/snippet}
						</EmptyState>
					{/snippet}
					{#snippet filteredEmptyState()}
						<EmptyState icon="search" title="No users match your search" description="Try a different name or email." compact>
							{#snippet actions()}<Button variant="ghost" size="sm" onclick={clearUserFilters}>Clear filters</Button>{/snippet}
						</EmptyState>
					{/snippet}
					{#snippet card(u)}
						<div class="flex items-center gap-2">
							<p class="truncate text-sm font-semibold text-fg">{u.username}</p>
							<Badge size="sm" variant={u.account_type === 'ADMIN' ? 'warning' : 'neutral'} class="font-mono uppercase">{u.account_type}</Badge>
							{#if isUserRestricted(u.id)}<Badge size="sm" variant="danger">Restricted</Badge>{/if}
						</div>
						<p class="font-mono text-xs text-fg-subtle truncate mt-0.5">{u.email}</p>
					{/snippet}
				</DataTable>
				<TablePager
					page={clampPage(usersPage, usersPageCount)}
					pageCount={usersPageCount}
					pageSize={usersPageSize}
					onPageChange={(p) => (usersPage = p)}
					onPageSizeChange={(s) => {
						usersPageSize = s;
						usersPage = 1;
					}}
				/>
			</div>
		{/if}
	{:else if loadingGroups || loadingMemberships}
		<div class="flex h-full flex-col items-center justify-center">
			<Spinner size="lg" />
			<p class="text-sm text-fg-muted mt-4">Loading groups…</p>
		</div>
	{:else if groupsError && groups.length === 0}
		<LoadErrorState message={groupsError} onRetry={loadGroups} retrying={loadingGroups} />
	{:else}
		<div class="flex flex-col gap-3 p-4">
			<DataTable
				columns={[
					{ key: 'name', label: 'Name', width: 'minmax(180px,1.4fr)', priority: 0, accessor: (g: adminApi.UserGroup) => g.name },
					{ key: 'built_in', label: 'Built-in', width: '110px', priority: 0, cell: groupBuiltInCell },
					{ key: 'members', label: 'Members', width: '100px', priority: 1, align: 'right', mono: true, accessor: (g: adminApi.UserGroup) => g.member_count ?? 0 },
					{ key: 'access', label: 'Presets / LLMs / Models', width: '170px', priority: 2, cell: groupAccessCell }
				] as DataTableColumn<adminApi.UserGroup>[]}
				rows={pagedGroups}
				getRowId={(g) => g.id}
				selected={selectedGroupIds}
				onSelectedChange={(next) => (selectedGroupIds = next)}
				onRowClick={(g) => openGroup(g.id)}
				isFiltered={!!groupsFilters.q}
			>
				{#snippet emptyState()}
					<EmptyState icon="group" title="No groups yet" description="Groups let you assign presets, LLMs, and models to many users at once." compact>
						{#snippet actions()}<Button variant="primary" size="sm" icon="plus" onclick={openCreateGroupModal}>Add group</Button>{/snippet}
					</EmptyState>
				{/snippet}
				{#snippet filteredEmptyState()}
					<EmptyState icon="search" title="No groups match your search" description="Try a different name or description." compact>
						{#snippet actions()}<Button variant="ghost" size="sm" onclick={clearGroupFilters}>Clear search</Button>{/snippet}
					</EmptyState>
				{/snippet}
				{#snippet card(g)}
					<div class="flex items-center gap-2">
						<p class="truncate text-sm font-semibold text-fg">{g.name}</p>
						{#if g.is_system}<Badge size="sm" variant="neutral">Built-in</Badge>{/if}
					</div>
					<p class="font-mono text-xs text-fg-subtle truncate mt-0.5">{g.member_count ?? 0} members</p>
				{/snippet}
			</DataTable>
			<TablePager
				page={clampPage(groupsPage, groupsPageCount)}
				pageCount={groupsPageCount}
				pageSize={groupsPageSize}
				onPageChange={(p) => (groupsPage = p)}
				onPageSizeChange={(s) => {
					groupsPageSize = s;
					groupsPage = 1;
				}}
			/>
		</div>
	{/if}
</LibraryShell>

{#if subView === 'users'}
	<SelectionActionBar
		active={selectedUserIds.size > 0}
		selectedCount={selectedUserIds.size}
		totalCount={filteredUsers.length}
		onSelectAll={() => (selectedUserIds = selectPage(selectedUserIds, filteredUsers.map((u) => u.id)))}
		onClearSelection={() => (selectedUserIds = clearAll())}
		onClose={() => (selectedUserIds = clearAll())}
	>
		<svelte:fragment slot="actionsBeforeCollection">
			{@render userBulkActions()}
		</svelte:fragment>
	</SelectionActionBar>
{:else if subView === 'groups'}
	<SelectionActionBar
		active={selectedGroupIds.size > 0}
		selectedCount={selectedGroupIds.size}
		totalCount={filteredGroups.length}
		onSelectAll={() => (selectedGroupIds = selectPage(selectedGroupIds, filteredGroups.map((g) => g.id)))}
		onClearSelection={() => (selectedGroupIds = clearAll())}
		onClose={() => (selectedGroupIds = clearAll())}
	>
		<svelte:fragment slot="actionsBeforeCollection">
			{@render groupBulkActions()}
		</svelte:fragment>
	</SelectionActionBar>
{/if}

<BulkPickerModal
	isOpen={showAddToGroupModal}
	title="Add {selectedUserIds.size} {selectedUserIds.size === 1 ? 'user' : 'users'} to a group"
	items={groups}
	getId={(g) => g.id}
	getSearchText={(g) => `${g.name} ${g.description || ''}`}
	searchPlaceholder="Search groups…"
	confirmLabel="Add to group"
	emptyTitle="No groups yet"
	busy={bulkAdding}
	onConfirm={handleAddSelectedUsersToGroup}
	onClose={() => (showAddToGroupModal = false)}
>
	{#snippet row(group)}
		<p class="text-sm font-medium text-fg truncate">{group.name}</p>
		{#if group.description}<p class="font-mono text-xs text-fg-subtle truncate mt-0.5">{group.description}</p>{/if}
	{/snippet}
</BulkPickerModal>

<BulkPickerModal
	isOpen={showAssignPresetModal}
	title="Assign {selectedUserIds.size} {selectedUserIds.size === 1 ? 'user' : 'users'} to a preset"
	items={allPresets}
	getId={(p) => p.id}
	getSearchText={(p) => `${p.name} ${p.id}`}
	searchPlaceholder="Search presets…"
	confirmLabel="Assign preset"
	emptyTitle="No presets installed"
	busy={bulkAssigning}
	onConfirm={handleAssignSelectedUsersToPreset}
	onClose={() => (showAssignPresetModal = false)}
>
	{#snippet row(preset)}
		<p class="text-sm font-medium text-fg truncate">{preset.name}</p>
		<p class="font-mono text-xs text-fg-subtle truncate mt-0.5">{preset.id}</p>
	{/snippet}
</BulkPickerModal>

<svelte:window on:keydown|capture={handleCreateModalsKeydown} />

<BaseModal isOpen={showUserModal} title="Create New User" size="md" handleEscapeKey={false} on:close={cancelCreateUser}>
	<div class="p-6 space-y-4">
		<div>
			<label class="block text-sm font-medium text-fg-muted mb-2" for="user-username-input">Username</label>
			<input id="user-username-input" type="text" class="input w-full" bind:value={userFormData.username} required />
		</div>
		<div>
			<label class="block text-sm font-medium text-fg-muted mb-2" for="user-email-input">Email</label>
			<input id="user-email-input" type="email" class="input w-full" bind:value={userFormData.email} required />
		</div>
		<div>
			<label class="block text-sm font-medium text-fg-muted mb-2" for="user-password-input">Password</label>
			<input id="user-password-input" type="password" class="input w-full" bind:value={userFormData.password} required />
		</div>
		<div>
			<label class="block text-sm font-medium text-fg-muted mb-2" for="user-type-input">Account Type</label>
			<select id="user-type-input" class="input w-full" bind:value={userFormData.account_type}>
				<option value="USER">Regular User</option>
				<option value="ADMIN">Administrator</option>
			</select>
		</div>
	</div>
	<svelte:fragment slot="footer">
		<ConfirmFooter
			confirmLabel="Create User"
			busy={savingNewUser}
			confirmDisabled={!userFormData.username || !userFormData.email || !userFormData.password}
			onCancel={cancelCreateUser}
			onConfirm={confirmCreateUser}
		/>
	</svelte:fragment>
</BaseModal>

<BaseModal isOpen={showGroupModal} title="Create New Group" size="md" handleEscapeKey={false} on:close={cancelCreateGroup}>
	<div class="p-6 space-y-4">
		<div>
			<label class="block text-sm font-medium text-fg-muted mb-2" for="group-name-input">Group Name</label>
			<input id="group-name-input" type="text" class="input w-full" bind:value={groupFormData.name} required />
		</div>
		<div>
			<label class="block text-sm font-medium text-fg-muted mb-2" for="group-description-input">Description</label>
			<textarea id="group-description-input" class="input w-full" rows="3" bind:value={groupFormData.description} placeholder="Optional description"></textarea>
		</div>
		<div>
			<span class="block text-sm font-medium text-fg-muted mb-2">Content policy</span>
			<SegmentedControl
				variant="toggle"
				ariaLabel="Group content policy"
				items={contentPolicyItems}
				selected={groupFormData.content_policy}
				onSelect={(id) => (groupFormData.content_policy = id)}
			/>
		</div>
	</div>
	<svelte:fragment slot="footer">
		<ConfirmFooter
			confirmLabel="Create Group"
			busy={savingNewGroup}
			confirmDisabled={!groupFormData.name}
			onCancel={cancelCreateGroup}
			onConfirm={confirmCreateGroup}
		/>
	</svelte:fragment>
</BaseModal>
