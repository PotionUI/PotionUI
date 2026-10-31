<script lang="ts">
	import { Badge } from '$lib/components/ui';
	import type { ModeConsentRequest, ModeConsentSlot, ModeConsentVariant } from '$lib/services/api/recipeModes';
	import {
		chosenVariant,
		selectedSlotIds,
		sizeLabel,
		usedByLabel,
		type ModeInstallState
	} from './modeInstallModel';

	let {
		request,
		state,
		onPick,
		onToggleExtra
	}: {
		request: ModeConsentRequest;
		state: ModeInstallState;
		onPick: (slotId: string, variantId: string) => void;
		onToggleExtra: (slotId: string, ticked: boolean) => void;
	} = $props();

	const selected = $derived(selectedSlotIds(request, state));
	const mainSlots = $derived(
		request.slots.filter((slot) => !slot.extra && selected.has(slot.id) && slot.variants.length > 1)
	);
	const otherSlots = $derived(
		request.slots.filter((slot) => !slot.extra && selected.has(slot.id) && slot.variants.length <= 1)
	);
	const extraSlots = $derived(request.slots.filter((slot) => slot.extra));
	const gpuLabel = $derived(request.gpu?.name ? request.gpu.name.replace(/^NVIDIA GeForce /, '') : null);

	function otherNote(slot: ModeConsentSlot, variant: ModeConsentVariant | null): string {
		if (slot.used_by.length <= 2) return usedByLabel(request, slot);
		return variant?.label ?? '';
	}

	function variantTone(slot: ModeConsentSlot, variantId: string): 'ok' | 'warn' {
		const variant = slot.variants.find((entry) => entry.id === variantId);
		return variant && !variant.recommended && variant.note ? 'warn' : 'ok';
	}
</script>

<div class="border-t border-line" data-mode-customize>
	{#each mainSlots as slot (slot.id)}
		{@const current = chosenVariant(slot, state)}
		<div class="px-5 pb-3 pt-4" data-customize-slot={slot.id}>
			<div class="mb-2 flex items-baseline gap-2">
				<h3 class="text-base font-semibold text-fg">{slot.label}</h3>
				<span class="text-sm text-fg-muted">{usedByLabel(request, slot)}</span>
			</div>
			<div class="space-y-1.5" role="radiogroup" aria-label={slot.label}>
				{#each slot.variants as variant (variant.id)}
					{@const isCurrent = current?.id === variant.id}
					{@const dimmed = variantTone(slot, variant.id) === 'warn' && !isCurrent}
					<button
						type="button"
						role="radio"
						aria-checked={isCurrent}
						data-variant={variant.id}
						class="flex w-full items-start gap-3 rounded border px-3 py-2 text-left transition-colors {isCurrent
							? 'border-signal bg-signal/10'
							: 'border-line bg-surface-1 hover:border-line-hover'} {dimmed ? 'opacity-70' : ''}"
						onclick={() => onPick(slot.id, variant.id)}
					>
						<span
							class="mt-1 flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border {isCurrent
								? 'border-signal'
								: 'border-line-strong'}"
							aria-hidden="true"
						>
							{#if isCurrent}<span class="h-1.5 w-1.5 rounded-full bg-signal-solid"></span>{/if}
						</span>
						<span class="flex min-w-0 flex-1 flex-col gap-0.5">
							<span class="flex flex-wrap items-center gap-1.5">
								<span class="font-mono text-sm font-semibold text-fg">{variant.label}</span>
								{#if variant.installed}
									<Badge variant="success" size="sm">Installed</Badge>
								{/if}
								{#if variant.best_fit && !variant.installed && gpuLabel}
									<Badge variant="signal" size="sm">Recommended for your {gpuLabel}</Badge>
								{/if}
							</span>
							{#if variant.note}
								<span class="text-sm {dimmed ? 'text-warning' : 'text-fg-muted'}">{variant.note}</span>
							{/if}
						</span>
						{#if variant.size_bytes != null}
							<span class="shrink-0 font-mono text-sm tabular-nums text-fg-muted">{sizeLabel(variant.size_bytes)}</span>
						{/if}
					</button>
				{/each}
			</div>
		</div>
	{/each}

	{#if otherSlots.length > 0}
		<div class="border-t border-line px-5 py-4" data-customize-others>
			<h3 class="mb-2 text-base font-semibold text-fg">Other files</h3>
			<ul class="space-y-2">
				{#each otherSlots as slot (slot.id)}
					{@const variant = chosenVariant(slot, state)}
					<li class="flex items-center gap-2 text-sm" data-other-slot={slot.id}>
						<span class="min-w-0 flex-1 truncate">
							<span class="font-semibold text-fg">{slot.label}</span>
							{#if otherNote(slot, variant)}
								<span class="ml-1 text-fg-muted">{otherNote(slot, variant)}</span>
							{/if}
						</span>
						{#if slot.manual}
							<span class="shrink-0 text-fg-muted">Pick your own file in Models</span>
						{:else if variant?.installed || slot.satisfied_by}
							<Badge variant="success" size="sm">Installed</Badge>
						{/if}
						{#if variant?.size_bytes != null && !slot.manual}
							<span class="w-16 shrink-0 text-right font-mono tabular-nums text-fg-muted">{sizeLabel(variant.size_bytes)}</span>
						{/if}
					</li>
				{/each}
			</ul>
		</div>
	{/if}

	{#if extraSlots.length > 0}
		<div class="border-t border-line px-5 py-3" data-customize-extras>
			{#each extraSlots as slot (slot.id)}
				{@const variant = chosenVariant(slot, state)}
				<label class="flex cursor-pointer items-center gap-3 py-1.5">
					<input
						type="checkbox"
						class="h-4 w-4 accent-signal-solid"
						checked={!!state.extras[slot.id]}
						disabled={variant?.installed}
						onchange={(event) => onToggleExtra(slot.id, event.currentTarget.checked)}
					/>
					<span class="min-w-0 flex-1">
						<span class="text-base font-medium text-fg">{slot.label}</span>
						{#if slot.reason}
							<span class="ml-1.5 text-sm text-fg-muted">{slot.reason}</span>
						{/if}
					</span>
					{#if variant?.installed}
						<Badge variant="success" size="sm">Installed</Badge>
					{:else if variant?.size_bytes != null}
						<span class="font-mono text-sm tabular-nums text-fg-muted">+ {sizeLabel(variant.size_bytes)}</span>
					{/if}
				</label>
			{/each}
		</div>
	{/if}
</div>
