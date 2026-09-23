<script lang="ts">
	import { Badge, Button } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { presetRequirementsBadge } from '$lib/utils/presetRequirementsBadge';
	import { presetVramLabel } from './presetFilters';
	import PresetCoverTile from './PresetCoverTile.svelte';
	import type { PresetInfo } from '$lib/types/api';

	const VISIBLE_TAGS = 3;

	let {
		preset,
		installing = false,
		onOpen,
		onInstall
	}: {
		preset: PresetInfo;
		installing?: boolean;
		onOpen: (preset: PresetInfo) => void;
		onInstall: (preset: PresetInfo) => void;
	} = $props();

	const requirementsBadge = $derived(presetRequirementsBadge(preset.requirements_summary));
	const vramLabel = $derived(presetVramLabel(preset));
	const shownTags = $derived((preset.tags ?? []).slice(0, VISIBLE_TAGS));
	const hiddenTagCount = $derived(Math.max(0, (preset.tags ?? []).length - VISIBLE_TAGS));

	function handleKeydown(event: KeyboardEvent) {
		if (event.key === 'Enter' || event.key === ' ') {
			event.preventDefault();
			onOpen(preset);
		}
	}

	function stop(fn: () => void) {
		return (event: Event) => {
			event.stopPropagation();
			fn();
		};
	}
</script>

<div
	class="group flex min-w-0 flex-col overflow-hidden rounded-lg border border-line-strong bg-surface-1 shadow-raised transition-colors hover:border-line-hover"
	role="button"
	tabindex="0"
	data-preset-card
	aria-label={preset.name}
	onclick={() => onOpen(preset)}
	onkeydown={handleKeydown}
>
	<PresetCoverTile
		presetId={preset.id}
		presetName={preset.name}
		cover={preset.media?.cover}
		category={preset.category}
		class="h-[132px]"
	/>

	<div class="flex flex-1 min-w-0 flex-col gap-1.5 p-3">
		<Tooltip text={preset.name} wrapperClass="flex w-full min-w-0">
			<h3 class="min-w-0 flex-1 truncate text-sm font-semibold text-fg">{preset.name}</h3>
		</Tooltip>

		<div class="flex flex-wrap items-center gap-1.5">
			{#if preset.engine}<Badge size="sm" variant="signal">{preset.engine}</Badge>{/if}
			<Badge size="sm" class="font-mono tabular-nums">v{preset.version}</Badge>
			{#if preset.installed}
				<Badge size="sm" variant="success" dot>installed</Badge>
			{:else if requirementsBadge}
				<Badge size="sm" variant={requirementsBadge.variant}>{requirementsBadge.label}</Badge>
			{:else}
				<Badge size="sm">available</Badge>
			{/if}
		</div>

		<div class="mt-auto flex flex-wrap gap-1 pt-1">
			{#each shownTags as tag (tag)}
				<span class="rounded border border-line px-1.5 py-0.5 font-mono text-xs text-fg-subtle">{tag}</span>
			{/each}
			{#if hiddenTagCount > 0}
				<span class="rounded border border-line px-1.5 py-0.5 font-mono text-xs text-fg-subtle">+{hiddenTagCount}</span>
			{/if}
		</div>

		<div class="mt-2 flex items-center justify-between gap-2 border-t border-line pt-2">
			<span class="font-mono text-xs tabular-nums text-fg-subtle">{vramLabel ?? ''}</span>
			{#if preset.installed}
				<Button size="xs" variant="secondary" onclick={stop(() => onOpen(preset))}>Open</Button>
			{:else}
				<Button size="xs" variant="primary" loading={installing} onclick={stop(() => onInstall(preset))}>Install</Button>
			{/if}
		</div>
	</div>
</div>
