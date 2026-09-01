<script lang="ts">
	import { onMount } from 'svelte';
	import { logger } from '$lib/utils/logger';
	import * as adminApi from '$lib/services/admin-api';
	import { toasts } from '$lib/stores/toast';
	import { Button, Input, Alert, Spinner, SegmentedControl, Switch } from '$lib/components/ui';
	import { DetailSection } from '$lib/components/detail';
	import { parseFileStorageSettings, buildFileStorageSettingsPayload } from '../fileStorageSettings';

	let {
		settings,
		onSettingChange
	}: { settings: Record<string, any>; onSettingChange: (key: string, value: unknown) => void } = $props();

	let loading = $state(true);
	let saving = $state(false);
	let error = $state<string | null>(null);

	let backend = $state<'local' | 's3'>('local');
	let bucket = $state('');
	let prefix = $state('');
	let endpointUrl = $state('');
	let region = $state('us-east-1');
	let accessKeyId = $state('');
	// Pre-filled with the server's mask ("***") when already configured; left
	// untouched, the mask round-trips back and the stored key is unchanged.
	let secretKey = $state('');
	let pathStyle = $state(false);

	onMount(load);

	async function load() {
		loading = true;
		error = null;
		try {
			const response = await adminApi.getSettings();
			if (!response.success || !response.data) {
				error = response.message ?? 'Failed to load storage settings.';
				return;
			}
			const parsed = parseFileStorageSettings(response.data);
			backend = parsed.backend;
			bucket = parsed.bucket;
			prefix = parsed.prefix;
			endpointUrl = parsed.endpointUrl;
			region = parsed.region;
			accessKeyId = parsed.accessKeyId;
			secretKey = parsed.secretKey;
			pathStyle = parsed.pathStyle;
		} catch (e) {
			logger.error('Failed to load storage settings:', e);
			error = 'Failed to load storage settings.';
		} finally {
			loading = false;
		}
	}

	async function save() {
		saving = true;
		error = null;
		try {
			const response = await adminApi.updateSettings(
				buildFileStorageSettingsPayload({
					backend,
					bucket,
					prefix,
					endpointUrl,
					region,
					accessKeyId,
					secretKey,
					pathStyle
				})
			);
			if (response.success) {
				toasts.success('File storage settings saved. New writes use this backend.');
				await load();
			} else {
				error = response.message ?? 'Failed to save storage settings.';
			}
		} catch (e) {
			logger.error('Failed to save storage settings:', e);
			error = 'Failed to save storage settings.';
		} finally {
			saving = false;
		}
	}
</script>

<div class="space-y-5">
	<DetailSection label="File Storage" padded={false}>
		<div class="px-4 sm:px-5 divide-y divide-line">
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
					oninput={(e) => onSettingChange('file_storage_directory', e.currentTarget.value)}
					placeholder="storage"
				/>
			</div>
		</div>
	</DetailSection>

	<DetailSection label="Storage Backend">
		<div class="space-y-3">
			<p class="text-sm text-fg-muted">
				Where generation outputs, uploads and thumbnails are written. Local disk is the default.
				Switching to S3 only affects <span class="font-medium text-fg">new</span> writes - files already
				on local disk are not migrated.
			</p>

			{#if loading}
				<Spinner size="sm" />
			{:else}
				{#if error}
					<Alert variant="danger" icon>{error}</Alert>
				{/if}

				<SegmentedControl
					items={[
						{ id: 'local', label: 'Local disk' },
						{ id: 's3', label: 'S3 / compatible' }
					]}
					selected={backend}
					onSelect={(id) => (backend = id as 'local' | 's3')}
					ariaLabel="Storage backend"
				/>

				{#if backend === 's3'}
					<div class="space-y-2 mt-2">
						<div>
							<label for="s3-bucket" class="block text-sm font-medium text-fg mb-1">Bucket</label>
							<Input id="s3-bucket" bind:value={bucket} placeholder="my-bucket" disabled={saving} />
						</div>
						<div>
							<label for="s3-prefix" class="block text-sm font-medium text-fg mb-1"
								>Key prefix (optional)</label
							>
							<Input id="s3-prefix" bind:value={prefix} placeholder="potionui/prod" disabled={saving} />
						</div>
						<div>
							<label for="s3-endpoint" class="block text-sm font-medium text-fg mb-1"
								>Endpoint URL (optional - MinIO, R2, ...)</label
							>
							<Input
								id="s3-endpoint"
								bind:value={endpointUrl}
								placeholder="Empty uses AWS S3"
								disabled={saving}
							/>
						</div>
						<div>
							<label for="s3-region" class="block text-sm font-medium text-fg mb-1">Region</label>
							<Input id="s3-region" bind:value={region} placeholder="us-east-1" disabled={saving} />
						</div>
						<div>
							<label for="s3-access-key" class="block text-sm font-medium text-fg mb-1"
								>Access key ID</label
							>
							<Input id="s3-access-key" bind:value={accessKeyId} disabled={saving} />
						</div>
						<div>
							<label for="s3-secret-key" class="block text-sm font-medium text-fg mb-1"
								>Secret access key</label
							>
							<Input id="s3-secret-key" type="password" bind:value={secretKey} disabled={saving} />
						</div>
						<div class="flex items-center gap-2 pt-1">
							<Switch
								id="s3-path-style"
								bind:checked={pathStyle}
								label="Use path-style addressing"
								disabled={saving}
							/>
							<label for="s3-path-style" class="text-sm text-fg-muted"
								>Path-style addressing (required by most non-AWS S3-compatible services)</label
							>
						</div>
					</div>
				{/if}

				<div class="flex items-center gap-3 mt-3">
					<Button variant="primary" onclick={save} loading={saving} disabled={saving}>Save</Button>
				</div>
			{/if}
		</div>
	</DetailSection>
</div>
