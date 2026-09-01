<script lang="ts">
	/**
	 * Read-only drawer listing a GLB's materials and their PBR channels
	 * (base color, metallic/roughness, normal, occlusion, emissive) - texture
	 * swatch + resolution when a channel carries a texture, factor values
	 * always. Data comes from `glbMaterials.ts`'s pure GLB parse; this
	 * component only renders it.
	 */
	import Icon from '$lib/components/Icon.svelte';
	import { IconButton } from '$lib/components/ui';
	import type { MaterialInfo, MaterialChannel } from './glb/glbMaterials';

	export let materials: MaterialInfo[] = [];
	export let loading: boolean = false;
	export let onClose: () => void;

	function formatFactor(factor: MaterialChannel['factor']): string {
		if (factor == null) return '—';
		const values = Array.isArray(factor) ? factor : [factor];
		return values.map((v) => v.toFixed(2)).join(', ');
	}

	function baseColorCss(channel: MaterialChannel): string | null {
		if (channel.kind !== 'baseColor' || !Array.isArray(channel.factor)) return null;
		const [r, g, b, a = 1] = channel.factor;
		return `rgba(${Math.round(r * 255)}, ${Math.round(g * 255)}, ${Math.round(b * 255)}, ${a})`;
	}
</script>

<div class="absolute inset-y-0 right-0 z-40 w-full sm:w-72 bg-surface-1/95 backdrop-blur-sm border-l border-line flex flex-col">
	<div class="flex items-center justify-between px-3 py-2.5 border-b border-line flex-shrink-0">
		<div class="flex items-center gap-1.5">
			<Icon name="layers" className="w-3.5 h-3.5 text-fg-subtle" />
			<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted">Materials</span>
		</div>
		<IconButton icon="close" label="Close material inspector" size="sm" onclick={onClose} />
	</div>

	<div class="flex-1 overflow-y-auto p-3 space-y-3">
		{#if loading}
			<p class="text-2xs text-fg-subtle text-center py-4">Reading material data…</p>
		{:else if materials.length === 0}
			<p class="text-2xs text-fg-subtle text-center py-4">No material data in this file.</p>
		{:else}
			{#each materials as material (material.index)}
				<div class="bg-surface-2 rounded-lg overflow-hidden">
					<div class="flex items-center justify-between gap-2 px-2.5 py-2 border-b border-line">
						<span class="text-xs font-medium text-fg truncate" title={material.name}>{material.name}</span>
						<div class="flex items-center gap-1 flex-shrink-0">
							<span class="font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle">{material.alphaMode}</span>
							{#if material.doubleSided}
								<span
									class="font-mono text-2xs uppercase tracking-[0.06em] text-signal bg-signal/10 rounded px-1"
									title="Double-sided"
								>
									2S
								</span>
							{/if}
						</div>
					</div>

					<div class="p-2 grid grid-cols-2 gap-2">
						{#each material.channels as channel (channel.kind)}
							<div class="bg-surface-1 border border-line rounded p-1.5 flex flex-col gap-1">
								{#if channel.texture}
									<img
										src={channel.texture.objectUrl}
										alt={channel.label}
										class="w-full aspect-square object-cover rounded bg-surface-3"
									/>
								{:else}
									<div
										class="w-full aspect-square rounded bg-surface-3 flex items-center justify-center"
										style={baseColorCss(channel) ? `background-color: ${baseColorCss(channel)}` : undefined}
									>
										{#if !baseColorCss(channel)}
											<Icon name="sliders" className="w-4 h-4 text-fg-disabled" />
										{/if}
									</div>
								{/if}
								<span class="text-2xs text-fg-muted truncate" title={channel.label}>{channel.label}</span>
								{#if channel.texture}
									<span class="font-mono tabular-nums text-2xs text-fg-subtle">
										{channel.texture.width ?? '?'}×{channel.texture.height ?? '?'}
									</span>
								{/if}
								<span class="font-mono tabular-nums text-2xs text-fg-disabled truncate" title={formatFactor(channel.factor)}>
									{formatFactor(channel.factor)}
								</span>
							</div>
						{/each}
					</div>
				</div>
			{/each}
		{/if}
	</div>
</div>
