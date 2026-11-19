<script lang="ts">
	import { Badge, Button, Spinner } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import type { EditorToolProps } from '$lib/editors/railTools';
	import { getGenerateController } from '$lib/tool-tabs/image/generate/context';
	import { FIX_FACES_TASK, clampStrength, idleDetections, kindLabel, scoreText, strengthText } from '$lib/tool-tabs/image/generate/detections';

	let {}: EditorToolProps = $props();

	const generate = getGenerateController()!;
	const ui = generate.ui;

	let found = $derived($ui.detections[FIX_FACES_TASK] ?? idleDetections());
	let offer = $derived.by(() => {
		void $ui;
		return generate.offerFor(FIX_FACES_TASK);
	});
	let bounds = $derived(offer?.strength ?? null);
	let strength = $derived.by(() => {
		void $ui;
		return bounds ? clampStrength(generate.strengthOf(FIX_FACES_TASK) ?? bounds.default, bounds) : null;
	});
	let ticked = $derived(found.items.filter((item) => item.on).length);
	let loading = $derived(found.status === 'loading');

	function setStrength(event: Event) {
		const value = Number((event.currentTarget as HTMLInputElement).value);
		if (bounds && Number.isFinite(value)) generate.setStrength(FIX_FACES_TASK, clampStrength(value, bounds));
	}

</script>

<div class="flex flex-col gap-2.5" data-fix-faces-list>
	<div class="flex items-center justify-between gap-2">
		<p class="font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">Faces and hands</p>
		<Button size="xs" icon="refresh" disabled={loading} onclick={() => void generate.detect(FIX_FACES_TASK)} attrs={{ 'data-fix-faces-find': '' }}>Find again</Button>
	</div>

	{#if loading}
		<p class="flex items-center gap-2 text-sm text-fg-muted" role="status" data-fix-faces-loading><Spinner size="sm" />Looking for faces and hands</p>
	{:else if found.status === 'unavailable'}
		<div class="flex items-start gap-2.5 rounded border border-line bg-surface-2 p-3" data-fix-faces-unavailable>
			<Icon name="info" className="mt-0.5 h-4 w-4 flex-none text-fg-subtle" />
			<div class="flex min-w-0 flex-col gap-1">
				<p class="text-sm font-semibold text-fg">Finding faces is not available</p>
				<p class="text-sm text-fg-muted">The detector is not installed on this server. You can still mark the areas to fix by hand on the picture.</p>
			</div>
		</div>
	{:else if found.status === 'failed'}
		<p class="text-sm text-danger" role="alert" data-fix-faces-error>{found.error ?? 'The search did not finish.'} Mark the areas by hand, or try again.</p>
	{:else if found.status === 'ready' && found.items.length === 0}
		<div class="flex items-start gap-2.5 rounded border border-line bg-surface-2 p-3" data-fix-faces-none>
			<Icon name="info" className="mt-0.5 h-4 w-4 flex-none text-fg-subtle" />
			<p class="text-sm text-fg-muted">No faces or hands found. Mark the areas to fix by hand on the picture.</p>
		</div>
	{:else if found.status === 'ready'}
		<ul class="m-0 flex list-none flex-col gap-1 p-0" aria-label="Found faces and hands">
			{#each found.items as item (item.id)}
				<li>
					<label
						class="flex min-h-7 cursor-pointer items-center gap-2.5 rounded border px-2.5 py-1 transition-colors {item.on ? 'border-line-strong bg-surface-2' : 'border-line bg-transparent'} {found.hover === item.id ? 'border-signal' : ''}"
						onmouseenter={() => generate.hoverDetection(FIX_FACES_TASK, item.id)}
						onmouseleave={() => generate.hoverDetection(FIX_FACES_TASK, null)}
						data-detection-row={item.id}
					>
						<input
							type="checkbox"
							class="h-4 w-4 flex-none rounded-sm border-line-strong bg-surface-2 accent-[rgb(var(--signal))]"
							checked={item.on}
							onchange={(event) => generate.tickDetection(FIX_FACES_TASK, item.id, (event.currentTarget as HTMLInputElement).checked)}
							aria-label="{kindLabel(item.kind)} {item.id}"
							data-detection-check={item.id}
						/>
						<Badge variant={item.kind === 'face' ? 'signal' : 'neutral'} size="sm">{kindLabel(item.kind)}</Badge>
						<span class="ml-auto font-mono text-sm tabular-nums text-fg-subtle">{scoreText(item.score)}</span>
					</label>
				</li>
			{/each}
		</ul>
		<div class="flex items-center justify-between text-sm text-fg-muted">
			<span class="font-mono tabular-nums" data-fix-faces-count>{ticked} of {found.items.length} ticked</span>
			<span class="flex items-center gap-2">
				<button type="button" class="rounded px-1 text-fg-muted hover:text-fg" onclick={() => generate.tickAllDetections(FIX_FACES_TASK, true)} data-fix-faces-all>All</button>
				<button type="button" class="rounded px-1 text-fg-muted hover:text-fg" onclick={() => generate.tickAllDetections(FIX_FACES_TASK, false)} data-fix-faces-none-button>None</button>
			</span>
		</div>
	{/if}
</div>

{#if bounds && strength !== null}
	<div class="flex flex-col gap-1.5" data-fix-faces-strength>
		<div class="flex items-center justify-between text-sm">
			<label class="text-fg-muted" for="fix-faces-strength">Strength</label>
			<span class="font-mono tabular-nums text-fg" data-fix-faces-strength-value>{strengthText(strength)}</span>
		</div>
		<input id="fix-faces-strength" type="range" class="w-full accent-[rgb(var(--signal))]" min={bounds.min} max={bounds.max} step="0.01" value={strength} oninput={setStrength} data-fix-faces-strength-input />
	</div>
{/if}
