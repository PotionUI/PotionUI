<script lang="ts">
	import { DetailSection } from '$lib/components/detail';
	import { ELIXIR_LIMITS, limitValue, parseLimit } from './elixirLimits';

	let {
		settings,
		onSettingChange
	}: { settings: Record<string, any>; onSettingChange: (key: string, value: unknown) => void } = $props();
</script>

<DetailSection label="Elixirs" padded={false}>
	<div class="px-4 sm:px-5 divide-y divide-line" data-testid="elixir-limits">
		{#each ELIXIR_LIMITS as limit (limit.key)}
			<div class="py-4 flex items-start justify-between gap-6">
				<div>
					<label for="limit-{limit.key}" class="block text-sm font-medium text-fg mb-1">{limit.label}</label>
					<p class="text-sm text-fg-muted">{limit.description}</p>
				</div>
				<input
					id="limit-{limit.key}"
					type="number"
					min="1"
					step="1"
					class="input w-24 font-mono tabular-nums"
					value={limitValue(settings[limit.key], limit.fallback)}
					onchange={(event) => {
						const next = parseLimit(event.currentTarget.value);
						if (next !== null) onSettingChange(limit.key, next);
					}}
				/>
			</div>
		{/each}
	</div>
</DetailSection>
