<script lang="ts">
	import { Input, SegmentedControl, Switch } from '$lib/components/ui';
	import { DetailSection } from '$lib/components/detail';
	import { parseFileStorageSettings } from '../fileStorageSettings';

	let {
		settings,
		onSettingChange
	}: { settings: Record<string, any>; onSettingChange: (key: string, value: unknown) => void } = $props();

	let values = $derived(parseFileStorageSettings(settings));

	function setBackend(id: string) {
		onSettingChange('storage_backend', id);
	}
</script>

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

<DetailSection label="Storage Backend" padded={false}>
	<div class="px-4 sm:px-5 py-4 space-y-3">
		<p class="text-sm text-fg-muted">
			Where generation outputs, uploads and thumbnails are written. Local disk is the default.
			Switching to S3 only affects <span class="font-medium text-fg">new</span> writes - files already
			on local disk are not migrated.
		</p>

		<SegmentedControl
			items={[
				{ id: 'local', label: 'Local disk' },
				{ id: 's3', label: 'S3 / compatible' }
			]}
			selected={values.backend}
			onSelect={setBackend}
			ariaLabel="Storage backend"
		/>

		{#if values.backend === 's3'}
			<div class="space-y-2 mt-2">
				<div>
					<label for="s3-bucket" class="block text-sm font-medium text-fg mb-1">Bucket</label>
					<Input
						id="s3-bucket"
						value={values.bucket}
						oninput={(e: Event) => onSettingChange('s3_bucket', (e.currentTarget as HTMLInputElement).value)}
						placeholder="my-bucket"
					/>
				</div>
				<div>
					<label for="s3-prefix" class="block text-sm font-medium text-fg mb-1">Key prefix (optional)</label>
					<Input
						id="s3-prefix"
						value={values.prefix}
						oninput={(e: Event) => onSettingChange('s3_prefix', (e.currentTarget as HTMLInputElement).value)}
						placeholder="potionui/prod"
					/>
				</div>
				<div>
					<label for="s3-endpoint" class="block text-sm font-medium text-fg mb-1"
						>Endpoint URL (optional - MinIO, R2, ...)</label
					>
					<Input
						id="s3-endpoint"
						value={values.endpointUrl}
						oninput={(e: Event) => onSettingChange('s3_endpoint_url', (e.currentTarget as HTMLInputElement).value)}
						placeholder="Empty uses AWS S3"
					/>
				</div>
				<div>
					<label for="s3-region" class="block text-sm font-medium text-fg mb-1">Region</label>
					<Input
						id="s3-region"
						value={values.region}
						oninput={(e: Event) => onSettingChange('s3_region', (e.currentTarget as HTMLInputElement).value)}
						placeholder="us-east-1"
					/>
				</div>
				<div>
					<label for="s3-access-key" class="block text-sm font-medium text-fg mb-1">Access key ID</label>
					<Input
						id="s3-access-key"
						value={values.accessKeyId}
						oninput={(e: Event) => onSettingChange('s3_access_key_id', (e.currentTarget as HTMLInputElement).value)}
					/>
				</div>
				<div>
					<label for="s3-secret-key" class="block text-sm font-medium text-fg mb-1">Secret access key</label>
					<Input
						id="s3-secret-key"
						type="password"
						value={values.secretKey}
						oninput={(e: Event) => onSettingChange('s3_secret_key', (e.currentTarget as HTMLInputElement).value)}
					/>
				</div>
				<div class="flex items-center gap-2 pt-1">
					<Switch
						id="s3-path-style"
						checked={values.pathStyle}
						onchange={(checked) => onSettingChange('s3_path_style', checked)}
						label="Use path-style addressing"
					/>
					<label for="s3-path-style" class="text-sm text-fg-muted"
						>Path-style addressing (required by most non-AWS S3-compatible services)</label
					>
				</div>
			</div>
		{/if}
	</div>
</DetailSection>
