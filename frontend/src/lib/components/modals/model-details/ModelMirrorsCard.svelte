<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Button } from '$lib/components/ui';
	import { timeAgo } from '$lib/utils/relativeTime';
	import type { ProviderInfo } from '$lib/types/models';
	import { formatMirrorIds } from './mirrorFormatting';
	import { DETAIL_INSET_CLASS } from '$lib/components/detail';

	let {
		providers = [],
		bare = false
	}: {
		providers: ProviderInfo[];
		bare?: boolean;
	} = $props();

	const entryClass = $derived(bare ? `${DETAIL_INSET_CLASS} p-2 text-xs space-y-1.5` : 'rounded border border-line-strong p-2 text-xs space-y-1.5');
</script>

{#snippet body()}
	{#if providers.length === 0}
		<p class="text-xs text-fg-subtle italic">
			No mirrors known yet. Look the model up with a provider action on its card.
		</p>
	{:else}
		<div class="space-y-2">
			{#each providers as mirror (mirror.id ?? `${mirror.provider}-${mirror.provider_model_id}`)}
				{@const label = mirror.provider_label ?? mirror.provider}
				{@const ids = formatMirrorIds(mirror.provider_model_id, mirror.provider_version_id)}
				<div class={entryClass}>
					<div class="flex items-center justify-between gap-2">
						<span class="text-sm font-medium text-fg truncate">{label}</span>
						{#if mirror.page_url}
							<Button
								href={mirror.page_url}
								target="_blank"
								rel="noreferrer noopener"
								variant="secondary"
								size="sm"
								icon="external-link"
							>
								Open on {label}
							</Button>
						{/if}
					</div>

					{#if mirror.name}
						<Tooltip text={mirror.name} wrapperClass="block min-w-0">
							<div class="text-fg-muted truncate">{mirror.name}</div>
						</Tooltip>
					{/if}

					<div class="flex items-center justify-between gap-2 font-mono tabular-nums text-xs text-fg-subtle">
						{#if ids}<span>{ids}</span>{/if}
						{#if mirror.updated_at}
							<span>matched {timeAgo(mirror.updated_at)}</span>
						{/if}
					</div>
				</div>
			{/each}
		</div>
	{/if}
{/snippet}

{#if bare}
	{@render body()}
{:else}
	<div class="bg-surface-2 rounded-lg p-3">
		<div class="flex items-center gap-2 mb-2">
			<Icon name="external-link" className="w-4 h-4 text-fg-muted" />
			<h3 class="text-sm font-semibold text-fg">Mirrors</h3>
			{#if providers.length > 0}
				<span class="font-mono tabular-nums text-xs text-fg-subtle">({providers.length})</span>
			{/if}
		</div>
		{@render body()}
	</div>
{/if}
