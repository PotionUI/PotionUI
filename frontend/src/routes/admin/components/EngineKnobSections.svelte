<script lang="ts">
	import { getApiErrorMessage } from '$lib/utils/logger';
	import { setEngineFlags, type EngineFlags } from '$lib/services/admin-api';
	import { toasts } from '$lib/stores/toast';
	import { Alert, Button } from '$lib/components/ui';
	import { DetailSection } from '$lib/components/detail';
	import RuntimeSettingRow from './settings/RuntimeSettingRow.svelte';
	import {
		changedKeysSince,
		restartPendingKeys,
		runtimeSettingKeys,
		runtimeSettingValue,
		runtimeSettingsFor,
		type RuntimeSettingDescriptor
	} from './settings/runtimeSettings';

	let {
		backendId,
		flags,
		baseline,
		local,
		restarting = false,
		onRestart,
		onFlagsChange
	}: {
		backendId: string;
		flags: EngineFlags;
		baseline: EngineFlags;
		local: boolean;
		restarting?: boolean;
		onRestart: () => void;
		onFlagsChange: (flags: EngineFlags) => void;
	} = $props();

	const keys = runtimeSettingKeys('backend');

	let sections = $derived(
		[
			{ label: 'Speed', settings: runtimeSettingsFor('speed') },
			{ label: 'Memory', settings: runtimeSettingsFor('memory') },
			{ label: 'Debug logging', settings: runtimeSettingsFor('debug') }
		].map((section) => ({
			...section,
			settings: section.settings.filter((d) => !(local && d.key === 'native_attention_backend'))
		}))
	);

	let restartPending = $derived(restartPendingKeys(changedKeysSince(keys, flags, baseline)).length > 0);

	let savingKey = $state<string | null>(null);
	let revision = $state(0);

	function descriptorFor(key: string): RuntimeSettingDescriptor | undefined {
		return sections.flatMap((s) => s.settings).find((d) => d.key === key);
	}

	async function save(key: string, value: unknown) {
		const descriptor = descriptorFor(key);
		if (!descriptor || runtimeSettingValue(descriptor, flags) === value) return;
		savingKey = key;
		try {
			const response = await setEngineFlags(backendId, { [key]: value as boolean | string | number });
			if (response.success && response.data) {
				onFlagsChange(response.data.engine_flags);
			} else {
				toasts.error(response.message || 'Failed to update engine flags');
				revision += 1;
			}
		} catch (e: unknown) {
			toasts.error(getApiErrorMessage(e, 'Failed to update engine flags'));
			revision += 1;
		} finally {
			savingKey = null;
		}
	}
</script>

<div class="space-y-1">
	{#if !local}
		<p class="text-sm text-fg-muted">
			These values are sent to the worker with every job, so the worker's own environment never decides them.
		</p>
	{/if}
	<p class="text-sm text-fg-muted">Changes apply to the next generation unless a setting says otherwise.</p>
</div>

{#if restartPending}
	<div data-testid="engine-restart-hint">
		{#if local}
			<Alert variant="warning" icon>
				Restart PotionUI to apply the settings marked Applies after restart.
				{#snippet actions()}
					<Button variant="secondary" size="sm" loading={restarting} onclick={onRestart}>
						{restarting ? 'Restarting…' : 'Restart now'}
					</Button>
				{/snippet}
			</Alert>
		{:else}
			<Alert variant="warning" icon>Restart the worker to apply.</Alert>
		{/if}
	</div>
{/if}

{#key revision}
	{#each sections as section (section.label)}
		<DetailSection label={section.label} padded={false}>
			<div class="px-4 sm:px-5 divide-y divide-line">
				{#each section.settings as descriptor (descriptor.key)}
					<RuntimeSettingRow
						{descriptor}
						settings={flags}
						onSettingChange={save}
						busy={savingKey === descriptor.key}
						locked={savingKey !== null}
					/>
				{/each}
			</div>
		</DetailSection>
	{/each}
{/key}
