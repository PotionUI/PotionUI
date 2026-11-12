<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { jsonEqual } from '$lib/utils/equal';
	import { onMount } from 'svelte';
	import { get } from 'svelte/store';
	import { page } from '$app/stores';
	import * as adminApi from '$lib/services/admin-api';
	import { toasts } from '$lib/stores/toast';
	import { Spinner } from '$lib/components/ui';
	import LibraryShell from '$lib/components/library/LibraryShell.svelte';
	import { DetailHeader, DetailBody, DetailLayout, DetailFooter } from '$lib/components/detail';
	import AccessPanel from './settings/AccessPanel.svelte';
	import AdminApiKeysPanel from './apikeys/AdminApiKeysPanel.svelte';
	import { CeilingDraft } from './apikeys/ceilingDraft.svelte';
	import ContentSafetyPanel from './settings/ContentSafetyPanel.svelte';
	import FileStoragePanel from './settings/FileStoragePanel.svelte';
	import ThumbnailsPanel from './settings/ThumbnailsPanel.svelte';
	import HousekeepingPanel from './settings/HousekeepingPanel.svelte';
	import BackupsPanel from './settings/BackupsPanel.svelte';
	import PromptSearchPanel from './settings/PromptSearchPanel.svelte';
	import MediaTaggingPanel from './settings/MediaTaggingPanel.svelte';
	import VisualSearchPanel from './settings/VisualSearchPanel.svelte';
	import GenerationPanel from './settings/GenerationPanel.svelte';
	import ElixirLimitsPanel from './settings/ElixirLimitsPanel.svelte';
	import DiagnosticsPanel from './settings/DiagnosticsPanel.svelte';
	import ExternalLoginPanel from './settings/ExternalLoginPanel.svelte';
	import LogsPanel from './settings/LogsPanel.svelte';
	import {
		SETTINGS_GROUPS,
		SETTINGS_KEY_GROUP,
		settingsGroupHasFooter,
		computeDirtyGroups,
		resolveSettingsGroupId,
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
	let activeGroup = $state<SettingsGroupId>(resolveSettingsGroupId(requestedGroup));

	function snapshotOf(s: Record<string, any>): string {
		return JSON.stringify(Object.fromEntries(USER_CONFIGURABLE_KEYS.map((k) => [k, s[k]])));
	}

	let dirtyKeys = $derived.by(() => {
		const before = JSON.parse(snapshot) as Record<string, any>;
		return USER_CONFIGURABLE_KEYS.filter((k) => !jsonEqual(settings[k], before[k]));
	});
	const ceiling = new CeilingDraft();
	let keysTotal = $state<number | null>(null);
	let dirtyCount = $derived(dirtyKeys.length + ceiling.count);
	let dirtyGroups = $derived.by(() => {
		const groups = computeDirtyGroups(dirtyKeys);
		if (ceiling.count > 0) groups.add('access');
		return groups;
	});
	let unsavedChanges = $derived(dirtyCount > 0);
	let activeGroupLabel = $derived(SETTINGS_GROUPS.find((g) => g.id === activeGroup)?.label ?? '');

	onMount(() => {
		loadSettings();
		void ceiling.load();
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
		ceiling.discard();
	}

	async function saveSettings() {
		try {
			saving = true;
			if (dirtyKeys.length > 0) {
				const userConfigurableSettings = Object.fromEntries(
					USER_CONFIGURABLE_KEYS.map((k) => [k, settings[k]])
				);
				const response = await adminApi.updateSettings(userConfigurableSettings);
				if (!response.success) return;
				snapshot = snapshotOf(settings);
			}
			if (ceiling.count > 0 && !(await ceiling.save())) {
				toasts.error('The MCP tool limits could not be saved. Please try again.');
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
			<DetailHeader title={activeGroupLabel}>
				{#snippet chips()}
					{#if activeGroup === 'api_keys' && keysTotal !== null}
						<span class="font-mono text-sm tabular-nums text-fg-subtle" data-testid="admin-keys-total">{keysTotal}</span>
					{/if}
				{/snippet}
			</DetailHeader>

			<DetailBody>
				<DetailLayout>
					{#snippet main()}
						{#if activeGroup === 'access'}
							<AccessPanel {settings} savedSnapshot={snapshot} {ceiling} onSettingChange={handleSettingChange} />
						{:else if activeGroup === 'api_keys'}
							<AdminApiKeysPanel onTotal={(total) => (keysTotal = total)} />
						{:else if activeGroup === 'content_safety'}
							<ContentSafetyPanel {settings} onSettingChange={handleSettingChange} />
						{:else if activeGroup === 'media_storage'}
							<FileStoragePanel {settings} onSettingChange={handleSettingChange} />
							<ThumbnailsPanel {settings} onSettingChange={handleSettingChange} savedSnapshot={snapshot} />
						{:else if activeGroup === 'backups'}
							<BackupsPanel {settings} onSettingChange={handleSettingChange} savedSnapshot={snapshot} />
						{:else if activeGroup === 'housekeeping'}
							<HousekeepingPanel {settings} onSettingChange={handleSettingChange} savedSnapshot={snapshot} />
						{:else if activeGroup === 'search_tagging'}
							<PromptSearchPanel {settings} onSettingChange={handleSettingChange} />
							<MediaTaggingPanel {settings} onSettingChange={handleSettingChange} />
							<VisualSearchPanel {settings} onSettingChange={handleSettingChange} />
						{:else if activeGroup === 'generation'}
							<GenerationPanel {settings} onSettingChange={handleSettingChange} />
							<ElixirLimitsPanel {settings} onSettingChange={handleSettingChange} />
						{:else if activeGroup === 'diagnostics'}
							<DiagnosticsPanel {settings} onSettingChange={handleSettingChange} />
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
					dirtyCount={dirtyCount}
					{saving}
					canSave={unsavedChanges}
					onSave={saveSettings}
					onDiscard={discardSettings}
				/>
			{/if}
		</div>
	{/if}
</LibraryShell>
