<script lang="ts">
	import { onMount } from 'svelte';
	import { Alert, Button, Input, Spinner } from '$lib/components/ui';
	import { getApiErrorMessage } from '$lib/utils/logger';
	import { getProviderFields, provisionCompute } from '$lib/services/admin-api';
	import type { ComputeField, ComputeProvider } from '$lib/services/admin-api';

	/**
	 * "Provision new compute" branch of the Add Backend flow for native.remote -
	 * rents a GPU through a registered `ComputeProvisioner` plugin and lets core
	 * turn it into a `native.remote` backend row (POST /api/admin/provisioning).
	 * The sibling "Connect to existing worker" branch needs none of this: it's
	 * just the ordinary `BackendForm` rendering native.remote's own served
	 * fields (base_url/worker_token/timeouts), same as any other driver.
	 */
	let {
		providers,
		onCreated,
		onCancel
	}: {
		providers: ComputeProvider[];
		onCreated: (backendId: string) => void;
		onCancel: () => void;
	} = $props();

	let name = $state('');
	let selectedProviderId = $state(providers[0]?.provider_id ?? '');
	let providerFields = $state<ComputeField[]>([]);
	let providerFieldsLoading = $state(false);
	// `any`, not `unknown` - mirrors BackendForm's own `draft: Record<string, any>`
	// (frontend/src/routes/admin/components/BackendForm.svelte), so `bind:value`
	// on both text and native number inputs below type-checks without per-field casts.
	let provisionValues = $state<Record<string, any>>({});
	let provisioning = $state(false);
	let provisionError = $state<string | null>(null);

	let canProvision = $derived(
		!provisioning &&
			selectedProviderId !== '' &&
			name.trim() !== '' &&
			providerFields.every((f) => !f.required || provisionValues[f.key] !== '')
	);

	onMount(() => {
		if (selectedProviderId) void loadProviderFields(selectedProviderId);
	});

	async function loadProviderFields(providerId: string) {
		providerFieldsLoading = true;
		provisionError = null;
		try {
			const response = await getProviderFields(providerId);
			if (response.success && response.data) {
				providerFields = response.data.fields;
				const values: Record<string, any> = {};
				for (const field of providerFields) {
					values[field.key] = field.default ?? (field.type === 'number' ? '' : '');
				}
				provisionValues = values;
			} else {
				provisionError = response.message || 'Failed to load provider fields';
			}
		} catch (e: unknown) {
			provisionError = getApiErrorMessage(e, 'Failed to load provider fields');
		} finally {
			providerFieldsLoading = false;
		}
	}

	function onProviderChange(providerId: string) {
		selectedProviderId = providerId;
		void loadProviderFields(providerId);
	}

	async function submitProvision() {
		if (!canProvision) return;
		provisioning = true;
		provisionError = null;
		try {
			const response = await provisionCompute({
				provider_id: selectedProviderId,
				name: name.trim(),
				values: provisionValues
			});
			if (response.success && response.data?.backend_id) {
				onCreated(response.data.backend_id);
			} else {
				provisionError = response.message || 'Failed to provision compute';
			}
		} catch (e: unknown) {
			provisionError = getApiErrorMessage(e, 'Failed to provision compute');
		} finally {
			provisioning = false;
		}
	}
</script>

<div class="space-y-5">
	{#if provisionError}
		<Alert variant="danger" density="compact">{provisionError}</Alert>
	{/if}

	<div>
		<label for="remote-worker-provision-name" class="label">Name <span class="text-danger">*</span></label>
		<Input id="remote-worker-provision-name" bind:value={name} placeholder="e.g. SDXL worker" required />
	</div>
	<div>
		<label for="remote-worker-provider" class="label">Provider</label>
		<select
			id="remote-worker-provider"
			class="input"
			value={selectedProviderId}
			onchange={(e) => onProviderChange((e.target as HTMLSelectElement).value)}
		>
			{#each providers as provider (provider.provider_id)}
				<option value={provider.provider_id}>{provider.label}</option>
			{/each}
		</select>
	</div>

	{#if providerFieldsLoading}
		<div class="flex items-center justify-center py-8">
			<Spinner size="md" />
		</div>
	{:else}
		{#each providerFields as field (field.key)}
			<div>
				{#if field.type === 'select'}
					<label for="remote-worker-field-{field.key}" class="label">
						{field.label}{#if field.required}<span class="text-danger"> *</span>{/if}
					</label>
					<select id="remote-worker-field-{field.key}" class="input" bind:value={provisionValues[field.key]}>
						{#each field.options ?? [] as option (option.value)}
							<option value={option.value}>{option.label}{option.detail ? ` — ${option.detail}` : ''}</option>
						{/each}
					</select>
				{:else if field.type === 'number'}
					<label for="remote-worker-field-{field.key}" class="label">
						{field.label}{#if field.required}<span class="text-danger"> *</span>{/if}
					</label>
					<input
						id="remote-worker-field-{field.key}"
						type="number"
						class="input font-mono tabular-nums"
						bind:value={provisionValues[field.key]}
						placeholder={field.default != null ? String(field.default) : ''}
						required={field.required}
					/>
				{:else}
					<label for="remote-worker-field-{field.key}" class="label">
						{field.label}{#if field.required}<span class="text-danger"> *</span>{/if}
					</label>
					<Input
						id="remote-worker-field-{field.key}"
						bind:value={provisionValues[field.key]}
						placeholder={field.default != null ? String(field.default) : ''}
						required={field.required}
					/>
				{/if}
				{#if field.help_text}
					<p class="text-xs text-fg-subtle mt-1">{field.help_text}</p>
				{/if}
			</div>
		{/each}
	{/if}

	<div class="flex gap-3 pt-1">
		<Button variant="primary" class="flex-1" loading={provisioning} disabled={!canProvision} onclick={submitProvision}>
			{provisioning ? 'Provisioning…' : 'Provision'}
		</Button>
		<Button variant="secondary" onclick={onCancel}>Cancel</Button>
	</div>
</div>
