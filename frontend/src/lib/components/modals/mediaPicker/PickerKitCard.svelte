<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { Badge } from '$lib/components/ui';
	import KitMosaic from '$lib/components/kits/KitMosaic.svelte';
	import type { Kit, KitItemKind } from '$lib/kits/types';

	const SHORT: Record<KitItemKind, string> = { image: 'img', video: 'vid', audio: 'aud', text: 'txt' };
	const RATIO = 1.6;

	let {
		kit,
		multiple,
		selected = false,
		disabled = false,
		openDisabled = false,
		reason = '',
		accepted,
		onOpen,
		onToggle
	}: {
		kit: Kit;
		multiple: boolean;
		selected?: boolean;
		disabled?: boolean;
		openDisabled?: boolean;
		reason?: string;
		accepted: (kind: KitItemKind) => boolean;
		onOpen: (kit: Kit) => void;
		onToggle: (kit: Kit) => void;
	} = $props();

	let counts = $derived(
		(Object.entries(kit.counts) as Array<[KitItemKind, number]>)
			.filter(([, count]) => count > 0)
			.sort((a, b) => Number(accepted(b[0])) - Number(accepted(a[0])))
			.map(([kind, count]) => `${count} ${SHORT[kind]}`)
			.join(' · ')
	);
	let openable = $derived(!openDisabled && kit.item_count > 0);
	let sizing = `--r:${RATIO};flex:${RATIO} 1 calc(${RATIO} * var(--pk-rh));min-width:calc(${RATIO} * var(--pk-min));max-width:calc(${RATIO} * var(--pk-rh) * 1.3)`;
</script>

<div class="flex min-w-0 flex-col" style={sizing} data-pick-kit={kit.id} data-selected={selected ? 'true' : undefined} data-disabled={disabled ? 'true' : undefined}>
	<div class="group relative">
		<button
			type="button"
			disabled={!openable}
			aria-label="Open {kit.name}"
			data-pick-main
			data-pick-focus
			class="block w-full rounded-lg text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-signal {openable
				? 'cursor-pointer'
				: 'cursor-not-allowed'}"
			onclick={() => onOpen(kit)}
		>
			<span
				class="relative block w-full overflow-hidden rounded-lg border bg-surface-2 transition-colors {selected
					? 'border-line-hover ring-2 ring-signal'
					: 'border-line group-hover:border-line-hover'} {disabled ? 'opacity-40' : ''}"
				style="aspect-ratio:{RATIO}"
			>
				<KitMosaic items={kit.preview_items} class="h-full w-full" />
				{#if selected}
					<span class="pointer-events-none absolute inset-0 bg-signal/15"></span>
				{/if}
			</span>
		</button>
		{#if multiple}
			<label
				class="absolute left-2 top-2 z-10 flex h-7 w-7 items-center justify-center rounded border border-line-strong bg-surface-1/90 focus-within:ring-2 focus-within:ring-signal {disabled
					? 'cursor-not-allowed'
					: 'cursor-pointer'}"
			>
				<input
					type="checkbox"
					class="h-4 w-4 accent-signal"
					checked={selected}
					{disabled}
					data-pick-focus
					data-whole-kit={kit.id}
					aria-label="Use whole kit {kit.name}"
					onchange={() => onToggle(kit)}
					onkeydown={(event) => {
						if (event.key === 'Enter') {
							event.preventDefault();
							onToggle(kit);
						}
					}}
				/>
			</label>
		{/if}
	</div>
	<p class="mt-1 flex min-w-0 items-center gap-1.5 text-sm text-fg">
		<span class="truncate">{kit.name}</span>
		{#if kit.label}<span class="flex-shrink-0"><Badge size="sm">{kit.label}</Badge></span>{/if}
	</p>
	{#if reason}
		<p class="flex min-w-0 items-center gap-1 text-sm text-fg-subtle" data-pick-reason>
			<Icon name="info" className="h-3.5 w-3.5 flex-shrink-0" />
			<span class="block truncate">{reason}</span>
		</p>
	{:else}
		<p class="truncate font-mono text-sm tabular-nums text-fg-subtle">{counts}</p>
	{/if}
</div>
