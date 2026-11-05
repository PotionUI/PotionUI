<script lang="ts">
	import { Badge, Button, IconButton } from '$lib/components/ui';
	import { DetailBody, DetailHeader } from '$lib/components/detail';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { isMobile } from '$lib/stores/viewport';
	import PathText from './PathText.svelte';
	import StateMark from './StateMark.svelte';
	import type { FoldersController } from './controller.svelte';
	import { bindingFiles, bindingKey, formatBytes, formatCount, typeRows } from './derive';

	let { ctrl, onBack }: { ctrl: FoldersController; onBack?: () => void } = $props();

	const ov = $derived(ctrl.overview);
	const types = $derived(ov ? typeRows(ov) : []);
</script>

<DetailHeader title="Downloads & priority" icon="download" backLabel={$isMobile ? 'Models' : undefined} {onBack}>
	{#snippet subtitle()}<span class="font-sans text-[12px]">Where new downloads are saved, and which folder wins when a model exists twice.</span>{/snippet}
</DetailHeader>
<DetailBody>
	<section class="overflow-hidden rounded-lg border border-line bg-surface-1 shadow-raised" data-routing aria-label="Downloads and priority">
		<p class="border-b border-line px-4 py-2.5 text-[12px] text-fg-muted sm:px-5">New downloads go to the folder marked Downloads. When a model exists in more than one folder, the one listed first is used. Existing files never move.</p>
		{#each types as t (t.type)}
			{@const order = ov?.types.find((x) => x.model_type === t.type)?.order ?? []}
			<div class="flex flex-wrap items-baseline gap-x-3 border-t border-line bg-surface-2 px-4 py-2" data-type-card>
				<span class="text-sm font-semibold text-fg">{t.label}</span>
				<span class="font-mono text-[12px] tabular-nums text-fg-subtle">{t.folders.length} folder{t.folders.length === 1 ? '' : 's'} - {formatCount(t.files)} files - {formatBytes(t.bytes, 1)}</span>
			</div>
			{#each t.folders as f, i (bindingKey(f.root.id, f.binding))}
				{@const first = i === 0 || t.folders[i - 1].root.id !== f.root.id}
				<div class="grid grid-cols-[22px_minmax(0,1fr)_auto_60px] items-center gap-x-3 border-t border-line py-1.5 pl-4 pr-3 md:grid-cols-[28px_minmax(0,1fr)_minmax(0,1.3fr)_60px_80px_116px_60px]" data-routing-row>
					<span class="font-mono text-[12px] tabular-nums text-fg-subtle">{first && t.multi ? f.position + 1 : ''}</span>
					<div class="min-w-0">
						<span class="flex min-w-0 items-center gap-2">
							<button type="button" class="truncate text-sm text-fg hover:underline" onclick={() => (ctrl.selected = f.root.id)}>{f.root.label}</button>
							<StateMark root={f.root} />
						</span>
						<span class="md:hidden"><PathText text={f.binding.subdir} full={f.binding.path} /></span>
					</div>
					<div class="hidden min-w-0 md:block"><PathText text={f.binding.subdir} full={f.binding.path} /></div>
					<span class="hidden text-right font-mono text-[12px] tabular-nums text-fg-muted md:block">{formatCount(bindingFiles(f.binding))}</span>
					<span class="hidden text-right font-mono text-[12px] tabular-nums text-fg-muted md:block">{formatBytes(f.binding.size_bytes, 1)}</span>
					<div class="flex items-center">
						{#if f.binding.is_write}<Badge variant="signal" size="sm">Downloads</Badge>
						{:else if !f.root.read_only && f.root.state === 'online'}<Button variant="ghost" size="sm" class="max-md:hidden" loading={ctrl.isBusy(`dl:${bindingKey(f.root.id, f.binding)}`)} onclick={() => ctrl.makeDownloads(f.root, f.binding)}>Use for downloads</Button>{/if}
					</div>
					<div class="flex items-center justify-end">
						{#if t.multi && first}
							<Tooltip text="Use earlier" position="top"><IconButton icon="chevron-up" label="Move {f.root.label} earlier for {t.label}" size="xs" disabled={f.position === 0 || ctrl.isBusy(`order:${t.type}`)} onclick={() => ctrl.moveInType(t.type, order, f.position, -1)} /></Tooltip>
							<Tooltip text="Use later" position="top"><IconButton icon="chevron-down" label="Move {f.root.label} later for {t.label}" size="xs" disabled={f.position === order.length - 1 || ctrl.isBusy(`order:${t.type}`)} onclick={() => ctrl.moveInType(t.type, order, f.position, 1)} /></Tooltip>
						{/if}
					</div>
				</div>
			{/each}
		{/each}
	</section>
</DetailBody>
