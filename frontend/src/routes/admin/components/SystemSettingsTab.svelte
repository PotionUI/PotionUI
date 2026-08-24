<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { onMount } from 'svelte';
	import * as adminApi from '$lib/services/admin-api';
	import { toasts } from '$lib/stores/toast';
	import { Button, Spinner } from '$lib/components/ui';
	import AdminTabShell from './AdminTabShell.svelte';
	import SemanticSearchSettingsCard from './SemanticSearchSettingsCard.svelte';
	import ModelsLocationCard from './ModelsLocationCard.svelte';
	import FileStorageCard from './FileStorageCard.svelte';

	let settings: Record<string, any> = {};
	let loading = true;
	let saving = false;
	let unsavedChanges = false;

	onMount(async () => {
		await loadSettings();
	});

	async function loadSettings() {
		try {
			loading = true;
			const response = await adminApi.getSettings();
			if (response.success && response.data) {
				settings = response.data;
			}
		} catch (error) {
			logger.error('Failed to load settings:', error);
		} finally {
			loading = false;
		}
	}

	function handleSettingChange(key: string, value: any) {
		settings = { ...settings, [key]: value };
		unsavedChanges = true;
	}

	async function saveSettings() {
		try {
			saving = true;
			const userConfigurableSettings = {
				file_storage_directory: settings.file_storage_directory,
				nsfw: settings.nsfw,
				prompt_embedding_provider: settings.prompt_embedding_provider,
				prompt_embedding_model: settings.prompt_embedding_model,
				prompt_embedding_device: settings.prompt_embedding_device,
				prompt_embedding_auto_download: settings.prompt_embedding_auto_download,
				prompt_embedding_ollama_base_url: settings.prompt_embedding_ollama_base_url,
				prompt_embedding_ollama_model: settings.prompt_embedding_ollama_model,
				registration_policy: settings.registration_policy,
				media_tagger_model: settings.media_tagger_model,
				media_tagger_device: settings.media_tagger_device,
				media_tagger_auto_download: settings.media_tagger_auto_download,
				media_tagger_tag_threshold: settings.media_tagger_tag_threshold,
				media_tagger_character_threshold: settings.media_tagger_character_threshold,
				media_nsfw_blur_threshold: settings.media_nsfw_blur_threshold,
				media_vision_model: settings.media_vision_model,
				media_vision_device: settings.media_vision_device,
				media_vision_auto_download: settings.media_vision_auto_download,
				mcp_enabled: settings.mcp_enabled
			};
			const response = await adminApi.updateSettings(userConfigurableSettings);
			if (response.success) {
				unsavedChanges = false;
			}
		} catch (error) {
			logger.error('Failed to save settings:', error);
			toasts.error('Failed to save settings. Please try again.');
		} finally {
			saving = false;
		}
	}

</script>

<div class="space-y-4">
	<AdminTabShell title="System Settings" icon="settings" />

{#if loading}
	<div class="text-center py-12">
		<Spinner size="lg" />
		<p class="text-fg-muted mt-4">Loading settings...</p>
	</div>
{:else}
	<div class="space-y-6">
		<!-- Application Settings -->
		<div class="bg-surface-1 rounded-lg border border-line shadow-raised">
			<div class="px-6 py-3 border-b border-line">
				<h3 class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted">Application Settings</h3>
			</div>
			<div class="px-6 divide-y divide-line">
				<div class="py-4 flex items-start justify-between gap-6">
					<div>
						<label for="file-storage-directory" class="block text-sm font-medium text-fg mb-1">
							File Storage Directory
						</label>
						<p class="text-sm text-fg-muted">
							Base directory for all file storage (generations, tmp, models)
						</p>
					</div>
					<input
						id="file-storage-directory"
						type="text"
						class="input w-64 flex-shrink-0"
						value={settings.file_storage_directory || ''}
						on:input={(e) => handleSettingChange('file_storage_directory', e.currentTarget.value)}
						placeholder="storage"
					/>
				</div>

				<div class="py-4 flex items-start justify-between gap-6">
					<div>
						<label for="nsfw" class="block text-sm font-medium text-fg mb-1">NSFW Content</label>
						<p class="text-sm text-fg-muted">Allow generation of NSFW content</p>
					</div>
					<input
						type="checkbox"
						id="nsfw"
						class="w-4 h-4 mt-1 text-signal border-line-strong rounded focus:ring-signal flex-shrink-0"
						checked={settings.nsfw || false}
						on:change={(e) => handleSettingChange('nsfw', e.currentTarget.checked)}
					/>
				</div>

				<div class="py-4 flex items-start justify-between gap-6">
					<div>
						<label for="allow-registration" class="block text-sm font-medium text-fg mb-1">
							Allow anyone to register
						</label>
						<p class="text-sm text-fg-muted">
							When off, only the owner can create accounts in Administration → Users.
						</p>
					</div>
					<input
						type="checkbox"
						id="allow-registration"
						class="w-4 h-4 mt-1 text-signal border-line-strong rounded focus:ring-signal flex-shrink-0"
						checked={settings.registration_policy === 'open'}
						on:change={(e) =>
							handleSettingChange('registration_policy', e.currentTarget.checked ? 'open' : 'closed')}
					/>
				</div>

				<div class="py-4 flex items-start justify-between gap-6">
					<div>
						<label for="mcp-enabled" class="block text-sm font-medium text-fg mb-1">MCP Access</label>
						<p class="text-sm text-fg-muted">
							Allow external MCP clients to connect and act as PotionUI users via their tokens
						</p>
					</div>
					<input
						type="checkbox"
						id="mcp-enabled"
						class="w-4 h-4 mt-1 text-signal border-line-strong rounded focus:ring-signal flex-shrink-0"
						checked={settings.mcp_enabled || false}
						on:change={(e) => handleSettingChange('mcp_enabled', e.currentTarget.checked)}
					/>
				</div>
			</div>
		</div>

		<ModelsLocationCard />
		<FileStorageCard />

		<SemanticSearchSettingsCard {settings} onSettingChange={handleSettingChange} />

		<!-- Save Button -->
		<div class="flex justify-end gap-4">
			<Button variant="secondary" disabled={saving} onclick={loadSettings}>Reset</Button>
			<Button variant="primary" disabled={!unsavedChanges || saving} loading={saving} onclick={saveSettings}>
				{saving ? 'Saving...' : 'Save Changes'}
			</Button>
		</div>
	</div>
{/if}
</div>
