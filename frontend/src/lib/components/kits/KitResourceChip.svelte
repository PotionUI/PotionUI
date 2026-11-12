<script lang="ts">
	import AnchoredPanel from '$lib/components/AnchoredPanel.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import MediaThumb from '$lib/components/media/MediaThumb.svelte';
	import { Button } from '$lib/components/ui';
	import { deriveKitChip, chipPopoverTitle } from '$lib/kits/kitChips';
	import { KIT_KIND_ICONS, type AcceptedKinds } from '$lib/kits/kitCounts';
	import { parseKitKey } from '$lib/kits/kitMarker';
	import { kitDirectory } from '$lib/stores/kits';

	let {
		itemKey,
		accepted = null,
		modelName = null,
		disabled = false,
		variant = 'default',
		onRemove,
		onSwitch
	}: {
		itemKey: string;
		accepted?: AcceptedKinds;
		modelName?: string | null;
		disabled?: boolean;
		variant?: 'default' | 'segment-composer';
		onRemove?: () => void;
		onSwitch?: () => void;
	} = $props();

	let key = $derived(parseKitKey(itemKey));
	let chip = $derived(key ? deriveKitChip(key, $kitDirectory, accepted, modelName) : null);
	let title = $derived(chip ? chipPopoverTitle(chip) : null);
	let anchor = $state<HTMLElement | undefined>();
	let open = $state(false);

	let tone = $derived(
		!chip ? '' : chip.state === 'kit-deleted' || chip.state === 'item-deleted' ? 'danger' : chip.warns ? 'warning' : ''
	);
	let prefix = $derived(chip && chip.handle && chip.itemName ? `${chip.handle}.` : '');
	let name = $derived(chip ? (chip.itemName ?? chip.label) : '');

	function activate() {
		if (chip?.warns) open = !open;
		else if (!disabled) onSwitch?.();
	}

	function keydown(event: KeyboardEvent) {
		if (event.key === 'Enter' || event.key === ' ') {
			event.preventDefault();
			activate();
		}
	}

	function remove() {
		open = false;
		onRemove?.();
	}
</script>

{#if chip}
	<span
		bind:this={anchor}
		class="{variant === 'segment-composer' ? 'chip resource-chip' : 'resource-chip inline-flex items-center rounded border mx-1 py-1 pl-1.5 pr-1.5'} {tone === 'danger'
			? 'border-danger/50 bg-danger/10 text-danger dangling'
			: tone === 'warning'
				? 'border-warning/50 bg-warning/10 text-warning'
				: variant === 'default'
					? 'border-line bg-surface-2 text-fg-muted'
					: ''} {disabled ? 'opacity-50' : ''}"
		contenteditable="false"
		role="button"
		tabindex="0"
		aria-label={chip.line ?? `${chip.label}`}
		data-kit-chip={chip.state}
		onclick={activate}
		onkeydown={keydown}
	>
		<span class="chip-main inline-flex items-center gap-1.5">
			<span class="chip-thumb">
				{#if chip.state === 'ok' && chip.bare && chip.thumbnails.length > 0}
					<span class="grid h-full w-full grid-cols-2 grid-rows-2 gap-px overflow-hidden">
						{#each chip.thumbnails.slice(0, 4) as thumb, index (index)}
							<img src={thumb} alt="" class="h-full w-full object-cover" />
						{/each}
					</span>
				{:else if chip.state === 'ok' && chip.thumbnails[0]}
					<MediaThumb url={chip.thumbnails[0]} kind={chip.kind} name={name} className="w-full h-full" rounded={false} iconClassName="icon" />
				{:else}
					<Icon
						name={chip.kind ? KIT_KIND_ICONS[chip.kind] : chip.state === 'pending' ? 'layers' : 'warning'}
						className="icon"
					/>
				{/if}
			</span>
			<span class="chip-label font-mono tabular-nums {chip.struck ? 'line-through' : ''}">
				{#if prefix}<span class="opacity-60">{prefix}</span>{/if}<strong class="font-semibold">{name}</strong>
			</span>
			{#if chip.bare && chip.count !== null}
				<span class="font-mono text-sm tabular-nums opacity-70" data-kit-chip-count>{chip.count}</span>
			{/if}
			{#if chip.warns}<Icon name="warning" className="icon" />{/if}
		</span>
	</span>

	{#if open && anchor}
		<AnchoredPanel
			{anchor}
			label={title ?? 'Kit'}
			width={300}
			escape
			testId="kit-chip-popover"
			class="rounded-lg border border-line-strong bg-surface-2 p-3 shadow-floating"
			onDismiss={() => (open = false)}
		>
			<div class="space-y-2">
				<div class="flex items-start gap-2">
					<Icon name="warning" className="mt-0.5 h-4 w-4 flex-shrink-0 {tone === 'danger' ? 'text-danger' : 'text-warning'}" />
					<div class="min-w-0">
						<p class="font-mono text-base font-semibold text-fg">{title}</p>
						<p class="mt-0.5 text-sm text-fg-muted">
							{#if chip.state === 'skipped'}
								{modelName ?? 'This model'} does not take {chip.kind}. The rest of the kit is used.
							{:else}
								{chip.line}
							{/if}
						</p>
					</div>
				</div>
				{#if !disabled}
					<div class="flex flex-col items-start gap-1">
						<Button variant="ghost" size="xs" onclick={remove}>Remove from prompt</Button>
					</div>
				{/if}
			</div>
		</AnchoredPanel>
	{/if}
{/if}

<style>
	span[data-kit-chip='skipped'],
	span[data-kit-chip='file-deleted'] {
		border-color: rgb(var(--warning) / 0.5);
		background: rgb(var(--warning) / 0.1);
		color: rgb(var(--warning));
	}

	span[data-kit-chip='kit-deleted'],
	span[data-kit-chip='item-deleted'] {
		border-color: rgb(var(--danger) / 0.5);
		background: rgb(var(--danger) / 0.1);
		color: rgb(var(--danger));
	}
</style>
