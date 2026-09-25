<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { onMount } from 'svelte';
	import { get } from 'svelte/store';
	import { page } from '$app/stores';
	import * as adminApi from '$lib/services/admin-api';
	import { toasts } from '$lib/stores/toast';
	import { Spinner } from '$lib/components/ui';
	import LibraryShell from '$lib/components/library/LibraryShell.svelte';
	import { DetailHeader, DetailBody, DetailLayout, DetailFooter } from '$lib/components/detail';
	import AccessPanel from './settings/AccessPanel.svelte';
	import ContentSafetyPanel from './settings/ContentSafetyPanel.svelte';
	import FileStoragePanel from './settings/FileStoragePanel.svelte';
	import ThumbnailsPanel from './settings/ThumbnailsPanel.svelte';
	import HousekeepingPanel from './settings/HousekeepingPanel.svelte';
	import BackupsPanel from './settings/BackupsPanel.svelte';
	import ModelsLocationPanel from './settings/ModelsLocationPanel.svelte';
	import PromptSearchPanel from './settings/PromptSearchPanel.svelte';
	import MediaTaggingPanel from './settings/MediaTaggingPanel.svelte';
	import VisualSearchPanel from './settings/VisualSearchPanel.svelte';
	import GenerationPanel from './settings/GenerationPanel.svelte';
	import ExternalLoginPanel from './settings/ExternalLoginPanel.svelte';
	import LogsPanel from './settings/LogsPanel.svelte';
	import {
		SETTINGS_GROUPS,
		SETTINGS_KEY_GROUP,
		settingsGroupHasFooter,
		computeDirtyGroups,
		type SettingsGroupId
	} from './settings/settingsGroups';

	// The PUT body System Settings sends - unchanged from the pre-rebuild
	// dict, just derived from the group map so there's one source of truth.
	const USER_CONFIGURABLE_KEYS = Object.keys(SETTINGS_KEY_GROUP);

	let settings = $state<Record<string, any>>({});
	// JSON snapshot of the last loaded/saved values, keyed the same as the
	// PUT body - diffing against it drives both per-group dirty dots and the
	// save bar without a heavier dirty-tracking system.
	let snapshot = $state('{}');
	let loading = $state(true);
	let saving = $state(false);
	const requestedGroup = get(page).url.searchParams.get('view');
	let activeGroup = $state<SettingsGroupId>(
		SETTINGS_GROUPS.find((g) => g.id === requestedGroup)?.id ?? 'access'
	);

	function snapshotOf(s: Record<string, any>): string {
		return JSON.stringify(Object.fromEntries(USER_CONFIGURABLE_KEYS.map((k) => [k, s[k]])));
	}

	let dirtyKeys = $derived.by(() => {
		const before = JSON.parse(snapshot) as Record<string, any>;
		return USER_CONFIGURABLE_KEYS.filter((k) => JSON.stringify(settings[k]) !== JSON.stringify(before[k]));
	});
	let dirtyGroups = $derived(computeDirtyGroups(dirtyKeys));
	let unsavedChanges = $derived(dirtyKeys.length > 0);
	let activeGroupLabel = $derived(SETTINGS_GROUPS.find((g) => g.id === activeGroup)?.label ?? '');

	onMount(() => {
		loadSettings();
	});

	async function loadSettings() {
		try {
			loading = true;
			const response = await adminApi.getSettings();
			if (response.success && response.data) {
				settings = response.data;
				snapshot = snapshotOf(settings);
			}
		} catch (error) {
			logger.error('Failed to load settings:', error);
		} finally {
			loading = false;
		}
	}

	function handleSettingChange(key: string, value: any) {
		settings = { ...settings, [key]: value };
	}

	function discardSettings() {
		const before = JSON.parse(snapshot) as Record<string, any>;
		settings = { ...settings, ...before };
	}

	async function saveSettings() {
		try {
			saving = true;
			const userConfigurableSettings = Object.fromEntries(
				USER_CONFIGURABLE_KEYS.map((k) => [k, settings[k]])
			);
			const response = await adminApi.updateSettings(userConfigurableSettings);
			if (response.success) {
				snapshot = snapshotOf(settings);
			}
		} catch (error) {
			logger.error('Failed to save settings:', error);
			toasts.error('Failed to save settings. Please try again.');
		} finally {
			saving = false;
		}
	}

</script>

<LibraryShell
	title="Settings"
	persistKey="admin-settings-library"
	heightClass="h-full"
	sections={SETTINGS_GROUPS}
	section={activeGroup}
	onSelectSection={(id) => (activeGroup = id)}
	detailOpen
>
	{#snippet sectionTrailing(id)}
		{#if dirtyGroups.has(id)}
			<span class="w-1.5 h-1.5 rounded-full bg-warning-solid flex-shrink-0" aria-hidden="true"></span>
		{/if}
	{/snippet}

	{#if loading}
		<div class="flex h-full flex-col items-center justify-center">
			<Spinner size="lg" />
			<p class="text-sm text-fg-muted mt-4">Loading settings…</p>
		</div>
	{:else}
		<div class="flex h-full min-h-0 flex-col">
			<DetailHeader title={activeGroupLabel} />

			<DetailBody>
				<DetailLayout>
					{#snippet main()}
						{#if activeGroup === 'access'}
							<AccessPanel {settings} onSettingChange={handleSettingChange} />
						{:else if activeGroup === 'content_safety'}
							<ContentSafetyPanel {settings} onSettingChange={handleSettingChange} />
						{:else if activeGroup === 'storage'}
							<FileStoragePanel {settings} onSettingChange={handleSettingChange} />
							<ThumbnailsPanel {settings} onSettingChange={handleSettingChange} savedSnapshot={snapshot} />
							<HousekeepingPanel {settings} onSettingChange={handleSettingChange} savedSnapshot={snapshot} />
							<BackupsPanel {settings} onSettingChange={handleSettingChange} savedSnapshot={snapshot} />
							<ModelsLocationPanel />
						{:else if activeGroup === 'search_tagging'}
							<PromptSearchPanel {settings} onSettingChange={handleSettingChange} />
							<MediaTaggingPanel {settings} onSettingChange={handleSettingChange} />
							<VisualSearchPanel {settings} onSettingChange={handleSettingChange} />
						{:else if activeGroup === 'generation'}
							<GenerationPanel {settings} onSettingChange={handleSettingChange} />
						{:else if activeGroup === 'external_login'}
							<ExternalLoginPanel {settings} onSettingChange={handleSettingChange} />
						{:else if activeGroup === 'logs'}
							<LogsPanel />
						{/if}
					{/snippet}
				</DetailLayout>
			</DetailBody>

			{#if settingsGroupHasFooter(activeGroup)}
				<DetailFooter
					dirtyCount={dirtyKeys.length}
					{saving}
					canSave={unsavedChanges}
					onSave={saveSettings}
					onDiscard={discardSettings}
				/>
			{/if}
		</div>
	{/if}
</LibraryShell>
