<script lang="ts">
	import type { SetupConsentGpuProfile, SetupConsentSlot } from '$lib/services/api/setup';
	import { Badge } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { formatBytes } from '$lib/utils/format';

	let {
		gpu,
		slots,
		selections,
		onPick
	}: {
		gpu?: SetupConsentGpuProfile | null;
		slots: SetupConsentSlot[];
		selections: Record<string, string>;
		onPick: (slotId: string, variantId: string) => void;
	} = $props();

	function resolved(slot: SetupConsentSlot): string {
		return selections[slot.id] ?? slot.recommended_variant_id;
	}

	function selectVariant(slot: SetupConsentSlot, variantId: string, event?: KeyboardEvent) {
		if (event) {
			if (event.key !== 'Enter' && event.key !== ' ') return;
			event.preventDefault();
		}
		onPick(slot.id, variantId);
	}

	const totalBytes = $derived(
		slots.reduce((sum, slot) => {
			const variant = slot.variants.find((v) => v.id === resolved(slot));
			if (!variant || variant.installed || variant.size_bytes == null) return sum;
			return sum + variant.size_bytes;
		}, 0)
	);

	interface Attribution {
		uploader: string;
		source_url: string | null;
		repo_id: string | null;
	}

	const attributions = $derived.by(() => {
		const seen = new Map<string, Attribution>();
		for (const slot of slots) {
			for (const variant of slot.variants) {
				if (!variant.uploader) continue;
				const key = `${variant.uploader}|${variant.source_url ?? variant.repo_id ?? ''}`;
				if (!seen.has(key)) {
					seen.set(key, {
						uploader: variant.uploader,
						source_url: variant.source_url,
						repo_id: variant.repo_id
					});
				}
			}
		}
		return Array.from(seen.values());
	});
</script>

<div class="space-y-4">
	{#if gpu}
		<div
			class="inline-flex items-center gap-2 rounded border border-line-strong bg-surface-2 px-3 py-1.5 text-sm"
		>
			<Icon name="cpu" className="w-4 h-4 text-fg-subtle" />
			<span class="font-semibold text-fg">{gpu.name ?? 'Unknown GPU'}</span>
			<span class="text-fg-subtle">·</span>
			<span class="font-mono tabular-nums text-fg-subtle">{Math.round(gpu.vram_gb)} GB</span>
			<span class="text-fg-subtle">·</span>
			<span class="text-fg-subtle">{gpu.generation_label}</span>
		</div>
	{/if}

	{#each slots as slot (slot.id)}
		<div class="space-y-2" data-consent-slot={slot.id}>
			<span class="text-sm font-semibold text-fg-muted font-mono uppercase tracking-wide">
				{slot.label}
			</span>

			{#if slot.variants.length <= 1}
				{@const variant = slot.variants[0]}
				<div
					class="flex items-center gap-2 rounded border px-3 py-2 text-sm {variant.installed
						? 'border-success/30 bg-success/5'
						: 'border-line bg-surface-2'}"
				>
					<Icon
						name="check"
						className="w-3.5 h-3.5 shrink-0 {variant.installed
							? 'text-success'
							: 'text-fg-subtle'}"
					/>
					<span class="flex-1 min-w-0 truncate font-mono text-fg">{variant.filename}</span>
					{#if variant.installed}
						<Badge size="sm" variant="success">Installed</Badge>
					{:else if variant.size_bytes != null}
						<span class="font-mono tabular-nums text-fg-subtle shrink-0">
							{formatBytes(variant.size_bytes)}
						</span>
					{/if}
					{#if variant.gated}
						<span class="flex items-center gap-1 text-sm text-warning shrink-0">
							<span>Licence required</span>
							{#if variant.license_url}
								<a
									href={variant.license_url}
									target="_blank"
									rel="noreferrer"
									class="underline decoration-dotted"
								>
									licence
								</a>
							{/if}
						</span>
					{/if}
				</div>
			{:else}
				<div
					class="grid gap-2"
					style="grid-template-columns: repeat({slot.variants.length}, minmax(0, 1fr));"
					role="radiogroup"
					aria-label={slot.label}
				>
					{#each slot.variants as variant (variant.id)}
						{@const chosen = resolved(slot) === variant.id}
						{@const isRecommended = variant.id === slot.recommended_variant_id}
						<div
							role="radio"
							tabindex="0"
							aria-checked={chosen}
							data-consent-variant={variant.id}
							class="relative flex min-w-0 flex-col gap-1.5 rounded border px-3 py-2.5 cursor-pointer {chosen
								? 'border-signal bg-signal/10'
								: 'border-line-strong bg-surface-2 hover:border-line-hover'} {variant.fast === false
								? 'opacity-75'
								: ''}"
							onclick={() => selectVariant(slot, variant.id)}
							onkeydown={(event) => selectVariant(slot, variant.id, event)}
						>
							<div class="flex items-center justify-between gap-2 min-w-0">
								{#if isRecommended}
									<Tooltip text={slot.reason} wrapperClass="flex min-w-0">
										<span class="text-sm font-semibold text-signal truncate">Recommended</span>
									</Tooltip>
								{:else}
									<span></span>
								{/if}
								{#if variant.note}
									<Tooltip text={variant.note} wrapperClass="flex shrink-0">
										<Icon name="info" className="w-3.5 h-3.5 text-fg-subtle" />
									</Tooltip>
								{/if}
							</div>
							<div class="flex items-center gap-2 min-w-0">
								<span class="text-base font-semibold text-fg truncate">{variant.label}</span>
								{#if variant.precision}
									<span
										class="font-mono text-sm text-fg-subtle border border-line rounded px-1 shrink-0"
									>
										{variant.precision}
									</span>
								{/if}
							</div>
							<div class="flex items-center gap-2 text-sm">
								{#if variant.installed}
									<Badge size="sm" variant="success">Installed</Badge>
								{:else}
									<span class="font-mono tabular-nums text-fg-muted">
										{formatBytes(variant.size_bytes ?? 0)}
									</span>
								{/if}
							</div>
							{#if variant.gated}
								<div class="flex items-center gap-1 text-sm text-warning">
									<span>Licence required</span>
									{#if variant.license_url}
										<a
											href={variant.license_url}
											target="_blank"
											rel="noreferrer"
											class="underline decoration-dotted"
											onclick={(event) => event.stopPropagation()}
										>
											licence
										</a>
									{/if}
								</div>
							{/if}
						</div>
					{/each}
				</div>
			{/if}
		</div>
	{/each}

	{#each attributions as attribution (attribution.uploader + '|' + (attribution.source_url ?? ''))}
		<div class="flex items-start gap-2 rounded border border-line bg-surface-2 px-3 py-2 text-sm text-fg-subtle">
			<Icon name="info" className="w-3.5 h-3.5 shrink-0 mt-0.5" />
			<span>
				Free to download thanks to Hugging Face and {attribution.uploader}, who uploaded this
				model.
				{#if attribution.source_url}
					<a
						href={attribution.source_url}
						target="_blank"
						rel="noreferrer"
						class="text-signal hover:underline"
					>
						{attribution.repo_id ?? attribution.source_url}
					</a>
				{/if}
			</span>
		</div>
	{/each}

	<div class="flex items-center justify-between text-sm border-t border-line pt-2">
		<span class="text-fg-muted">Total to download</span>
		<span class="font-mono tabular-nums text-fg font-semibold">{formatBytes(totalBytes)}</span>
	</div>
</div>
