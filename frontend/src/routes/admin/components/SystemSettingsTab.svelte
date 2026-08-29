<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { onMount } from 'svelte';
	import * as adminApi from '$lib/services/admin-api';
	import { toasts } from '$lib/stores/toast';
	import { Button, Spinner } from '$lib/components/ui';
	import { MasterDetailLayout } from '$lib/components/master-detail';
	import { Pane, PaneRow } from '$lib/components/pane';
	import AdminTabShell from './AdminTabShell.svelte';
	import AccessPanel from './settings/AccessPanel.svelte';
	import ContentSafetyPanel from './settings/ContentSafetyPanel.svelte';
	import FileStoragePanel from './settings/FileStoragePanel.svelte';
	import ModelsLocationPanel from './settings/ModelsLocationPanel.svelte';
	import PromptSearchPanel from './settings/PromptSearchPanel.svelte';
	import MediaTaggingPanel from './settings/MediaTaggingPanel.svelte';
	import VisualSearchPanel from './settings/VisualSearchPanel.svelte';
	import { SETTINGS_GROUPS, SETTINGS_KEY_GROUP, type SettingsGroupId } from './settings/settingsGroups';

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
	let activeGroup = $state<SettingsGroupId>('access');

	function snapshotOf(s: Record<string, any>): string {
		return JSON.stringify(Object.fromEntries(USER_CONFIGURABLE_KEYS.map((k) => [k, s[k]])));
	}

	let dirtyKeys = $derived.by(() => {
		const before = JSON.parse(snapshot) as Record<string, any>;
		return USER_CONFIGURABLE_KEYS.filter((k) => settings[k] !== before[k]);
	});
	let dirtyGroups = $derived(new Set(dirtyKeys.map((k) => SETTINGS_KEY_GROUP[k])));
	let unsavedChanges = $derived(dirtyKeys.length > 0);

	onMount(loadSettings);

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

	function shortModelName(id: string): string {
		return id.split('/').pop() || id;
	}

	/** Bare mono hint per section, never prose - matches the pane family's
	 * subtitle idiom elsewhere in admin. */
	function subtitleFor(id: SettingsGroupId): string | undefined {
		switch (id) {
			case 'access':
				return `${settings.registration_policy === 'open' ? 'open' : 'closed'} · mcp ${settings.mcp_enabled ? 'on' : 'off'}`;
			case 'content_safety':
				return `nsfw ${settings.nsfw ? 'on' : 'off'} · blur ${settings.media_nsfw_blur_threshold ?? 0.6}`;
			case 'file_storage':
				return settings.storage_backend === 's3' ? 's3' : 'local';
			case 'models_location':
				return undefined;
			case 'prompt_search': {
				const model =
					settings.prompt_embedding_provider === 'ollama'
						? settings.prompt_embedding_ollama_model
						: settings.prompt_embedding_model;
				return model ? shortModelName(model) : undefined;
			}
			case 'media_tagging':
				return settings.media_tagger_model ? shortModelName(settings.media_tagger_model) : undefined;
			case 'visual_search':
				return settings.media_vision_model ? shortModelName(settings.media_vision_model) : undefined;
		}
	}
</script>

<div class="flex h-[calc(100dvh-var(--header-h)-2rem)] min-h-[36rem] flex-col gap-4 sm:h-[calc(100dvh-var(--header-h)-3rem)]">
	<AdminTabShell title="System Settings" icon="settings" />

	<section class="flex-1 min-h-0 rounded-lg border border-line bg-surface-1 overflow-hidden">
		{#if loading}
			<div class="h-full flex flex-col items-center justify-center">
				<Spinner size="lg" />
				<p class="text-sm text-fg-muted mt-4">Loading settings…</p>
			</div>
		{:else}
			<MasterDetailLayout leftWidth={300} minWidth={240} maxWidth={400} storageKey="admin-settings-width">
				<div slot="list" class="h-full min-h-0">
					<Pane label="Settings" bodyRole="listbox" ariaLabel="Settings sections">
						{#snippet children()}
							{#each SETTINGS_GROUPS as group (group.id)}
								{#snippet rowTrailing()}
									{#if dirtyGroups.has(group.id)}
										<span
											class="w-1.5 h-1.5 rounded-full bg-warning-solid flex-shrink-0"
											title="Unsaved changes"
											aria-hidden="true"
										></span>
									{/if}
								{/snippet}
								<PaneRow
									selected={activeGroup === group.id}
									onclick={() => (activeGroup = group.id)}
									icon={group.icon}
									title={group.label}
									subtitle={subtitleFor(group.id)}
									subtitleMono
									trailing={rowTrailing}
								/>
							{/each}
						{/snippet}
					</Pane>
				</div>

				<div slot="detail" class="h-full min-h-0 flex flex-col">
					<div class="flex-1 min-h-0 overflow-y-auto bg-surface-2 p-4 sm:p-5">
						<div class="max-w-[760px] space-y-5">
							{#if activeGroup === 'access'}
								<AccessPanel {settings} onSettingChange={handleSettingChange} />
							{:else if activeGroup === 'content_safety'}
								<ContentSafetyPanel {settings} onSettingChange={handleSettingChange} />
							{:else if activeGroup === 'file_storage'}
								<FileStoragePanel {settings} onSettingChange={handleSettingChange} />
							{:else if activeGroup === 'models_location'}
								<ModelsLocationPanel />
							{:else if activeGroup === 'prompt_search'}
								<PromptSearchPanel {settings} onSettingChange={handleSettingChange} />
							{:else if activeGroup === 'media_tagging'}
								<MediaTaggingPanel {settings} onSettingChange={handleSettingChange} />
							{:else if activeGroup === 'visual_search'}
								<VisualSearchPanel {settings} onSettingChange={handleSettingChange} />
							{/if}
						</div>
					</div>

					{#if unsavedChanges}
						<div
							class="flex-shrink-0 border-t border-line bg-surface-1 px-4 sm:px-5 py-3 flex items-center justify-end gap-3"
						>
							<span class="font-mono text-xs tabular-nums text-fg-muted mr-auto">
								{dirtyKeys.length} unsaved change{dirtyKeys.length === 1 ? '' : 's'}
							</span>
							<Button variant="secondary" size="sm" disabled={saving} onclick={loadSettings}>Reset</Button>
							<Button variant="primary" size="sm" loading={saving} disabled={saving} onclick={saveSettings}>
								{saving ? 'Saving...' : 'Save Changes'}
							</Button>
						</div>
					{/if}
				</div>
			</MasterDetailLayout>
		{/if}
	</section>
</div>
