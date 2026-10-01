<script lang="ts">
	import { SWATCHES } from './palette';
	import type { PaintTool, ToolSettings } from './types';

	export let tool: PaintTool | undefined;
	export let settings: ToolSettings;
	export let compact: boolean = false;
	export let onSize: (value: number) => void;
	export let onOpacity: (value: number) => void;
	export let onColor: (value: string) => void;
	export let onTolerance: (value: number) => void;

	$: options = tool?.options ?? [];
	$: hasSize = options.includes('size');
	$: hasOpacity = options.includes('opacity');
	$: hasColor = options.includes('color');
	$: hasTolerance = options.includes('tolerance');

	function number(event: Event): number {
		return Number((event.currentTarget as HTMLInputElement).value);
	}

	function text(event: Event): string {
		return (event.currentTarget as HTMLInputElement).value;
	}
</script>

{#if compact}
	<div class="flex items-center gap-3 px-3 py-2 border-t border-line bg-surface-1">
		{#if hasColor}
			<label
				class="relative shrink-0 w-8 h-8 rounded border border-line-hover focus-within:ring-2 focus-within:ring-accent"
				style="background: {settings.color}"
			>
				<input
					type="color"
					value={settings.color}
					aria-label="Colour"
					class="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
					on:input={(event) => onColor(text(event))}
				/>
			</label>
		{/if}
		{#if hasSize}
			<input
				type="range"
				min="1"
				max="200"
				value={settings.size}
				aria-label="Size"
				class="flex-1 min-w-0 h-8 accent-signal"
				on:input={(event) => onSize(number(event))}
			/>
			<span class="w-12 text-right font-mono text-xs tabular-nums text-fg-muted">{settings.size}px</span>
		{/if}
		{#if hasTolerance && !hasSize}
			<input
				type="range"
				min="0"
				max="100"
				value={settings.tolerance}
				aria-label="Tolerance"
				class="flex-1 min-w-0 h-8 accent-signal"
				on:input={(event) => onTolerance(number(event))}
			/>
			<span class="w-12 text-right font-mono text-xs tabular-nums text-fg-muted">{settings.tolerance}</span>
		{/if}
		<slot />
	</div>
{:else}
	<div class="flex flex-col gap-3 p-3">
		<p class="font-mono text-xs uppercase tracking-[0.08em] text-fg-subtle">{tool?.label ?? ''}</p>

		{#if hasSize}
			<div class="flex items-center gap-2">
				<label for="paint-size" class="w-16 shrink-0 text-xs text-fg-muted">Size</label>
				<input
					id="paint-size"
					type="range"
					min="1"
					max="200"
					value={settings.size}
					class="flex-1 min-w-0 h-6 accent-signal"
					on:input={(event) => onSize(number(event))}
				/>
				<span class="w-12 text-right font-mono text-xs tabular-nums text-fg-muted">{settings.size}px</span>
			</div>
		{/if}

		{#if hasOpacity}
			<div class="flex items-center gap-2">
				<label for="paint-opacity" class="w-16 shrink-0 text-xs text-fg-muted">Opacity</label>
				<input
					id="paint-opacity"
					type="range"
					min="1"
					max="100"
					value={settings.opacity}
					class="flex-1 min-w-0 h-6 accent-signal"
					on:input={(event) => onOpacity(number(event))}
				/>
				<span class="w-12 text-right font-mono text-xs tabular-nums text-fg-muted">{settings.opacity}%</span>
			</div>
		{/if}

		{#if hasTolerance}
			<div class="flex items-center gap-2">
				<label for="paint-tolerance" class="w-16 shrink-0 text-xs text-fg-muted">Tolerance</label>
				<input
					id="paint-tolerance"
					type="range"
					min="0"
					max="100"
					value={settings.tolerance}
					class="flex-1 min-w-0 h-6 accent-signal"
					on:input={(event) => onTolerance(number(event))}
				/>
				<span class="w-12 text-right font-mono text-xs tabular-nums text-fg-muted">{settings.tolerance}</span>
			</div>
		{/if}

		{#if hasColor}
			<div class="flex items-center gap-2">
				<span class="w-16 shrink-0 text-xs text-fg-muted">Colour</span>
				<label
					class="relative shrink-0 w-14 h-7 rounded border border-line-hover focus-within:ring-2 focus-within:ring-accent"
					style="background: {settings.color}"
				>
					<input
						type="color"
						value={settings.color}
						aria-label="Colour"
						class="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
						on:input={(event) => onColor(text(event))}
					/>
				</label>
				<span class="font-mono text-xs uppercase tabular-nums text-fg-muted">{settings.color}</span>
			</div>
			<div class="grid grid-cols-8 gap-1">
				{#each SWATCHES as swatch (swatch)}
					<button
						type="button"
						aria-label="Use colour {swatch}"
						class="h-6 rounded-sm border border-line-hover {settings.color.toLowerCase() === swatch ? 'ring-2 ring-signal' : ''}"
						style="background: {swatch}"
						on:click={() => onColor(swatch)}
					></button>
				{/each}
			</div>
		{/if}

		{#if tool?.hint}
			<p class="text-xs leading-relaxed text-fg-subtle">{tool.hint}</p>
		{/if}
		<slot />
	</div>
{/if}
