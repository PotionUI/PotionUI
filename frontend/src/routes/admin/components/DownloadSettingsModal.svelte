<script lang="ts">
	import { createEventDispatcher, onMount } from 'svelte';
	import { downloadStore, downloadSettings, type DownloadSettings } from '$lib/stores/downloads';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import { Button, Spinner, Switch, Alert } from '$lib/components/ui';

	const dispatch = createEventDispatcher();

	let settings: DownloadSettings = {
		max_concurrent_downloads: 2,
		auto_retry_failed: true,
		max_retries: 3,
		chunk_size_kb: 1024,
		verify_checksum: true,
		default_model_directory: 'models',
		default_media_directory: 'storage/media'
	};

	let saving = false;
	let errorMessage = '';
	let successMessage = '';

	onMount(() => {
		if ($downloadSettings) {
			settings = { ...$downloadSettings };
		}
	});

	// Update local settings when store changes
	$: if ($downloadSettings) {
		settings = { ...$downloadSettings };
	}

	async function handleSubmit() {
		saving = true;
		errorMessage = '';
		successMessage = '';

		try {
			const success = await downloadStore.updateSettings(settings);
			if (success) {
				successMessage = 'Settings saved successfully';
				setTimeout(() => {
					successMessage = '';
				}, 3000);
			} else {
				errorMessage = 'Failed to save settings';
			}
		} catch (err: any) {
			errorMessage = err.message || 'Failed to save settings';
		} finally {
			saving = false;
		}
	}

	function close() {
		dispatch('close');
	}
</script>

<BaseModal isOpen={true} title="Download Settings" sizeClass="md:max-w-lg md:w-full" on:close={close}>
	<form on:submit|preventDefault={handleSubmit} class="px-6 py-4 space-y-5">
		<!-- Error Message -->
		{#if errorMessage}
			<Alert variant="danger" density="compact">{errorMessage}</Alert>
		{/if}

		<!-- Success Message -->
		{#if successMessage}
			<Alert variant="success" density="compact">{successMessage}</Alert>
		{/if}

		<!-- Concurrent Downloads -->
		<div>
			<label for="maxConcurrent" class="block text-sm font-medium text-fg-muted mb-1">
				Max Concurrent Downloads
			</label>
			<input
				id="maxConcurrent"
				type="number"
				bind:value={settings.max_concurrent_downloads}
				min="1"
				max="10"
				class="input tabular-nums"
			/>
			<p class="text-xs text-fg-subtle mt-1">
				Number of downloads that can run simultaneously (1-10)
			</p>
		</div>

		<!-- Auto Retry -->
		<div class="flex items-center justify-between">
			<div>
				<label for="autoRetry" class="text-sm font-medium text-fg-muted">
					Auto-Retry Failed Downloads
				</label>
				<p class="text-xs text-fg-subtle">
					Automatically retry downloads that fail
				</p>
			</div>
			<Switch
				id="autoRetry"
				size="lg"
				bind:checked={settings.auto_retry_failed}
				label="Toggle auto-retry failed downloads"
			/>
		</div>

		<!-- Max Retries -->
		{#if settings.auto_retry_failed}
			<div>
				<label for="maxRetries" class="block text-sm font-medium text-fg-muted mb-1">
					Max Retries
				</label>
				<input
					id="maxRetries"
					type="number"
					bind:value={settings.max_retries}
					min="1"
					max="10"
					class="input tabular-nums"
				/>
				<p class="text-xs text-fg-subtle mt-1">
					Maximum number of retry attempts (1-10)
				</p>
			</div>
		{/if}

		<!-- Chunk Size -->
		<div>
			<label for="chunkSize" class="block text-sm font-medium text-fg-muted mb-1">
				Download Chunk Size (KB)
			</label>
			<select id="chunkSize" bind:value={settings.chunk_size_kb} class="input">
				<option value={256}>256 KB</option>
				<option value={512}>512 KB</option>
				<option value={1024}>1 MB (Recommended)</option>
				<option value={2048}>2 MB</option>
				<option value={4096}>4 MB</option>
			</select>
			<p class="text-xs text-fg-subtle mt-1">
				Larger chunks may improve speed but use more memory
			</p>
		</div>

		<!-- Verify Checksum -->
		<div class="flex items-center justify-between">
			<div>
				<label for="verifyChecksum" class="text-sm font-medium text-fg-muted">
					Verify Checksums
				</label>
				<p class="text-xs text-fg-subtle">
					Verify SHA256 checksums when provided
				</p>
			</div>
			<Switch
				id="verifyChecksum"
				size="lg"
				bind:checked={settings.verify_checksum}
				label="Toggle checksum verification"
			/>
		</div>

		<!-- Default Model Directory -->
		<div>
			<label for="modelDir" class="block text-sm font-medium text-fg-muted mb-1">
				Default Model Directory
			</label>
			<input
				id="modelDir"
				type="text"
				bind:value={settings.default_model_directory}
				placeholder="models"
				class="input"
			/>
		</div>

		<!-- Default Media Directory -->
		<div>
			<label for="mediaDir" class="block text-sm font-medium text-fg-muted mb-1">
				Default Media Directory
			</label>
			<input
				id="mediaDir"
				type="text"
				bind:value={settings.default_media_directory}
				placeholder="storage/media"
				class="input"
			/>
		</div>

		<!-- Submit Button -->
		<div class="flex gap-3 pt-2">
			<Button type="submit" variant="primary" class="flex-1" disabled={saving}>
				{#if saving}
					<Spinner size="sm" />
					Saving...
				{:else}
					Save Settings
				{/if}
			</Button>
			<Button type="button" variant="secondary" onclick={close}>
				Close
			</Button>
		</div>
	</form>
</BaseModal>
