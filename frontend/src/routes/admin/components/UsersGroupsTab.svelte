<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { onMount } from 'svelte';
	import * as adminApi from '$lib/services/admin-api';
	import { api } from '$lib/services/api/index';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import type { User } from '$lib/stores/auth';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import ModelAssignmentPicker from '$lib/components/modals/ModelAssignmentPicker.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, Badge, Input, SegmentedControl, Spinner, EmptyState, Switch } from '$lib/components/ui';
	import { MasterDetailLayout } from '$lib/components/master-detail';
	import { Pane, PaneRow } from '$lib/components/pane';
	import AdminTabShell from './AdminTabShell.svelte';
	import AdminFilterBar from './AdminFilterBar.svelte';
	import AssignmentList from './AssignmentList.svelte';

	export let currentUser: any;

	type SubView = 'users' | 'groups';
	type UserDetailTab = 'overview' | 'groups' | 'presets' | 'llms' | 'models';
	type GroupDetailTab = 'overview' | 'users' | 'presets' | 'llms' | 'models';

	let subView: SubView = 'users';

	// ========== Users ==========
	let users: User[] = [];
	let loadingUsers = true;
	let searchQuery = '';
	let userTypeFilter = 'all';

	let showUserModal = false; // create-only - editing happens in the detail pane
	let userFormData = { username: '', email: '', password: '', account_type: 'USER' };

	let selectedUserId: string | null = null;
	let userDetailTab: UserDetailTab = 'overview';

	type UserEditFormData = { username: string; email: string; password: string; account_type: string };
	function emptyUserFormData(): UserEditFormData {
		return { username: '', email: '', password: '', account_type: 'USER' };
	}
	let editUserFormData: UserEditFormData = emptyUserFormData();
	let editUserSnapshot = JSON.stringify(editUserFormData);
	let editUserSaving = false;
	$: editUserDirty = JSON.stringify(editUserFormData) !== editUserSnapshot;

	// ========== Groups ==========
	let groups: adminApi.UserGroup[] = [];
	let loadingGroups = true;
	let groupSearchQuery = '';

	let showGroupModal = false; // create-only
	let groupFormData = { name: '', description: '' };

	let selectedGroupId: string | null = null;
	let groupDetailTab: GroupDetailTab = 'overview';

	type GroupEditFormData = { name: string; description: string };
	let editGroupFormData: GroupEditFormData = { name: '', description: '' };
	let editGroupSnapshot = JSON.stringify(editGroupFormData);
	let editGroupSaving = false;
	$: editGroupDirty = JSON.stringify(editGroupFormData) !== editGroupSnapshot;

	let membersByGroup: Record<string, adminApi.UserGroupMember[]> = {};
	let loadingMemberships = true;

	$: groupById = Object.fromEntries(groups.map((g) => [g.id, g]));
	$: userGroupIds = (() => {
		const map: Record<string, string[]> = {};
		for (const [groupId, members] of Object.entries(membersByGroup)) {
			for (const member of members) {
				(map[member.user_id] ||= []).push(groupId);
			}
		}
		return map;
	})();

	let togglingMembership: string | null = null; // `${userId}:${groupId}`

	// ========== Shared resource catalogs, used by both Users' and Groups'
	// Presets/LLMs assignment tabs ==========
	let allLLMConfigs: any[] = [];
	let allPresets: any[] = [];

	// ========== Per-user assignment state ==========
	let userLLMAssignments: Record<string, any[]> = {};
	let userPresetAssignments: Record<string, any[]> = {};
	let userModelAssignments: Record<string, string[]> = {};
	let loadingUserLLMs = false;
	let loadingUserPresets = false;
	let loadingUserModelAssignments = false;
	let assigningUserLLM: string | null = null;
	let assigningUserPreset: string | null = null;
	let assigningUserModel: string | null = null;

	// Presence of the key (not its value) distinguishes "not loaded yet" from "loaded, off".
	let userMcpEnabled: Record<string, boolean> = {};
	let togglingUserMcp: string | null = null;

	// ========== Per-group assignment state ==========
	// Keyed by group id, mirroring the per-user assignment maps above: presence of the
	// key (not array length) is what distinguishes "not loaded yet" from "loaded, empty".
	let groupLLMsByGroup: Record<string, any[]> = {};
	let groupPresetsByGroup: Record<string, any[]> = {};
	let groupModelsByGroup: Record<string, any[]> = {};
	let loadingGroupLLMs = false;
	let loadingGroupPresets = false;
	let loadingGroupModelAssignments = false;
	let assigningGroupLLM: string | null = null;
	let assigningGroupPreset: string | null = null;
	let assigningGroupModel: string | null = null;

	$: groupLLMs = selectedGroupId ? (groupLLMsByGroup[selectedGroupId] ?? []) : [];
	$: groupPresets = selectedGroupId ? (groupPresetsByGroup[selectedGroupId] ?? []) : [];
	$: groupModels = selectedGroupId ? (groupModelsByGroup[selectedGroupId] ?? []) : [];

	onMount(async () => {
		await Promise.all([loadUsers(), loadGroups()]);
		await loadMemberships();
		await Promise.all([loadLLMConfigs(), loadPresets()]);
	});

	// ---------- Loading ----------

	async function loadUsers() {
		try {
			loadingUsers = true;
			const response = await adminApi.getUsers();
			if (response.success && response.data) {
				users = response.data;
			}
		} catch (error) {
			logger.error('Failed to load users:', error);
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
			}
		} catch (error) {
			logger.error('Failed to load user groups:', error);
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

	// ---------- User selection / edit draft ----------

	async function selectUser(id: string) {
		if (editUserDirty && !(await confirmDialog({
			title: 'Discard unsaved changes',
			message: 'Discard unsaved changes to this user?',
			variant: 'warning'
		}))) return;
		selectedUserId = id;
		userDetailTab = 'overview';
		loadUserEditForm(users.find((u) => u.id === id) ?? null);
	}

	function loadUserEditForm(user: any | null) {
		editUserFormData = user
			? { username: user.username, email: user.email, password: '', account_type: user.account_type }
			: emptyUserFormData();
		editUserSnapshot = JSON.stringify(editUserFormData);
	}

	function discardUserEdit() {
		loadUserEditForm(activeUser);
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

	// ---------- User create (modal, create-only) ----------

	function openCreateUserModal() {
		userFormData = { username: '', email: '', password: '', account_type: 'USER' };
		showUserModal = true;
	}

	async function handleSaveNewUser() {
		try {
			const response: any = await adminApi.createUser(userFormData);
			await loadUsers();
			showUserModal = false;
			const created = response?.data;
			if (created?.id) selectUser(created.id);
		} catch (error) {
			logger.error('Failed to save user:', error);
			toasts.error('Failed to save user. Please check the form and try again.');
		}
	}

	async function handleDeleteUser(userId: string, username: string) {
		if (userId === currentUser?.id) {
			toasts.error('You cannot delete your own account.');
			return;
		}

		if (await confirmDialog({
			title: `Are you sure you want to delete user "${username}"?`,
			message: 'This action cannot be undone.',
			variant: 'danger'
		})) {
			try {
				await adminApi.deleteUser(userId);
				const wasSelected = selectedUserId === userId;
				await loadUsers();
				await loadMemberships();
				if (wasSelected) {
					selectedUserId = null;
					loadUserEditForm(null);
				}
			} catch (error) {
				logger.error('Failed to delete user:', error);
				toasts.error('Failed to delete user.');
			}
		}
	}

	// ---------- Group selection / edit draft ----------

	async function selectGroup(id: string) {
		if (editGroupDirty && !(await confirmDialog({
			title: 'Discard unsaved changes',
			message: 'Discard unsaved changes to this group?',
			variant: 'warning'
		}))) return;
		selectedGroupId = id;
		groupDetailTab = 'overview';
		loadGroupEditForm(groups.find((g) => g.id === id) ?? null);
	}

	function loadGroupEditForm(group: any | null) {
		editGroupFormData = group ? { name: group.name, description: group.description || '' } : { name: '', description: '' };
		editGroupSnapshot = JSON.stringify(editGroupFormData);
	}

	function discardGroupEdit() {
		loadGroupEditForm(activeGroupEntity);
	}

	async function saveGroupEdit() {
		if (!activeGroupEntity) return;
		editGroupSaving = true;
		try {
			const updateData: any = {};
			if (editGroupFormData.name) updateData.name = editGroupFormData.name;
			updateData.description = editGroupFormData.description;
			const response: any = await adminApi.updateUserGroup(activeGroupEntity.id, updateData);
			if (response.success !== false) {
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

	// ---------- Group create (modal, create-only) ----------

	function openCreateGroupModal() {
		groupFormData = { name: '', description: '' };
		showGroupModal = true;
	}

	async function handleSaveNewGroup() {
		try {
			const response: any = await adminApi.createUserGroup(groupFormData);
			await loadGroups();
			await loadMemberships();
			showGroupModal = false;
			const created = response?.data;
			if (created?.id) selectGroup(created.id);
		} catch (error) {
			logger.error('Failed to save group:', error);
			toasts.error('Failed to save group. Please check the form and try again.');
		}
	}

	async function handleDeleteGroup(group: any) {
		if (group.is_system) return;
		if (await confirmDialog({
			title: `Are you sure you want to delete group "${group.name}"?`,
			message: 'This action cannot be undone.',
			variant: 'danger'
		})) {
			try {
				await adminApi.deleteUserGroup(group.id);
				const wasSelected = selectedGroupId === group.id;
				await loadGroups();
				await loadMemberships();
				if (wasSelected) {
					selectedGroupId = null;
					loadGroupEditForm(null);
				}
			} catch (error: any) {
				logger.error('Failed to delete group:', error);
				const message = error?.response?.data?.detail || 'Failed to delete group.';
				toasts.error(message);
			}
		}
	}

	// ---------- Group membership (immediate-commit, from either detail pane) ----------

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

	// ---------- Per-user: LLM assignments ----------

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

	// ---------- Per-user: preset assignments ----------

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

	// ---------- Per-user: model assignments ----------

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

	// ---------- Per-user: MCP access ----------

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

	// ---------- Per-group: LLM assignments ----------

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

	// ---------- Per-group: preset assignments ----------

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

	// ---------- Per-group: model assignments ----------

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

	// ---------- Derived ----------

	$: activeUser = users.find((u) => u.id === selectedUserId) ?? null;
	$: activeGroupEntity = groups.find((g) => g.id === selectedGroupId) ?? null;

	$: filteredUsers = users.filter((user: any) => {
		const matchesSearch =
			user.username.toLowerCase().includes(searchQuery.toLowerCase()) ||
			user.email.toLowerCase().includes(searchQuery.toLowerCase());
		const matchesType = userTypeFilter === 'all' || user.account_type === userTypeFilter;
		return matchesSearch && matchesType;
	});
	$: activeUserFilterCount = Number(!!searchQuery.trim()) + Number(userTypeFilter !== 'all');
	function clearUserFilters() {
		searchQuery = '';
		userTypeFilter = 'all';
	}

	$: filteredGroups = groups.filter((group: any) => {
		if (!groupSearchQuery.trim()) return true;
		const q = groupSearchQuery.trim().toLowerCase();
		return group.name.toLowerCase().includes(q) || (group.description || '').toLowerCase().includes(q);
	});
	$: activeGroupFilterCount = Number(!!groupSearchQuery.trim());

	$: if (userDetailTab === 'presets' && selectedUserId && !userPresetAssignments[selectedUserId]) {
		loadUserPresetAssignments(selectedUserId);
	}
	$: if (userDetailTab === 'llms' && selectedUserId && !userLLMAssignments[selectedUserId]) {
		loadUserLLMAssignments(selectedUserId);
	}
	$: if (userDetailTab === 'models' && selectedUserId && !userModelAssignments[selectedUserId]) {
		loadUserModelAssignments(selectedUserId);
	}
	$: if (userDetailTab === 'overview' && selectedUserId && !(selectedUserId in userMcpEnabled)) {
		loadUserMcpSetting(selectedUserId);
	}

	$: if (groupDetailTab === 'presets' && selectedGroupId && !groupPresetsByGroup[selectedGroupId] && !loadingGroupPresets) {
		loadGroupPresets(selectedGroupId);
	}
	$: if (groupDetailTab === 'llms' && selectedGroupId && !groupLLMsByGroup[selectedGroupId] && !loadingGroupLLMs) {
		loadGroupLLMs(selectedGroupId);
	}
	$: if (groupDetailTab === 'models' && selectedGroupId && !groupModelsByGroup[selectedGroupId] && !loadingGroupModelAssignments) {
		loadGroupModelAssignments(selectedGroupId);
	}
</script>

<div class="flex h-[calc(100dvh-var(--header-h)-2rem)] min-h-[36rem] flex-col gap-4 sm:h-[calc(100dvh-var(--header-h)-3rem)]">
	<SegmentedControl
		items={[
			{ id: 'users', label: 'Users', icon: 'group', count: users.length },
			{ id: 'groups', label: 'Groups', icon: 'group', count: groups.length }
		]}
		selected={subView}
		onSelect={(id) => (subView = id as SubView)}
		ariaLabel="Users / Groups views"
	/>

	<AdminTabShell
		title={subView === 'users' ? 'Users' : 'Groups'}
		icon="group"
		counts={subView === 'users'
			? [{ label: users.length === 1 ? 'user' : 'users', value: users.length }]
			: [{ label: groups.length === 1 ? 'group' : 'groups', value: groups.length }]}
	>
		{#snippet actions()}
			{#if subView === 'users'}
				<Button variant="primary" size="sm" icon="plus" onclick={openCreateUserModal}>Add User</Button>
			{:else}
				<Button variant="primary" size="sm" icon="plus" onclick={openCreateGroupModal}>Add Group</Button>
			{/if}
		{/snippet}
	</AdminTabShell>

	{#if subView === 'users'}
		{#snippet userSearch()}
			<div class="relative">
				<Icon name="search" className="w-4 h-4 text-fg-subtle absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
				<Input bind:value={searchQuery} type="search" class="pl-9" placeholder="Search by name or email…" aria-label="Search users" />
			</div>
		{/snippet}
		{#snippet userFilters()}
			<select class="input" bind:value={userTypeFilter} aria-label="Filter by account type">
				<option value="all">All Users</option>
				<option value="USER">Regular Users</option>
				<option value="ADMIN">Administrators</option>
			</select>
		{/snippet}
		{#snippet userTrailing()}
			<span class="text-sm text-fg-muted whitespace-nowrap font-mono tabular-nums">{filteredUsers.length} {filteredUsers.length === 1 ? 'user' : 'users'}</span>
		{/snippet}

		<AdminFilterBar
			search={userSearch}
			filters={userFilters}
			trailing={userTrailing}
			activeCount={activeUserFilterCount}
			onClear={clearUserFilters}
		/>

		<section class="flex-1 min-h-0 rounded-lg border border-line bg-surface-1 overflow-hidden">
			{#if loadingUsers || loadingMemberships}
				<div class="h-full flex flex-col items-center justify-center">
					<Spinner size="lg" />
					<p class="text-sm text-fg-muted mt-4">Loading users…</p>
				</div>
			{:else if users.length === 0}
				<div class="h-full p-5 flex items-center justify-center">
					<EmptyState
						icon="group"
						title="No users yet"
						description="Accounts show up here once people sign up or you add them yourself."
						compact
					>
						{#snippet actions()}<Button variant="primary" size="sm" icon="plus" onclick={openCreateUserModal}>Add User</Button>{/snippet}
					</EmptyState>
				</div>
			{:else}
				<MasterDetailLayout leftWidth={340} minWidth={280} maxWidth={480} storageKey="admin-users-width">
					<div slot="list" class="h-full min-h-0">
						<Pane
							label="Users"
							count={filteredUsers.length}
							isEmpty={filteredUsers.length === 0}
							bodyRole="listbox"
							ariaLabel="Users"
						>
							{#snippet empty()}
								<div class="p-4 h-full flex items-center justify-center">
									<EmptyState title="No users match your search" description="Try a different name or email." icon="search" compact>
										{#snippet actions()}<Button variant="ghost" size="sm" onclick={clearUserFilters}>Clear filters</Button>{/snippet}
									</EmptyState>
								</div>
							{/snippet}

							{#snippet children()}
								{#each filteredUsers as user (user.id)}
									{#snippet userBadges()}
										{#if user.account_type === 'ADMIN'}<Badge variant="warning" size="sm">admin</Badge>{/if}
									{/snippet}
									{#snippet userTrailing()}
										{#if (userGroupIds[user.id] || []).length}
											<Badge variant="neutral" size="sm">{(userGroupIds[user.id] || []).length}</Badge>
										{/if}
									{/snippet}
									<PaneRow
										selected={selectedUserId === user.id}
										onclick={() => selectUser(user.id)}
										title={user.username}
										subtitle={user.email}
										subtitleMono
										badges={userBadges}
										trailing={userTrailing}
									/>
								{/each}
							{/snippet}
						</Pane>
					</div>

					<div slot="detail" class="h-full min-h-0 flex flex-col">
						{#if activeUser}
							<div class="flex flex-wrap items-center gap-2 px-4 sm:px-5 py-2.5 border-b border-line bg-surface-1 flex-shrink-0">
								<nav class="inline-flex items-center gap-1" aria-label="User details">
									<button
										type="button"
										class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors {userDetailTab === 'overview' ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
										on:click={() => (userDetailTab = 'overview')}
										aria-current={userDetailTab === 'overview' ? 'page' : undefined}
									><Icon name="info" className="w-3.5 h-3.5" />Overview</button>
									<button
										type="button"
										class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors {userDetailTab === 'groups' ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
										on:click={() => (userDetailTab = 'groups')}
										aria-current={userDetailTab === 'groups' ? 'page' : undefined}
									>
										<Icon name="group" className="w-3.5 h-3.5" />Groups
										<span class="font-mono text-2xs opacity-70">{(userGroupIds[activeUser.id] || []).length}</span>
									</button>
									<button
										type="button"
										class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors {userDetailTab === 'presets' ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
										on:click={() => (userDetailTab = 'presets')}
										aria-current={userDetailTab === 'presets' ? 'page' : undefined}
									>
										<Icon name="layers" className="w-3.5 h-3.5" />Presets
										{#if userPresetAssignments[activeUser.id]}<span class="font-mono text-2xs opacity-70">{userPresetAssignments[activeUser.id].length}</span>{/if}
									</button>
									<button
										type="button"
										class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors {userDetailTab === 'llms' ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
										on:click={() => (userDetailTab = 'llms')}
										aria-current={userDetailTab === 'llms' ? 'page' : undefined}
									>
										<Icon name="chat" className="w-3.5 h-3.5" />LLMs
										{#if userLLMAssignments[activeUser.id]}<span class="font-mono text-2xs opacity-70">{userLLMAssignments[activeUser.id].length}</span>{/if}
									</button>
									<button
										type="button"
										class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors {userDetailTab === 'models' ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
										on:click={() => (userDetailTab = 'models')}
										aria-current={userDetailTab === 'models' ? 'page' : undefined}
									>
										<Icon name="cube" className="w-3.5 h-3.5" />Models
										{#if userModelAssignments[activeUser.id]}<span class="font-mono text-2xs opacity-70">{userModelAssignments[activeUser.id].length}</span>{/if}
									</button>
								</nav>
								<div class="ml-auto flex items-center gap-2 flex-wrap">
									{#if editUserDirty}<Badge variant="warning" size="sm" dot>Unsaved</Badge>{/if}
									<Button
										variant="ghost"
										size="sm"
										icon="trash"
										class="text-danger hover:text-danger"
										title={activeUser.id === currentUser?.id ? 'Cannot delete yourself' : 'Delete user'}
										disabled={activeUser.id === currentUser?.id}
										onclick={() => handleDeleteUser(activeUser.id, activeUser.username)}
									/>
								</div>
							</div>

							<div class="flex-1 min-h-0 overflow-y-auto bg-surface-2">
								{#if userDetailTab === 'overview'}
									<div class="p-4 sm:p-5 space-y-5">
										<div class="flex items-center gap-2 flex-wrap">
											<h2 class="text-base font-semibold text-fg truncate">{activeUser.username}</h2>
											<Badge variant="neutral" size="sm" class="font-mono uppercase">{activeUser.account_type}</Badge>
											<span class="font-mono text-2xs text-fg-subtle truncate">{activeUser.email}</span>
										</div>
										<div class="max-w-2xl space-y-5">
											<section class="rounded-lg border border-line bg-surface-1 shadow-raised">
												<div class="px-4 sm:px-5 py-3 border-b border-line">
													<h3 class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted">Identity</h3>
												</div>
												<div class="px-4 sm:px-5 py-4 space-y-4">
													<div>
														<label for="edit-user-username" class="block text-sm font-medium text-fg-muted mb-1">Username</label>
														<Input id="edit-user-username" type="text" bind:value={editUserFormData.username} />
													</div>
													<div>
														<label for="edit-user-email" class="block text-sm font-medium text-fg-muted mb-1">Email</label>
														<Input id="edit-user-email" type="text" bind:value={editUserFormData.email} />
													</div>
													<div>
														<label for="edit-user-password" class="block text-sm font-medium text-fg-muted mb-1">New Password</label>
														<Input id="edit-user-password" type="password" bind:value={editUserFormData.password} placeholder="Leave empty to keep current" />
													</div>
													<div>
														<label for="edit-user-type" class="block text-sm font-medium text-fg-muted mb-1">Account Type</label>
														<select id="edit-user-type" class="input" bind:value={editUserFormData.account_type}>
															<option value="USER">Regular User</option>
															<option value="ADMIN">Administrator</option>
														</select>
													</div>
												</div>
											</section>

											<div class="flex items-center justify-end gap-2">
												<Button variant="ghost" size="sm" disabled={!editUserDirty} onclick={discardUserEdit}>Discard</Button>
												<Button variant="primary" size="sm" loading={editUserSaving} disabled={!editUserDirty} onclick={saveUserEdit}>Save</Button>
											</div>

											<section class="rounded-lg border border-line bg-surface-1 shadow-raised">
												<div class="px-4 sm:px-5 py-3 border-b border-line">
													<h3 class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted">MCP Access</h3>
												</div>
												<div class="px-4 sm:px-5 py-4 flex items-start justify-between gap-6">
													<div>
														<p class="text-sm font-medium text-fg mb-1">Allow MCP connections</p>
														<p class="text-sm text-fg-muted">
															Lets this user mint tokens external MCP clients can use to act as them.
														</p>
													</div>
													<Switch
														checked={userMcpEnabled[activeUser.id] ?? true}
														busy={togglingUserMcp === activeUser.id}
														onchange={(next) => toggleUserMcp(activeUser.id, next)}
														label="Allow MCP connections"
													/>
												</div>
											</section>
										</div>
									</div>
								{:else if userDetailTab === 'groups'}
									<div class="p-4 sm:p-5">
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
												{#if group.description}<p class="font-mono text-2xs text-fg-subtle truncate mt-0.5">{group.description}</p>{/if}
											{/snippet}
										</AssignmentList>
									</div>
								{:else if userDetailTab === 'presets'}
									<div class="p-4 sm:p-5">
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
												<p class="font-mono text-2xs text-fg-subtle truncate mt-0.5">{preset.id}</p>
											{/snippet}
										</AssignmentList>
									</div>
								{:else if userDetailTab === 'llms'}
									<div class="p-4 sm:p-5">
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
												<p class="font-mono text-2xs text-fg-subtle truncate mt-0.5">{llm.type} · {llm.model}</p>
											{/snippet}
										</AssignmentList>
									</div>
								{:else if userDetailTab === 'models'}
									<div class="p-4 sm:p-5">
										<div class="rounded-lg border border-line bg-surface-1 overflow-hidden">
											<ModelAssignmentPicker
												assignedModelIds={userModelAssignments[activeUser.id] || []}
												processingModelId={assigningUserModel}
												assignedUserId={activeUser.id}
												onAssign={(modelId) => assignUserModel(modelId)}
												onUnassign={(modelId) => unassignUserModel(modelId)}
											/>
										</div>
									</div>
								{/if}
							</div>
						{:else}
							<div class="h-full p-5 flex items-center justify-center">
								<EmptyState title="No user selected" description="Choose a user from the list to see and edit their details." icon="group" compact />
							</div>
						{/if}
					</div>
				</MasterDetailLayout>
			{/if}
		</section>
	{:else}
		{#snippet groupSearch()}
			<div class="relative">
				<Icon name="search" className="w-4 h-4 text-fg-subtle absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
				<Input bind:value={groupSearchQuery} type="search" class="pl-9" placeholder="Search by name or description…" aria-label="Search groups" />
			</div>
		{/snippet}
		{#snippet groupTrailing()}
			<span class="text-sm text-fg-muted whitespace-nowrap font-mono tabular-nums">{filteredGroups.length} {filteredGroups.length === 1 ? 'group' : 'groups'}</span>
		{/snippet}

		<AdminFilterBar
			search={groupSearch}
			trailing={groupTrailing}
			activeCount={activeGroupFilterCount}
			onClear={() => (groupSearchQuery = '')}
		/>

		<section class="flex-1 min-h-0 rounded-lg border border-line bg-surface-1 overflow-hidden">
			{#if loadingGroups || loadingMemberships}
				<div class="h-full flex flex-col items-center justify-center">
					<Spinner size="lg" />
					<p class="text-sm text-fg-muted mt-4">Loading groups…</p>
				</div>
			{:else if groups.length === 0}
				<div class="h-full p-5 flex items-center justify-center">
					<EmptyState
						icon="group"
						title="No groups yet"
						description="Groups let you assign presets, LLMs, and models to many users at once."
						compact
					>
						{#snippet actions()}<Button variant="primary" size="sm" icon="plus" onclick={openCreateGroupModal}>Add Group</Button>{/snippet}
					</EmptyState>
				</div>
			{:else}
				<MasterDetailLayout leftWidth={340} minWidth={280} maxWidth={480} storageKey="admin-groups-width">
					<div slot="list" class="h-full min-h-0">
						<Pane
							label="Groups"
							count={filteredGroups.length}
							isEmpty={filteredGroups.length === 0}
							bodyRole="listbox"
							ariaLabel="Groups"
						>
							{#snippet empty()}
								<div class="p-4 h-full flex items-center justify-center">
									<EmptyState title="No groups match your search" description="Try a different name or description." icon="search" compact>
										{#snippet actions()}<Button variant="ghost" size="sm" onclick={() => (groupSearchQuery = '')}>Clear search</Button>{/snippet}
									</EmptyState>
								</div>
							{/snippet}

							{#snippet children()}
								{#each filteredGroups as group (group.id)}
									{#snippet groupBadges()}
										{#if group.is_system}<Badge variant="neutral" size="sm">Built in</Badge>{/if}
									{/snippet}
									{#snippet groupTrailingBadge()}
										<Badge variant="neutral" size="sm">{group.member_count ?? 0}</Badge>
									{/snippet}
									<PaneRow
										selected={selectedGroupId === group.id}
										onclick={() => selectGroup(group.id)}
										title={group.name}
										subtitle={group.description || undefined}
										subtitleMono
										badges={groupBadges}
										trailing={groupTrailingBadge}
									/>
								{/each}
							{/snippet}
						</Pane>
					</div>

					<div slot="detail" class="h-full min-h-0 flex flex-col">
						{#if activeGroupEntity}
							<div class="flex flex-wrap items-center gap-2 px-4 sm:px-5 py-2.5 border-b border-line bg-surface-1 flex-shrink-0">
								<nav class="inline-flex items-center gap-1" aria-label="Group details">
									<button
										type="button"
										class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors {groupDetailTab === 'overview' ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
										on:click={() => (groupDetailTab = 'overview')}
										aria-current={groupDetailTab === 'overview' ? 'page' : undefined}
									><Icon name="info" className="w-3.5 h-3.5" />Overview</button>
									<button
										type="button"
										class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors {groupDetailTab === 'users' ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
										on:click={() => (groupDetailTab = 'users')}
										aria-current={groupDetailTab === 'users' ? 'page' : undefined}
									><Icon name="user" className="w-3.5 h-3.5" />Users<span class="font-mono text-2xs opacity-70">{activeGroupEntity.member_count ?? 0}</span></button>
									<button
										type="button"
										class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors {groupDetailTab === 'presets' ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
										on:click={() => (groupDetailTab = 'presets')}
										aria-current={groupDetailTab === 'presets' ? 'page' : undefined}
									><Icon name="layers" className="w-3.5 h-3.5" />Presets<span class="font-mono text-2xs opacity-70">{groupPresets.length}</span></button>
									<button
										type="button"
										class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors {groupDetailTab === 'llms' ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
										on:click={() => (groupDetailTab = 'llms')}
										aria-current={groupDetailTab === 'llms' ? 'page' : undefined}
									><Icon name="chat" className="w-3.5 h-3.5" />LLMs<span class="font-mono text-2xs opacity-70">{groupLLMs.length}</span></button>
									<button
										type="button"
										class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors {groupDetailTab === 'models' ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
										on:click={() => (groupDetailTab = 'models')}
										aria-current={groupDetailTab === 'models' ? 'page' : undefined}
									><Icon name="cube" className="w-3.5 h-3.5" />Models<span class="font-mono text-2xs opacity-70">{groupModels.length}</span></button>
								</nav>
								<div class="ml-auto flex items-center gap-2 flex-wrap">
									{#if editGroupDirty}<Badge variant="warning" size="sm" dot>Unsaved</Badge>{/if}
									<Button
										variant="ghost"
										size="sm"
										icon="trash"
										class="text-danger hover:text-danger"
										title={activeGroupEntity.is_system ? "Built-in group — can't be deleted" : 'Delete group'}
										disabled={activeGroupEntity.is_system}
										onclick={() => handleDeleteGroup(activeGroupEntity)}
									/>
								</div>
							</div>

							<div class="flex-1 min-h-0 overflow-y-auto bg-surface-2">
								{#if groupDetailTab === 'overview'}
									<div class="p-4 sm:p-5 space-y-5">
										<div class="flex items-center gap-2 flex-wrap">
											<h2 class="text-base font-semibold text-fg truncate">{activeGroupEntity.name}</h2>
											{#if activeGroupEntity.is_system}<Badge variant="neutral" size="sm">Built in</Badge>{/if}
											<span class="font-mono text-2xs text-fg-subtle">{activeGroupEntity.member_count ?? 0} members</span>
										</div>
										<div class="max-w-2xl space-y-5">
											<section class="rounded-lg border border-line bg-surface-1 shadow-raised">
												<div class="px-4 sm:px-5 py-3 border-b border-line">
													<h3 class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted">Identity</h3>
												</div>
												<div class="px-4 sm:px-5 py-4 space-y-4">
													<div>
														<label for="edit-group-name" class="block text-sm font-medium text-fg-muted mb-1">Group Name</label>
														<Input id="edit-group-name" type="text" bind:value={editGroupFormData.name} />
													</div>
													<div>
														<label for="edit-group-description" class="block text-sm font-medium text-fg-muted mb-1">Description</label>
														<textarea id="edit-group-description" class="input" rows="3" bind:value={editGroupFormData.description} placeholder="Optional description"></textarea>
													</div>
												</div>
											</section>

											<div class="flex items-center justify-end gap-2">
												<Button variant="ghost" size="sm" disabled={!editGroupDirty} onclick={discardGroupEdit}>Discard</Button>
												<Button variant="primary" size="sm" loading={editGroupSaving} disabled={!editGroupDirty} onclick={saveGroupEdit}>Save</Button>
											</div>
										</div>
									</div>
								{:else if groupDetailTab === 'users'}
									<div class="p-4 sm:p-5">
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
												<p class="font-mono text-2xs text-fg-subtle truncate mt-0.5">{user.email}</p>
											{/snippet}
										</AssignmentList>
									</div>
								{:else if groupDetailTab === 'presets'}
									<div class="p-4 sm:p-5">
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
												<p class="font-mono text-2xs text-fg-subtle truncate mt-0.5">{preset.id}</p>
											{/snippet}
										</AssignmentList>
									</div>
								{:else if groupDetailTab === 'llms'}
									<div class="p-4 sm:p-5">
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
												<p class="font-mono text-2xs text-fg-subtle truncate mt-0.5">{llm.type} · {llm.model}</p>
											{/snippet}
										</AssignmentList>
									</div>
								{:else if groupDetailTab === 'models'}
									<div class="p-4 sm:p-5">
										<div class="rounded-lg border border-line bg-surface-1 overflow-hidden">
											<ModelAssignmentPicker
												assignedModelIds={groupModels.map((gm) => gm.model_id)}
												processingModelId={assigningGroupModel}
												assignedGroupId={activeGroupEntity.id}
												onAssign={(modelId) => handleAssignGroupModel(modelId)}
												onUnassign={(modelId) => handleUnassignGroupModel(modelId)}
											/>
										</div>
									</div>
								{/if}
							</div>
						{:else}
							<div class="h-full p-5 flex items-center justify-center">
								<EmptyState title="No group selected" description="Choose a group from the list to see and edit its details." icon="group" compact />
							</div>
						{/if}
					</div>
				</MasterDetailLayout>
			{/if}
		</section>
	{/if}
</div>

<!-- ========== Create User modal (create only — editing happens in the detail pane) ========== -->
<BaseModal isOpen={showUserModal} title="Create New User" size="md" on:close={() => (showUserModal = false)}>
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
		<div class="flex justify-end gap-3 px-6 py-4">
			<Button variant="secondary" onclick={() => (showUserModal = false)}>Cancel</Button>
			<Button
				variant="primary"
				disabled={!userFormData.username || !userFormData.email || !userFormData.password}
				onclick={handleSaveNewUser}
			>
				Create User
			</Button>
		</div>
	</svelte:fragment>
</BaseModal>

<!-- ========== Create Group modal (create only) ========== -->
<BaseModal isOpen={showGroupModal} title="Create New Group" size="md" on:close={() => (showGroupModal = false)}>
	<div class="p-6 space-y-4">
		<div>
			<label class="block text-sm font-medium text-fg-muted mb-2" for="group-name-input">Group Name</label>
			<input id="group-name-input" type="text" class="input w-full" bind:value={groupFormData.name} required />
		</div>
		<div>
			<label class="block text-sm font-medium text-fg-muted mb-2" for="group-description-input">Description</label>
			<textarea
				id="group-description-input"
				class="input w-full"
				rows="3"
				bind:value={groupFormData.description}
				placeholder="Optional description"
			></textarea>
		</div>
	</div>
	<svelte:fragment slot="footer">
		<div class="flex justify-end gap-3 px-6 py-4">
			<Button variant="secondary" onclick={() => (showGroupModal = false)}>Cancel</Button>
			<Button variant="primary" disabled={!groupFormData.name} onclick={handleSaveNewGroup}>Create Group</Button>
		</div>
	</svelte:fragment>
</BaseModal>
