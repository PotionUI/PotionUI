<script lang="ts">
	import { onMount } from 'svelte';
	import { toasts } from '$lib/stores/toast';
	import { logger } from '$lib/utils/logger';
	import { saveMediaToolPoolSetting, type MediaToolPool, type PoolLimitId } from '$lib/services/api/mediaToolsPool';
	import { Spinner } from '$lib/components/ui';
	import { DetailSection } from '$lib/components/detail';
	import {
		limitHint,
		parsePoolLimit,
		POOL_LIMIT_DEFAULTS,
		POOL_LIMIT_FIELDS,
		POOL_LIMIT_KEYS,
		POOL_SETTING_MAX
	} from '../activity/mediaToolsPool';

	let {
		pool,
		settings,
		onSaved
	}: {
		pool: MediaToolPool;
		settings: Record<string, unknown>;
		onSaved: () => void;
	} = $props();

	let values = $state<Record<PoolLimitId, number>>({ ...POOL_LIMIT_DEFAULTS });
	let drafts = $state<Record<string, string>>({});
	let saving = $state<Record<string, boolean>>({});
	let saved = $state<Record<string, boolean>>({});
	let failed = $state<Record<string, string>>({});

	onMount(() => {
		const next = { ...POOL_LIMIT_DEFAULTS };
		for (const field of POOL_LIMIT_FIELDS) {
			const raw = Number(settings[POOL_LIMIT_KEYS[field.id]]);
			if (Number.isFinite(raw) && raw >= 0) next[field.id] = raw;
		}
		values = next;
	});

	function keyFor(id: PoolLimitId): string {
		return pool.setting_keys?.[id] ?? POOL_LIMIT_KEYS[id];
	}

	async function commit(id: PoolLimitId, min: number, text: string) {
		const parsed = parsePoolLimit(text, min);
		delete drafts[id];
		if (parsed === null) {
			failed = { ...failed, [id]: `Enter a whole number from ${min} to ${POOL_SETTING_MAX}.` };
			return;
		}
		failed = { ...failed, [id]: '' };
		if (parsed === values[id]) return;
		saving = { ...saving, [id]: true };
		saved = { ...saved, [id]: false };
		try {
			await saveMediaToolPoolSetting(keyFor(id), parsed);
			values = { ...values, [id]: parsed };
			saved = { ...saved, [id]: true };
			onSaved();
		} catch (error) {
			logger.error('Failed to save the media tools limit:', error);
			toasts.error(error instanceof Error ? error.message : 'Could not save the limit.');
		} finally {
			saving = { ...saving, [id]: false };
		}
	}
</script>

<DetailSection label="Limits" padded={false}>
	<div class="px-4 sm:px-5 divide-y divide-line" data-pool-limits>
		{#each POOL_LIMIT_FIELDS as field (field.id)}
			{@const hint = limitHint(field.id, values[field.id], pool)}
			<div class="py-4 flex items-start justify-between gap-6">
				<div class="min-w-0">
					<label for="pool-limit-{field.id}" class="block text-sm font-medium text-fg mb-1">{field.label}</label>
					<p class="text-sm text-fg-muted">{field.description}</p>
					{#if failed[field.id]}
						<p class="mt-1 text-sm text-danger" role="alert">{failed[field.id]}</p>
					{/if}
				</div>
				<div class="flex flex-shrink-0 flex-col items-end gap-1">
					<div class="flex items-center gap-2">
						{#if saving[field.id]}
							<Spinner size="sm" />
						{:else if saved[field.id]}
							<span class="text-xs text-success">Saved</span>
						{/if}
						<input
							id="pool-limit-{field.id}"
							type="number"
							min={field.min}
							max={POOL_SETTING_MAX}
							step="1"
							class="input w-24 font-mono tabular-nums"
							value={drafts[field.id] ?? String(values[field.id])}
							disabled={!!saving[field.id]}
							oninput={(event) => {
								saved = { ...saved, [field.id]: false };
								drafts[field.id] = event.currentTarget.value;
							}}
							onchange={(event) => commit(field.id, field.min, event.currentTarget.value)}
						/>
					</div>
					{#if hint}
						<span class="font-mono text-xs tabular-nums text-fg-subtle">{hint}</span>
					{/if}
				</div>
			</div>
		{/each}
	</div>
</DetailSection>
